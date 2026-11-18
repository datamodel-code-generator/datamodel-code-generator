"""Compare generated output of the current checkout with a released version over an input x option matrix.

Usage:
  python scripts/compare_release.py --base 0.85.0 [--types pydantic_v2.BaseModel,msgspec.Struct]
      [--options 'default;--collapse-root-models'] [--extra '--input-file-type openapi'] [--import-check]
      [--show-diff N] [-j N] INPUT [INPUT ...]

Each option set is one quoted string of extra CLI arguments (`default` means none), separated by `;`.
The candidate is this checkout's `src/`, run with the interpreter running this script unless `--candidate-python`
is given. A base that is a released version (`0.85.0`) is installed once into a cached virtual environment (needs
`uv`) with the candidate's formatter, pydantic and msgspec versions pinned, so only generator changes show up as
differences; any other base is a git revision of this repository (`main~1`, a commit): its `src/` is extracted once
with `git archive` and run with the candidate's interpreter, as the candidate is (`resolve_base`). Base outputs are
cached as well, so reruns only generate the candidate side.

Generation runs in warm worker processes: each worker imports datamodel-code-generator once and generates in itself,
`RECYCLE` runs before a new worker takes its place, so runs pay neither interpreter start-up nor imports. Each run
has its own log for its output and errors, working directory, warning filters, timeout (which ends the worker, as a
crash does) and memory cap; what a run records does not depend on the runs its worker ran before it. An import check
runs the generated code, in a fresh child of the worker. POSIX only. Both sides run in an environment built from
scratch (`run_environment`), and each worker refuses to run a package imported from outside the tree its side names.
A run shows its warnings under Python's default filters with `DeprecationWarning` and `PendingDeprecationWarning`
shown too, and records each as its category, message and the module the filters match it with (`Outcome.warnings`),
so a warning whose call moves keeps its record; `Outcome.handled` are the classes of the exceptions the code of
`datamodel_code_generator.__main__` caught and did not raise again (Python 3.12+); `Outcome.messages` is what the run
printed, with the frames of tracebacks left out, so an error is its class and message. A run that refuses a single
output file for a modular one is run again into a directory, and `Outcome.first` keeps the refusal (its exit status,
messages, warnings and handled exceptions).

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
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from types import CodeType

Record = dict[str, Any]
"""A JSON object a worker job or reply carries."""
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
# The exit code of a run that hit its memory cap, also reported for a run whose own handler printed the MemoryError:
# like a timeout it is not cached, since another cap may let it finish.
OUT_OF_MEMORY = 125
PROCESS_STATE_OPTIONS = frozenset({"--debug"})
"""Options that turn on state for the rest of the process (`--debug`: tracing in every parser imported after it): the
worker of such a run is closed after it, as after running out of memory, so later runs do not inherit it."""
RECYCLE = 200
"""Jobs a worker runs before a new one takes its place, which bounds the memory and the state a worker gathers."""
CACHE_FORMAT = 4
ROOT = Path(__file__).resolve().parents[1]
RELEASE = re.compile(r"\d+(?:\.\d+)+")
"""A base that is a released version, installed from the package index; any other base is a git revision."""
TRACEBACK_FRAMES = re.compile(r"^Traceback \(most recent call last\):\n(?:[ \t].*(?:\n|\Z))*", re.MULTILINE)
PASSED_VARIABLES = (
    "PATH",
    "HOME",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SYSTEMROOT",
    "WINDIR",
    "PATHEXT",
    "COMSPEC",
    "COVERAGE_PROCESS_START",
    "COVERAGE_PROCESS_CONFIG",
)
"""The caller's variables a side's processes see: where programs, the home and temporary files are, what Windows needs
to start a Python process (its sockets fail without `SYSTEMROOT`) and coverage's measurement of subprocesses. Any other
(PYTHONPATH, PYTHONWARNINGS, the test suite's checks) could change what a side runs or how."""
HANDLED_TOOL = 3
"""The `sys.monitoring` tool id that records the exceptions `datamodel_code_generator.__main__` handles."""


def _default_cache_dir() -> Path:
    root = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(root) / "datamodel-code-generator" / "compare-release"


# Worker side: runs inside the base or candidate interpreter.


def _exit_code(main: Callable[[list[str]], object], argv: list[str]) -> int:
    try:
        result = main(argv)
    except SystemExit as exc:
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 1
    except MemoryError:
        with contextlib.suppress(MemoryError):
            traceback.print_exc()
        return OUT_OF_MEMORY
    except Exception:  # ruff: ignore[blind-except] - report any generator crash as a failed run
        try:
            traceback.print_exc()
        except MemoryError:
            return OUT_OF_MEMORY
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


class _WarningRecorder:
    """Record each warning a run shows as its category, message and the module the warning filters match it with.

    Installed as the first filter, its `match` is called with the module of every warning that reaches the filters and
    matches nothing, so the real filters still decide; `show` stands in for `warnings.showwarning`.
    """

    def __init__(self) -> None:
        self.local = threading.local()
        self.records: list[list[str]] = []

    def match(self, module: str) -> bool:
        self.local.module = module
        return False

    def show(self, message: Warning | str, category: type[Warning], *_: object) -> None:
        self.records.append([
            f"{category.__module__}.{category.__qualname__}",
            str(message),
            getattr(self.local, "module", ""),
        ])


def _track(
    caught: list[BaseException],
    filename: str,
    code: CodeType,
    _: int,
    exception: BaseException,
    *,
    handled: bool = True,
) -> None:
    """Add the exception `code` handled to `caught`, or take it out when it is raised again (`handled=False`)."""
    if code.co_filename != filename:
        return
    if handled:
        caught.append(exception)
    else:
        with contextlib.suppress(ValueError):
            caught.remove(exception)


@contextlib.contextmanager
def _handled_exceptions(filename: str) -> Generator[list[BaseException], None, None]:
    """Collect, while the block runs, the exceptions the code of `filename` catches and does not raise again.

    A handler that only cleans up (`finally`, `with`, an `except` that does not match or re-raises) raises the
    exception again, which takes it out. Python 3.12+ (`sys.monitoring`); earlier the list stays empty. The tool is
    freed at the end, so the next run of a worker starts again.
    """
    caught: list[BaseException] = []
    if monitoring := getattr(sys, "monitoring", None):
        monitoring.use_tool_id(HANDLED_TOOL, "compare_release")
        monitoring.register_callback(
            HANDLED_TOOL, monitoring.events.EXCEPTION_HANDLED, functools.partial(_track, caught, filename)
        )
        monitoring.register_callback(
            HANDLED_TOOL, monitoring.events.RERAISE, functools.partial(_track, caught, filename, handled=False)
        )
        monitoring.set_events(HANDLED_TOOL, monitoring.events.EXCEPTION_HANDLED | monitoring.events.RERAISE)
    try:
        yield caught
    finally:
        if monitoring:
            monitoring.set_events(HANDLED_TOOL, 0)
            monitoring.free_tool_id(HANDLED_TOOL)


def _generate_recorded(job: Record, main: Callable[[list[str]], object]) -> int:
    """Run one generation and write what it showed and handled to `job["record"]` (see `Outcome`).

    Warnings show under Python's default filters with deprecations shown too; entering the filters again per run
    resets the registries of warnings already shown, and emptying the cache of `re` makes a pattern an earlier run
    compiled warn again. The memory cap holds while the generator runs, so memory runs
    out in the run, which reports it, and not in the recording around it. The record starts empty, like the log, so
    a run that dies or runs out of memory leaves no record of an earlier run: its exit status says what happened.
    """
    record = Path(job["record"])
    record.write_text("", encoding="utf-8")
    re.purge()
    recorder = _WarningRecorder()
    main_file = sys.modules["datamodel_code_generator.__main__"].__file__ or ""
    with _handled_exceptions(main_file) as caught, warnings.catch_warnings():
        warnings.simplefilter("default", PendingDeprecationWarning)
        warnings.simplefilter("default", DeprecationWarning)
        warnings.filters.insert(0, ("always", None, Warning, recorder, 0))
        warnings.showwarning = recorder.show
        with _memory_cap(job.get("memory_mb", 0)):
            code = _exit_code(main, job["args"])
        handled = [f"{type(exception).__module__}.{type(exception).__qualname__}" for exception in caught]
    partial = record.with_name(record.name + ".tmp")
    partial.write_text(json.dumps({"warnings": recorder.records, "handled": handled}), encoding="utf-8")
    partial.replace(record)
    return code


@contextlib.contextmanager
def _memory_cap(limit_mb: int) -> Generator[None, None, None]:
    """Cap the data memory of this process (`RLIMIT_DATA`) at `limit_mb` while the block runs (0: no cap).

    The cap counts the whole worker, which recycling bounds; where the platform refuses it (macOS), the run goes
    uncapped.
    """
    if not limit_mb:
        yield
        return
    import resource  # ruff: ignore[import-outside-top-level] - POSIX only, like the fork of an import check

    limits = resource.getrlimit(resource.RLIMIT_DATA)
    with contextlib.suppress(ValueError, OSError):
        resource.setrlimit(resource.RLIMIT_DATA, (limit_mb << 20, limits[1]))
    try:
        yield
    finally:
        resource.setrlimit(resource.RLIMIT_DATA, limits)


@contextlib.contextmanager
def _job_scope(job: Record) -> Generator[None, None, None]:
    """Run a job with its output and errors in its log, in its directory and under its timeout.

    All of it is restored at the end, so the worker runs its next job as it ran this one. The timeout is `SIGALRM` at
    its default action: a run that hangs, in C code too, ends the process, and the driver reads the signal as the
    run's exit status.
    """
    saved = [os.dup(1), os.dup(2)]
    log = os.open(job["log"], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.dup2(log, 1)
    os.dup2(log, 2)
    os.close(log)
    cwd = Path.cwd()
    os.chdir(job["cwd"])
    signal.alarm(job["timeout"])
    try:
        yield
    finally:
        signal.alarm(0)
        sys.stdout.flush()
        sys.stderr.flush()
        os.chdir(cwd)
        for fd, copy in enumerate(saved, start=1):
            os.dup2(copy, fd)
            os.close(copy)


def _run_job(job: Record, main: Callable[[list[str]], object]) -> int:
    """Run one job and return its exit code: a generation in this worker, an import check in a fresh child.

    An import check runs the generated code, which has to leave nothing behind, so it gets a child of its own.
    """
    if job["kind"] == "generate":
        with _job_scope(job):
            return _generate_recorded(job, main)
    if (pid := os.fork()) == 0:  # pragma: no cover - child process
        code = 1
        try:
            with _job_scope(job):
                code = _import_modules(job["out"])
        finally:
            os._exit(code)
    return os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1])


