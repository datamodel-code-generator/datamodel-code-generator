"""Generate every shape of the shape families and hold their differences from the validator to the known ones.

The families (``tests/main/shape_families/families.py``) enumerate ordinary modelling idioms completely within their
stated bound; each shape is generated for the backends of its family under the default output and under the newest
dated preset, and its model is judged on a bounded payload pool by the payload suite's validator
(``shape_families/verdict.py``). Every difference must be one ``KEPT`` names (``shape_families/kept.py``), and every
cell a rule names must still differ: under the default output the set is frozen, under the preset it is the to-do list.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from scripts.behaviour_diff import FAMILY_SHAPES
from scripts.legacy_rows import LEGACY_ROWS
from tests.capabilities import needs
from tests.main.payload_validation import BACKEND_UNASSERTED_MUTATION_CONSTRAINTS
from tests.main.shape_families.families import COMPONENT_REF, FAMILIES, REF, TYPED_DICT
from tests.main.shape_families.kept import BASES, ISSUE, KEPT, LEGACY_ROW, Differences, differences
from tests.main.shape_families.verdict import CONTRACTS, KINDS, Cell, judge, payload_backend

if TYPE_CHECKING:
    from tests.main.shape_families.kept import Kept

ROOT = Path(__file__).resolve().parents[2]
STATED_BOUND = {"extend": 432, "union": 72, "mixin": 70, "container": 144, "tagged": 32}
"""The number of shapes of each family, as the module docstring of the family library states them."""
ISSUE_REFERENCE = re.compile(r"#[1-9][0-9]*")
LEGACY_ROW_NAMES = frozenset(row.name for row in LEGACY_ROWS)


def _domain(family: str) -> dict[str, tuple[object, ...]]:
    """Return the values each parameter of a family takes, the backend among them."""
    return {**dict(FAMILIES[family].dimensions), "backend": FAMILIES[family].backends}


def _rule_backends(rule: Kept) -> set[str]:
    """Return the backends whose cells a rule may cover."""
    return {backend for box in rule.boxes for backend in box.get("backend", FAMILIES[rule.family].backends)}


def _reference_holds(rule: Kept) -> bool:
    """Return whether a rule's reference is a record of its basis.

    An issue is ``#n``; a legacy row is a row of ``scripts/legacy_rows.py``; a difference by design or a library limit
    names a keyword the payload suite's conformance table leaves unasserted for every backend the rule covers.
    """
    if rule.basis == ISSUE:
        return ISSUE_REFERENCE.fullmatch(rule.reference) is not None
    if rule.basis == LEGACY_ROW:
        return rule.reference in LEGACY_ROW_NAMES
    return all(
        rule.reference in BACKEND_UNASSERTED_MUTATION_CONSTRAINTS[payload_backend(backend)]
        for backend in _rule_backends(rule)
    )


def _enumerated(family: str) -> list[Cell]:
    """Return every cell of a family under every contract, unjudged: the cells the rules may name."""
    return [
        Cell(shape, backend, contract)
        for shape in FAMILIES[family].shapes()
        for backend in FAMILIES[family].backends
        for contract in CONTRACTS
    ]


@pytest.mark.allow_direct_assert("The shape counts of the families are compared with the bound their docstring states.")
def test_families_enumerate_their_stated_bound() -> None:
    """Each family yields exactly the shapes its docstring states, so a dropped or added dimension value fails."""
    assert {name: len(family.shapes()) for name, family in FAMILIES.items()} == STATED_BOUND


@pytest.mark.allow_direct_assert("Each shape's OpenAPI spelling is compared with its own definitions.")
def test_openapi_spelling_carries_each_shape() -> None:
    """Each shape's OpenAPI spelling holds its definitions as components, every reference to a component of that name.

    No reference to ``$defs`` is left, and its one operation sends the checked definition, so the targets generated
    from it carry the shape.
    """
    moved = [
        shape.name
        for family in FAMILIES.values()
        for shape in family.shapes()
        if REF in json.dumps(shape.openapi)
        or json.loads(json.dumps(shape.openapi["components"]).replace(f'"{COMPONENT_REF}', f'"{REF}'))
        != {"schemas": shape.document["$defs"]}
        or shape.openapi["paths"]["/check"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        != {"$ref": COMPONENT_REF + shape.name}
    ]
    assert moved == []


@pytest.mark.allow_direct_assert("The gate's listing of the library is compared with the library's own shapes.")
def test_gate_lists_every_shape_of_the_library() -> None:
    """The behaviour gate's listing (`FAMILY_SHAPES`, run as the gate runs it) holds every shape of the library.

    Each row is the shape's family, its parameters as text and its two spellings, so a library change the gate
    cannot read (a renamed `FAMILIES`, `shapes`, `params`, `document` or `openapi`) fails here, not in the gate.
    """
    listed = subprocess.run(
        [sys.executable, "-c", FAMILY_SHAPES], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert listed.returncode == 0, listed.stderr
    rows = json.loads(listed.stdout)
    shapes = [shape for family in FAMILIES.values() for shape in family.shapes()]
    written = [[s.family, [[n, str(v)] for n, v in s.params], s.document, s.openapi] for s in shapes]
    assert len(rows) == sum(STATED_BOUND.values())
    assert rows == json.loads(json.dumps(written))


@pytest.mark.allow_direct_assert("The rule list is a static table; each check lists the rules that break it.")
def test_kept_rules_are_well_formed() -> None:
    """Every rule names a family, kind, contracts, parameter values and a reference that exist, and covers cells.

    No two rules of one kind cover one cell, so narrowing one rule after a fix never leaves the cell to another.
    """
    assert [
        rule for rule in KEPT if rule.family not in FAMILIES or rule.kind not in KINDS or rule.basis not in BASES
    ] == []
    assert [rule for rule in KEPT if not rule.contracts or not rule.contracts <= CONTRACTS.keys()] == []
    assert [rule.reference for rule in KEPT if not _reference_holds(rule)] == []
    assert [
        (rule.reference, name, values)
        for rule in KEPT
        for box in rule.boxes
        for name, values in box.items()
        if not set(values) <= set(_domain(rule.family).get(name, ()))
    ] == []
    cells = {family: _enumerated(family) for family in FAMILIES}
    assert [rule for rule in KEPT if not any(map(rule.covers, cells[rule.family]))] == []
    assert [
        (cell.params, cell.contract, first.reference, second.reference)
        for index, first in enumerate(KEPT)
        for second in KEPT[index + 1 :]
        if first.family == second.family and first.kind == second.kind
        for cell in cells[first.family]
        if first.covers(cell) and second.covers(cell)
    ] == []


FAMILY_BACKENDS = [
    pytest.param(family, backend, marks=needs("typed-dict-validation") if backend == TYPED_DICT else ())
    for family, definition in FAMILIES.items()
    for backend in definition.backends
]
"""Each family with each of its backends: TypedDict cells need a Pydantic that validates ``typing.TypedDict``."""


def _differences(family: str, backend: str, contract: str, directory: Path) -> Differences:
    """Judge every shape of a family for one backend under one contract and hold the cells to the rule list."""
    cells = [
        judge(shape, backend, contract, directory / str(index)) for index, shape in enumerate(FAMILIES[family].shapes())
    ]
    return differences(cells, KEPT)


@pytest.mark.allow_direct_assert(
    "The verdicts of a family are held to its rule list both ways; each line names a cell."
)
@pytest.mark.parametrize(("family", "backend"), FAMILY_BACKENDS)
def test_default_output_differences_are_frozen(family: str, backend: str, tmp_path: Path) -> None:
    """Under the default output every difference is a known one, and every known one still differs: the set is frozen.

    A change that adds a difference fails, and so does a fix until it narrows or removes its rule.
    """
    found = _differences(family, backend, "default", tmp_path)
    assert not found.uncovered, "differences no rule of shape_families/kept.py covers:\n" + "\n".join(found.uncovered)
    assert not found.stale, "rules to narrow or remove:\n" + "\n".join(found.stale)


@pytest.mark.allow_direct_assert(
    "The verdicts of a family are held to its rule list both ways; each line names a cell."
)
@pytest.mark.parametrize(("family", "backend"), FAMILY_BACKENDS)
def test_newest_preset_differences_are_the_to_do_list(family: str, backend: str, tmp_path: Path) -> None:
    """Under the newest dated preset every difference is a rule of the to-do list, and every rule still differs.

    A fix that clears a rule's cells under the preset (an option the preset turns on) removes or narrows the rule.
    """
    found = _differences(family, backend, "preset", tmp_path)
    assert not found.uncovered, "differences no rule of shape_families/kept.py covers:\n" + "\n".join(found.uncovered)
    assert not found.stale, "rules to narrow or remove:\n" + "\n".join(found.stale)
