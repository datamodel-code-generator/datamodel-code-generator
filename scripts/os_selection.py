"""Read the tests the Windows and macOS jobs of a pull request run (``tests/os_selection.toml``), and keep the file.

``pytest --os-selection=tests/os_selection.toml`` (tests/conftest.py) keeps a test the file selects and deselects the
others, and ``--test-shard`` then splits what it kept. Pushes to main and the scheduled run keep the whole suite on
Windows and macOS, so a platform failure outside the selection still shows there, one run later.

Paths and node ids in the file are relative to the repository root, and a test function stands for each of its
parametrizations (the ids of some differ by platform and Python):

- ``ubuntu-only``: globs of test modules that Windows and macOS never run. No test of them is selected.
- ``[select]``, each a reason to keep tests:
  - ``facts``: whole test modules that name, in ``needs(...)`` or ``varies_with=(...)``, a capability of
    tests/capabilities.py whose clauses read one of these facts, or whose ``.refs`` keys a stored line on one;
  - ``source``: whole test modules whose source matches one of these patterns;
  - ``modules``: globs of whole test modules;
  - ``tests``: test functions (``module::function`` or ``module::Class::function``);
  - ``cover``: test functions `cover` writes from a per-test coverage map.

``cover`` adds, cheapest first by the Windows durations of scripts/ci_shard_weights.json (greedy), the test functions
that run the arcs of the platform functions which the rest of the selection leaves unrun. A platform function makes a
platform effect directly (file system, processes, time, signals, streams, path semantics): heki reports it under the
effect boundaries of scripts/heki_platform_effects.toml. The map is a coverage data file of the whole suite with branch
arcs and test contexts, recorded with the Python tracer (the sys.monitoring core of Python 3.12 and later records an arc
once per process, not once per test)::

    COVERAGE_CORE=pytrace pytest -p no:randomly --cov=datamodel_code_generator --cov-branch --cov-context=test \
        --cov-report= tests
    coverage combine   # when the run wrote one data file per process

usage:
  python scripts/os_selection.py check            fail on an entry that names no test module or no test function
  python scripts/os_selection.py ubuntu-only      print the test modules Windows and macOS never run
  python scripts/os_selection.py cover MAP        rewrite ``cover`` from a per-test coverage map
"""

from __future__ import annotations

import argparse
import heapq
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import sysconfig
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts._source_model import SourceModule
from scripts.select_ci_test_shard import WEIGHTS_PATH, estimate_test_durations
from tests.capabilities import reading
from tests.units import FACTS

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

ROOT = Path(__file__).resolve().parents[1]
SELECTION = ROOT / "tests" / "os_selection.toml"
PLATFORM_EFFECTS = Path(__file__).with_name("heki_platform_effects.toml")
COVER_PROFILE = "windows"
SRC_MARK = "src/datamodel_code_generator/"
_SELECT_KEYS = frozenset({"facts", "source", "modules", "tests", "cover"})
_TOP_KEYS = frozenset({"ubuntu-only", "select"})
_COVER = re.compile(r"^cover = \[\n(?:  .*\n)*\]\n", re.MULTILINE)


def _strings(table: Mapping[str, object], key: str, name: str) -> tuple[str, ...]:
    """Return a list of strings of the file, or raise for any other value."""
    value = table.get(key, [])
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    msg = f"{name} is not a list of strings"
    raise ValueError(msg)


def _table(document: Mapping[str, object], key: str) -> Mapping[str, object]:
    """Return a table of the file, or raise for any other value."""
    value = document.get(key, {})
    if isinstance(value, dict):
        return value
    msg = f"{key} is not a table"
    raise ValueError(msg)


def function_of(nodeid: str) -> str:
    """Return the test function a node id belongs to: the id without its parametrization."""
    return nodeid.partition("[")[0]


def _module_of(nodeid: str) -> str:
    return nodeid.partition("::")[0]


def _glob(root: Path, patterns: Iterable[str]) -> frozenset[str]:
    """Return the files under a root that any of the globs match, relative to it."""
    return frozenset(
        path.relative_to(root).as_posix() for pattern in patterns for path in root.glob(pattern) if path.is_file()
    )


def _alternatives(names: Iterable[str]) -> str:
    return "|".join(map(re.escape, names))


