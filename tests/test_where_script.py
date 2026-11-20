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
    "unknown-long-flag": [f"--{'x' * 200}"],
    "unknown-legacy-row": ["legacy-no-such-row"],
    "ambiguous-name": ["plans"],
    "kind-fact": ["--kind", "fact", "plans"],
    "kind-symbol": ["--kind", "symbol", "plans"],
    "all": ["--all", "Fact.BASES"],
    "negated-flag": ["--no-use-annotated"],
    "negated-flag-of-no-toggle": ["--no-output"],
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
    "decision-in-registry-order": ["omit_untaken_union_constraints"],
    "fact-without-attribute-rows": ["Fact.MODEL_KIND"],
    "undocumented-option": ["--treat-dot-as-module"],
    "users-through-a-package": ["ConstraintsBase"],
    "users-through-a-reexport": ["--kind", "symbol", "DEFAULT_FORMATTERS"],
    "definition-in-a-conditional-block": ["--kind", "symbol", "_ParserResults"],
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
    "page-with-a-fenced-notes-example": (
        ["#7"],
        (
            f"{TABLES}\n\n```\n### Case Notes\n\n- **#7** the example\n```\n\n### Case Notes\n\n"
            "```\n# a comment\n- **#8** in a fence\n```\n\n- **#7** the note\n"
        ),
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


def _where(capsys: pytest.CaptureFixture[str], args: list[str], page: str | None = None, root: Path = ROOT) -> str:
    """Run the script and return its exit status with what it printed."""
    code = where.main([_resolved(arg) if root == ROOT else arg for arg in args], page=page, root=root)
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


def test_where_matches_a_note_by_the_file_it_cites(capsys: pytest.CaptureFixture[str]) -> None:
    """A case note that cites ``file.py:name`` names that file's definition, not one of the same name elsewhere."""
    page = f"{TABLES}\n\n### Case Notes\n\n- **#9** `_format_types.py:DEFAULT_FORMATTERS`\n"
    answers = [_where(capsys, [f"{path}:DEFAULT_FORMATTERS"], page) for path in ("_format_types.py", "format.py")]
    assert_output(_unmasked("".join(answers)))


def test_where_refuses_a_path_outside_the_repository(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A file outside the repository is never read."""
    outside = tmp_path / "outside.py"
    outside.write_text("value = 1\n", encoding="utf-8")
    assert_output(_where(capsys, [str(outside)]).replace(str(outside), "<path>"))


def test_where_reports_a_source_it_cannot_read(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A repository without the architecture page exits 2 and names the page."""
    code = where.main(["Fact.BASES"], root=tmp_path)
    assert_output(f"exit status {code}\n{capsys.readouterr().err}")


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
    every_line = _where(capsys, ["--all", "use_annotated"]).splitlines()
    every_width = _where(capsys, ["--all", "apply_generic_base_class"]).splitlines()
    report += [f"all: {len(every_line) - 1 > LINE_LIMIT}", f"all width: {max(map(len, every_width)) > LINE_WIDTH}"]
    assert_output("\n".join(report))


def _scratch(tmp_path: Path, files: dict[str, str]) -> Path:
    """Write a repository of scripts, with the related-tests rules the answers read, and return its root."""
    (tmp_path / "scripts").mkdir()
    for name, text in {"__init__.py": "", "related_tests.toml": "rule = []\n", **files}.items():
        (tmp_path / "scripts" / name).write_text(text, encoding="utf-8")
    return tmp_path


SCRATCH = {
    "provider.py": '"""Provider."""\n\n\ndef answer():\n    return 1\n',
    "consumer.py": (
        '"""Consumer."""\n\nimport scripts.provider as mod\n\n\n'
        "def inspect(mod):\n    return mod.answer\n\n\ndef use():\n    return mod.answer\n"
    ),
    "aliased.py": (
        '"""Aliased."""\n\nfrom scripts.provider import answer\n\nlocal = answer\n\n\n'
        "def use():\n    return local()\n\n\ndef other(local):\n    return local()\n"
    ),
    "busy.py": (
        '"""Busy."""\n\nfrom scripts.provider import answer\n\n\n'
        + "".join(f"def user_{name}():\n    return answer()\n\n\n" for name in "abcd")
    ),
}


def test_where_follows_the_binding_each_use_reads(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A use counts when its name leads to the definition: through an alias of the file, not through a parameter."""
    root = _scratch(tmp_path, SCRATCH)
    assert_output(_where(capsys, ["scripts/provider.py:answer"], f"{TABLES}\n\n### Case Notes\n", root))


SHAPES = {
    "provider.py": SCRATCH["provider.py"],
    "other.py": SCRATCH["provider.py"].replace("Provider", "Other"),
    "shapes.py": (
        '"""Shapes."""\n\nfrom scripts.provider import answer as mod\n\n\n'
        "def one():\n    import scripts.provider as mod\n\n    return mod.answer()\n\n\n"
        "def two():\n    import scripts.other as mod\n\n    return mod.answer()\n\n\n"
        "def spaced():\n    import scripts.provider as lib\n\n    return lib .answer()\n\n\n"
        "def parenthesised():\n    import scripts.provider as lib\n\n    return (lib).answer()\n\n\n"
        "def unrelated(lib):\n    return lib.answer()\n"
    ),
}


def test_where_decides_each_use_from_the_model(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """One name bound to two modules in two scopes and to a direct binding, and dots with spaces or parentheses."""
    root = _scratch(tmp_path, SHAPES)
    assert_output(_where(capsys, ["scripts/provider.py:answer"], f"{TABLES}\n\n### Case Notes\n", root))


def test_where_all_lists_every_user_scope(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Four scopes of one file are three names and a count by default, and all four with ``--all``."""
    root = _scratch(tmp_path, SCRATCH)
    answers = [
        _where(capsys, [*flags, "scripts/provider.py:answer"], f"{TABLES}\n\n### Case Notes\n", root)
        for flags in ([], ["--all"])
    ]
    assert_output("".join(answers))


def test_where_runs_only_the_lookups_of_the_kind(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A source no selected lookup reads is never parsed: a half-written file does not fail ``--kind``."""
    root = _scratch(tmp_path, {"broken.py": "MODEL_KIND = other = (\n", "other.py": "def other():\n    return 1\n"})
    page = f"{TABLES}\n\n### Case Notes\n"
    answers = [
        _where(capsys, ["--kind", "fact", "Fact.MODEL_KIND"], page, root),
        _where(capsys, ["--kind", "line", "scripts/other.py:2"], page, root),
    ]
    assert_output("".join(answers))


def test_where_counts_an_empty_section_against_the_line_limit() -> None:
    """Blocks that fill their shares by their listed sections show no empty-section message beyond the limit."""
    sites = where.Section("defined", [f"site {number}" for number in range(9)], cap=9)
    block = where.Block(where.Kind.SYMBOL, "symbol name", [sites, where.Section("rows", [], empty="none name it")])
    assert_output(f"{len(where.render([block] * 6, everything=False))} lines of {where.LIMIT}")


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
