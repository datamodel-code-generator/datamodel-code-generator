"""Answer the queries of `python -m scripts.where` from the repository's own tables, as stored outputs."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from datamodel_code_generator.cli_options import OPTIONS
from scripts import where
from scripts.fact_tables import Fact, RenderFact
from scripts.pass_decls import DECISION_DECLS, PASS_DECLS
from tests.conftest import assert_output

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "where.py"
LINE_NUMBER = re.compile(r"(\.py):\d+|line \d+")
LINE_LIMIT = 60
LINE_WIDTH = 160
STRING_LIMIT = 3
STORED = {
    "option": "--use-annotated",
    "pass": "apply_generic_base_class",
    "fact": "Fact.BASES",
    "legacy-row": "legacy-document-order",
    "claimed-by-two-rows": "render_type_expr",
}
GRAMMAR = {
    "unknown-name": ["apply_generic_base_clas"],
    "unknown-with-kind": ["--kind", "fact", "apply_generic_base_class"],
    "unknown-flag": ["--no-such-option"],
    "unknown-legacy-row": ["legacy-no-such-row"],
    "ambiguous-name": ["plans"],
    "kind-fact": ["--kind", "fact", "plans"],
    "kind-symbol": ["--kind", "symbol", "plans"],
    "all": ["--all", "Fact.BASES"],
    "negated-flag": ["--no-use-annotated"],
    "config-field": ["--kind", "option", "use_annotated"],
    "path": ["model/_decisions/tag_value.py"],
    "path-from-src": ["src/datamodel_code_generator/_mro.py"],
    "line-in-a-method": ["src/datamodel_code_generator/types.py::    def type_hint"],
    "line-in-module-scope": ["src/datamodel_code_generator/_mro.py:1"],
    "line-in-a-script": ["scripts/related_tests.py::def _matches"],
    "symbol-of-a-file": ["types.py:DataType.type_hint"],
    "method-with-users": ["DataType.type_hint"],
    "issue": ["#4855"],
    "case-note": ["#4834"],
    "plan-id": ["DC12"],
    "legacy-row-without-an-option": ["legacy-exact-ints-as-floats"],
    "decision-without-a-definition": ["omit_held_null"],
    "undocumented-option": ["--treat-dot-as-module"],
    "users-through-a-package": ["ConstraintsBase"],
    "path-with-no-rows": ["scripts/where.py"],
    "line-in-a-comprehension": ['scripts/related_tests.py::_git(root, "show", f"{left}:{name}") for name'],
}
TABLES = "\n".join(
    f"{header}\n| --- | --- | --- |" for header in (where.DECISION_HEADER, where.LEGACY_HEADER, where.COUPLING_HEADER)
)
OTHER = {
    "page-without-tables": (["apply_generic_base_class"], "# Architecture\n"),
    "page-without-case-notes": (["apply_generic_base_class"], f"{TABLES}\n"),
    "page-with-case-notes-last": (
        ["#7"],
        f"{TABLES}\n\n### Case Notes\n\n- a note without a number\n- **[#7](url)** (seven): the note\n  goes on\n",
    ),
    "line-past-the-file": (["scripts/related_tests.py:99999"], None),
    "line-in-a-test": (["tests/test_where_script.py:1"], None),
}


def _resolved(arg: str) -> str:
    """Turn ``path::text`` into ``path:line`` of the first line that holds the text."""
    path, separator, text = arg.partition("::")
    if not separator:
        return arg
    lines = (ROOT / path).read_text(encoding="utf-8").splitlines()
    return f"{path}:{next(number for number, line in enumerate(lines, 1) if text in line)}"


def _where(capsys: pytest.CaptureFixture[str], args: list[str], page: str | None = None) -> str:
    """Run the script and return its exit status with what it printed."""
    code = where.main([_resolved(arg) for arg in args], page=page)
    captured = capsys.readouterr()
    return f"exit status {code}\n{captured.out}{captured.err}"


def _unmasked(text: str) -> str:
    """Mask the line numbers of code, which move with every edit above them."""
    return LINE_NUMBER.sub(lambda found: f"{found[1]}:<line>" if found[1] else "line <line>", text)


@pytest.mark.parametrize("query", STORED.values(), ids=STORED)
def test_where_answers(query: str, capsys: pytest.CaptureFixture[str]) -> None:
    """Each source family answers from today's tables; the answer changes where a row, relation or writer changes."""
    assert_output(_unmasked(_where(capsys, [query])))


