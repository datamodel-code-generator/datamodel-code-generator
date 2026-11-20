"""The lines of the package that read each ``legacy-*`` row, and a recorder of the rows a run reads.

A row is read where a line that tests it runs. `reading_sites_of` finds where the package's source names each row,
through `scripts/_source_model.py`, and sorts the sites by kind (`SiteKind`):

1. ``test``: a string that is the row's name, in code that runs when a function runs, as an operand of a comparison
   (``==``, ``!=``, ``in``, ``not in``; also inside a set, list or tuple that is such an operand), a ``case`` pattern,
   or an argument of a call that hands the row to its callee: the statement that holds it, or the header of the clause
   of a compound statement that holds it (an ``if`` test, a ``case`` pattern);
2. ``value``: any other string of such code that names the row (the arm of a conditional expression, an assigned or
   returned value, a message);
3. ``docstring`` and ``default``: the docstring of a function, or a default value in its header: the function's first
   statement after its docstring;
4. ``constant``: a string that names the row in the value of a module-level assignment, or in the docstring that
   follows one: the statements of code that runs when a function runs and loads a name that assignment binds, in its
   module or, through imports, in another one (``from m import NAME``, a local import, and ``m.NAME`` after
   ``import m`` or ``from package import m``).

A row's reading lines are its ``test`` lines; only a row with none is read at the lines of the other kinds
(`reading_lines_of`). A string in a table or a collection of rows names no line that reads them, in a function or at
module level: a set, list, tuple or dict display (other than the operand of a comparison), and the arguments of a call
that builds or changes a collection (``frozenset(...)``, ``rows.add(...)``: `COLLECTION_TYPES`,
`COLLECTION_METHODS`). Such a line runs whether or not the row's branch does, as the line that collects the rows an
option keeps. Annotations name no line either, nor do a module or class docstring, a class attribute and its
docstring, and a module-level statement of another kind.

A row read only in ``_runtime/``, the code that generated packages copy, runs in those packages, not in the run that
writes them: the run reads it where it writes that code (`copied_rows`). `recording_reads` records the rows whose
lines run while its block runs, with ``sys.monitoring`` (Python 3.12 and later); `run_witness` runs a witness of
`scripts/legacy_rows.py` under it in a new Python, which ``tests/test_legacy_rows.py`` does once per witness. The code
lives here, not in ``tests/checks``: a test-style guard of ``tests/`` counts every ``sys.monitoring`` hook as a mock of
a normal path, and its allowlist only shrinks.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

import datamodel_code_generator
from datamodel_code_generator.__main__ import main
from scripts._source_model import SourceModule, SourceTree

if TYPE_CHECKING:
    from collections.abc import Collection, Generator, Iterable, Mapping, Sequence
    from types import CodeType

    from scripts._source_model import Reference

ROW: Final = re.compile(r"legacy[-.][A-Za-z0-9_]+(?:[-.][A-Za-z0-9_]+)*(?![-\w])")
"""A row name in the text of the package."""
PACKAGE: Final = "datamodel_code_generator"
ROOT: Final = Path(__file__).parents[1]
TOOL: Final = 4
"""The ``sys.monitoring`` tool id of the recorder: not the debugger's, coverage's, the profiler's or the optimizer's,
and not ``scripts/compare_release.py``'s."""
WITNESS_TIMEOUT: Final = 300
"""The seconds a witness may run; the slowest takes about two."""
SiteKind: TypeAlias = Literal["test", "value", "docstring", "default", "constant"]
"""The kinds of place that name a row, in the module docstring's order."""
TEST: Final = "test"
COLLECTION_TYPES: Final = frozenset({
    "builtins.dict",
    "builtins.frozenset",
    "builtins.list",
    "builtins.set",
    "builtins.tuple",
})
"""The builtin collection types, by origin."""
COLLECTION_METHODS: Final = frozenset({
    "add",
    "append",
    "difference",
    "difference_update",
    "discard",
    "extend",
    "insert",
    "intersection",
    "intersection_update",
    "remove",
    "setdefault",
    "symmetric_difference",
    "symmetric_difference_update",
    "union",
    "update",
})
"""The methods of sets, lists and dicts that add or drop members, by name."""
_ASSIGNMENTS: Final = frozenset({"assign", "annassign", "annotation-only"})
_DOCUMENTED: Final = frozenset({"module", "class", "function"})
"""The kinds of scope whose body may open with a docstring."""
_DISPLAYS: Final = (ast.Set, ast.List, ast.Tuple, ast.Dict)
_HANDING: Final = (ast.Compare, ast.MatchValue, ast.keyword, ast.Call)
"""The nodes whose string operand tests a row or hands it to a callee."""

