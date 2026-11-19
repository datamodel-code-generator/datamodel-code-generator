"""The lines of the package that read each ``legacy-*`` row, and a recorder of the rows a run reads.

A row is read where a line that names it runs. `reading_lines` finds those lines in the package's source, through
`scripts/_source_model.py`:

1. a string that names the row in code that runs when a function runs, other than a docstring: the statement that
   holds it, or the header of the clause of a compound statement that holds it (a ``case`` pattern, an ``if`` test);
2. the docstring of a function, or a string in its header (a default value): the function's first statement after its
   docstring;
3. a string that names the row in the value of a module-level assignment, or in the docstring that follows one: the
   statements of code that runs when a function runs and loads a name that assignment binds, in its module or,
   through imports, in another one;
4. a module or class docstring, a class attribute and its docstring, and a module-level statement of another kind
   name no line that reads the row.

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
from typing import TYPE_CHECKING, Final

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
_ASSIGNMENTS: Final = frozenset({"assign", "annassign", "annotation-only"})
_DOCUMENTED: Final = frozenset({"module", "class", "function"})
"""The kinds of scope whose body may open with a docstring."""

Line = tuple[str, int]
"""A line of the package: the file name its code objects carry, and the line number."""


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


def _string_rows(node: ast.AST) -> set[str]:
    return {
        row
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
        for row in _rows(child.value)
    }


def _docstring(body: list[ast.stmt]) -> ast.Constant | None:
    first = body[0] if body else None
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        return first.value
    return None


class _Module:
    """The rows one module names and where: the lines that read each, and the names its assignments bind for each."""

    def __init__(self, model: SourceModule, filename: str) -> None:
        self.model = model
        self.filename = filename
        self.reads: dict[str, set[Line]] = {}
        self.bound: dict[str, set[str]] = {}
        """Row -> the module-level names whose assignment or its docstring names the row."""
        docstrings: set[ast.Constant] = set()
        for scope in model.scopes:
            if scope.kind in _DOCUMENTED and (docstring := _docstring(dict(ast.iter_fields(scope.node))["body"])):
                docstrings.add(docstring)
        self._bind_module(docstrings)
        for scope in model.scopes:
            if scope.kind == "function":
                self._read_function(scope.node, docstrings)
        for node in ast.walk(model.tree):
            if isinstance(node, ast.Constant) and node not in docstrings and isinstance(node.value, str):
                self._read_string(node)

    def read_at(self, rows: Iterable[str], line: int) -> None:
        """Record that the statement holding ``line`` reads the rows."""
        lines = {(self.filename, number) for number in self.starting_lines(line)}
        for row in rows:
            self.reads.setdefault(row, set()).update(lines)

    def starting_lines(self, line: int) -> range:
        """Return the lines that run as the statement holding ``line`` starts.

        That is all of a simple statement, and the header of the clause of a compound one that holds the line.
        """
        if (statement := self.model.statement_at(line)) is None:
            return range(0)
        clauses = self.model.clauses(statement)
        return next((clause.header for clause in clauses if line in clause.header), self.model.extent(statement))

    def _bind_module(self, docstrings: set[ast.Constant]) -> None:
        body = self.model.tree.body
        statements = {id(statement): index for index, statement in enumerate(body)}
        for binding in self.model.bindings(scope=self.model.module_scope):
            statement = self.model.statement_at(binding.line)
            if binding.kind not in _ASSIGNMENTS or statement is None or id(statement) not in statements:
                continue
            following = body[statements[id(statement)] + 1 : statements[id(statement)] + 2]
            named = _string_rows(statement)
            if (docstring := _docstring(following)) is not None:
                docstrings.add(docstring)
                named |= _rows(str(docstring.value))
            for row in named:
                self.bound.setdefault(row, set()).add(binding.name)

    def _read_function(self, node: ast.AST, docstrings: set[ast.Constant]) -> None:
        body: list[ast.stmt] = dict(ast.iter_fields(node))["body"]
        docstring = _docstring(body)
        statements = body[1:] if docstring is not None else body
        if not statements:
            return
        named = _rows(str(docstring.value)) if docstring is not None else set()
        header = self.model.header(self.model.scope_defined_by(node))
        named |= {
            row
            for child in ast.walk(node)
            if isinstance(child, ast.Constant)
            and child not in docstrings
            and isinstance(child.value, str)
            and child.lineno in header
            for row in _rows(child.value)
        }
        self.read_at(named, statements[0].lineno)

    def _read_string(self, node: ast.Constant) -> None:
        if not (rows := _rows(str(node.value))):
            return
        line = self.model.lines[node.lineno - 1]
        scope = self.model.owner(node.lineno, len(line.encode()[: node.col_offset].decode()))
        if scope.runs_in == "function" and not (scope.kind == "function" and node.lineno in self.model.header(scope)):
            self.read_at(rows, node.lineno)


@cache
def reading_lines() -> Mapping[str, frozenset[Line]]:
    """Return the lines of the package that runs that read each row it names (`package_directory`)."""
    return reading_lines_of(package_directory().parent, PACKAGE)


def reading_lines_of(root: Path, package: str) -> Mapping[str, frozenset[Line]]:
    """Return the lines of a package under ``root`` that read each row it names (none for a row named nowhere so)."""
    tree = SourceTree(root, [package], verify=False)
    texts = {name: path.read_text(encoding="utf-8") for name, path in tree.paths.items()}
    modules = {
        name: _Module(tree.module(name), str(tree.paths[name])) for name, text in texts.items() if "legacy" in text
    }
    reads: dict[str, set[Line]] = {}
    bound: dict[tuple[str, str], set[str]] = {}
    for name, module in modules.items():
        for row, lines in module.reads.items():
            reads.setdefault(row, set()).update(lines)
        for row, names in module.bound.items():
            reads.setdefault(row, set())
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
            for definition in _definitions(tree, module.model, reference):
                for row in bound.get(definition, ()):
                    reads[row] |= lines
    return MappingProxyType({row: frozenset(lines) for row, lines in reads.items()})


def _definitions(tree: SourceTree, model: SourceModule, reference: Reference) -> set[tuple[str, str]]:
    """Return the module-level names of the tree that a load leads to, through module-level and local imports."""
    found: set[tuple[str, str]] = set()
    for binding in model.resolve(reference.node).bindings:
        if binding.scope.kind == "module" and binding.kind in _ASSIGNMENTS:
            found.add((model.module, binding.name))
        elif binding.kind == "import-from" and binding.origin is not None:
            source, _, name = binding.origin.rpartition(".")
            if source in tree.paths:
                found.update((item.module, item.name) for item in tree.definition(source, name))
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