@dataclass
class Selection:
    """The tests one selection file keeps, read from a repository root."""

    root: Path
    ubuntu_only: tuple[str, ...]
    facts: tuple[str, ...]
    source: tuple[re.Pattern[str], ...]
    modules: tuple[str, ...]
    tests: tuple[str, ...]
    cover: tuple[str, ...]
    excluded: frozenset[str] = field(init=False)
    """The test modules Windows and macOS never run."""
    functions: frozenset[str] = field(init=False)
    """The test functions the file names one by one."""
    _named: frozenset[str] = field(init=False, repr=False)
    _texts: tuple[re.Pattern[str], ...] = field(init=False, repr=False)
    _keys: re.Pattern[str] | None = field(init=False, repr=False)
    _whole: dict[str, bool] = field(init=False, repr=False, default_factory=dict)

    def __post_init__(self) -> None:
        """Expand the globs and build the patterns of the rules once."""
        self.excluded = _glob(self.root, self.ubuntu_only)
        self.functions = frozenset((*self.tests, *self.cover))
        self._named = _glob(self.root, self.modules)
        names = _alternatives(reading(self.facts))
        marks = (re.compile(rf'(?:needs\(|varies_with=\()[^)]*"(?:{names})"'),) if names else ()
        self._texts = (*marks, *self.source)
        self._keys = re.compile(rf"\b(?:{_alternatives(self.facts)})(?:==|!=|-eq-|-ne-)") if self.facts else None

    @classmethod
    def load(cls, path: Path, root: Path) -> Selection:
        """Read a selection file; raise `ValueError` for a missing or malformed one."""
        try:
            document = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as error:
            msg = f"cannot read the selection {path}: {error}"
            raise ValueError(msg) from error
        select = _table(document, "select")
        if unknown := sorted(set(document) - _TOP_KEYS) + sorted(set(select) - _SELECT_KEYS):
            msg = f"unknown keys in {path}: {', '.join(unknown)}"
            raise ValueError(msg)
        if unknown := sorted(set(facts := _strings(select, "facts", "select.facts")) - set(FACTS)):
            msg = f"select.facts names no fact of tests/units.py FACTS: {', '.join(unknown)}"
            raise ValueError(msg)
        try:
            source = tuple(map(re.compile, _strings(select, "source", "select.source")))
        except re.error as error:
            msg = f"select.source holds a pattern that does not compile: {error}"
            raise ValueError(msg) from error
        return cls(
            root,
            _strings(document, "ubuntu-only", "ubuntu-only"),
            facts,
            source,
            _strings(select, "modules", "select.modules"),
            _strings(select, "tests", "select.tests"),
            _strings(select, "cover", "select.cover"),
        )

    def _rule_holds(self, module: str) -> bool:
        """Return whether the ``facts`` or ``source`` rule keeps a whole module."""
        path = self.root / module
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        refs = path.with_suffix(".refs")
        return any(pattern.search(text) for pattern in self._texts) or bool(
            self._keys is not None and refs.is_file() and self._keys.search(refs.read_text(encoding="utf-8"))
        )

    def keeps_whole(self, module: str) -> bool:
        """Return whether the file keeps every test of a module."""
        if (whole := self._whole.get(module)) is None:
            whole = self._whole[module] = module not in self.excluded and (
                module in self._named or self._rule_holds(module)
            )
        return whole

    def may_hold(self, module: str) -> bool:
        """Return whether a module may hold a test the file keeps, so it has to be collected."""
        return self.keeps_whole(module) or (
            module not in self.excluded and any(_module_of(name) == module for name in self.functions)
        )

    def selects(self, nodeid: str) -> bool:
        """Return whether the file keeps a test."""
        module = _module_of(nodeid)
        return self.keeps_whole(module) or (module not in self.excluded and function_of(nodeid) in self.functions)

    def stale(self) -> Iterator[str]:
        """Yield each entry that names no file, no test function, or a test of a module Windows never runs."""
        for key, patterns in (("ubuntu-only", self.ubuntu_only), ("select.modules", self.modules)):
            yield from (f"{key}: {pattern} matches no file" for pattern in patterns if not _glob(self.root, (pattern,)))
        defined: dict[str, frozenset[str]] = {}
        for key, names in (("select.tests", self.tests), ("select.cover", self.cover)):
            for name in names:
                module, _, qualname = name.partition("::")
                if module in self.excluded:
                    yield f"{key}: {name} is in a module Windows and macOS never run (ubuntu-only)"
                elif qualname not in defined.setdefault(module, _defined_tests(self.root / module)):
                    yield f"{key}: {name} names no test function"


