"""Exercise the release comparison script end to end with the running interpreter on both sides."""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

import datamodel_code_generator
import datamodel_code_generator.__main__  # noqa: F401
from scripts import compare_release
from tests.conftest import assert_output

ROOT = Path(__file__).resolve().parents[1]
IMPORTED = re.compile(r"from \S+,")
"""Where a refused side imported datamodel-code-generator from: the interpreter's own installation."""
EXPECTED = ROOT / "tests/data/expected/compare_release"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="compare_release.py needs POSIX (SIGALRM, resource, fork)"
)


@pytest.mark.parametrize(
    ("case", "args"),
    [
        (
            "identical",
            [
                "--types",
                "pydantic_v2.BaseModel,typing.TypedDict",
                "--options",
                "default;--collapse-root-models",
                "--import-check",
                str(ROOT / "tests/data/openapi/api.yaml"),
            ],
        ),
        (
            "directory",
            [
                "--types",
                "pydantic_v2.BaseModel",
                "--extra=--input-file-type jsonschema",
                "--import-check",
                str(ROOT / "tests/data/jsonschema/alias_import_alias"),
            ],
        ),
        (
            "path_option",
            [
                "--types",
                "pydantic_v2.BaseModel",
                f"--options=--aliases={ROOT / 'tests/data/openapi/aliases.json'}",
                str(ROOT / "tests/data/openapi/api.yaml"),
            ],
        ),
        (
            "fail_both",
            [
                "--types",
                "pydantic_v2.BaseModel",
                "--extra=--input-file-type jsonschema",
                "--options=--no-such-option",
                str(ROOT / "tests/data/jsonschema/person.json"),
            ],
        ),
        (
            "import_fails_both",
            [
                "--types",
                "pydantic_v2.BaseModel",
                "--extra=--input-file-type jsonschema",
                "--options=--base-class=missing_compare_release_module.Base",
                "--import-check",
                str(ROOT / "tests/data/jsonschema/person.json"),
            ],
        ),
    ],
)
def test_compare_release(case: str, args: list[str], tmp_path: Path) -> None:
    """Both sides run through warm workers; the second run reuses the cached base outputs."""
    outputs = []
    for _ in range(2):
        stdout = StringIO()
        with redirect_stdout(stdout), redirect_stderr(StringIO()):
            code = compare_release.main([
                "--base-python",
                sys.executable,
                "--candidate-python",
                sys.executable,
                "--cache-dir",
                str(tmp_path),
                "-j",
                "2",
                *args,
            ])
        outputs.append(f"exit={code}\n{stdout.getvalue()}")
    assert_output("\n".join(outputs), EXPECTED / f"{case}.txt")


def test_ran_out_of_memory() -> None:
    """A failed run under a memory cap counts as stopped by the cap by its last log line."""
    lines = (
        "MemoryError",
        "MemoryError: cannot allocate memory",
        "SystemError: error return without exception set",
        "SystemError: another failure",
        "Error: the schema is invalid",
    )
    assert_output("".join(f"{compare_release.ran_out_of_memory(line)}\t{line}\n" for line in lines))


def test_handled_exceptions_are_tracked() -> None:
    """The monitoring callbacks collect a handled exception of the compared file and drop one raised again."""
    caught: list[BaseException] = []
    kept, dropped, other = ValueError("kept"), KeyError("dropped"), OSError("other file")
    here = compare_release._track.__code__
    elsewhere = compare_release.main.__code__
    for exception in (kept, dropped):
        compare_release._track(caught, here.co_filename, here, 0, exception)
    compare_release._track(caught, here.co_filename, elsewhere, 0, other)
    compare_release._track(caught, "other.py", here, 0, other)
    for exception in (dropped, other):
        compare_release._track(caught, here.co_filename, here, 0, exception, handled=False)
    assert_output(repr(caught))


