"""Compare generated output of the current checkout with a released version over an input x option matrix.

Usage:
  python scripts/compare_release.py --base 0.85.0 [--types pydantic_v2.BaseModel,msgspec.Struct]
      [--options 'default;--collapse-root-models'] [--extra '--input-file-type openapi'] [--import-check]
      [--show-diff N] [-j N] INPUT [INPUT ...]

Each option set is one quoted string of extra CLI arguments (`default` means none), separated by `;`.
The candidate is the interpreter running this script unless `--candidate-python` is given. The released version
is installed once into a cached virtual environment (needs `uv`) with the candidate's formatter, pydantic and
msgspec versions pinned, so only generator changes show up as differences. Released outputs are cached as well,
so reruns only generate the candidate side.

Generation runs in warm worker processes: each worker imports datamodel-code-generator once and forks a fresh
child per run, so runs do not pay interpreter start-up and import time and do not share state. POSIX only.

Prints one line per differing or failing run and a summary; exits 1 when any run differs or fails on the
candidate only.
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import functools
import hashlib
import importlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

DEFAULT_TYPES = (
    "pydantic_v2.BaseModel",
    "pydantic_v2.dataclass",
    "dataclasses.dataclass",
    "typing.TypedDict",
    "msgspec.Struct",
)
COMMON_ARGS = ("--disable-timestamp", "--formatters", "black", "isort")
WARM_IMPORTS = ("black", "isort", "pydantic", "msgspec", "graphql", "yaml")
MODULAR_OUTPUT_ERRORS = ("--output <directory>", "require an output directory")
CACHE_FORMAT = 2


def _default_cache_dir() -> Path:
    root = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(root) / "datamodel-code-generator" / "compare-release"


# Worker side: runs inside the base or candidate interpreter.


def _exit_code(main: Callable[[list[str]], Any], argv: list[str]) -> int:
    try:
        result = main(argv)
    except SystemExit as exc:
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 1
    except Exception:  # ruff: ignore[blind-except] - report any generator crash as a failed run
        traceback.print_exc()
        return 1
    return int(result or 0)


def _import_modules(out: str) -> int:
    root = Path(out)
    sys.path.insert(0, out)
    files = sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)
    try:
        for file in files:
            module = ".".join(file.relative_to(root).with_suffix("").parts).removesuffix(".__init__")
            importlib.import_module(module)
    except Exception as exc:  # ruff: ignore[blind-except] - any import failure is the result
        sys.stderr.write(traceback.format_exception_only(type(exc), exc)[-1])
        return 1
    return 0


def _run_child(job: dict[str, Any], main: Callable[[list[str]], Any]) -> int:
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.alarm(job["timeout"])
    log = os.open(job["log"], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.dup2(log, 1)
    os.dup2(log, 2)
    os.close(log)
    os.chdir(job["cwd"])
    if job["kind"] == "import":
        return _import_modules(job["out"])
    return _exit_code(main, job["args"])


def _worker() -> None:
    sys.argv[0] = "datamodel-codegen"
    warnings.simplefilter("ignore")
    from datamodel_code_generator.__main__ import main  # ruff: ignore[import-outside-top-level] - imported once per worker

    # Importing the CLI installs its own SIGINT handler; the driver stops workers by closing their stdin.
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    for name in WARM_IMPORTS:
        with contextlib.suppress(ImportError):
            importlib.import_module(name)
    for line in sys.stdin:
        job = json.loads(line)
        pid = os.fork()
        if pid == 0:  # pragma: no cover - child process
            code = 1
            try:
                code = _run_child(job, main)
            finally:
                sys.stdout.flush()
                sys.stderr.flush()
                os._exit(code)
        _, status = os.waitpid(pid, 0)
        sys.stdout.write(json.dumps({"rc": os.waitstatus_to_exitcode(status)}) + "\n")
        sys.stdout.flush()


# Driver side.


class Worker:
    """A warm worker process for one interpreter."""

    def __init__(self, python: Path, env: dict[str, str]) -> None:
        """Start the worker."""
        self.process = subprocess.Popen(
            [str(python), str(Path(__file__).resolve()), "--worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            env=env,
        )

    def run(self, job: dict[str, Any]) -> int:
        """Run one job in a fresh child of the worker and return its exit code."""
        stdin, stdout = self.process.stdin, self.process.stdout
        assert stdin is not None
        assert stdout is not None
        stdin.write(json.dumps(job) + "\n")
        stdin.flush()
        reply = stdout.readline()
        if not reply:
            msg = f"worker exited with {self.process.wait()}"
            raise RuntimeError(msg)
        return json.loads(reply)["rc"]

    def close(self) -> None:
        """Stop the worker."""
        if self.process.stdin:
            self.process.stdin.close()
        self.process.wait()


@dataclass(frozen=True)
class Side:
    """One interpreter to generate with."""

    name: str
    python: Path


@dataclass
class Outcome:
    """Result of generating one run on one side."""

    ok: bool
    error: str
    tree: dict[str, str]
    import_error: str
    cacheable: bool = True


def _probe(python: Path) -> tuple[str, str]:
    """Return the interpreter's `major.minor` version and its installed datamodel-code-generator version."""
    code = (
        "import sys\n"
        "from importlib.metadata import version\n"
        "print(f'{sys.version_info[0]}.{sys.version_info[1]}', version('datamodel-code-generator'))\n"
    )
    python_version, dcg_version = subprocess.run(
        [str(python), "-c", code], capture_output=True, text=True, check=True
    ).stdout.split()
    return python_version, dcg_version