Line = tuple[str, int]
"""A line of the package: the file name its code objects carry, and the line number."""
RowSites = dict[SiteKind, set[Line]]


@dataclass(frozen=True)
class WitnessRun:
    """What a run of a witness gives: its exit status, files and printed text, and the rows it reads."""

    output: str
    read: frozenset[str]


def package_directory() -> Path:
    """Return the directory of the package that runs: its code objects carry the file names under it."""
    return Path(datamodel_code_generator.__file__).parent


def _rows(text: str) -> set[str]:
    return set(ROW.findall(text))


def _docstring(body: list[ast.stmt]) -> ast.Constant | None:
    first = body[0] if body else None
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        return first.value
    return None


class _Module:
    """The rows one module names and where: the sites of each by kind, and the names its assignments bind for each."""

    def __init__(self, model: SourceModule, filename: str) -> None:
        self.model = model
        self.filename = filename
        self.sites: dict[str, RowSites] = {}
        self.bound: dict[str, set[str]] = {}
        """Row -> the module-level names whose assignment or its docstring names the row."""
        self.parents = {child: node for node in ast.walk(model.tree) for child in ast.iter_child_nodes(node)}
        self.unread = self._unread_strings()
        """The strings that name no line: docstrings, annotations, and the members of tables and collections."""
        self._bind_module()
        for scope in model.scopes:
            if scope.kind == "function":
                self._read_function(scope.node)
        for node in ast.walk(model.tree):
            if isinstance(node, ast.Constant) and node not in self.unread and isinstance(node.value, str):
                self._read_string(node)

    def read_at(self, rows: Iterable[str], line: int, kind: SiteKind) -> None:
        """Record that the statement holding ``line`` names the rows, as a site of ``kind``."""
        lines = {(self.filename, number) for number in self.starting_lines(line)}
        for row in rows:
            self.sites.setdefault(row, {}).setdefault(kind, set()).update(lines)

    def starting_lines(self, line: int) -> range:
        """Return the lines that run as the statement holding ``line`` starts.

        That is all of a simple statement, and the header of the clause of a compound one that holds the line.
        """
        if (statement := self.model.statement_at(line)) is None:
            return range(0)
        clauses = self.model.clauses(statement)
        return next((clause.header for clause in clauses if line in clause.header), self.model.extent(statement))

    def _unread_strings(self) -> set[ast.AST]:
        unread: set[ast.AST] = set()
        for scope in self.model.scopes:
            if scope.kind in _DOCUMENTED and (docstring := _docstring(dict(ast.iter_fields(scope.node))["body"])):
                unread.add(docstring)
        for node in ast.walk(self.model.tree):
            fields = dict(ast.iter_fields(node))
            for annotation in (fields.get("annotation"), fields.get("returns")):
                if isinstance(annotation, ast.expr):
                    unread.update(ast.walk(annotation))
            if isinstance(node, ast.Constant) and self._collected(node):
                unread.add(node)
        return unread

    def _collected(self, node: ast.Constant) -> bool:
        """Whether a string is a member of a table or collection: a display, or an argument of a collection call."""
        parent = self.parents.get(node)
        if isinstance(parent, _DISPLAYS):
            return not isinstance(self.parents.get(parent), ast.Compare)
        if not isinstance(parent, ast.Call) or node not in parent.args:
            return False
        if isinstance(parent.func, ast.Attribute):
            return parent.func.attr in COLLECTION_METHODS
        return self.model.origin(parent.func) in COLLECTION_TYPES

    def _kind(self, node: ast.Constant) -> SiteKind:
        """Return ``test`` for a string that is a row's name and tests it or hands it to a callee, else ``value``."""
        parent = self.parents.get(node)
        if isinstance(parent, _DISPLAYS):
            parent = self.parents.get(parent)
        return TEST if isinstance(parent, _HANDING) and ROW.fullmatch(str(node.value)) else "value"

    def _bind_module(self) -> None:
        body = self.model.tree.body
        statements = {id(statement): index for index, statement in enumerate(body)}
        for binding in self.model.bindings(scope=self.model.module_scope):
            statement = self.model.statement_at(binding.line)
            if binding.kind not in _ASSIGNMENTS or statement is None or id(statement) not in statements:
                continue
            following = body[statements[id(statement)] + 1 : statements[id(statement)] + 2]
            named = {
                row
                for child in ast.walk(statement)
                if isinstance(child, ast.Constant) and child not in self.unread and isinstance(child.value, str)
                for row in _rows(child.value)
            }
            if (docstring := _docstring(following)) is not None:
                self.unread.add(docstring)
                named |= _rows(str(docstring.value))
            for row in named:
                self.bound.setdefault(row, set()).add(binding.name)

    def _read_function(self, node: ast.AST) -> None:
        body: list[ast.stmt] = dict(ast.iter_fields(node))["body"]
        docstring = _docstring(body)
        statements = body[1:] if docstring is not None else body
        if not statements:
            return
        if docstring is not None:
            self.read_at(_rows(str(docstring.value)), statements[0].lineno, "docstring")
        header = self.model.header(self.model.scope_defined_by(node))
        defaults = {
            row
            for child in ast.walk(node)
            if isinstance(child, ast.Constant)
            and child not in self.unread
            and isinstance(child.value, str)
            and child.lineno in header
            for row in _rows(child.value)
        }
        self.read_at(defaults, statements[0].lineno, "default")

    def _read_string(self, node: ast.Constant) -> None:
        if not (rows := _rows(str(node.value))):
            return
        line = self.model.lines[node.lineno - 1]
        scope = self.model.owner(node.lineno, len(line.encode()[: node.col_offset].decode()))
        if scope.runs_in == "function" and not (scope.kind == "function" and node.lineno in self.model.header(scope)):
            self.read_at(rows, node.lineno, self._kind(node))


