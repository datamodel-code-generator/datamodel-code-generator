"""Generate every row of three decision tables and hold the differences from the validator to the known ones.

The tables (``tests/main/decision_tables/``) enumerate a decision of the generator over a finite domain: which keyword
a model enforces on which declared type (``constraints``), where a model admits null (``nulls``), and how a
discriminated union is tagged (``tags``). Each row is generated through the command line for the backends of its
table under the default output and under the newest dated preset, and its model is judged on the row's payloads by
the payload suite's validator (``shape_families/verdict.py``). Every difference must be a rule of
``decision_tables/rules.py``, and every cell a rule names must still differ: under the default output the set is
frozen, under the preset it is the to-do list.

``DCG_DECISION_TABLES`` picks the tier: ``pr`` (the default) runs the rows of ``PR_TIER``, ``all`` every row (the
nightly tier).
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Final, Literal

import pytest

from scripts.legacy_rows import LEGACY_ROWS
from tests.capabilities import needs
from tests.main.decision_tables import constraints, nulls, tags
from tests.main.decision_tables.rules import RULES
from tests.main.shape_families.families import TYPED_DICT
from tests.main.shape_families.kept import BASES, ISSUE, LEGACY_ROW, Differences, differences
from tests.main.shape_families.verdict import CONTRACTS, KINDS, Cell, judge_batch, row_shape

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path
    from types import ModuleType

    from tests.main.shape_families.kept import Kept
    from tests.main.shape_families.verdict import Batch

TABLES: Final[Mapping[str, ModuleType]] = {module.TABLE: module for module in (constraints, nulls, tags)}
STATED_BOUND: Final = {"constraints": 540, "nulls": 156, "tags": 176}
"""The number of rows of each table, as the module docstring of the table states it."""
Tier = Literal["pr", "all"]


def tier_from_env(raw: str | None) -> Tier:
    """Return the tier ``DCG_DECISION_TABLES`` names: ``pr`` when unset or empty."""
    match raw:
        case None | "" | "pr":
            return "pr"
        case "all":
            return "all"
        case _:
            msg = "DCG_DECISION_TABLES must be either 'pr' or 'all'"
            raise ValueError(msg)


TIER: Final = tier_from_env(os.environ.get("DCG_DECISION_TABLES"))
PR_TIER: Final[Mapping[str, Callable[[Batch], bool]]] = {
    constraints.TABLE: lambda batch: dict(batch.rows[0].params)["site"] != "item",
    nulls.TABLE: lambda _batch: True,
    tags.TABLE: lambda batch: (
        dict(batch.rows[0].params)["count"] == 2 and dict(batch.rows[0].params)["kind"] == "oneOf"
    ),
}
"""The rows the pull request tier runs: the constraint rows but those in array items, every null row, and the tag rows
of two variants under ``oneOf``."""
ISSUE_REFERENCE: Final = re.compile(r"#[1-9][0-9]*")
SLUG: Final = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
LEGACY_ROW_NAMES: Final = frozenset(row.name for row in LEGACY_ROWS)


def _enumerated(table: str) -> list[Cell]:
    """Return every cell of a table under every contract, unjudged: the cells the rules may name."""
    module = TABLES[table]
    return [
        Cell(row_shape(batch, row), backend, contract)
        for batch in module.batches()
        for row in batch.rows
        for backend in module.BACKENDS
        for contract in CONTRACTS
    ]


def _reference_holds(rule: Kept) -> bool:
    """Return whether a rule's reference is a record of its basis: ``#n``, a legacy row, or a name."""
    if rule.basis == ISSUE:
        return ISSUE_REFERENCE.fullmatch(rule.reference) is not None
    if rule.basis == LEGACY_ROW:
        return rule.reference in LEGACY_ROW_NAMES
    return SLUG.fullmatch(rule.reference) is not None


@pytest.mark.allow_direct_assert("The row counts of the tables are compared with the bound their docstrings state.")
def test_tables_enumerate_their_stated_bound() -> None:
    """Each table yields exactly the rows its docstring states, so a dropped or added dimension value fails."""
    assert {name: sum(len(batch.rows) for batch in module.batches()) for name, module in TABLES.items()} == STATED_BOUND