@pytest.mark.parametrize("args", GRAMMAR.values(), ids=GRAMMAR)
def test_where_grammar(args: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    """A query is read by its form first, then by the tables that know it."""
    assert_output(_unmasked(_where(capsys, args)))


@pytest.mark.parametrize(("args", "page"), OTHER.values(), ids=OTHER)
def test_where_reads_other_inputs(args: list[str], page: str | None, capsys: pytest.CaptureFixture[str]) -> None:
    """A page without a table or the notes, a line past the end and a file that is no module exit 2 with the cause."""
    assert_output(_where(capsys, args, page))


def test_where_refuses_a_path_outside_the_repository(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A file outside the repository is never read."""
    outside = tmp_path / "outside.py"
    outside.write_text("value = 1\n", encoding="utf-8")
    assert_output(_where(capsys, [str(outside)]).replace(str(outside), "<path>"))


def test_where_needs_one_query(capsys: pytest.CaptureFixture[str]) -> None:
    """Two queries, or none, are a usage error."""
    with pytest.raises(SystemExit) as raised:
        where.main([])
    assert_output(f"exit status {raised.value.code}\n{capsys.readouterr().err.splitlines()[-1]}")


def test_where_keeps_to_its_line_limit(capsys: pytest.CaptureFixture[str]) -> None:
    """Answers that share the limit stay within 60 lines of at most 160 characters; ``--all`` lifts both."""
    report = []
    for query in ("plans", "use_annotated", "required"):
        lines = _where(capsys, [query]).splitlines()
        report.append(f"{query}: {len(lines) - 1 > LINE_LIMIT} {max(map(len, lines)) > LINE_WIDTH}")
    report.append(f"all: {len(_where(capsys, ['--all', 'use_annotated']).splitlines()) > LINE_LIMIT}")
    assert_output("\n".join(report))


def test_where_line_answers_as_its_symbol(capsys: pytest.CaptureFixture[str]) -> None:
    """The symbol block of a line is that of the definition that holds it."""
    lines = (ROOT / "src/datamodel_code_generator/types.py").read_text("utf-8").splitlines()
    line = next(number for number, text in enumerate(lines, 1) if text.startswith("def render_type_expr("))

    def block(query: str) -> str:
        text = _where(capsys, [query])
        return text[text.index("symbol render_type_expr") :]

    by_line, by_name = block(f"types.py:{line}"), block("render_type_expr")
    assert_output("" if by_line == by_name else f"{by_line}\n--- is not ---\n{by_name}")


def _vocabulary() -> set[str]:
    """Return the table entries a script must not copy: flags, config fields, passes, facts, row ids, files."""
    page = where.read_page((ROOT / where.PAGE).read_text(encoding="utf-8"))
    entries = {name for option in OPTIONS for name in (*option.flags, option.dest)}
    entries |= {declaration.name for declaration in [*PASS_DECLS, *DECISION_DECLS.values()]}
    entries |= {value for member in [*Fact, *RenderFact] for value in (member.name, member.value)}
    entries |= {row.cells[0].strip("`") for row in page.legacy}
    entries |= {cite.file for row in page.decisions for cite in where.cites(row.cells[1]) if cite.file}
    return {entry for entry in entries if re.search(r"[_\-A-Z/.]", entry)}


def test_where_spells_no_table_entry() -> None:
    """The script holds nothing that could go stale: no entry of a table it reads, no list that copies one."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    vocabulary = sorted(_vocabulary())
    statements = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Expr)}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in statements:
            found += [
                f"{node.lineno}: {node.value!r} holds {entry}"
                for entry in vocabulary
                if re.search(rf"(?<![\w-]){re.escape(entry)}(?![\w-])", node.value)
            ]
        if isinstance(node, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
            items = node.values if isinstance(node, ast.Dict) else node.elts
            strings = [item for item in [*items, *getattr(node, "keys", [])] if isinstance(item, ast.Constant)]
            found += [f"{node.lineno}: a display holds {len(strings)} string constants"] * (len(strings) > STRING_LIMIT)
    assert_output("\n".join(found))