def test_compare_release_tracks_referenced_files(tmp_path: Path) -> None:
    """A change to a file referenced from outside the input's directory invalidates the cached base output."""
    schemas = tmp_path / "schemas"
    (schemas / "nested").mkdir(parents=True)
    common = schemas / "common.json"
    common.write_text('{"type": "object", "properties": {"id": {"type": "integer"}}}', encoding="utf-8")
    schema = schemas / "nested" / "root.json"
    schema.write_text('{"type": "object", "properties": {"item": {"$ref": "../common.json"}}}', encoding="utf-8")
    outputs = []
    for revision in ("integer", "integer", "string"):
        common.write_text(
            f'{{"type": "object", "properties": {{"id": {{"type": "{revision}"}}}}}}',
            encoding="utf-8",
        )
        stdout = StringIO()
        with redirect_stdout(stdout), redirect_stderr(StringIO()):
            code = compare_release.main([
                "--base-python",
                sys.executable,
                "--candidate-python",
                sys.executable,
                "--cache-dir",
                str(tmp_path / "cache"),
                "--types",
                "pydantic_v2.BaseModel",
                "--extra=--input-file-type jsonschema",
                str(schema),
            ])
        outputs.append(f"{revision}: exit={code}\n{stdout.getvalue()}")
    assert_output("\n".join(outputs), EXPECTED / "referenced_files.txt")


def _fake_release(cache: Path, version: str, site: Path | None) -> Path:
    """Make the cached environment of `version`: the running interpreter with `site` as its packages, if any."""
    candidate = Path(sys.executable).absolute()
    release = (
        cache / "venvs" / compare_release._environment_name(version, *compare_release._candidate_packages(candidate))
    )
    (release / "bin").mkdir(parents=True)
    path = f'PYTHONPATH="{release / "site"}" ' if site else ""
    (release / "bin" / "python").write_text(f'#!/bin/sh\n{path}exec "{candidate}" "$@"\n', encoding="utf-8")
    (release / "bin" / "python").chmod(0o755)
    if site:
        (release / "site").mkdir()
        (release / "site" / "datamodel_code_generator").symlink_to(site)
    (release / ".complete").touch()
    return release


FAKE_TREE_MAIN = """from pathlib import Path


def main(argv):
    Path(argv[argv.index("--output") + 1]).write_text("# from the caller's PYTHONPATH\\n", encoding="utf-8")
    return 0
"""


def test_compare_release_resolves_the_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A released base runs from its cached environment; a base that is neither a release nor a revision stops.

    A base or candidate whose datamodel-code-generator comes from outside its own environment or tree stops too, and
    the caller's PYTHONPATH reaches neither side: an interpreter given as the base runs its own installation.
    """
    package = Path(datamodel_code_generator.__file__).parent
    _fake_release(tmp_path / "cache", "0.0.1", package)
    unpacked = _fake_release(tmp_path / "cache", "0.0.2", None)
    common = ["--candidate-python", sys.executable, "--cache-dir", str(tmp_path / "cache"), "--types"]
    common += ["pydantic_v2.BaseModel", str(ROOT / "tests/data/jsonschema/person.json")]
    stdout = StringIO()
    refusals = []
    with redirect_stdout(stdout), redirect_stderr(StringIO()):
        code = compare_release.main(["--base", "0.0.1", *common])
        for base in ("no-such-revision", "0.0.2"):
            with pytest.raises(SystemExit) as refused:
                compare_release.main(["--base", base, *common])
            refusals.append(str(refused.value.code))
        fake = tmp_path / "fake" / "datamodel_code_generator"
        fake.mkdir(parents=True)
        (fake / "__init__.py").write_text("", encoding="utf-8")
        (fake / "__main__.py").write_text(FAKE_TREE_MAIN, encoding="utf-8")
        monkeypatch.setenv("PYTHONPATH", str(fake.parent))
        installed = compare_release.main(["--base-python", sys.executable, "--no-cache", *common])
    text = IMPORTED.sub("from <package>,", "\n".join(refusals).replace(str(unpacked), "<release>"))
    assert_output(f"exit={code}, {installed}\n{stdout.getvalue()}{text.replace(str(ROOT), '<root>')}\n")


def test_the_environment_keeps_what_windows_needs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A side keeps the variables Python needs on Windows, and still not the caller's PYTHONPATH."""
    for name in compare_release.PASSED_VARIABLES:
        monkeypatch.setenv(name, f"<{name}>")
    monkeypatch.setenv("PYTHONPATH", "elsewhere")
    monkeypatch.setenv("VIRTUAL_ENV", "elsewhere")
    assert_output(
        json.dumps(compare_release.run_environment(tmp_path), indent=1, sort_keys=True).replace(str(tmp_path), "<src>")
    )