@pytest.mark.allow_direct_assert("The rule lists are static tables; each check lists the rules that break it.")
@pytest.mark.parametrize("table", TABLES)
def test_table_rules_are_well_formed(table: str) -> None:
    """Every rule names a kind, contracts, a reference and parameter values of its table, and covers cells.

    No two rules of one kind cover one cell, so narrowing one rule after a fix never leaves the cell to another.
    """
    rules = RULES[table]
    cells = _enumerated(table)
    domain: dict[str, set[object]] = {}
    for cell in cells:
        for name, value in cell.params.items():
            domain.setdefault(name, set()).add(value)
    assert [rule for rule in rules if rule.family != table or rule.kind not in KINDS or rule.basis not in BASES] == []
    assert [rule for rule in rules if not rule.contracts or not rule.contracts <= CONTRACTS.keys()] == []
    assert [rule.reference for rule in rules if not _reference_holds(rule)] == []
    assert [
        (rule.reference, name, values)
        for rule in rules
        for box in rule.boxes
        for name, values in box.items()
        if not set(values) <= domain.get(name, set())
    ] == []
    assert [rule for rule in rules if not any(map(rule.covers, cells))] == []
    assert [
        (cell.params, cell.contract, first.reference, second.reference)
        for index, first in enumerate(rules)
        for second in rules[index + 1 :]
        if first.kind == second.kind
        for cell in cells
        if first.covers(cell) and second.covers(cell)
    ] == []


def _backends(table: str) -> list[object]:
    """Return a table's backends as parameters: TypedDict cells need a Pydantic that validates ``typing.TypedDict``."""
    return [
        pytest.param(backend, marks=needs("typed-dict-validation") if backend == TYPED_DICT else ())
        for backend in TABLES[table].BACKENDS
    ]


def _differences(table: str, backend: str, contract: str, directory: Path) -> Differences:
    """Judge the rows of a table in this tier for one backend under one contract and hold them to the rule list."""
    selected = [batch for batch in TABLES[table].batches() if TIER == "all" or PR_TIER[table](batch)]
    cells = [
        cell
        for index, batch in enumerate(selected)
        for cell in judge_batch(batch, backend, contract, directory / str(index), judges_null=table == nulls.TABLE)
    ]
    return differences(cells, RULES[table])


@pytest.mark.allow_direct_assert("The verdicts of a table are held to its rule list both ways; each line names a cell.")
@pytest.mark.parametrize("contract", CONTRACTS)
@pytest.mark.parametrize("backend", _backends(constraints.TABLE))
def test_constraint_rows_differ_only_as_known(backend: str, contract: str, tmp_path: Path) -> None:
    """Every keyword a model fails to enforce beside a declared type is a known difference, and every known one holds.

    Under the default output a change that adds a difference fails, and so does a fix until it narrows or removes its
    rule; under the preset a fix that clears a rule's cells (an option the preset turns on) removes or narrows it.
    """
    found = _differences(constraints.TABLE, backend, contract, tmp_path)
    assert not found.uncovered, "differences no rule of decision_tables/rules.py covers:\n" + "\n".join(found.uncovered)
    assert not found.stale, "rules to narrow or remove:\n" + "\n".join(found.stale)


@pytest.mark.allow_direct_assert("The verdicts of a table are held to its rule list both ways; each line names a cell.")
@pytest.mark.parametrize("contract", CONTRACTS)
@pytest.mark.parametrize("backend", _backends(nulls.TABLE))
def test_null_rows_differ_only_as_known(backend: str, contract: str, tmp_path: Path) -> None:
    """Every place a model admits or refuses null otherwise than the schema is a known difference, both ways."""
    found = _differences(nulls.TABLE, backend, contract, tmp_path)
    assert not found.uncovered, "differences no rule of decision_tables/rules.py covers:\n" + "\n".join(found.uncovered)
    assert not found.stale, "rules to narrow or remove:\n" + "\n".join(found.stale)


@pytest.mark.allow_direct_assert("The verdicts of a table are held to its rule list both ways; each line names a cell.")
@pytest.mark.parametrize("contract", CONTRACTS)
@pytest.mark.parametrize("backend", _backends(tags.TABLE))
def test_tag_rows_differ_only_as_known(backend: str, contract: str, tmp_path: Path) -> None:
    """Every discriminated union whose model decides otherwise than the schema is a known difference, both ways."""
    found = _differences(tags.TABLE, backend, contract, tmp_path)
    assert not found.uncovered, "differences no rule of decision_tables/rules.py covers:\n" + "\n".join(found.uncovered)
    assert not found.stale, "rules to narrow or remove:\n" + "\n".join(found.stale)


@pytest.mark.allow_direct_assert("A pure parser of the tier variable is checked over its values.")
@pytest.mark.parametrize(("raw", "tier"), [(None, "pr"), ("", "pr"), ("pr", "pr"), ("all", "all")])
def test_tier_is_read_from_the_environment(raw: str | None, tier: Tier) -> None:
    """``DCG_DECISION_TABLES`` names the tier; unset or empty is the pull request tier."""
    assert tier_from_env(raw) == tier


def test_tier_refuses_other_values() -> None:
    """A tier other than ``pr`` and ``all`` is refused, so a mistyped nightly setting does not run the PR tier."""
    with pytest.raises(ValueError, match="DCG_DECISION_TABLES"):
        tier_from_env("nightly")