@cache
def reading_sites() -> Mapping[str, Mapping[SiteKind, frozenset[Line]]]:
    """Return the sites of each row the package that runs names (`package_directory`), by kind."""
    return reading_sites_of(package_directory().parent, PACKAGE)


@cache
def reading_lines() -> Mapping[str, frozenset[Line]]:
    """Return the lines of the package that runs that read each row it names."""
    return _reading_lines(reading_sites())


def reading_lines_of(root: Path, package: str) -> Mapping[str, frozenset[Line]]:
    """Return the lines of a package under ``root`` that read each row it names (none for a row named nowhere so)."""
    return _reading_lines(reading_sites_of(root, package))


def _reading_lines(sites: Mapping[str, Mapping[SiteKind, frozenset[Line]]]) -> Mapping[str, frozenset[Line]]:
    """Return each row's ``test`` lines, or the lines of its other sites where it has none."""
    return MappingProxyType({
        row: kinds.get(TEST) or frozenset().union(*kinds.values()) for row, kinds in sites.items()
    })


def untested_rows(sites: Mapping[str, Mapping[SiteKind, frozenset[Line]]]) -> dict[str, frozenset[SiteKind]]:
    """Return the rows with no ``test`` line, each with the kinds of the sites that read it instead."""
    return {
        row: frozenset(kind for kind, lines in kinds.items() if lines)
        for row, kinds in sites.items()
        if not kinds.get(TEST)
    }


def reading_sites_of(root: Path, package: str) -> Mapping[str, Mapping[SiteKind, frozenset[Line]]]:
    """Return the sites of each row a package under ``root`` names, by kind (none for a row named nowhere so)."""
    tree = SourceTree(root, [package], verify=False)
    texts = {name: path.read_text(encoding="utf-8") for name, path in tree.paths.items()}
    modules = {
        name: _Module(tree.module(name), str(tree.paths[name])) for name, text in texts.items() if "legacy" in text
    }
    sites: dict[str, RowSites] = {}
    bound: dict[tuple[str, str], set[str]] = {}
    for name, module in modules.items():
        for row, kinds in module.sites.items():
            for kind, lines in kinds.items():
                sites.setdefault(row, {}).setdefault(kind, set()).update(lines)
        for row, names in module.bound.items():
            sites.setdefault(row, {})
            for binding in names:
                bound.setdefault((name, binding), set()).add(row)
    spelled = {binding for _, binding in bound}
    for name, text in texts.items():
        if not any(binding in text for binding in spelled):
            continue
        module = modules.get(name) or _Module(tree.module(name), str(tree.paths[name]))
        for reference in module.model.references:
            if reference.context != "load" or reference.annotation or reference.scope.runs_in != "function":
                continue
            lines = {(module.filename, line) for line in module.starting_lines(reference.line)}
            for definition in _definitions(tree, module, reference):
                for row in bound.get(definition, ()):
                    sites[row].setdefault("constant", set()).update(lines)
    return MappingProxyType({
        row: MappingProxyType({kind: frozenset(lines) for kind, lines in kinds.items()}) for row, kinds in sites.items()
    })


