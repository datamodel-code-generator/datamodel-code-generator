"""Exercise the release comparison script end to end with the running interpreter on both sides."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

import pytest

import datamodel_code_generator
import datamodel_code_generator.__main__  # noqa: F401
from scripts import _change_units as change_units
from scripts import compare_release
from tests.capabilities import needs
from tests.conftest import assert_output

ROOT = Path(__file__).resolve().parents[1]
IMPORTED = re.compile(r"from \S+,")
"""Where a refused side imported datamodel-code-generator from: the interpreter's own installation."""
EXPECTED = ROOT / "tests/data/expected/compare_release"

pytestmark = needs("posix")


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
        text = stdout.getvalue().replace(os.path.realpath(tmp_path), "<tmp>").replace(str(tmp_path), "<tmp>")
        outputs.append(f"{revision}: exit={code}\n{text}")
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
        compare_release._generate_recorded({"record": str(record), "out": str(tmp_path), "args": []}, lambda _: 0)
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


@needs("linux")
def test_a_run_out_of_memory_closes_its_worker(tmp_path: Path) -> None:
    """The worker of a run that ran out of its memory cap is replaced: the next run starts in a fresh one."""
    runner = _fake_runner(tmp_path, 1, import_check=False)
    assert_output(_worker_runs(runner, tmp_path, [({}, "memory", ()), ({"memory_mb": 0}, "plain", ())]))


ORACLE_MAIN = """import json
from pathlib import Path

import datamodel_code_generator._config_fields
from datamodel_code_generator import _case
from datamodel_code_generator.arguments import arg_parser


def main(argv):
    ns = arg_parser.parse_args(argv)
    spec = json.loads(Path(ns.input).read_text(encoding="utf-8"))
    value = _case.run(spec)
    if spec.get("fail"):
        return 1
    Path(ns.output).write_text(f"VALUE = {value!r}\\n", encoding="utf-8")
    if ns.client_output:
        client = Path(ns.client_output)
        client.mkdir(parents=True, exist_ok=True)
        (client / "client.py").write_text(f"CLIENT = {_case.CLIENT!r}\\n", encoding="utf-8")
    return 0
"""
ORACLE_ARGUMENTS = """import argparse

arg_parser = argparse.ArgumentParser()
for option in ("--input", "--output-model-type", "--output", "--client-output"):
    arg_parser.add_argument(option)
arg_parser.add_argument("--disable-timestamp", action="store_true")
arg_parser.add_argument("--formatters", nargs="*")
"""
ORACLE_INPUTS = {
    "plain.json": {"value": "ab"},
    "reached.json": {"value": "ab", "reach": True},
    "failed.json": {"value": "ab", "fail": True},
    "failed_reached.json": {"value": "ab", "fail": True, "reach": True},
}
"""What a run of `_case.run` gets: `reach` takes the branch a case changes, `fail` makes the run fail on both sides."""
PLAIN = ("plain.json",)
REACHED = ("reached.json",)
MISSED_THEN_REACHED = (("incomplete", PLAIN), ("complete", REACHED))
"""The verdicts of a change only `reach` runs: a run that misses it compared too little, one that takes it is enough."""
NO_UNIT = (("complete", PLAIN),)
"""The verdict of a change no run has to reach: it changes nothing that runs."""


@dataclass(frozen=True)
class Oracle:
    """A change of a stand-in package and the verdict each comparison of it must give.

    `base` holds the package's modules and files at the base commit, `head` what the working tree changes (None
    deletes a file); each run is the verdict and the arguments of one `compare_release.py` run in the inputs'
    directory (`ORACLE_INPUTS`). A verdict is `complete` (exit 0), `incomplete` (exit 1, the evidence compared too
    little) or `differ` (exit 1, a cell differs).
    """

    base: dict[str, str]
    head: dict[str, str | None]
    runs: tuple[tuple[str, tuple[str, ...]], ...] = MISSED_THEN_REACHED


def _edit(text: str, *changes: tuple[str, str]) -> str:
    """Return `text` with each `(old, new)` change made; an `old` the text holds other than once raises ValueError."""
    for old, new in changes:
        before, after = text.split(old)
        text = before + new + after
    return text


def _reaching(definition: str) -> str:
    """Return a `_case` module whose `reach` runs call `unused`, the function `definition` defines."""
    return f"""{definition}

def run(spec):
    if spec.get("reach"):
        return unused(spec["value"])
    return spec["value"]
"""


def _changed(
    base: str, *changes: tuple[str, str], runs: tuple[tuple[str, tuple[str, ...]], ...] = MISSED_THEN_REACHED
) -> Oracle:
    """Return the oracle of a `_case` module changed in place."""
    return Oracle({"_case.py": base}, {"_case.py": _edit(base, *changes)}, runs)