def _worker() -> None:
    sys.argv[0] = "datamodel-codegen"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from datamodel_code_generator.__main__ import main  # ruff: ignore[import-outside-top-level] - imported once per worker

        for name in WARM_IMPORTS:
            with contextlib.suppress(ImportError):
                importlib.import_module(name)
    # Importing the CLI installs its own SIGINT handler; a worker stops on SIGINT, the driver by closing its stdin.
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    sys.stdout.write(json.dumps({"package": sys.modules["datamodel_code_generator"].__file__}) + "\n")
    sys.stdout.flush()
    for line in sys.stdin:
        code = _run_job(json.loads(line), main)
        sys.stdout.write(json.dumps({"rc": code}) + "\n")
        sys.stdout.flush()


# Driver side.


def run_environment(source: Path | None) -> dict[str, str]:
    """Return the environment of a side's processes, built from scratch: `source` is the tree it imports, if any.

    Only `PASSED_VARIABLES` come from the caller; the hash seed is the same on each side, so the order of a set
    cannot differ between them.
    """
    env = {name: os.environ[name] for name in PASSED_VARIABLES if name in os.environ}
    env |= {"PYTHONWARNINGS": "ignore", "PYTHONHASHSEED": "0", "OBJC_DISABLE_INITIALIZE_FORK_SAFETY": "YES"}
    return env | ({"PYTHONPATH": str(source)} if source else {})