def _defined_tests(path: Path) -> frozenset[str]:
    """Return the functions of a test module pytest may collect: module-level ones and methods (``Class::name``)."""
    if not path.is_file():
        return frozenset()
    module = SourceModule(path.read_text(encoding="utf-8"), path.as_posix())
    return frozenset(
        scope.qualname.replace(".", "::")
        for scope in module.scopes
        if scope.kind == "function" and "<locals>" not in scope.qualname and _collectible(scope.qualname)
    )


def _collectible(qualname: str) -> bool:
    """Tell whether pytest collects a function of this name: ``test*``, at module level or in a ``Test*`` class."""
    *classes, name = qualname.split(".")
    return name.startswith("test") and all(part.startswith("Test") for part in classes)


def platform_functions(root: Path) -> dict[str, set[str]]:
    """Return the qualified names of the functions heki reports under the platform effect boundaries, per module."""
    heki = shutil.which("heki", path=sysconfig.get_path("scripts")) or "heki"
    command = [heki, "check", "--config", str(PLATFORM_EFFECTS), "--output-format", "json", "src"]
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, encoding="utf-8", check=False)
    if result.returncode not in {0, 1}:
        msg = f"heki failed ({result.returncode}): {result.stderr.strip()}"
        raise SystemExit(msg)
    functions: dict[str, set[str]] = {}
    for report in json.loads(result.stdout or "[]"):
        functions.setdefault(report["path"], set()).add(report["qualname"])
    return functions


def _body_ranges(path: Path) -> dict[str, tuple[int, int]]:
    """Return the lines of each function's body, from the line after its header, by qualified name.

    A generator expression, which heki names like a function (``f.<locals>.<genexpr>``), spans its lines to the end of
    the statement that holds it.
    """
    module = SourceModule(path.read_text(encoding="utf-8"), path.as_posix())
    ranges: dict[str, tuple[int, int]] = {}
    for scope in module.scopes:
        if scope.kind in {"function", "comprehension"} and (statement := module.statement_at(scope.line)) is not None:
            last = module.extent(statement).stop - 1
            first = min(module.header(scope).stop, last) if scope.kind == "function" else scope.line
            ranges[scope.qualname] = (first, last)
    return ranges


def _repo_path(path: str) -> str | None:
    """Return a measured file's path in the repository, read from its ``src/datamodel_code_generator/`` suffix."""
    normalized = path.replace("\\", "/")
    return None if (index := normalized.find(SRC_MARK)) < 0 else normalized[index:]


def _platform_arcs(root: Path, data: Path, functions: Mapping[str, set[str]]) -> dict[str, set[tuple[int, int, int]]]:
    """Return the arcs of the platform functions (qualified names per module) each test function of a map runs."""
    connection = sqlite3.connect(f"{data.resolve().as_uri()}?mode=ro", uri=True)
    rows = []
    for file_id, measured in connection.execute("select id, path from file"):
        if (path := _repo_path(measured)) is None or path not in functions:
            continue
        ranges = _body_ranges(root / path)
        rows.extend((file_id, *ranges[name]) for name in functions[path] if name in ranges)
    connection.execute("create temp table body (file_id integer, low integer, high integer)")
    connection.executemany("insert into body values (?, ?, ?)", rows)
    contexts = {
        context_id: function_of(context.rpartition("|")[0] or context)
        for context_id, context in connection.execute("select id, context from context")
        if context
    }
    arcs: dict[str, set[tuple[int, int, int]]] = {}
    query = (
        "select arc.context_id, arc.file_id, arc.fromno, arc.tono from arc join body"
        " on arc.file_id = body.file_id and (arc.fromno between body.low and body.high"
        " or arc.tono between body.low and body.high or -arc.tono between body.low and body.high)"
    )
    for context_id, file_id, start, end in connection.execute(query):
        if (test := contexts.get(context_id)) is not None:
            arcs.setdefault(test, set()).add((file_id, start, end))
    connection.close()
    return arcs