P1_CLASS_LAMBDA = """class Table:
    convert = staticmethod(lambda value: value)


def run(spec):
    if spec.get("reach"):
        return Table.convert(spec["value"])
    return spec["value"]
"""
P1_FUNCTION_LAMBDA = """def run(spec):
    convert = lambda value: value
    if spec.get("reach"):
        return convert(spec["value"])
    return spec["value"]
"""
P1_GENEXPR = """def run(spec):
    parts = (part.lower() for part in spec["value"])
    if spec.get("reach"):
        return "".join(parts)
    return spec["value"]
"""
P1_ONE_LINE_DEF = """def run(spec):
    def convert(value): return value.lower()
    if spec.get("reach"):
        return convert(spec["value"])
    return spec["value"]
"""
P2_TUPLE_IN_LAMBDA = """def run(spec):
    known = lambda value: value in (
        "x",
        "y",
    )
    if spec.get("reach"):
        return str(known(spec["value"]))
    return spec["value"]
"""
P3_ELIF = """def run(spec):
    if not spec.get("reach"):
        result = spec["value"]
    elif spec.get("graph", True):
        result = str(spec["value"])
    else:
        result = spec["value"] + ""
    return result
"""
P3_ELSE = """def run(spec):
    result = spec["value"]
    if not spec.get("reach"):
        result = str(result)
    else:
        result = result + ""
    return result
"""
P4_OVERRIDE = """class Base:
    def label(self, value):
        return value


class Child(Base):
    def other(self, value):
        return value

    def label(self, value):
        return str(value)


def run(spec):
    if spec.get("reach"):
        return Child().label(spec["value"])
    return Child().other(spec["value"])
"""
C4_OVERRIDE_BODY = """class Base:
    def label(self, value):
        return value


class Child(Base):
    def label(self, value):
        return value.lower()


def run(spec):
    if spec.get("reach"):
        return Child().label(spec["value"])
    return Base().label(spec["value"])
"""
C1_NESTED_BODY = """def run(spec):
    def nested(value):
        return value.lower()

    if spec.get("reach"):
        return nested(spec["value"])
    return spec["value"]
"""
UNUSED_COMMENT = """def unused(value):
    # one
    return value


def run(spec):
    return spec["value"]
"""
UNUSED_BLANK = """def unused(value):
    value = str(value)

    return value


def run(spec):
    return spec["value"]
"""
MOVED_FUNCTIONS = """def f1(value):
    return value + "1"


def f2(value):
    return value.lower()


def f3(value):
    return value + "3"
"""
CALLS_MOVED = """from datamodel_code_generator import _moved


def run(spec):
    return _moved.f2(spec["value"])
"""
PARSE = """def parse(kind):
    if kind == "a":
        return "one"
    elif kind == "graph":
        return "other"
    return "other"
"""
CALLS_PARSE = """from datamodel_code_generator import _parse


def run(spec):
    return _parse.parse("a")
"""
GLOBAL_DECLARED = """X = "x"


def outer(reach):
    global X
    X = "x"

    def inner():
        return X

    return inner() if reach else X


def run(spec):
    return outer(spec.get("reach"))
"""
TERNARY_ARM = """def run(spec):
    value = spec["value"]
    return value.upper() if not spec.get("reach") else value.casefold()
"""
OR_OPERAND = """def run(spec):
    value = spec["value"]
    return str(not spec.get("reach") or value.casefold())
"""
COMPREHENSION_FILTER = """def run(spec):
    reach = spec.get("reach")
    return "".join([part for part in spec["value"] if reach and part.casefold()])
"""
ONE_LINE_IF = """def run(spec):
    value = spec["value"]
    if spec.get("reach"): value = value.casefold()
    return value
"""
DEFAULT_IN_HEADER = """def convert(value, suffix=""):
    return value + suffix


def run(spec):
    return convert(spec["value"])
"""
OVERLOADED = """from __future__ import annotations

from typing import overload


@overload
def convert(value: int) -> int: ...
@overload
def convert(value: str) -> str: ...
def convert(value):
    return value


def run(spec):
    return convert(spec["value"])
"""
OVERRIDDEN = """from __future__ import annotations

from typing import override


class Base:
    def label(self, value: str) -> str:
        return value


class Child(Base):
    def label(self, value: str) -> str:
        return value


def run(spec):
    return Base().label(spec["value"])
"""
ANNOTATED = """from __future__ import annotations


def unused(value: int) -> int:
    return value


def run(spec):
    return spec["value"]
"""
CHECKED_ONLY = """import typing

if typing.TYPE_CHECKING:
    from os import PathLike


def run(spec):
    return spec["value"]
"""
CODEC = """def encode(value):
    return value
"""
READS_CODEC = """from pathlib import Path


def run(spec):
    if spec.get("reach"):
        return spec["value"] + Path(__file__).with_name("_runtime").joinpath("codec.py").read_text(encoding="utf-8")[:0]
    return spec["value"]
"""
IMPORTS_SHARED = """from pathlib import Path

from datamodel_code_generator._runtime.shared import token


def run(spec):
    value = token(spec["value"])
    if spec.get("reach"):
        value += Path(__file__).with_name("_runtime").joinpath("shared.py").read_text(encoding="utf-8")[:0]
    return value
"""
SHARED = """def token(value):
    return value
"""
REGISTRY = """REGISTRY = {}


def register(name):
    def add(function):
        REGISTRY[name] = function
        return function

    return add
"""
HANDLER = """

@register("x")
def handler(value):
    return value
"""
CALLS_HANDLER = """from datamodel_code_generator import _handlers, _registry


def run(spec):
    if spec.get("reach"):
        return (_registry.REGISTRY.get("x") or _handlers.REGISTRY["x"])(spec["value"])
    return spec["value"]
"""
IMPORTED_REGISTER = """from datamodel_code_generator._registry import REGISTRY, register
"""
PLAIN_RUN = """def run(spec):
    return spec["value"]
"""
RELATIVE_USER = """from .base import X


def f(value):
    return value + X
"""
CLASS_ATTRIBUTE = """class Statement:
    DEFAULT_IMPORTS = ("a", "b")

    def imports(self):
        return self.DEFAULT_IMPORTS


def run(spec):
    if spec.get("reach"):
        return "".join(Statement().imports())
    return spec["value"]
"""
ALIASED = """Alias = str


def convert(value: "Alias") -> str:
    return value


def run(spec):
    if spec.get("reach"):
        return Alias(spec["value"])
    return convert(spec["value"])
"""
READS_RESOURCE = """import json
from pathlib import Path


def run(spec):
    if spec.get("reach"):
        json.loads(Path(__file__).with_name("resources").joinpath("table.json").read_text(encoding="utf-8"))
    return spec["value"]
"""
READS_TEMPLATE = """from pathlib import Path


def run(spec):
    if spec.get("reach"):
        Path(__file__).with_name("templates").joinpath("model.jinja2").read_text(encoding="utf-8")
    return spec["value"]
"""
REFORMATTED = """def run(spec):
    value = spec["value"]
    value = value if spec else value.lower()
    return value
"""
KEYWORD_ADDED = """def run(spec):
    return str(spec["value"])
"""
REINDENTED = """def run(spec):
    value = spec["value"]
    value = str(value)
    return value
"""
ELSE_ARM_DELETED = """def run(spec):
    value = spec["value"]
    result = value if not spec.get("reach") else str(value)
    return result
"""
LOCAL_ANNOTATION = """def run(spec):
    local: int = spec["value"]
    return local
"""
HELPERS = """def f1(value):
    return value + "1"


def f2(value):
    return value


def f3(value):
    return value + "3"
"""
CALLS_HELPERS = """from datamodel_code_generator import _helpers


def run(spec):
    return _helpers.f2(spec["value"])
"""
REORDERED = """def first(value):
    return value


def second(value):
    return first(value)


def run(spec):
    return second(spec["value"])
"""
METHOD_MOVED = """class A:
    def keep(self, value):
        return value

    def moved(self, value):
        return value


class B:
    def keep(self, value):
        return value


def run(spec):
    if spec.get("reach"):
        return (A if hasattr(A, "moved") else B)().moved(spec["value"])
    return spec["value"]
"""
DELETION_ONLY = """def unused(fields):
    if not fields:
        fields.append("typename")
    return fields


def run(spec):
    if spec.get("reach"):
        return str(unused([1]))
    return spec["value"]
"""
FOLDED_TUPLE = _reaching("""def unused(value):
    if value not in (
        "typename__",
        "__typename",
    ):
        return "1"
    return "2"
""")
TUPLE_DEFAULT = _reaching("""def unused(value, names=("a",)):
    return value
""")
MULTI_LINE_DEFAULT = _reaching("""def unused(
    value,
    label: str | None = "x",
):
    return value
""")
FOLDED_SET = _reaching("""def unused(value):
    if value in {
        "x",
        "y",
    }:
        return "1"
    return value
""")
FOLDED_STRING = _reaching("""def unused(value):
    note = (
        "small "
        "base"
    )
    return value if note else ""
""")
FOLDED_COMPREHENSION = _reaching("""def unused(value):
    return "".join(
        part for part in value
        if part not in (
            "x",
        )
    )
""")
CACHED = """import functools


@functools.cache
def table():
    return {"a": "x"}


def run(spec):
    return table()["a"] + spec["value"]
"""
LAZY = """TABLE = {}
TABLE.update(a="x")
"""
READS_LAZY = """def run(spec):
    from datamodel_code_generator import _lazy

    return _lazy.TABLE["a"] if spec.get("reach") else spec["value"]
"""
CLIENT = """CLIENT = "base"


def run(spec):
    return spec["value"]
"""
LATER_CASE = """def run(spec):
    match spec.get("reach"):
        case None:
            return spec["value"]
        case True:
            return spec["value"].upper()
    return "other"
"""
LATER_CASE_PASSES = """def run(spec):
    value = spec["value"]
    match spec.get("reach"):
        case None:
            value = value + ""
        case True:
            pass
    return value
"""
FIELDS = """from dataclasses import astuple, dataclass


@dataclass
class Row:
    a: str = "a"
    b: str = "b"


def run(spec):
    if spec.get("reach"):
        return "".join(astuple(Row()))
    return spec["value"]
"""
BASE_LABEL = """class Base:
    def label(self, value):
        return value
"""
OVERRIDING_FIELD = """from datamodel_code_generator._base import Base


class Field(Base):
    def label(self, value):
        return value.upper()
"""
PLAIN_FIELD = """from datamodel_code_generator._base import Base


class Field(Base):
    pass
"""
CALLS_FIELD = """import datamodel_code_generator._y
from datamodel_code_generator._x import Field


def run(spec):
    if spec.get("reach"):
        return Field().label(spec["value"])
    return spec["value"]
"""
REEXPORTED = """from datamodel_code_generator._reexport import LIMIT


def run(spec):
    if spec.get("reach"):
        return spec["value"] * LIMIT
    return spec["value"]
"""
READ_ON_A_VARIABLE = """class Table:
    LIMIT = 1


def scale(kind, value):
    return value * kind.LIMIT


def run(spec):
    if spec.get("reach"):
        return scale(Table, spec["value"])
    return spec["value"]
"""
READ_ON_A_VARIABLE_AND_SELF = """class Table:
    LIMIT = 1

    def unused(self):
        return self.LIMIT


def scale(kind, value):
    return value * kind.LIMIT


def run(spec):
    if spec.get("reach"):
        return scale(Table, spec["value"])
    return spec["value"]
"""
TUPLE_ASSIGNED = """A, B = "a", "b"


def run(spec):
    if spec.get("reach"):
        return A + B
    return spec["value"] + A
"""
DOCUMENTED = """def unused(value):
    \"\"\"Return the value.\"\"\"
    return value


def run(spec):
    if spec.get("reach"):
        return unused(spec["value"])
    return spec["value"]
"""
RENAMED = """def f1(value):
    return value + "1"


def f2(value):
    return value.lower()


def f3(value):
    return value + "3"


def f4(value):
    return value + "4"
"""
LAMBDAS_ON_ONE_LINE = """def run(spec):
    first, second = (lambda value: value.lower()), (lambda value: value.lower())
    return second(spec["value"]) if spec.get("reach") else first(spec["value"])
"""
LABELLED = """LABEL = "a"


def other(value):
    return value if LABEL else ""


def render(value):
    return LABEL + value
"""
CALLS_RENDER = """import datamodel_code_generator._b
from datamodel_code_generator import _a


def run(spec):
    if spec.get("reach"):
        return _a.render(spec["value"])
    return _a.other(spec["value"]) if callable(_a.render) else ""
"""
READ_BY_ANOTHER_CALL = """class Base:
    LIMIT = 1

    def render(self, read):
        return str(self.LIMIT) if read else ""


class Child(Base):
    LIMIT = 3


def run(spec):
    Base().render(False)
    Child().render(True)
    if spec.get("reach"):
        return Base().render(True) + spec["value"]
    return spec["value"]
"""
IMPORTS_TWO_IN_ONE_STATEMENT = """def run(spec):
    if spec.get("fail"):
        from datamodel_code_generator import _lazy, _other
    else:
        from datamodel_code_generator import _other
    return spec["value"]
"""
TABLE_AT_IMPORT = """import json
from pathlib import Path

TABLE = json.loads(Path(__file__).with_name("resources").joinpath("table.json").read_text(encoding="utf-8"))
"""
IMPORTS_TABLE = """def run(spec):
    if spec.get("reach"):
        from datamodel_code_generator import _table

        return str(_table.TABLE["a"]) + spec["value"]
    return spec["value"]
"""
DOCUMENTED_AND_READ = (
    DOCUMENTED
    + """

def doc():
    import pydoc

    return pydoc.getdoc(unused)
"""
)
READ_BY_TYPE_CHECKERS = """from typing import TYPE_CHECKING, Literal

LIMIT = 1

if TYPE_CHECKING:
    Alias = Literal[LIMIT]


def run(spec):
    return spec["value"]
"""
EVERY_ORDER = (("complete", ("failed.json", "plain.json")), ("complete", ("plain.json", "failed.json")))
"""Both orders of a compared run and a run that fails on both sides, which runs the change first in one of them."""
ORACLES: dict[str, Oracle] = {
    "r2_p1_class_level_lambda": _changed(P1_CLASS_LAMBDA, ("lambda value: value)", "lambda value: value.lower())")),
    "r2_p1_function_level_lambda": _changed(
        P1_FUNCTION_LAMBDA, ("lambda value: value\n", "lambda value: value.lower()\n")
    ),
    "r2_p1_genexpr": _changed(P1_GENEXPR, ("part.lower()", "part.casefold()")),
    "r2_p1_one_line_def": _changed(P1_ONE_LINE_DEF, ("value.lower()", "value.casefold()")),
    "r2_p2_tuple_in_lambda": _changed(P2_TUPLE_IN_LAMBDA, ('        "y",\n', '        "y",\n        "z",\n')),
    "r2_p3_elif_deleted": _changed(
        P3_ELIF, ('    elif spec.get("graph", True):\n        result = str(spec["value"])\n', "")
    ),
    "r2_p3_else_deleted": _changed(P3_ELSE, ('    else:\n        result = result + ""\n', "")),
    "r2_p4_override_deleted": _changed(P4_OVERRIDE, ("\n    def label(self, value):\n        return str(value)\n", "")),
    "r2_c4_override_body_changed": _changed(C4_OVERRIDE_BODY, ("value.lower()", "value.casefold()")),
    "r2_c1_nested_def_body_changed": _changed(C1_NESTED_BODY, ("value.lower()", "value.casefold()")),
    "r2_comment_only_edit": _changed(UNUSED_COMMENT, ("# one", "# two"), runs=NO_UNIT),
    "r2_blank_line_removed": _changed(UNUSED_BLANK, ("str(value)\n\n", "str(value)\n"), runs=NO_UNIT),
    "r2_shim_left_by_a_move": Oracle(
        {"_moved.py": MOVED_FUNCTIONS, "_case.py": CALLS_MOVED},
        {
            "_moved.py": "from datamodel_code_generator._target import f1, f2, f3  # noqa: F401\n",
            "_target.py": _edit(MOVED_FUNCTIONS, ("value.lower()", "value.casefold()")),
        },
        (("complete", PLAIN),),
    ),
    "r2_cache_first_computed_by_a_cell_that_fails": _changed(
        CACHED, ('{"a": "x"}', '{"a": str("x")}'), runs=EVERY_ORDER
    ),
    "f1_copy_hides_a_deletion": Oracle(
        {
            "_parse.py": PARSE,
            "_case.py": CALLS_PARSE,
        },
        {
            "_parse.py": _edit(PARSE, ('    elif kind == "graph":\n        return "other"\n', "")),
            "_copy.py": PARSE,
            "_case.py": "from datamodel_code_generator import _copy, _parse\n\n\ndef run(spec):\n"
            '    return _parse.parse("a") if all(_copy.parse(kind) for kind in ("a", "graph", "b")) else ""\n',
        },
        (("incomplete", PLAIN),),
    ),
    "f2_global_declaration_removed": _changed(GLOBAL_DECLARED, ("    global X\n", "")),
    "f3_ternary_arm": _changed(TERNARY_ARM, ("value.casefold()", "value.lower()")),
    "f3_or_operand": _changed(OR_OPERAND, ("value.casefold()", "value.lower()")),
    "f3_comprehension_filter": _changed(COMPREHENSION_FILTER, ("part.casefold()", "part.lower()")),
    "f3_one_line_if_body": _changed(ONE_LINE_IF, ("value.casefold()", "value.lower()")),
    "f4_header_first_seen_in_a_cell_not_compared": _changed(
        DEFAULT_IN_HEADER, ('suffix=""', "suffix=str()"), runs=(("complete", ("failed.json", "plain.json")),)
    ),
    "f5_overload_stub": _changed(
        OVERLOADED, ("def convert(value: int) -> int", "def convert(value: bytes) -> bytes"), runs=NO_UNIT
    ),
    "f5_override_decorator": _changed(
        OVERRIDDEN, ("class Child(Base):\n", "class Child(Base):\n    @override\n"), runs=NO_UNIT
    ),
    "f5_annotation_under_the_future_import": _changed(ANNOTATED, ("value: int", "value: str"), runs=NO_UNIT),
    "f5_typing_dot_type_checking_body": _changed(
        CHECKED_ONLY, ("import PathLike", "import PathLike, fspath"), runs=NO_UNIT
    ),
    "f6_copied_runtime_module": Oracle(
        {"_runtime/__init__.py": "", "_runtime/codec.py": CODEC, "_case.py": READS_CODEC},
        {"_runtime/codec.py": _edit(CODEC, ("return value", "return str(value)"))},
    ),
    "f6_imported_runtime_module": Oracle(
        {"_runtime/__init__.py": "", "_runtime/shared.py": SHARED, "_case.py": IMPORTS_SHARED},
        {"_runtime/shared.py": _edit(SHARED, ("return value", "return str(value)"))},
    ),
    "f8_move_to_a_module_with_its_own_registry": Oracle(
        {"_registry.py": REGISTRY + HANDLER, "_handlers.py": REGISTRY, "_case.py": CALLS_HANDLER},
        {"_registry.py": REGISTRY, "_handlers.py": REGISTRY + HANDLER},
    ),
    "f8_control_move_with_the_registry_imported": Oracle(
        {"_registry.py": REGISTRY + HANDLER, "_handlers.py": IMPORTED_REGISTER, "_case.py": CALLS_HANDLER},
        {"_registry.py": REGISTRY, "_handlers.py": IMPORTED_REGISTER + HANDLER},
        (("complete", PLAIN), ("complete", REACHED)),
    ),
    "f8_rename_whose_relative_import_reaches_another_module": Oracle(
        {
            "a/__init__.py": "",
            "a/base.py": 'X = "x"\n',
            "a/mod.py": RELATIVE_USER,
            "b/__init__.py": "",
            "b/base.py": 'X = "y"\n',
            "_case.py": PLAIN_RUN,
        },
        {"a/mod.py": None, "b/mod.py": RELATIVE_USER},
        (("incomplete", PLAIN),),
    ),
    "f8_control_rename_in_its_package": Oracle(
        {"a/__init__.py": "", "a/base.py": 'X = "x"\n', "a/mod.py": RELATIVE_USER, "_case.py": PLAIN_RUN},
        {"a/mod.py": None, "a/other.py": RELATIVE_USER},
        NO_UNIT,
    ),
    "q1_class_attribute_no_compared_cell_reads": _changed(CLASS_ATTRIBUTE, ('("a", "b")', 'tuple("ab")')),
    "q1_binding_read_in_a_string_annotation_too": _changed(ALIASED, ("Alias = str", 'Alias = type("")')),
    "q3_json_resource": Oracle(
        {"resources/table.json": '{"a": 1}\n', "_case.py": READS_RESOURCE},
        {"resources/table.json": '{"a": 1, "b": 2}\n'},
    ),
    "q3_template_without_a_compiled_renderer": Oracle(
        {"templates/model.jinja2": "class {{ name }}: ...\n", "_case.py": READS_TEMPLATE},
        {"templates/model.jinja2": "class {{ name }}:\n    pass\n"},
    ),
    "v2_reformat_only": _changed(
        REFORMATTED,
        (
            "value = value if spec else value.lower()",
            "value = (\n        value if spec\n        else value.lower()\n    )",
        ),
        runs=(("complete", PLAIN),),
    ),
    "v2_keyword_added": _changed(
        KEYWORD_ADDED, ('str(spec["value"])', 'str(object=spec["value"])'), runs=(("complete", PLAIN),)
    ),
    "v2_reindented_block": _changed(
        REINDENTED, ("    value = str(value)\n", '    if spec.get("reach"):\n        value = str(value)\n')
    ),
    "v2_else_arm_deleted": _changed(ELSE_ARM_DELETED, (' if not spec.get("reach") else str(value)', "")),
    "v2_local_annotation": _changed(LOCAL_ANNOTATION, ("local: int", "local: object"), runs=NO_UNIT),
    "v2_functions_moved_out": Oracle(
        {"_helpers.py": HELPERS, "_case.py": CALLS_HELPERS},
        {
            "_helpers.py": "from datamodel_code_generator._split import f2, f3  # noqa: F401\n\n\n"
            + HELPERS[: HELPERS.index("def f2")].rstrip()
            + "\n",
            "_split.py": HELPERS[HELPERS.index("def f2") :],
        },
        (("complete", PLAIN),),
    ),
    "v2_functions_reordered": _changed(
        REORDERED,
        (
            "def first(value):\n    return value\n\n\ndef second(value):\n    return first(value)\n",
            "def second(value):\n    return first(value)\n\n\ndef first(value):\n    return value\n",
        ),
        runs=(("complete", PLAIN),),
    ),
    "v2_method_moved_to_another_class": _changed(
        METHOD_MOVED,
        ("\n    def moved(self, value):\n        return value\n\n\nclass B:", "\n\nclass B:"),
        (
            "class B:\n    def keep(self, value):\n        return value\n",
            (
                "class B:\n    def keep(self, value):\n        return value\n\n"
                "    def moved(self, value):\n        return value\n"
            ),
        ),
    ),
    "r1_a_deletion_only_hunk": _changed(
        DELETION_ONLY, ('    if not fields:\n        fields.append("typename")\n', ""), runs=(("incomplete", PLAIN),)
    ),
    "r1_b_member_of_a_folded_tuple": _changed(
        FOLDED_TUPLE, ('        "__typename",\n', '        "__typename",\n        "typename",\n')
    ),
    "r1_d_tuple_default": _changed(TUPLE_DEFAULT, ('names=("a",)', 'names=("a", "b")')),
    "r1_d2_default_in_a_multi_line_signature": _changed(MULTI_LINE_DEFAULT, ('= "x"', '= "y"')),
    "r1_fold_member_of_a_set": _changed(FOLDED_SET, ('        "y",\n', '        "y",\n        "z",\n')),
    "r1_fold_part_of_a_joined_string": _changed(FOLDED_STRING, ('"base"', '"head"')),
    "r1_fold_member_in_a_comprehension": _changed(
        FOLDED_COMPREHENSION, ('            "x",\n', '            "x",\n            "y",\n')
    ),
    "r1_e_every_cell_fails_on_the_base_only": _changed(
        'def run(spec):\n    raise ValueError(spec["value"])\n',
        ("raise ValueError(", "return ("),
        runs=(("incomplete", PLAIN),),
    ),
    "r1_c1_client_written_to_a_relative_path": _changed(
        CLIENT, ('"base"', '"head"'), runs=(("differ", ("--options", "--client-output client", "plain.json")),)
    ),
    "r1_c1_client_written_outside_the_run": _changed(
        CLIENT,
        ('CLIENT = "base"', 'CLIENT = str("base")'),
        runs=(("incomplete", ("--options", "--client-output {tmp}/client", "plain.json")),),
    ),
    "r1_lazy_module_imported_first_by_a_cell_that_fails": Oracle(
        {"_lazy.py": LAZY, "_case.py": READS_LAZY},
        {"_lazy.py": _edit(LAZY, ('a="x"', 'a=str("x")'))},
        (("complete", ("failed_reached.json", "reached.json")), ("complete", ("reached.json", "failed_reached.json"))),
    ),
    "r1_rename_with_an_edit": Oracle(
        {"_old.py": RENAMED, "_case.py": CALLS_HELPERS.replace("_helpers", "_old")},
        {
            "_old.py": None,
            "_new.py": _edit(RENAMED, ("value.lower()", "value.casefold()")),
            "_case.py": CALLS_HELPERS.replace("_helpers", "_new"),
        },
        (("complete", PLAIN),),
    ),
    "r3_m1_pattern_of_a_later_case": _changed(LATER_CASE, ("case True:", "case False | True:")),
    "r3_m1_wildcard_of_a_later_case": _changed(LATER_CASE, ("case True:", "case _:")),
    "r3_m1_wildcard_with_nothing_to_run": _changed(LATER_CASE_PASSES, ("case True:", "case _:")),
    "r3_m1_alternative_no_run_tries": _changed(
        LATER_CASE, ("case True:", "case True | False:"), runs=(("incomplete", PLAIN), ("incomplete", REACHED))
    ),
    "r3_m2_dataclass_fields_reordered": _changed(
        FIELDS,
        ('    a: str = "a"\n    b: str = "b"\n', '    b: str = "b"\n    a: str = "a"\n'),
        runs=(("incomplete", PLAIN), ("differ", REACHED)),
    ),
    "r3_m2_method_moved_between_same_named_classes": Oracle(
        {"_base.py": BASE_LABEL, "_x.py": OVERRIDING_FIELD, "_y.py": PLAIN_FIELD, "_case.py": CALLS_FIELD},
        {"_x.py": PLAIN_FIELD, "_y.py": OVERRIDING_FIELD},
        (("incomplete", PLAIN), ("differ", REACHED)),
    ),
    "r3_m3_constant_read_through_a_reexport": Oracle(
        {
            "_consts.py": "LIMIT = 1\n",
            "_reexport.py": 'from datamodel_code_generator._consts import LIMIT\n\n__all__ = ["LIMIT"]\n',
            "_case.py": REEXPORTED,
        },
        {"_consts.py": "LIMIT = 2\n"},
        (("incomplete", PLAIN), ("differ", REACHED)),
    ),
    "r3_m4_class_attribute_read_on_a_variable": _changed(
        READ_ON_A_VARIABLE, ("LIMIT = 1", "LIMIT = 2"), runs=(("incomplete", PLAIN), ("differ", REACHED))
    ),
    "r3_m4_class_attribute_read_on_a_variable_reached": _changed(
        READ_ON_A_VARIABLE_AND_SELF, ("    LIMIT = 1\n", "    LIMIT = int(1)\n")
    ),
    "r3_s1_tuple_assignment_other_name_read": _changed(
        TUPLE_ASSIGNED, ('"a", "b"', '"a", "c"'), runs=(("incomplete", PLAIN), ("differ", REACHED))
    ),
    "r3_s3_docstring_of_a_function_no_run_calls": _changed(
        DOCUMENTED, ("Return the value.", "Return the given value."), runs=NO_UNIT
    ),
    "r4_lambdas_on_one_line": _changed(
        LAMBDAS_ON_ONE_LINE, ("(lambda value: value.lower())\n", "(lambda value: value.casefold())\n")
    ),
    "r4_moved_function_reads_another_module_s_binding": Oracle(
        {"_a.py": LABELLED, "_b.py": 'LABEL = "b"\n', "_case.py": CALLS_RENDER},
        {
            "_a.py": "from datamodel_code_generator._b import render  # noqa: F401\n\n"
            + _edit(LABELLED[: LABELLED.index("def render")].rstrip() + "\n", ('"a"', '"c"')),
            "_b.py": 'LABEL = "b"\n\n\n' + LABELLED[LABELLED.index("def render") :],
        },
        (("incomplete", PLAIN), ("differ", REACHED)),
    ),
    "r4_receiver_of_another_call": _changed(
        READ_BY_ANOTHER_CALL, ("    LIMIT = 1\n", "    LIMIT = 2\n"), runs=(("incomplete", PLAIN), ("differ", REACHED))
    ),
    "r4_sibling_imported_by_the_same_statement": Oracle(
        {"_lazy.py": LAZY, "_other.py": 'VALUE = "y"\n', "_case.py": IMPORTS_TWO_IN_ONE_STATEMENT},
        {"_lazy.py": _edit(LAZY, ('a="x"', 'a=str("x")'))},
        (("incomplete", ("failed.json", "plain.json")),),
    ),
    "r4_resource_read_by_a_module_s_import": Oracle(
        {"resources/table.json": '{"a": 1}\n', "_table.py": TABLE_AT_IMPORT, "_case.py": IMPORTS_TABLE},
        {"resources/table.json": '{"a": 1, "b": 2}\n'},
    ),
    "r4_docstring_reader_called_on_its_module": _changed(
        DOCUMENTED_AND_READ, ("Return the value.", "Return the given value.")
    ),
    "r4_binding_read_only_by_type_checkers": _changed(
        READ_BY_TYPE_CHECKERS, ("LIMIT = 1", "LIMIT = 2"), runs=(("complete", PLAIN),)
    ),
}
"""The shapes of the oracle reviews of C3 and of its design, each with the verdict it must give."""