def test_the_record_is_published_by_a_replace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The record of a run stays empty until it is complete: an interrupted publication leaves no partial JSON."""
    record = tmp_path / "record.json"
    monkeypatch.setattr(os, "replace", lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
    with contextlib.suppress(KeyboardInterrupt):
        compare_release._generate_recorded({"record": str(record), "args": []}, lambda _: 0)
    assert_output(repr(record.read_text(encoding="utf-8")))


FAKE_RECORDED_MAIN = """import re
import warnings
from pathlib import Path

MODEL = '''import resource

if resource.getrlimit(resource.RLIMIT_DATA)[0] == 3000 << 20:
    raise ImportError("the cap of the run stayed")
'''

RUNS = []


def main(argv):
    RUNS.append(argv)
    kind = Path(argv[argv.index("--input") + 1]).read_text(encoding="utf-8")
    if kind == "memory":
        bytearray(64 << 20)
    warnings.warn("an old option", DeprecationWarning, stacklevel=1)
    if "--debug" in argv:
        warnings.warn(f"{len(RUNS) - 1} earlier runs in this process", UserWarning, stacklevel=1)
    re.compile("[[a]")
    broken = "raise ImportError('broken')\\n" if kind == "broken" else ""
    Path(argv[argv.index("--output") + 1]).write_text(MODEL + broken, encoding="utf-8")
    return 0
"""
Step = tuple[dict[str, int], str, tuple[str, ...]]
"""What one run of `_worker_runs` changes on the runner first, its input and its options."""


def _fake_runner(tmp_path: Path, memory_mb: int, *, import_check: bool) -> compare_release.Runner:
    """Return a runner of a stand-in generator that warns and writes a model; its inputs name what it writes."""
    package = tmp_path / "fake" / "datamodel_code_generator"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(FAKE_RECORDED_MAIN, encoding="utf-8")
    for kind in ("plain", "broken", "memory"):
        (tmp_path / f"{kind}.txt").write_text(kind, encoding="utf-8")
    side = compare_release.Side("fake", Path(sys.executable), package.parent)
    env = compare_release.run_environment(package.parent)
    return compare_release.Runner(side, env, import_check=import_check, timeout=60, memory_mb=memory_mb)


def _worker_runs(runner: compare_release.Runner, tmp_path: Path, steps: list[Step]) -> str:
    """Generate each step's input in order; each line names the worker its runs ended on, counted from 1."""
    pids: list[int] = []
    lines = []
    for number, (settings, kind, options) in enumerate(steps):
        for name, value in settings.items():
            setattr(runner, name, value)
        (tmp := tmp_path / f"run-{number}").mkdir()
        outcome = runner.generate(["--input", str(tmp_path / f"{kind}.txt"), *options], tmp)
        pid = next(iter(runner.workers.values())).process.pid
        pids += [pid] if pid not in pids else []
        label = " ".join([*(f"{name}={value}" for name, value in settings.items()), kind, *options])
        facts = f"code={outcome.code} warnings={outcome.warnings} import_error={outcome.import_error!r}"
        lines.append(f"{label}: worker {pids.index(pid) + 1}, {facts}")
    runner.close()
    return "\n".join(lines) + "\n"


def test_a_worker_runs_each_job_as_a_fresh_process(tmp_path: Path) -> None:
    """Later runs of a worker record the warnings, import checks and memory cap a fresh process would.

    A run that turns on process state (`--debug`) starts in a worker that ran no job and closes it, and a worker runs
    at most `recycle` jobs.
    """
    runner = _fake_runner(tmp_path, 3000, import_check=True)
    steps: list[Step] = [({}, "plain", ()), ({}, "broken", ()), ({}, "plain", ("--debug",)), ({}, "plain", ())]
    steps += [({"recycle": 1}, "plain", ()), ({}, "plain", ())]
    assert_output(_worker_runs(runner, tmp_path, steps))


@pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_DATA caps the memory of a run on Linux only")
def test_a_run_out_of_memory_closes_its_worker(tmp_path: Path) -> None:
    """The worker of a run that ran out of its memory cap is replaced: the next run starts in a fresh one."""
    runner = _fake_runner(tmp_path, 1, import_check=False)
    assert_output(_worker_runs(runner, tmp_path, [({}, "memory", ()), ({"memory_mb": 0}, "plain", ())]))
