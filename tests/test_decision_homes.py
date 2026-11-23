"""Tests for the decision homes of scripts/decision_homes.py: homes and sites are definitions their rows name.

heki's contracts (`scripts/build_heki_contracts.py`) keep the reads of a row's homes inside its home modules and
adapter sites. The passes a placement row puts its decision after are declared and write a fact the row reads.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from scripts._decision_home_reads import builder_problems, default_repo, missing
from scripts.build_architecture_docs import (
    DECISION_BEGIN_MARKER,
    DECISION_END_MARKER,
    PLACEMENT_BEGIN_MARKER,
    PLACEMENT_END_MARKER,
    generate_decision_table,
    generate_placement_table,
)
from scripts.decision_homes import (
    BUILDER_HOMES,
    DECISION_HOMES,
    PLACEMENTS,
    BuilderHome,
    DecisionHome,
    Home,
    Mark,
    Placement,
)
from scripts.fact_tables import Fact, RenderFact
from scripts.pass_decls import DECISION_DECLS, PASS_DECLS
from scripts.where import SPAN, Page, Repo

if TYPE_CHECKING:
    from collections.abc import Iterable


ROOT = Path(__file__).parents[1]
PACKAGE = "datamodel_code_generator"


def _named(home: Home, cell: str) -> bool:
    """Return whether a cell names a home: its file in a code span, and the last part of its name in one."""
    spans = SPAN.findall(cell)
    files = {file for span in spans if (file := span.partition(":")[0]).endswith(".py")}
    words = {word for span in spans for word in re.findall(r"[A-Za-z_]\w*", span)}
    cited = any(home.path == file or home.path.endswith(f"/{file}") for file in files)
    return cited and (not home.name or home.name.rpartition(".")[2] in words)


def _problems(records: Iterable[DecisionHome]) -> list[str]:
    """Return what is wrong with records: a home or site src/ lacks, one its cell does not name, or no home at all."""
    problems: list[str] = []
    for record in records:
        key = f"{record.decision[:60]}..."
        if not record.homes:
            problems.append(f"{key}: no home")
        for kind, cell, homes in (("core", record.core, record.homes), ("adapters", record.adapters, record.sites)):
            for home in homes:
                if (gap := missing(home)) is not None:
                    problems.append(f"{key}: {gap}")
                elif not _named(home, cell):
                    problems.append(f"{key}: the {kind} cell does not name {':'.join(filter(None, home))}")
                elif kind == "adapters" and not home.name and (named := _named_after(home, cell)):
                    problems.append(f"{key}: the adapters cell names `{named}` after {home.path}, no whole-module site")
    return problems


def _named_after(home: Home, cell: str) -> str:
    """Return the name a cell gives right after the file of a whole-module site (`` `f.py`: `x` ``, `` `f.py` (`x` ``).

    A cell states a whole module by citing its file with no name after it; a name it gives there that no scope
    carries (a constant, a parameter, an alias) must not widen the site to the module.
    """
    for match in re.finditer(r"`([^`:]+\.py)`(?::| \()\s*`([^`]+)`", cell):
        if home.path == match[1] or home.path.endswith(f"/{match[1]}"):
            return match[2]
    return ""


def placement_problems(records: Iterable[Placement]) -> list[str]:
    """Return what is wrong with placement records: a pass that is not declared or writes none of the row's facts.

    A pass of ``after`` is a pass or a decision of ``scripts/pass_decls.py`` that writes a fact the row reads, as
    `scripts/check_pass_pipeline.py` asks of an ``After`` edge, and the place cell names it in a code span; a pass the
    place cell names is in ``after``. Every fact is a member of ``Fact`` or ``RenderFact``, and no two records read
    the same.
    """
    declared = {decl.name: decl for decl in (*PASS_DECLS, *DECISION_DECLS.values())}
    problems: list[str] = []
    for record in records:
        key = f"{record.reads[:60]}..."
        spans = SPAN.findall(record.place)
        problems.extend(
            f"{key}: {fact!r} is no member of Fact or RenderFact"
            for fact in record.facts
            if not isinstance(fact, (Fact, RenderFact))
        )
        for name in record.after:
            if (decl := declared.get(name)) is None:
                problems.append(f"{key}: {name} is no pass of scripts/pass_decls.py")
            elif not decl.writes & record.facts:
                problems.append(f"{key}: after {name}, which writes none of its facts")
            if name not in spans:
                problems.append(f"{key}: the place cell does not name {name}")
        problems.extend(
            f"{key}: the place cell names {name}, which after leaves out"
            for name in spans
            if name in declared and name not in record.after
        )
    repeated = [reads for reads, count in Counter(record.reads for record in records).items() if count > 1]
    problems.extend(f"{reads[:60]}...: recorded more than once" for reads in repeated)
    return problems


@pytest.mark.allow_direct_assert("an invariant of the records must fail the test, not be stored as an expected output")
def test_decision_homes_are_defined_in_src() -> None:
    """Every home of a record is a definition of src/ that the record's core cell names."""
    problems = _problems(DECISION_HOMES)

    assert not problems, "\n".join(problems)


@pytest.mark.allow_direct_assert("an invariant of the records must fail the test, not be stored as an expected output")
def test_decisions_are_recorded_once() -> None:
    """No two records hold the same decision."""
    repeated = [decision for decision, count in Counter(r.decision for r in DECISION_HOMES).items() if count > 1]

    assert not repeated, "\n".join(repeated)


@pytest.mark.allow_direct_assert("an invariant of the records must fail the test, not be stored as an expected output")
def test_the_page_table_is_written_from_the_records() -> None:
    """The decision table of docs/architecture.md is the table the records write, row for row."""
    docs = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    written = f"{DECISION_BEGIN_MARKER}{generate_decision_table()}{DECISION_END_MARKER}"

    assert written in docs, "docs/architecture.md: run python scripts/build_architecture_docs.py"


@pytest.mark.allow_direct_assert("an invariant of the records must fail the test, not be stored as an expected output")
def test_placements_name_declared_passes_that_write_their_facts() -> None:
    """Every pass a placement row puts its decision after is declared, named by its place and writes a fact it reads."""
    problems = placement_problems(PLACEMENTS)

    assert not problems, "\n".join(problems)


@pytest.mark.allow_direct_assert("an invariant of the records must fail the test, not be stored as an expected output")
def test_the_placement_table_is_written_from_the_records() -> None:
    """The placement table of docs/architecture.md is the table the records write, row for row."""
    docs = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    written = f"{PLACEMENT_BEGIN_MARKER}{generate_placement_table()}{PLACEMENT_END_MARKER}"

    assert written in docs, "docs/architecture.md: run python scripts/build_architecture_docs.py"


@pytest.mark.allow_direct_assert("an invariant of the records must fail the test, not be stored as an expected output")
def test_builder_homes_are_stated_and_defined() -> None:
    """Every builder record's passage is on the page and names its builder; src/ defines what it names."""
    page = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    problems = builder_problems(default_repo(), BUILDER_HOMES, page)

    assert not problems, "\n".join(problems)


SEEDED = (
    DecisionHome(
        "seeded: homes src/ lacks",
        homes=(
            Home("types.py", "no_such_decision"),
            Home("no_such_module.py", "decide"),
            Home("types.py", "DataType.no_such_member"),
            Home("parser/_intersection.py", "json_values_equal"),
        ),
        core="`types.py`: `no_such_decision`, `DataType.no_such_member`; `no_such_module.py:decide`; "
        "`parser/_intersection.py:json_values_equal`",
        adapters="`types.py:no_such_site`",
        sites=(Home("types.py", "no_such_site"),),
    ),
    DecisionHome(
        "seeded: a home the core cell does not name",
        homes=(Home("types.py", "DataType.admits_none"), Home("_json_schema.py")),
        core="`types.py:DataType`",
        adapters="`types.py:DataType`",
        sites=(Home("types.py", "DataType.admits_none"),),
    ),
    DecisionHome(
        "seeded: a whole-module site the cell qualifies",
        homes=(Home("types.py", "DataType"),),
        core="`types.py:DataType`",
        adapters="`parser/avro.py` (`NULL_SCHEMA`), `_json_schema.py`",
        sites=(Home("parser/avro.py"), Home("_json_schema.py")),
    ),
    DecisionHome("seeded: no home", homes=(), core="", adapters="", sites=()),
)
"""Records whose homes or sites src/ does not define, or that their cells do not name or qualify."""


@pytest.mark.allow_direct_assert("the problems of seeded records are the check's verdict, not a stored output")
def test_decision_home_check_fails_on_seeded_records() -> None:
    """A missing function, module or class member, an import, an unnamed home or site and no home are problems."""
    assert _problems(SEEDED) == [
        "seeded: homes src/ lacks...: types.py defines no no_such_decision",
        f"seeded: homes src/ lacks...: src/{PACKAGE}/no_such_module.py does not exist",
        "seeded: homes src/ lacks...: types.py defines no DataType.no_such_member",
        "seeded: homes src/ lacks...: parser/_intersection.py defines no json_values_equal",
        "seeded: homes src/ lacks...: types.py defines no no_such_site",
        "seeded: a home the core cell does not name...: the core cell does not name types.py:DataType.admits_none",
        "seeded: a home the core cell does not name...: the core cell does not name _json_schema.py",
        "seeded: a home the core cell does not name...: the adapters cell does not name types.py:DataType.admits_none",
        (
            "seeded: a whole-module site the cell qualifies...: "
            "the adapters cell names `NULL_SCHEMA` after parser/avro.py, no whole-module site"
        ),
        "seeded: no home...: no home",
    ]


SEEDED_BUILDER_PACKAGE = {
    "parser/__init__.py": "",
    "parser/_schema_dispatch.py": (
        "class Row:\n    pass\n\n\nclass Default:\n    pass\n\n\nclass DispatchTable:\n    pass\n"
    ),
    "parser/kinds.py": (
        "from datamodel_code_generator.parser._schema_dispatch import Default, DispatchTable, Row\n\n\n"
        "class Parser:\n"
        "    def _build_enum(self, site):\n        return site\n\n"
        "    _ROWS = (Row('enum', None, _build_enum),)\n"
        "    _TABLE = DispatchTable(_ROWS, Default('default', None))\n"
        "    _OTHER = DispatchTable((), Default('other', None))\n\n"
        "    def parse_site(self, site):\n        return self._TABLE.choose(self, site)\n"
    ),
    "parser/maps.py": (
        "def build_map(parser):\n    return part(parser)\n\n\n"
        "def part(parser):\n    return parser.data_type(is_dict=True)\n\n\n"
        "def unreached(parser):\n    return parser\n"
    ),
    "model.py": "class Ref:\n    pass\n\n\ndef name_model():\n    return Ref()\n",
}
"""A package with a dispatch table in ``parser/kinds.py`` and an object-map builder in ``parser/maps.py``.

``_OTHER`` is a table no record holds, ``_ROWS`` holds rows but is no table, and ``build_map`` does not reach
``unreached``.
"""

SEEDED_BUILDERS = (
    BuilderHome(
        output="seeded: a parse of the table",
        prose="the table that `parse_site` chooses from",
        builder=Home("parser/kinds.py", "Parser.parse_site"),
        table=Home("parser/kinds.py", "Parser._TABLE"),
    ),
    BuilderHome(
        output="seeded: a map",
        prose="one builder (`build_map`)",
        builder=Home("parser/maps.py", "build_map"),
        marks=(Mark(f"{PACKAGE}.types.DataType", "is_dict", value=True),),
        parts=(Home("parser/maps.py", "part"),),
        within="parser/",
    ),
    BuilderHome(
        output="seeded: a named model",
        prose="Names come from `name_model`.",
        builder=Home("model.py", "name_model"),
        marks=(Mark(f"{PACKAGE}.model.Ref"),),
    ),
)
"""Records of the seeded package that its page states."""

SEEDED_BUILDER_PAGE = (
    "Each site takes the table that\n`parse_site` chooses from. A map has one builder (`build_map`). Names come from "
    "`name_model`."
)


def _seeded_builder_repo(root: Path) -> Repo:
    """Write the seeded builder package under a root and return the repository read there."""
    package = root / "src" / PACKAGE
    (root / "scripts").mkdir()
    for name, text in SEEDED_BUILDER_PACKAGE.items():
        (package / name).parent.mkdir(parents=True, exist_ok=True)
        (package / name).write_text(text, encoding="utf-8")
    return Repo(root, Page([], [], [], []))


@pytest.mark.allow_direct_assert("the problems of seeded records are the check's verdict, not a stored output")
def test_builder_problems_on_seeded_records(tmp_path: Path) -> None:
    """A passage the page lacks or that names another builder, a missing def, no table, no reach are problems."""
    records = (
        *SEEDED_BUILDERS,
        BuilderHome(
            output="seeded: a passage the page lacks",
            prose="the builder (`build_map`) of nothing",
            builder=Home("parser/maps.py", "build_map"),
            marks=(Mark("build"),),
        ),
        BuilderHome(
            output="seeded: a passage that names another builder",
            prose="one builder (`build_map`)",
            builder=Home("parser/maps.py", "part"),
            table=Home("parser/kinds.py", "Parser._ROWS"),
            marks=(Mark("build"),),
            parts=(Home("parser/maps.py", "unreached"), Home("parser/maps.py", "no_such_part")),
        ),
        BuilderHome(
            output="seeded: tables src/ lacks",
            prose="one builder (`build_map`)",
            builder=Home("parser/maps.py", "build_map"),
            table=Home("parser/no_such_module.py", "Parser._TABLE"),
        ),
        BuilderHome(
            output="seeded: a table of a class src/ lacks",
            prose="one builder (`build_map`)",
            builder=Home("parser/maps.py", "build_map"),
            table=Home("parser/kinds.py", "NoSuchParser._TABLE"),
        ),
        BuilderHome(
            output="seeded: neither a table nor a mark",
            prose="one builder (`build_map`)",
            builder=Home("parser/maps.py", "build_map"),
        ),
    )

    assert builder_problems(_seeded_builder_repo(tmp_path), records, SEEDED_BUILDER_PAGE) == [
        "seeded: a passage the page lacks...: the page has no passage 'the builder (`build_map`) of nothing'",
        "seeded: a passage that names another builder...: the passage does not name part",
        "seeded: a passage that names another builder...: a record states either a table or a mark",
        "seeded: a passage that names another builder...: parser/maps.py defines no no_such_part",
        ("seeded: a passage that names another builder...: parser/kinds.py assigns no dispatch table to Parser._ROWS"),
        "seeded: a passage that names another builder...: the builder does not reach parser/maps.py:unreached",
        "seeded: a passage that names another builder...: the builder does not reach parser/maps.py:no_such_part",
        "seeded: tables src/ lacks...: parser/no_such_module.py assigns no dispatch table to Parser._TABLE",
        "seeded: a table of a class src/ lacks...: parser/kinds.py assigns no dispatch table to NoSuchParser._TABLE",
        "seeded: neither a table nor a mark...: a record states either a table or a mark",
        "parser/kinds.py:Parser._OTHER is a dispatch table no record holds",
    ]


SEEDED_PLACEMENTS = (
    Placement("seeded: a pass pass_decls.py lacks", frozenset({Fact.BASES}), "after `no_such_pass`", ("no_such_pass",)),
    Placement(
        "seeded: a pass that writes none of its facts",
        frozenset({Fact.METADATA}),
        "after L2 `name_models`",
        ("name_models",),
    ),
    Placement(
        "seeded: a pass the place cell does not name",
        frozenset({RenderFact.CLASS_NAMES}),
        "after L2",
        ("name_models",),
    ),
    Placement("seeded: a pass the record leaves out", frozenset({Fact.BASES}), "L6, after `apply_generic_base_class`"),
    Placement("seeded: a fact of no fact table", frozenset({"field_set"}), "L1 at that object"),
    Placement("seeded: a fact of no fact table", frozenset(), "any pass"),
)
"""Placement records whose passes are not declared, write none of their facts or differ from their place cell."""


@pytest.mark.allow_direct_assert("the problems of seeded records are the check's verdict, not a stored output")
def test_placement_check_fails_on_seeded_records() -> None:
    """A pass undeclared, writing none of the facts, unnamed or left out, a fact of no table, a repeat: problems."""
    assert placement_problems(SEEDED_PLACEMENTS) == [
        "seeded: a pass pass_decls.py lacks...: no_such_pass is no pass of scripts/pass_decls.py",
        "seeded: a pass that writes none of its facts...: after name_models, which writes none of its facts",
        "seeded: a pass the place cell does not name...: the place cell does not name name_models",
        (
            "seeded: a pass the record leaves out...: "
            "the place cell names apply_generic_base_class, which after leaves out"
        ),
        "seeded: a fact of no fact table...: 'field_set' is no member of Fact or RenderFact",
        "seeded: a fact of no fact table...: recorded more than once",
    ]