def _oracle_project(tmp_path: Path, oracle: Oracle) -> tuple[Path, list[str]]:
    """Commit the stand-in package of `oracle` beside this script, then write its change into the working tree.

    Return the repository and the command that compares its working tree with the commit.
    """
    repo = tmp_path / "repo"
    package = repo / "src" / "datamodel_code_generator"
    files = {
        "__init__.py": "",
        "__main__.py": ORACLE_MAIN,
        "arguments.py": ORACLE_ARGUMENTS,
        "_config_fields.py": 'OUTPUT_PATH_FIELDS = ("output", "client_output")\n',
        **oracle.base,
    }
    for name, text in files.items():
        (package / name).parent.mkdir(parents=True, exist_ok=True)
        (package / name).write_text(text, encoding="utf-8")
    (repo / "scripts").mkdir()
    for script in ("compare_release.py", "_change_units.py", "_source_model.py"):
        shutil.copy(ROOT / "scripts" / script, repo / "scripts" / script)
    for name, spec in ORACLE_INPUTS.items():
        (repo / "inputs" / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / "inputs" / name).write_text(json.dumps(spec), encoding="utf-8")
    for args in (
        ["init", "-q"],
        ["add", "-A", "."],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout
    for name, text in oracle.head.items():
        if text is None:
            (package / name).unlink()
        else:
            (package / name).parent.mkdir(parents=True, exist_ok=True)
            (package / name).write_text(text, encoding="utf-8")
    command = [sys.executable, str(repo / "scripts" / "compare_release.py"), "--base", commit.strip()]
    command += ["--candidate-python", sys.executable, "--cache-dir", str(tmp_path / "cache"), "-j", "1"]
    return repo, [*command, "--types", "pydantic_v2.BaseModel"]


def _verdict(code: int, stdout: str) -> str:
    """Return the verdict a run gave: `complete`, `incomplete`, `differ`, or its exit status where it gave none."""
    lines = stdout.splitlines()
    shown = {
        "complete": code == 0 and "evidence: complete" in lines,
        "differ": code == 1 and any(line.startswith("summary:") and "differ=0," not in line for line in lines),
        "incomplete": code == 1 and any(line.startswith("evidence: incomplete") for line in lines),
    }
    return next((verdict for verdict, holds in shown.items() if holds), f"exit={code}")


@pytest.mark.allow_direct_assert(
    "an oracle case fails on its own verdict, never through a stored output a fix run accepts"
)
@needs("sys-monitoring")
@pytest.mark.parametrize("case", sorted(ORACLES))
def test_compare_release_evidence_oracle(case: str, tmp_path: Path) -> None:
    """Against a git revision, each oracle shape gives its verdict: the evidence names what no compared run ran.

    The shapes are the false passes and false fails the reviews of the comparator's evidence found, their controls and
    the shapes they keep: a change counts by the code objects whose changed tokens it touches, measured on the base
    side for what it removes and on the head side for what it adds, in compared runs only.
    """
    oracle = ORACLES[case]
    repo, command = _oracle_project(tmp_path, oracle)
    outputs, verdicts = [], []
    for wanted, args in oracle.runs:
        run = subprocess.run(
            [*command, *(arg.format(tmp=tmp_path) for arg in args)],
            cwd=repo / "inputs",
            capture_output=True,
            text=True,
            check=False,
        )
        verdicts.append((wanted, _verdict(run.returncode, run.stdout)))
        output = f"{' '.join(args)}: exit={run.returncode}\n{run.stdout}"
        outputs.append(output.replace(os.path.realpath(tmp_path), "<tmp>").replace(str(tmp_path), "<tmp>"))
    assert [got for _, got in verdicts] == [wanted for wanted, _ in verdicts], "\n".join(outputs)
    assert_output("\n".join(outputs))


@pytest.mark.allow_direct_assert(
    "an oracle case fails on its own verdict, never through a stored output a fix run accepts"
)
@needs("no-sys-monitoring")
def test_compare_release_measures_nothing_without_sys_monitoring(tmp_path: Path) -> None:
    """On Python 3.11 a change with code objects to run cannot be measured: the evidence is incomplete."""
    repo, command = _oracle_project(tmp_path, ORACLES["f3_ternary_arm"])
    run = subprocess.run([*command, *REACHED], cwd=repo / "inputs", capture_output=True, text=True, check=False)
    assert _verdict(run.returncode, run.stdout) == "incomplete", run.stdout
    assert_output(f"exit={run.returncode}\n{run.stdout}".replace(str(tmp_path), "<tmp>"))


@pytest.mark.allow_direct_assert("a table pinned to the package fails on its own, never through a stored output")
def test_the_annotation_reader_sites_are_the_package_s() -> None:
    """`READER_SITES` lists exactly the code of the package that may read annotations at run time, both ways.

    Every site the source finds is listed (with why it reads no function's annotations) and every listed site is
    still there, so the table is a check and no escape hatch.
    """
    found = set(change_units.reader_sites(change_units.Side("head", ROOT / "src")))
    listed = set(change_units.READER_SITES)
    assert found == listed, f"not listed: {sorted(found - listed)}; listed, not found: {sorted(listed - found)}"
    assert_output(
        "".join(f"{path} {qualname}: {change_units.READER_SITES[path, qualname]}\n" for path, qualname in sorted(found))
    )


@pytest.mark.allow_direct_assert("a table pinned to the package fails on its own, never through a stored output")
def test_the_docstring_reader_sites_are_the_package_s() -> None:
    """`DOC_READER_SITES` lists exactly the code of the package that may read docstrings at run time, both ways."""
    found = set(change_units.reader_sites(change_units.Side("head", ROOT / "src"), change_units.DOCSTRINGS))
    listed = set(change_units.DOC_READER_SITES)
    assert found == listed, f"not listed: {sorted(found - listed)}; listed, not found: {sorted(listed - found)}"
    assert_output(
        "".join(f"{path} {name}: {change_units.DOC_READER_SITES[path, name]}\n" for path, name in sorted(found))
    )
