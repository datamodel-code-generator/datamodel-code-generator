"""List the lines excluded from coverage (`pragma: no cover` and the other configured forms) that the suite ran."""

from __future__ import annotations

import ast
import re
import sys
from bisect import bisect_left, bisect_right
from pathlib import Path
from typing import TYPE_CHECKING

from coverage import Coverage
from coverage.exceptions import CoverageException
from coverage.python import PythonFileReporter

if TYPE_CHECKING:
    from collections.abc import Iterator

    from coverage.python import PythonParser

KEYWORD_HEADER = re.compile(r"\s*(?:else|finally)\s*:")
"""The header of an `else` or `finally` clause, the clauses without a node of their own."""


def _area(path: str) -> str:
    """Name the package directory below `src/datamodel_code_generator`, or the first directory elsewhere."""
    directories = Path(path).parts[:-1]
    package = directories[2:] if directories[:2] == ("src", "datamodel_code_generator") else directories
    return package[0] if package else "(top level)"


def _span(suite: list[ast.stmt]) -> tuple[int, int]:
    return suite[0].lineno, suite[-1].end_lineno or suite[-1].lineno


def _clauses(tree: ast.Module, lines: list[str]) -> Iterator[tuple[int, int, int, int]]:
    """Yield `(first line, header line, suite start, suite end)` of each clause, a header and the suite it owns.

    A definition starts at its first decorator; an `else` or `finally` header is the line of its keyword, the only line
    between the preceding suite and its own that matches `KEYWORD_HEADER`.
    """
    for node in ast.walk(tree):
        match node:
            case ast.FunctionDef() | ast.AsyncFunctionDef() | ast.ClassDef():
                first = min((decorator.lineno for decorator in node.decorator_list), default=node.lineno)
                yield first, node.lineno, *_span(node.body)
            case ast.Match():
                yield node.lineno, node.lineno, node.cases[0].pattern.lineno, _span(node.cases[-1].body)[1]
            case ast.match_case():
                yield node.pattern.lineno, node.pattern.lineno, *_span(node.body)
            case ast.ExceptHandler() | ast.With() | ast.AsyncWith():
                yield node.lineno, node.lineno, *_span(node.body)
            case ast.If() | ast.For() | ast.AsyncFor() | ast.While() | ast.Try() | ast.TryStar():
                yield node.lineno, node.lineno, *_span(node.body)
                before = [*node.body, *getattr(node, "handlers", ())]
                for suite in (node.orelse, getattr(node, "finalbody", [])):
                    window = range(_span(before)[1] + 1, suite[0].lineno + 1) if suite else range(0)
                    if header := next((line for line in window if KEYWORD_HEADER.match(lines[line - 1])), None):
                        yield header, header, *_span(suite)
                    before += suite


def _executed_regions(parser: PythonParser, executed: set[int]) -> Iterator[tuple[int, int]]:
    """Yield `(marked line, body statements that ran)` of each excluded region whose body ran.

    A region is what coverage excludes for one mark: the marked statement and, for a clause or definition header, its
    suite, a nested region staying inside the suite that owns it. Its body is its statements less the header ones
    (decorators, the `def` or `class` line, a clause line such as `if` or `except`), which run without entering the
    suite: a marked simple statement is its own body, a one-line clause has none apart from its header.
    """
    statements = sorted(parser.first_lines(parser.raw_statements - parser.raw_docstrings) - parser.raw_docstrings)

    def between(start: int, end: int) -> set[int]:
        return set(statements[bisect_left(statements, start) : bisect_right(statements, end)])

    ends: dict[int, int] = {}
    for line, first in parser.multiline_map.items():
        ends[first] = max(ends.get(first, first), line)
    headers: dict[int, tuple[int, int, int, int]] = {}
    for first, header, start, end in _clauses(ast.parse(parser.text), parser.text.splitlines()):
        last = ends.get(header, header)
        headers.update(dict.fromkeys(range(first, last + 1), (first, last, start, end)))
    regions: dict[int, tuple[int, set[int]]] = {}
    for mark in sorted(parser.raw_excluded):
        if clause := headers.get(mark):
            first, last, start, end = clause
            regions.setdefault(first, (mark, between(start, end) - between(first, last)))
        else:
            statement = parser.first_line(mark)
            regions.setdefault(statement, (mark, between(statement, statement)))
    for mark, body in regions.values():
        if ran := body & executed:
            yield mark, len(ran)


def executed_exclusions(data_file: Path) -> list[tuple[str, int, int, str]]:
    """List `(file, line, executed statements, source)` of each excluded region that ran, by file and line.

    Coverage's own reporter facts decide: the configured patterns with their plugins (so `pragma: no cover` and the
    other exclusion forms count alike), the measured lines translated to statement starts, and the file name relative
    to the directory coverage reports from, whether the data stores it absolute or relative.
    """
    session = Coverage(data_file=str(data_file))
    session.load()
    data = session.get_data()
    found = []
    for file in data.measured_files():
        reporter = PythonFileReporter(file, session)
        try:
            parser = reporter.parser
        except CoverageException:
            continue
        name = Path(reporter.relative_filename()).as_posix()
        lines = parser.text.splitlines()
        found.extend(
            (name, mark, ran, lines[mark - 1].strip())
            for mark, ran in _executed_regions(parser, reporter.translate_lines(data.lines(file) or ()))
        )
    return sorted(found)


def main(argv: list[str] | None = None) -> None:
    """Print the executed exclusions of the coverage data file given as the only argument (report only)."""
    (data_file,) = sys.argv[1:] if argv is None else argv
    found = executed_exclusions(Path(data_file))
    print(f"Executed coverage exclusions: {len(found)}")
    for file, line, ran, source in found:
        print(f"{file}:{line} [{_area(file)}] {ran} line(s) ran: {source}")


if __name__ == "__main__":
    main()