def _definitions(tree: SourceTree, module: _Module, reference: Reference) -> set[tuple[str, str]]:
    """Return the module-level names of the tree that a load leads to.

    A name leads through module-level and local imports; a module an import binds leads, through the attribute read
    on it (``m.NAME``), to that module's ``NAME``.
    """
    found: set[tuple[str, str]] = set()
    model = module.model
    attribute = module.parents.get(reference.node)
    for binding in model.resolve(reference.node).bindings:
        if binding.scope.kind == "module" and binding.kind in _ASSIGNMENTS:
            found.add((model.module, binding.name))
        elif binding.kind in {"import", "import-from"} and binding.origin is not None:
            source, _, name = binding.origin.rpartition(".")
            if binding.kind == "import-from" and source in tree.paths:
                found.update((item.module, item.name) for item in tree.definition(source, name))
            if binding.origin in tree.paths and isinstance(attribute, ast.Attribute):
                found.update((item.module, item.name) for item in tree.definition(binding.origin, attribute.attr))
    return found


@cache
def copied_rows() -> frozenset[str]:
    """Return the rows whose reading lines are all in ``_runtime/``: a run reads them by writing that code."""
    runtime = package_directory() / "_runtime"
    return frozenset(
        row
        for row, lines in reading_lines().items()
        if lines and all(Path(filename).is_relative_to(runtime) for filename, _ in lines)
    )


def write_reading_lines(path: Path) -> None:
    """Write `reading_lines` to ``path`` as JSON, for the Pythons `run_witness` starts."""
    path.write_text(json.dumps({row: sorted(lines) for row, lines in reading_lines().items()}), encoding="utf-8")


def run_witness(arguments: Sequence[str], directory: Path, lines: Path) -> WitnessRun:
    """Run ``datamodel-codegen`` in a new Python, in a new directory, recording the rows it reads.

    A new Python holds no cache of an earlier run, which would skip the lines it read. A path under the repository is
    given absolute, ``--output`` names ``out`` in the directory and ``--disable-timestamp`` is added, so that two runs
    print and write the same where they read the same. ``lines`` is the file `write_reading_lines` wrote.
    """
    directory.mkdir()
    rows = directory.with_suffix(".rows")
    command = [str(ROOT / token) if "/" in token and (ROOT / token).exists() else token for token in arguments]
    command += [] if "--disable-timestamp" in command else ["--disable-timestamp"]
    result = subprocess.run(
        [sys.executable, "-m", "scripts.legacy_row_reads", str(lines), str(rows), *command, "--output", "out"],
        capture_output=True,
        check=False,
        cwd=directory,
        timeout=WITNESS_TIMEOUT,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, (str(ROOT), os.environ.get("PYTHONPATH"))))},
        text=True,
    )
    written = sorted(path for path in directory.rglob("*") if path.is_file())
    output = "\n".join((
        f"exit status {result.returncode}",
        *(f"## {path.relative_to(directory)}\n{path.read_text(encoding='utf-8')}" for path in written),
        result.stdout,
        result.stderr,
    ))
    read = set(rows.read_text(encoding="utf-8").split()) if rows.exists() else set()
    return WitnessRun(output, frozenset(read | {row for row in copied_rows() if row in output}))


@contextmanager
def recording_reads(rows_at: Mapping[Line, Collection[str]]) -> Generator[set[str], None, None]:
    """Record the rows whose lines (``rows_at``) run while the block runs, into the set it yields (Python 3.12+)."""
    assert sys.version_info >= (3, 12), "sys.monitoring records the lines that run from Python 3.12"
    monitoring = sys.monitoring
    read: set[str] = set()

    def line_runs(code: CodeType, line: int) -> object:
        if (rows := rows_at.get((code.co_filename, line))) is None:
            return monitoring.DISABLE
        read.update(rows)
        return None

    monitoring.use_tool_id(TOOL, "legacy rows")
    monitoring.register_callback(TOOL, monitoring.events.LINE, line_runs)
    monitoring.set_events(TOOL, monitoring.events.LINE)
    try:
        yield read
    finally:
        monitoring.set_events(TOOL, monitoring.events.NO_EVENTS)
        monitoring.register_callback(TOOL, monitoring.events.LINE, None)
        monitoring.free_tool_id(TOOL)


def _record(lines: Path, rows: Path, arguments: list[str]) -> int:
    """Run ``datamodel-codegen`` with ``arguments``, write the rows it reads to ``rows``, and return its exit status."""
    rows_at: dict[Line, set[str]] = {}
    for row, numbered in json.loads(lines.read_text(encoding="utf-8")).items():
        for filename, number in numbered:
            rows_at.setdefault((filename, number), set()).add(row)
    with recording_reads(rows_at) as read:
        status = main(arguments)
    rows.write_text("\n".join(sorted(read)), encoding="utf-8")
    return int(status)


if __name__ == "__main__":
    sys.exit(_record(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]))