def _is_generator_requirement(line: str) -> bool:
    """Return whether a `pip freeze` line installs datamodel-code-generator itself, e.g. this checkout."""
    if line.startswith("-e "):
        source = Path(line.removeprefix("-e ").removeprefix("file://"))
        return any((source / sub / "datamodel_code_generator").is_dir() for sub in ("src", "."))
    return line.lower().replace("_", "-").startswith(("datamodel-code-generator==", "datamodel-code-generator @"))


def _base_python(version: str, candidate: Path, cache_dir: Path) -> Path:
    """Install the release next to the candidate's other packages, pinned to the same versions."""
    python_version, _ = _probe(candidate)
    freeze = subprocess.run(
        ["uv", "pip", "freeze", "--python", str(candidate)], capture_output=True, text=True, check=True
    ).stdout
    pins = sorted(line for line in freeze.splitlines() if line and not _is_generator_requirement(line))
    key = hashlib.sha256(json.dumps([version, python_version, pins]).encode()).hexdigest()[:12]
    root = cache_dir / "venvs" / f"{version}-py{python_version}-{key}"
    complete = root / ".complete"
    root.parent.mkdir(parents=True, exist_ok=True)
    import fcntl  # ruff: ignore[import-outside-top-level] - POSIX only; keeps the module importable elsewhere

    with (root.parent / f"{root.name}.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # another run may be installing the same environment
        if not complete.exists():
            _install_base(version, python_version, pins, root)
            complete.touch()
    return root / "bin" / "python"


def _install_base(version: str, python_version: str, pins: list[str], root: Path) -> None:
    """Create the release environment with the candidate's package versions pinned."""
    print(f"installing datamodel-code-generator {version} into {root}", file=sys.stderr, flush=True)
    shutil.rmtree(root, ignore_errors=True)
    subprocess.run(["uv", "venv", "-q", "--python", python_version, str(root)], check=True)
    requirements = root / "pins.txt"
    requirements.write_text("\n".join(pins) + "\n", encoding="utf-8")
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "-q",
            "--python",
            str(root / "bin" / "python"),
            "-r",
            str(requirements),
            f"datamodel-code-generator=={version}",
        ],
        check=True,
    )


def _tree(path: Path) -> dict[str, str]:
    return {
        file.relative_to(path).as_posix(): file.read_text(encoding="utf-8")
        for file in sorted(path.rglob("*.py"))
        if "__pycache__" not in file.parts
    }


def _last_line(path: Path) -> str:
    lines = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    return lines[-1] if lines else "error"


class Runner:
    """Runs one side of the matrix through per-thread warm workers."""

    def __init__(self, side: Side, env: dict[str, str], *, import_check: bool, timeout: int) -> None:
        """Prepare a runner; workers start lazily."""
        self.side = side
        self.env = env
        self.import_check = import_check
        self.timeout = timeout
        self.local = threading.local()
        self.workers: list[Worker] = []
        self.lock = threading.Lock()

    def _worker(self) -> Worker:
        worker = getattr(self.local, "worker", None)
        if worker is None:
            worker = Worker(self.side.python, self.env)
            self.local.worker = worker
            with self.lock:
                self.workers.append(worker)
        return worker

    def generate(self, args: list[str], tmp: Path) -> Outcome:
        """Generate one run into a fresh directory under `tmp`."""
        out = tmp / self.side.name
        out.mkdir()
        log = tmp / f"{self.side.name}.log"
        worker = self._worker()
        job = {
            "kind": "generate",
            "cwd": str(tmp),
            "log": str(log),
            "timeout": self.timeout,
            "args": [*args, "--output", str(out / "model.py")],
        }
        rc = worker.run(job)
        if rc != 0 and any(text in log.read_text(encoding="utf-8", errors="replace") for text in MODULAR_OUTPUT_ERRORS):
            job["args"] = [*args, "--output", str(out / "pkg")]
            rc = worker.run(job)
        if rc != 0:
            return Outcome(ok=False, error=self._error(rc, log), tree={}, import_error="", cacheable=rc > 0)
        import_error = ""
        if self.import_check:
            import_log = tmp / f"{self.side.name}-import.log"
            job = {"kind": "import", "cwd": str(tmp), "log": str(import_log), "timeout": self.timeout, "out": str(out)}
            if (rc := worker.run(job)) != 0:
                import_error = self._error(rc, import_log)
        return Outcome(ok=True, error="", tree=_tree(out), import_error=import_error, cacheable=rc >= 0)

    def _error(self, rc: int, log: Path) -> str:
        if rc == -signal.SIGALRM:
            return f"timed out after {self.timeout}s"
        return _last_line(log)

    def close(self) -> None:
        """Stop all workers."""
        for worker in self.workers:
            worker.close()