def _collected(root: Path) -> list[str]:
    """Return the node ids of the whole suite."""
    command = [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "-p",
        "no:xdist",
        "--rootdir",
        str(root),
        "tests",
    ]
    env = {**os.environ, "PYTHONUTF8": "1"}  # the child writes what the parent decodes, whatever the locale
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, encoding="utf-8", env=env, check=False)
    if result.returncode:
        msg = f"collecting the suite failed:\n{result.stdout[-2000:]}{result.stderr[-2000:]}"
        raise SystemExit(msg)
    return [line for line in result.stdout.splitlines() if "::" in line]


def _function_weights(nodeids: Iterable[str]) -> dict[str, int]:
    """Return each test function's Windows duration: its tests' measured or estimated CI durations, summed."""
    profile = json.loads(WEIGHTS_PATH.read_text(encoding="utf-8"))["profiles"][COVER_PROFILE]
    weights: dict[str, int] = {}
    for nodeid, weight in estimate_test_durations(nodeids, profile["slow"], profile["fast"]).items():
        weights[function_of(nodeid)] = weights.get(function_of(nodeid), 0) + weight
    return weights


def greedy_cover(
    arcs: Mapping[str, set[tuple[int, int, int]]], weights: Mapping[str, int], kept: Iterable[str]
) -> list[str]:
    """Return the test functions that run every arc the kept ones leave unrun, the most arcs per duration first."""
    kept = set(kept)
    covered = set().union(*(arcs.get(name, set()) for name in kept))
    heap = [
        (-len(fresh) / max(weights.get(name, 1), 1), name)
        for name, found in arcs.items()
        if name not in kept and (fresh := found - covered)
    ]
    heapq.heapify(heap)
    chosen: list[str] = []
    while heap:
        _, name = heapq.heappop(heap)
        if not (fresh := arcs[name] - covered):
            continue
        score = -len(fresh) / max(weights.get(name, 1), 1)
        if heap and (score, name) > heap[0]:
            heapq.heappush(heap, (score, name))
            continue
        chosen.append(name)
        covered |= fresh
    return sorted(chosen)


def write_cover(path: Path, cover: Iterable[str]) -> None:
    """Replace the ``cover`` list of a selection file, keeping the rest of its text."""
    text = path.read_text(encoding="utf-8")
    lines = "".join(f'  "{name}",\n' for name in cover)
    if (match := _COVER.search(text)) is None:
        msg = f"{path} has no 'cover = [' list of one entry per line to rewrite"
        raise SystemExit(msg)
    path.write_text(
        f"{text[: match.start()]}cover = [\n{lines}]\n{text[match.end() :]}", encoding="utf-8", newline="\n"
    )


def cover(selection: Selection, data: Path, functions: Mapping[str, set[str]]) -> list[str]:
    """Return the cover functions of a selection from a per-test coverage map and the platform functions per module."""
    nodeids = _collected(selection.root)
    weights = _function_weights(nodeids)
    excluded = selection.excluded
    arcs = {
        name: found
        for name, found in _platform_arcs(selection.root, data, functions).items()
        if name in weights and _module_of(name) not in excluded
    }
    kept = {function_of(nodeid) for nodeid in nodeids if selection.keeps_whole(_module_of(nodeid))}
    return greedy_cover(arcs, weights, kept | set(selection.tests))


def main(argv: list[str] | None = None) -> int:
    """Check the selection file, print its Ubuntu-only modules, or rewrite its cover."""
    parser = argparse.ArgumentParser(description=__doc__.partition("\n")[0])
    parser.add_argument("--file", type=Path, default=SELECTION, help="the selection file (tests/os_selection.toml)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="fail on an entry that names no test module or no test function")
    commands.add_parser("ubuntu-only", help="print the test modules Windows and macOS never run")
    cover_command = commands.add_parser("cover", help="rewrite the cover from a per-test coverage map")
    cover_command.add_argument("map", type=Path, help="a coverage data file with branch arcs and test contexts")
    args = parser.parse_args(argv)
    try:
        selection = Selection.load(args.file, ROOT)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    match args.command:
        case "check":
            if problems := list(selection.stale()):
                print(*problems, sep="\n", file=sys.stderr)
                return 1
        case "ubuntu-only":
            print(*sorted(selection.excluded), sep="\n")
        case "cover":
            write_cover(args.file, cover(selection, args.map, platform_functions(ROOT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
