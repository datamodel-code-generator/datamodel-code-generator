"""Tests for the decision homes of scripts/decision_homes.py: each home is a definition of src/ that its row names."""

from __future__ import annotations

import re
from collections import Counter
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from scripts.build_architecture_docs import DECISION_BEGIN_MARKER, DECISION_END_MARKER, generate_decision_table
from scripts.decision_homes import DECISION_HOMES, DecisionHome, Home
from scripts.where import SPAN, Page, Repo

if TYPE_CHECKING:
    from collections.abc import Iterable

ROOT = Path(__file__).parents[1]
PACKAGE = "datamodel_code_generator"


@cache
def _repo() -> Repo:
    """Return the package and the scripts as `scripts/where.py` reads them, through the source model."""
    return Repo(ROOT, Page([], [], [], []))


def _missing(home: Home) -> str | None:
    """Return what ``src/`` lacks of a home: its module, or a def, class or module-level assignment of its name.

    `Repo.definitions` finds a def or class by its qualified name and a module-level assignment by its name; an import
    defines nothing, so an imported name is no home.
    """
    path = f"src/{PACKAGE}/{home.path}"
    if path not in _repo().names:
        return f"{path} does not exist"
    if home.name and not any(found.qualname == home.name for found in _repo().definitions(home.name, path)):
        return f"{home.path} defines no {home.name}"
    return None


def _named(home: Home, cell: str) -> bool:
    """Return whether a cell names a home: its file in a code span, and the last part of its name in one."""
    spans = SPAN.findall(cell)
    files = {file for span in spans if (file := span.partition(":")[0]).endswith(".py")}
    words = {word for span in spans for word in re.findall(r"[A-Za-z_]\w*", span)}
    cited = any(home.path == file or home.path.endswith(f"/{file}") for file in files)
    return cited and (not home.name or home.name.rpartition(".")[2] in words)


def _problems(records: Iterable[DecisionHome]) -> list[str]:
    """Return what is wrong with records: a home src/ lacks, a home its core cell does not name, or no home at all."""
    problems: list[str] = []
    for record in records:
        key = f"{record.decision[:60]}..."
        if not record.homes:
            problems.append(f"{key}: no home")
        for home in record.homes:
            if (missing := _missing(home)) is not None:
                problems.append(f"{key}: {missing}")
            elif not _named(home, record.core):
                problems.append(f"{key}: the core cell does not name {':'.join(filter(None, home))}")
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
        adapters="",
    ),
    DecisionHome(
        "seeded: a home the core cell does not name",
        homes=(Home("types.py", "DataType.admits_none"), Home("_json_schema.py")),
        core="`types.py:DataType`",
        adapters="",
    ),
    DecisionHome("seeded: no home", homes=(), core="", adapters=""),
)
"""Records whose homes src/ does not define, or that their core cells do not name."""


@pytest.mark.allow_direct_assert("the problems of seeded records are the check's verdict, not a stored output")
def test_decision_home_check_fails_on_seeded_records() -> None:
    """A missing function, module or class member, an import, an unnamed home and a record without one are problems."""
    assert _problems(SEEDED) == [
        "seeded: homes src/ lacks...: types.py defines no no_such_decision",
        f"seeded: homes src/ lacks...: src/{PACKAGE}/no_such_module.py does not exist",
        "seeded: homes src/ lacks...: types.py defines no DataType.no_such_member",
        "seeded: homes src/ lacks...: parser/_intersection.py defines no json_values_equal",
        "seeded: a home the core cell does not name...: the core cell does not name types.py:DataType.admits_none",
        "seeded: a home the core cell does not name...: the core cell does not name _json_schema.py",
        "seeded: no home...: no home",
    ]
