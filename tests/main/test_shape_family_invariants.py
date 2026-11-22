"""Hold every shape family cell's payload-free invariants, and in the nightly tier its ty errors, to the known ones.

Each shape of the families (``shape_families/families.py``: ``FAMILIES`` and ``REDECLARING_FAMILIES``) is generated for
the backends of its family under the default output and under the newest dated preset, and the generated modules are
read by a checker: ``invariants`` (``shape_families/invariants.py``: imports, validators, wire keys, ``Any``) and,
where ``TYPING_VARIABLE`` is set, ``types`` (ty, as the typing e2e tests run it; the nightly job sets the variable in an
environment that has ty). Every diagnostic must be one a rule of ``shape_families/kept_invariants.py`` names, and every
cell a rule names must still show it: under the default output the set is frozen, under the preset it is the to-do list.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Final

import pytest

from scripts.legacy_rows import LEGACY_ROWS
from tests.api_generation.support.strict_typing import _ty
from tests.capabilities import needs
from tests.main.shape_families.families import FAMILIES, REDECLARING_FAMILIES, TYPED_DICT
from tests.main.shape_families.invariants import KINDS, invariant_diagnostics
from tests.main.shape_families.kept import ISSUE, LEGACY_ROW, differences
from tests.main.shape_families.kept_invariants import INVARIANTS, TYPES
from tests.main.shape_families.verdict import CONTRACTS, Cell, _generate

if TYPE_CHECKING:
    from pathlib import Path

    from tests.main.shape_families.invariants import Diagnostic
    from tests.main.shape_families.kept import Differences, Kept

TYPING_VARIABLE: Final = "DATAMODEL_CODE_GENERATOR_SHAPE_FAMILY_TYPING_E2E"
ALL_FAMILIES: Final = {**FAMILIES, **REDECLARING_FAMILIES}
STATED_BOUND: Final = {"redeclare": 16, "tagged-allof": 32}
"""The number of shapes of each redeclaring family, as the docstring of ``REDECLARING_FAMILIES`` states them."""

CHECKERS: Final = {"invariants": invariant_diagnostics, "types": _ty}
"""Each check by name: a function of a root and the generated modules under it that returns their diagnostics."""
RULES: Final[dict[str, tuple[Kept, ...]]] = {"invariants": INVARIANTS, "types": TYPES}
CHECKS: Final = ("invariants", "types") if os.environ.get(TYPING_VARIABLE) else ("invariants",)
"""The checks this run makes: the invariants always, ty in the nightly tier."""

REFERENCES: Final = {ISSUE: re.compile(r"#[1-9][0-9]*"), LEGACY_ROW: re.compile(r"legacy-[a-z0-9-]+")}
"""The form of a rule's reference for each basis."""
TY_RULE: Final = re.compile(r"[a-z]+(-[a-z]+)+")
LEGACY_ROW_NAMES: Final = frozenset(row.name for row in LEGACY_ROWS)


def _domain(family: str) -> dict[str, tuple[object, ...]]:
    """Return the values each parameter of a family takes, the backend among them."""
    return {**dict(ALL_FAMILIES[family].dimensions), "backend": ALL_FAMILIES[family].backends}


def _enumerated(family: str) -> list[Cell]:
    """Return every cell of a family under every contract, unchecked: the cells the rules may name."""
    return [
        Cell(shape, backend, contract)
        for shape in ALL_FAMILIES[family].shapes()
        for backend in ALL_FAMILIES[family].backends
        for contract in CONTRACTS
    ]


def _kind_holds(check: str, kind: str) -> bool:
    """Return whether a rule's kind is one its check reports: an invariant of ``KINDS``, or a ty rule's name."""
    return kind in KINDS if check == "invariants" else kind not in KINDS and TY_RULE.fullmatch(kind) is not None


def _reference_holds(rule: Kept) -> bool:
    """Return whether a rule's reference has its basis's form and, for a legacy row, names a row that exists."""
    form = REFERENCES.get(rule.basis)
    return (
        form is not None
        and form.fullmatch(rule.reference) is not None
        and (rule.basis != LEGACY_ROW or rule.reference in LEGACY_ROW_NAMES)
    )


@pytest.mark.allow_direct_assert("The shape counts of the families are compared with the bound their docstring states.")
def test_redeclaring_families_enumerate_their_stated_bound() -> None:
    """Each redeclaring family yields exactly the shapes its docstring states, so a dropped or added value fails."""
    assert {name: len(family.shapes()) for name, family in REDECLARING_FAMILIES.items()} == STATED_BOUND