def _hashed_files(path: Path) -> list[Path]:
    """Return the files a hash covers: the file itself, or a directory's files outside hidden and cache dirs."""
    if not path.is_dir():
        return [path]
    return [
        file
        for file in sorted(path.rglob("*"))
        if file.is_file()
        and not any(part.startswith(".") or part == "__pycache__" for part in file.relative_to(path).parts[:-1])
    ]


@functools.cache
def _content_hash(path: Path) -> str | None:
    """Hash a file, or every file under a directory with its relative path; None when something is unreadable."""
    digest = hashlib.sha256()
    try:
        for file in _hashed_files(path):
            digest.update(file.relative_to(path).as_posix().encode() + b"\0" if file != path else b"")
            digest.update(hashlib.sha256(file.read_bytes()).digest())
    except OSError:
        return None
    return digest.hexdigest()


LOCAL_REFERENCE = re.compile(r"""\$ref["']?\s*:\s*["']?([^"'#\s,}]+)""")


@functools.cache
def _referenced_files(path: Path) -> frozenset[Path]:
    """Return the local files a schema file or directory references through `$ref`, followed transitively."""
    found: set[Path] = set()
    pending = list(_hashed_files(path))
    while pending:
        file = pending.pop()
        try:
            text = file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for target in LOCAL_REFERENCE.findall(text):
            if "://" in target:
                continue
            referenced = (file.parent / target).resolve()
            if referenced not in found and referenced.is_file():
                found.add(referenced)
                pending.append(referenced)
    return frozenset(found)


def _path_arguments(args: list[str]) -> list[Path]:
    """Return existing absolute paths among the arguments, including `--option=path` values."""
    values = [arg.partition("=")[2] if arg.startswith("--") else arg for arg in args]
    return [Path(value) for value in values if value and Path(value).is_absolute() and Path(value).exists()]


class OutputCache:
    """Base outputs keyed by the input, the files it can reference, and the arguments.

    The key covers every file under the input's directory, the local files the input references through `$ref`
    (transitively, also outside that directory), and every file or directory named by an absolute path argument,
    such as `--aliases` or `--custom-template-dir`. Runs whose files cannot all be read are not cached.
    """

    def __init__(self, root: Path | None) -> None:
        """Use `root` as the cache directory, or disable caching when it is None."""
        self.root = root
        self.hits = 0

    def _path(self, schema: Path, args: list[str], *, import_check: bool) -> Path | None:
        if self.root is None:
            return None
        schema = schema.resolve()
        paths = [schema.parent, *sorted(_referenced_files(schema)), *_path_arguments(args)]
        files = [(str(path), _content_hash(path)) for path in paths]
        if any(digest is None for _, digest in files):
            return None
        key = json.dumps([CACHE_FORMAT, str(schema), files, args, import_check])
        return self.root / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    def get(self, schema: Path, args: list[str], *, import_check: bool) -> Outcome | None:
        """Return a cached outcome, if any."""
        path = self._path(schema, args, import_check=import_check)
        if path is None or not path.exists():
            return None
        self.hits += 1
        return Outcome(**json.loads(path.read_text(encoding="utf-8")))

    def put(self, schema: Path, args: list[str], outcome: Outcome, *, import_check: bool) -> None:
        """Store an outcome."""
        path = self._path(schema, args, import_check=import_check)
        if path is None or not outcome.cacheable:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(outcome.__dict__), encoding="utf-8")
        tmp.replace(path)