class Worker:
    """A warm worker process for one interpreter; `jobs` counts the jobs it was given."""

    def __init__(self, python: Path, env: dict[str, str], package: Path | None) -> None:
        """Start the worker with Python's default warning filters, which its runs show warnings under.

        It refuses a datamodel-code-generator imported from outside `package` (any where None). Both paths are
        normalized, not resolved: a side's package path and its `package` come from the same unresolved path.
        """
        self.process = subprocess.Popen(
            [str(python), str(Path(__file__).resolve()), "--worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            env={name: value for name, value in env.items() if name != "PYTHONWARNINGS"},
        )
        self.jobs = 0
        if (ready := self._reply()) is None:
            msg = f"worker exited with {self.process.wait()}"
            raise RuntimeError(msg)
        self.package = Path(os.path.normpath(Path(ready["package"]).absolute())).parent
        if package is not None and not self.package.is_relative_to(os.path.normpath(package.absolute())):
            self.close()
            msg = f"{python} imports datamodel_code_generator from {self.package}, not from below {package}"
            raise SystemExit(msg)

    def _reply(self) -> Record | None:
        stdout = self.process.stdout
        assert stdout is not None
        return json.loads(reply) if (reply := stdout.readline()) else None

    def run(self, job: Record) -> int:
        """Run one job and return its exit code; a job that ended the worker returns the worker's exit status.

        Such a job hit its timeout or crashed the interpreter, as it would have ended a process of its own.
        """
        stdin = self.process.stdin
        assert stdin is not None
        stdin.write(json.dumps(job) + "\n")
        stdin.flush()
        self.jobs += 1
        return self.process.wait() if (reply := self._reply()) is None else reply["rc"]

    def close(self) -> None:
        """Stop the worker."""
        if self.process.stdin:
            self.process.stdin.close()
        self.process.wait()


@dataclass(frozen=True)
class Side:
    """One interpreter to generate with, and the directory its datamodel-code-generator must come from.

    `package` is None for an interpreter whose own installation runs (`--base-python`).
    """

    name: str
    python: Path
    package: Path | None


FACTS = ("code", "tree", "warnings", "handled", "messages")
"""What `identity_changes` compares, in the order it reports them."""


@dataclass
class Outcome:
    """Result of generating one run on one side.

    `code` is the exit status, `messages` what the run printed without the frames of tracebacks, `warnings` the
    warnings it showed as records (category, message, module) and `handled` the classes of the exceptions
    `datamodel_code_generator.__main__` caught; paths of the run's directory, output and package read `<tmp>`,
    `<output>` and `<package>`. `first` holds these facts of a refused single-file attempt before a modular run.
    """

    ok: bool
    error: str
    tree: dict[str, str]
    import_error: str
    cacheable: bool = True
    code: int = 0
    messages: str = ""
    warnings: list[list[str]] = field(default_factory=list)
    handled: list[str] = field(default_factory=list)
    first: Record = field(default_factory=dict)

    def stopped(self) -> bool:
        """Whether the timeout or the memory cap stopped the run: another cap may let it finish."""
        return self.code in {-signal.SIGALRM, OUT_OF_MEMORY}


def _fact_changes(base: Record, candidate: Record) -> list[tuple[str, str]]:
    """Return what differs between the `FACTS` of two attempts as (kind, detail); a fact one lacks is None."""
    changes = []
    for kind in FACTS:
        old, new = base.get(kind), candidate.get(kind)
        if old == new:
            continue
        match kind:
            case "code":
                changes.append(("exit", f"exit status {old} -> {new}"))
            case "tree":
                old, new = old or {}, new or {}
                files = [name for name in sorted(old.keys() | new.keys()) if old.get(name) != new.get(name)]
                changes.append(("output", f"output differs: {', '.join(files)}"))
            case "warnings" | "handled":
                before, after = Counter(map(json.dumps, old or [])), Counter(map(json.dumps, new or []))
                removed, added = sorted((before - after).elements()), sorted((after - before).elements())
                moved = f"{kind} differ: {', '.join(removed) or 'none'} -> {', '.join(added) or 'none'}"
                detail = moved if removed or added else f"{kind} in another order"
                changes.append((kind, detail))
            case _:
                before, after = (old or "").splitlines(), (new or "").splitlines()
                index = next(
                    (index for index, (one, other) in enumerate(zip(before, after, strict=False)) if one != other),
                    min(len(before), len(after)),
                )
                changes.append((kind, f"messages differ: {before[index : index + 1]} -> {after[index : index + 1]}"))
    return changes


def identity_changes(base: Outcome, candidate: Outcome) -> list[tuple[str, str]]:
    """Return what differs between two runs of one cell as (kind, detail).

    The kinds are `exit`, `output`, `warnings`, `handled`, `messages` and `first attempt` (a fact of the refused
    single-file attempt before a modular run).
    """
    first = [("first attempt", f"first attempt {detail}") for _, detail in _fact_changes(base.first, candidate.first)]
    return _fact_changes(base.__dict__, candidate.__dict__) + first


def _probe(python: Path) -> tuple[str, str]:
    """Return the interpreter's `major.minor` version and its installed datamodel-code-generator version."""
    code = (
        "import sys\n"
        "from importlib.metadata import version\n"
        "print(f'{sys.version_info[0]}.{sys.version_info[1]}', version('datamodel-code-generator'))\n"
    )
    python_version, dcg_version = subprocess.run(
        [str(python), "-c", code], capture_output=True, text=True, env=run_environment(None), check=True
    ).stdout.split()
    return python_version, dcg_version


def _is_generator_requirement(line: str) -> bool:
    """Return whether a `pip freeze` line installs datamodel-code-generator itself, e.g. this checkout."""
    if line.startswith("-e "):
        source = Path(line.removeprefix("-e ").removeprefix("file://"))
        return any((source / sub / "datamodel_code_generator").is_dir() for sub in ("src", "."))
    return line.lower().replace("_", "-").startswith(("datamodel-code-generator==", "datamodel-code-generator @"))


def _candidate_packages(candidate: Path) -> tuple[str, list[str]]:
    """Return the candidate's `major.minor` Python version and its packages but the generator, as `pip freeze` pins."""
    python_version, _ = _probe(candidate)
    freeze = subprocess.run(
        ["uv", "pip", "freeze", "--python", str(candidate)], capture_output=True, text=True, check=True
    ).stdout
    return python_version, sorted(line for line in freeze.splitlines() if line and not _is_generator_requirement(line))


def _environment_name(base: str, python_version: str, pins: list[str]) -> str:
    key = hashlib.sha256(json.dumps([base, python_version, pins]).encode()).hexdigest()[:12]
    return f"{base}-py{python_version}-{key}"


def _built(root: Path, build: Callable[[], object]) -> Path:
    """Return `root` once `build` has filled it, built once under a lock: another run may be building the same.

    A root without its `.complete` mark (a build that stopped half way) is removed and built again.
    """
    root.parent.mkdir(parents=True, exist_ok=True)
    import fcntl  # ruff: ignore[import-outside-top-level] - POSIX only; keeps the module importable elsewhere

    with (root.parent / f"{root.name}.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (root / ".complete").exists():
            shutil.rmtree(root, ignore_errors=True)
            build()
            (root / ".complete").touch()
    return root


def _base_python(version: str, candidate: Path, cache_dir: Path) -> Path:
    """Install the release next to the candidate's other packages, pinned to the same versions."""
    python_version, pins = _candidate_packages(candidate)
    root = cache_dir / "venvs" / _environment_name(version, python_version, pins)
    return _built(root, lambda: _install_base(version, python_version, pins, root)) / "bin" / "python"


def _install_base(version: str, python_version: str, pins: list[str], root: Path) -> None:
    """Create the release environment with the candidate's package versions pinned."""
    print(f"installing datamodel-code-generator {version} into {root}", file=sys.stderr, flush=True)
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


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in `repo` without the caller's repository settings (a hook's GIT_DIR) or an interactive prompt.

    A command that fails prints nothing on stdout.
    """
    env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=env, check=False)


def commit_of(spec: str, repo: Path = ROOT) -> str:
    """Return the commit a git revision of `repo` names, or an empty string for a spec that names none."""
    return git(repo, "rev-parse", "--verify", "--quiet", f"{spec}^{{commit}}").stdout.strip()


def archive_version(revision: str, repo: Path = ROOT) -> str:
    """Return the version the build hook writes for a revision (`git archive` leaves the version file out)."""
    result = git(repo, "describe", "--tags", "--long", "--match", "[0-9]*", "--exclude", "*+*", revision)
    if (described := result.stdout.strip()).count("-") < 2:  # ruff: ignore[magic-value-comparison]
        return f"0.0.0+g{revision[:9]}"
    tag, distance, commit = described.rsplit("-", 2)
    if distance == "0":
        return tag
    major_minor, _, patch = tag.rpartition(".")
    return f"{major_minor}.{int(patch) + 1}.dev{distance}+{commit}"


def _extract(revision: str, repo: Path, paths: tuple[str, ...], root: Path) -> None:
    root.mkdir(parents=True)
    archive = root / "archive.tar"
    git(repo, "archive", "--output", str(archive), revision, *paths).check_returncode()
    subprocess.run(["tar", "-x", "-f", str(archive), "-C", str(root)], check=True)
    archive.unlink()
    version = f"__version__ = {archive_version(revision, repo)!r}\n"
    (root / "src" / "datamodel_code_generator" / "_version.py").write_text(version, encoding="utf-8")


def source_tree(revision: str, cache_dir: Path, repo: Path = ROOT, paths: tuple[str, ...] = ("src",)) -> Path:
    """Return the root of a commit's `paths` (a base runs `src`), extracted once below `cache_dir/trees`.

    The version file is not in the repository (the build hook writes it), so it is written here.
    """
    root = cache_dir / "trees" / "-".join((revision, *paths))
    return _built(root, lambda: _extract(revision, repo, paths, root))


@dataclass(frozen=True)
class Base:
    """The side a run is compared with: its interpreter, the source tree it imports, and its outputs' cache name.

    `package` is the directory its datamodel-code-generator must come from: the tree of a revision, the environment
    of a release, None for an interpreter that has the base installed (`--base-python`).
    """

    python: Path
    source: Path | None
    cache_name: str
    revision: str | None = None
    package: Path | None = None

    def env(self) -> dict[str, str]:
        """Return the environment of the base's processes: a revision's tree on PYTHONPATH, nothing of the caller's."""
        return run_environment(self.source)


def installed_base(python: Path) -> Base:
    """Return the base of an interpreter that has the base version installed already (`--base-python`)."""
    identity = json.dumps([str(python), *_probe(python)])
    return Base(python, None, f"python-{hashlib.sha256(identity.encode()).hexdigest()[:12]}")


def resolve_base(spec: str, candidate: Path, cache_dir: Path, repo: Path = ROOT) -> Base:
    """Resolve the base a run is compared with, a released version or a git revision of `repo`.

    A release is installed beside the candidate's packages (`_base_python`); a revision's `src/` runs with the
    candidate's interpreter. Either way only generator changes differ.
    """
    if RELEASE.fullmatch(spec):
        python = _base_python(spec, candidate, cache_dir)
        return Base(python, None, python.parents[1].name, package=python.parents[1])
    if not (revision := commit_of(spec, repo)):
        msg = f"{spec} is neither a released version nor a revision of {repo}"
        raise SystemExit(msg)
    source = source_tree(revision, cache_dir, repo) / "src"
    name = _environment_name(revision, *_candidate_packages(candidate))
    return Base(candidate, source, name, revision, source)


def _tree(path: Path) -> dict[str, str]:
    return {
        file.relative_to(path).as_posix(): file.read_text(encoding="utf-8")
        for file in sorted(path.rglob("*.py"))
        if "__pycache__" not in file.parts
    }


def _relative(text: str, out: Path, tmp: Path, package: Path) -> str:
    """Return `text` with the paths of a run's output, directory and package as `<output>`, `<tmp>`, `<package>`.

    So they read the same on each run and side. The resolved form of each goes first (macOS resolves `/var` to
    `/private/var`, which ends in the other form).
    """
    for path, name in ((out, "<output>"), (tmp, "<tmp>"), (package, "<package>")):
        text = text.replace(os.path.realpath(path), name).replace(str(path), name)
    return text


def _last_line(path: Path) -> str:
    lines = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    return lines[-1] if lines else "error"


def ran_out_of_memory(last_line: str) -> bool:
    """Tell from the last log line of a failed run under a memory cap whether the cap stopped it.

    Besides the MemoryError, an allocation that fails inside C code can end the run with the SystemError CPython raises
    for an error returned without an exception set (11-10: such a run was cached, and the next gate run read it back).
    """
    return (
        last_line.partition(":")[0] == "MemoryError" or last_line == "SystemError: error return without exception set"
    )


def _attempt(rc: int, log: Path, record: Path, paths: tuple[Path, Path, Path]) -> Record:
    """Return what one attempt of a run showed (`Outcome`); `paths` are its output, directory and package."""
    printed = TRACEBACK_FRAMES.sub("", log.read_text(encoding="utf-8", errors="replace"))
    recorded = json.loads(text) if record.exists() and (text := record.read_text(encoding="utf-8")) else {}
    return {
        "code": rc,
        "messages": _relative(printed, *paths),
        "warnings": [
            [category, _relative(message, *paths), module] for category, message, module in recorded.get("warnings", [])
        ],
        "handled": recorded.get("handled", []),
    }


class Runner:
    """Runs one side of the matrix through per-thread warm workers, each replaced after `recycle` jobs."""

    def __init__(
        self, side: Side, env: dict[str, str], *, import_check: bool, timeout: int, memory_mb: int = 0
    ) -> None:
        """Prepare a runner; workers start lazily. `memory_mb` caps the data memory of a worker's run (0: no cap)."""
        self.side = side
        self.env = env
        self.import_check = import_check
        self.timeout = timeout
        self.memory_mb = memory_mb
        self.recycle = RECYCLE
        self.workers: dict[int, Worker] = {}
        self.lock = threading.Lock()

    def _worker(self, *, fresh: bool = False) -> Worker:
        """Return this thread's worker: a new one in place of one that ran `recycle` jobs or has ended.

        A `fresh` job (one that changes process-wide state, as `--debug` does by wrapping the classes imported after
        it) also gets a new worker in place of one that ran any job: a run in a used process would not behave like one
        in a process of its own.
        """
        worker = self.workers.get(thread := threading.get_ident())
        if (
            worker is None
            or worker.jobs >= self.recycle
            or worker.process.poll() is not None
            or (fresh and worker.jobs)
        ):
            if worker is not None:
                worker.close()
            worker = Worker(self.side.python, self.env, self.side.package)
            with self.lock:
                self.workers[thread] = worker
        return worker

    def generate(self, args: list[str], tmp: Path) -> Outcome:
        """Generate one run into a fresh directory under `tmp`."""
        out = tmp / self.side.name
        out.mkdir()
        log = tmp / f"{self.side.name}.log"
        record = tmp / f"{self.side.name}-record.json"
        job = {
            "kind": "generate",
            "cwd": str(tmp),
            "log": str(log),
            "record": str(record),
            "timeout": self.timeout,
            "memory_mb": self.memory_mb,
            "args": [*args, "--output", str(out / "model.py")],
        }
        fresh = bool(PROCESS_STATE_OPTIONS.intersection(args))
        rc = (worker := self._worker(fresh=fresh)).run(job)
        first: Record = {}
        if rc != 0 and any(text in log.read_text(encoding="utf-8", errors="replace") for text in MODULAR_OUTPUT_ERRORS):
            first = _attempt(rc, log, record, (out, tmp, worker.package))
            job["args"] = [*args, "--output", str(out / "pkg")]
            rc = (worker := self._worker(fresh=fresh)).run(job)
        if rc > 0 and self.memory_mb and ran_out_of_memory(_last_line(log)):
            rc = OUT_OF_MEMORY
        if rc == OUT_OF_MEMORY or fresh:
            worker.close()
        facts = _attempt(rc, log, record, (out, tmp, worker.package)) | {"first": first}
        if rc != 0:
            return Outcome(
                ok=False,
                error=self._error(rc, log),
                tree=_tree(out),
                import_error="",
                cacheable=rc > 0 and rc != OUT_OF_MEMORY,
                **facts,
            )
        import_error = ""
        if self.import_check:
            import_log = tmp / f"{self.side.name}-import.log"
            job = {"kind": "import", "cwd": str(tmp), "log": str(import_log), "timeout": self.timeout, "out": str(out)}
            if (rc := self._worker().run(job)) != 0:
                import_error = self._error(rc, import_log)
        return Outcome(ok=True, error="", tree=_tree(out), import_error=import_error, cacheable=rc >= 0, **facts)

    def _error(self, rc: int, log: Path) -> str:
        if rc == -signal.SIGALRM:
            return f"timed out after {self.timeout}s"
        if rc == OUT_OF_MEMORY and self.memory_mb:
            return f"ran out of its {self.memory_mb} MB"
        return _last_line(log)

    def close(self) -> None:
        """Stop all workers."""
        for worker in self.workers.values():
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
    base.add_argument("--base", help="released version (0.85.0) or git revision of this repository to compare with")
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
    resolved = (
        installed_base(ns.base_python.absolute())
        if ns.base_python is not None
        else resolve_base(ns.base, candidate_python, ns.cache_dir.absolute())
    )
    cache_root = None if ns.no_cache else ns.cache_dir / "outputs" / resolved.cache_name
    side = Side("base", resolved.python, resolved.package)
    base = Runner(side, resolved.env(), import_check=ns.import_check, timeout=ns.timeout)
    side = Side("cand", candidate_python, ROOT / "src")
    cand = Runner(side, run_environment(ROOT / "src"), import_check=ns.import_check, timeout=ns.timeout)
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
        elif c.import_error:
            note = f" [import fails on both sides: {c.import_error}]"
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