@pytest.mark.allow_direct_assert("The rule lists are static tables; each check lists the rules that break it.")
@pytest.mark.parametrize("check", list(RULES))
def test_invariant_rules_are_well_formed(check: str) -> None:
    """Every rule names a family, kind, contracts, parameter values and a reference that exist, and covers cells.

    No two rules of one kind cover one cell, so narrowing one rule after a fix never leaves the cell to another.
    """
    rules = RULES[check]
    assert [rule for rule in rules if rule.family not in ALL_FAMILIES or not _kind_holds(check, rule.kind)] == []
    assert [rule for rule in rules if not rule.contracts or not rule.contracts <= CONTRACTS.keys()] == []
    assert [rule.reference for rule in rules if not _reference_holds(rule)] == []
    assert [
        (rule.reference, name, values)
        for rule in rules
        for box in rule.boxes
        for name, values in box.items()
        if not set(values) <= set(_domain(rule.family).get(name, ()))
    ] == []
    cells = {family: _enumerated(family) for family in ALL_FAMILIES}
    assert [rule for rule in rules if not any(map(rule.covers, cells[rule.family]))] == []
    assert [
        (cell.params, cell.contract, first.reference, second.reference)
        for index, first in enumerate(rules)
        for second in rules[index + 1 :]
        if first.family == second.family and first.kind == second.kind
        for cell in cells[first.family]
        if first.covers(cell) and second.covers(cell)
    ] == []


def _findings(diagnostics: list[Diagnostic], target: str) -> dict[str, str]:
    """Return the smallest witness of each kind among the diagnostics of one module."""
    found: dict[str, str] = {}
    for file, line, rule in sorted(diagnostics, key=lambda diagnostic: (len(diagnostic[2]), diagnostic)):
        kind, _, detail = rule.partition(": ")
        if file == target:
            found.setdefault(kind, detail or f"line {line}")
    return found


def _differences(check: str, family: str, backend: str, contract: str, root: Path) -> Differences:
    """Generate every shape of a family for one backend under one contract, check the modules and hold them to rules."""
    shapes = ALL_FAMILIES[family].shapes()
    targets = [
        f"{index}/{_generate(shape.document, 'jsonschema', backend, contract, root / str(index)).name}"
        for index, shape in enumerate(shapes)
    ]
    diagnostics = CHECKERS[check](root, targets)
    cells = [
        Cell(shape, backend, contract, _findings(diagnostics, target))
        for shape, target in zip(shapes, targets, strict=True)
    ]
    return differences(cells, RULES[check])


CHECKED = [
    pytest.param(check, family, backend, marks=needs("typed-dict-validation") if backend == TYPED_DICT else ())
    for check in CHECKS
    for family, definition in ALL_FAMILIES.items()
    for backend in definition.backends
]
"""Each check with each family and backend: TypedDict cells need a Pydantic that validates ``typing.TypedDict``."""


@pytest.mark.allow_direct_assert(
    "The diagnostics of a family are held to its rule list both ways; each line names a cell."
)
@pytest.mark.parametrize(("check", "family", "backend"), CHECKED)
def test_default_output_invariants_are_frozen(check: str, family: str, backend: str, tmp_path: Path) -> None:
    """Under the default output every diagnostic is a known one, and every known one still shows: the set is frozen.

    A change that adds a diagnostic fails, and so does a fix until it narrows or removes its rule.
    """
    found = _differences(check, family, backend, "default", tmp_path)
    assert not found.uncovered, "diagnostics no rule of kept_invariants.py covers:\n" + "\n".join(found.uncovered)
    assert not found.stale, "rules to narrow or remove:\n" + "\n".join(found.stale)


@pytest.mark.allow_direct_assert(
    "The diagnostics of a family are held to its rule list both ways; each line names a cell."
)
@pytest.mark.parametrize(("check", "family", "backend"), CHECKED)
def test_newest_preset_invariants_are_the_to_do_list(check: str, family: str, backend: str, tmp_path: Path) -> None:
    """Under the newest dated preset every diagnostic is a rule of the to-do list, and every rule still shows.

    A fix that clears a rule's cells under the preset (an option the preset turns on) removes or narrows the rule.
    """
    found = _differences(check, family, backend, "preset", tmp_path)
    assert not found.uncovered, "diagnostics no rule of kept_invariants.py covers:\n" + "\n".join(found.uncovered)
    assert not found.stale, "rules to narrow or remove:\n" + "\n".join(found.stale)