def _diff(base: dict[str, str], cand: dict[str, str], limit: int) -> str:
    lines: list[str] = []
    for key in sorted(set(base) | set(cand)):
        lines += difflib.unified_diff(
            base.get(key, "").splitlines(), cand.get(key, "").splitlines(), f"base/{key}", f"cand/{key}", lineterm=""
        )
    return "\n" + "\n".join(lines[:limit])


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    base = parser.add_mutually_exclusive_group(required=True)
    base.add_argument("--base", help="released version to compare with, e.g. 0.85.0")
    base.add_argument("--base-python", type=Path, help="interpreter with the base version already installed")
    parser.add_argument("--candidate-python", type=Path, default=Path(sys.executable))
    parser.add_argument("--types", default=",".join(DEFAULT_TYPES), help="comma-separated --output-model-type values")
    parser.add_argument("--options", default="default", help="option sets separated by ';' ('default' = none)")
    parser.add_argument("--extra", default="", help="arguments added to every run, e.g. '--input-file-type openapi'")
    parser.add_argument("--import-check", action="store_true", help="also import every generated module")
    parser.add_argument("--show-diff", type=int, default=0, help="print up to N diff lines per differing run")
    parser.add_argument("-j", type=int, default=os.cpu_count() or 1, help="parallel runs (default: CPU count)")
    parser.add_argument("--timeout", type=int, default=300, help="seconds before one generation or import fails")
    parser.add_argument("--cache-dir", type=Path, default=_default_cache_dir())
    parser.add_argument("--no-cache", action="store_true", help="regenerate base outputs instead of reusing them")
    parser.add_argument("inputs", nargs="+", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the comparison."""
    ns = _parse_args(argv)
    _content_hash.cache_clear()
    _referenced_files.cache_clear()
    candidate_python = ns.candidate_python.absolute()
    if ns.base_python is not None:
        base_python = ns.base_python.absolute()
        identity = json.dumps([str(base_python), *_probe(base_python)])
        cache_name = f"python-{hashlib.sha256(identity.encode()).hexdigest()[:12]}"
    else:
        base_python = _base_python(ns.base, candidate_python, ns.cache_dir)
        cache_name = base_python.parents[1].name
    cache_root = None if ns.no_cache else ns.cache_dir / "outputs" / cache_name
    env = {**os.environ, "PYTHONWARNINGS": "ignore", "OBJC_DISABLE_INITIALIZE_FORK_SAFETY": "YES"}
    base = Runner(Side("base", base_python), env, import_check=ns.import_check, timeout=ns.timeout)
    cand = Runner(Side("cand", candidate_python), env, import_check=ns.import_check, timeout=ns.timeout)
    cache = OutputCache(cache_root)
    extra = shlex.split(ns.extra)
    runs = [
        (schema.absolute(), model_type, [] if opts == "default" else shlex.split(opts), opts)
        for schema in ns.inputs
        for model_type in ns.types.split(",")
        for opts in (o.strip() for o in ns.options.split(";"))
    ]
    counts = dict.fromkeys(("identical", "differ", "fail-both", "fail-base-only", "fail-candidate-only"), 0)

    def one(run: tuple[Path, str, list[str], str]) -> tuple[str, str]:
        schema, model_type, options, label = run
        args = ["--input", str(schema), "--output-model-type", model_type, *COMMON_ARGS, *extra, *options]
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            b = cache.get(schema, args, import_check=ns.import_check)
            if b is None:
                b = base.generate(args, tmp)
                cache.put(schema, args, b, import_check=ns.import_check)
            c = cand.generate(args, tmp)
        name = f"{schema.name} | {model_type} | {label}"
        if not b.ok and not c.ok:
            return "fail-both", f"{name}: {c.error}"
        if not c.ok:
            return "fail-candidate-only", f"{name}: {c.error}"
        if not b.ok:
            return "fail-base-only", f"{name}: {b.error}"
        note = ""
        if c.import_error and not b.import_error:
            note = f" [import fails on candidate only: {c.import_error}]"
        elif b.import_error and not c.import_error:
            note = f" [import fixed: {b.import_error}]"
        if b.tree == c.tree:
            if c.import_error and not b.import_error:
                return "fail-candidate-only", f"{name}:{note}"
            return "identical", (f"{name}:{note}" if note else "")
        return "differ", f"{name}{note}{_diff(b.tree, c.tree, ns.show_diff) if ns.show_diff else ''}"

    started = time.monotonic()
    pool = ThreadPoolExecutor(ns.j)
    try:
        for status, line in pool.map(one, runs):
            counts[status] += 1
            if line:
                print(f"{status}: {line}", flush=True)
    except BaseException:
        pool.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        pool.shutdown(wait=True)
    finally:
        base.close()
        cand.close()
    print("summary:", ", ".join(f"{k}={v}" for k, v in counts.items()))
    print(f"cached base outputs: {cache.hits} of {len(runs)}")
    print(f"elapsed: {time.monotonic() - started:.1f}s", file=sys.stderr)
    if counts["fail-both"] == len(runs):
        print("every run failed on both sides; check the inputs and option sets", file=sys.stderr)
        return 1
    return 1 if counts["differ"] or counts["fail-candidate-only"] else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--worker"]:
        _worker()
    else:
        sys.exit(main())
