"""Generate every shape of the shape families and hold their differences and their relations to the known ones.

The families (``tests/main/shape_families/families.py``) enumerate ordinary modelling idioms completely within their
stated bound; each shape is generated for the backends of its family under the default output and under the newest
dated preset, and its model is judged on a bounded payload pool by the payload suite's validator
(``shape_families/verdict.py``) and against the model of every other spelling of the same meaning, of the reference
backend, of the default output without a presentation option, and against the generated client's argument
(``shape_families/relations.py``). Every difference must be one ``KEPT`` names (``shape_families/kept.py``), and every
cell a rule names must still differ: under the default output the set is frozen, under the preset it is the to-do list.

The relations between backends, the presentation options and the client run on a bounded subset of the families in
the pull request tier (``TIERS``) and on all of them where ``DATAMODEL_CODE_GENERATOR_TEST_SHAPE_FAMILIES`` is ``all``
(the nightly tier).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, replace
from itertools import repeat
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import pytest

from datamodel_code_generator.__main__ import Config
from datamodel_code_generator.arguments import arg_parser
from scripts.behaviour_diff import FAMILY_SHAPES
from scripts.legacy_rows import LEGACY_ROWS
from tests.capabilities import needs
from tests.main.payload_validation import BACKEND_UNASSERTED_MUTATION_CONSTRAINTS
from tests.main.shape_families.families import COMPONENT_REF, FAMILIES, MSGSPEC, PYDANTIC_V2, REF, TYPED_DICT
from tests.main.shape_families.kept import BASES, ISSUE, KEPT, LEGACY_ROW, Differences, differences
from tests.main.shape_families.options import ACCEPTANCE, PRESENTATION
from tests.main.shape_families.relations import (
    BACKENDS,
    CLIENT,
    OPTION_PREFIX,
    RELATIONS,
    REWRITES,
    TWIN_RELATIONS,
    client_findings,
    option_kind,
    rewritten,
    with_backends,
    with_base,
    with_twins,
)
from tests.main.shape_families.verdict import CONTRACTS, KINDS, Cell, judge, payload_backend

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from tests.main.shape_families.families import Family, Shape
    from tests.main.shape_families.kept import Kept

ROOT = Path(__file__).resolve().parents[2]
STATED_BOUND = {"extend": 432, "union": 72, "mixin": 70, "container": 144, "tagged": 32}
"""The number of shapes of each family, as the module docstring of the family library states them."""
ISSUE_REFERENCE = re.compile(r"#[1-9][0-9]*")
LEGACY_ROW_NAMES = frozenset(row.name for row in LEGACY_ROWS)
OPTION_FIELDS = frozenset(name for name, field in Config.model_fields.items() if not field.exclude) | {
    action.dest for action in arg_parser._actions if action.option_strings
}
"""Every configuration field and option destination: the names the option-effect table may place."""


def cover(family: Family) -> tuple[Shape, ...]:
    """Return a few shapes of a family that together take every value of every dimension.

    Each next shape is the first that takes the most values no shape before it took, until none is left.
    """
    shapes = family.shapes()
    left = {pair for shape in shapes for pair in shape.params}
    chosen: list[Shape] = []
    while left:
        best = max(shapes, key=lambda shape: len(left.intersection(shape.params)))
        chosen.append(best)
        left -= set(best.params)
    return tuple(chosen)


def _every_shape(family: Family) -> tuple[Shape, ...]:
    return family.shapes()


def _reference_backend(_family: Family) -> tuple[str, ...]:
    return (PYDANTIC_V2,)


def _family_backends(family: Family) -> tuple[str, ...]:
    return family.backends


@dataclass(frozen=True)
class Tier:
    """The cells the relations between backends, options and the client judge."""

    shapes: Callable[[Family], tuple[Shape, ...]]
    variant_backends: Callable[[Family], tuple[str, ...]]
    """The backends of a family the rewrites and the presentation options are generated for."""
    contracts: tuple[str, ...]
    """The contracts of the rewrites and the presentation options; the backends and the client take both in every
    tier."""
    client_backends: tuple[str, ...]


TierName = Literal["pr", "all"]
TIER_VARIABLE = "DATAMODEL_CODE_GENERATOR_TEST_SHAPE_FAMILIES"
TIERS: dict[TierName, Tier] = {
    "pr": Tier(cover, _reference_backend, ("default",), (PYDANTIC_V2, TYPED_DICT, MSGSPEC)),
    "all": Tier(_every_shape, _family_backends, tuple(CONTRACTS), FAMILIES["container"].backends),
}
"""The pull request tier (a cover of each family; the reference backend and the default output for the rewrites and
the options; a backend of each runtime for the client) and the nightly one (every cell)."""


def _tier_from_env(raw: str | None = None) -> TierName:
    """Return the tier the environment names: unset or empty is the pull request tier, ``all`` the nightly one."""
    if raw is None:
        raw = os.environ.get(TIER_VARIABLE)
    if raw in {None, ""}:
        return "pr"
    if raw == "all":
        return "all"
    msg = f"{TIER_VARIABLE} must be empty or 'all'"
    raise ValueError(msg)


TIER = TIERS[_tier_from_env()]


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


def _known_kind(kind: str) -> bool:
    """Return whether a kind is one of the verdict, one relation or the relation of a presentation option."""
    return kind in KINDS or kind in RELATIONS or kind.removeprefix(OPTION_PREFIX) in PRESENTATION


def _hold(cells: Iterable[Cell], kinds: Iterable[str]) -> Differences:
    """Hold the findings of the kinds a test judges to the rules of those kinds, both ways."""
    judged = set(kinds)
    return differences(
        (
            replace(cell, findings={kind: text for kind, text in cell.findings.items() if kind in judged})
            for cell in cells
        ),
        [rule for rule in KEPT if rule.kind in judged],
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
        rule for rule in KEPT if rule.family not in FAMILIES or not _known_kind(rule.kind) or rule.basis not in BASES
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


@pytest.mark.allow_direct_assert("The option-effect table is static; each check lists the entries that break it.")
def test_option_effect_table_is_well_formed() -> None:
    """Every option the table places exists, none is both kinds, and the cover takes every value of every dimension."""
    assert sorted((PRESENTATION.keys() | ACCEPTANCE) - OPTION_FIELDS) == []
    assert sorted(PRESENTATION.keys() & ACCEPTANCE) == []
    assert [
        (name, dimension, value)
        for name, family in FAMILIES.items()
        for dimension, values in family.dimensions
        for value in values
        if not any(dict(shape.params)[dimension] == value for shape in TIERS["pr"].shapes(family))
    ] == []


@pytest.mark.allow_direct_assert("The tiers are static; each check lists the cells that break it.")
def test_pull_request_tier_is_within_the_nightly_tier() -> None:
    """The pull request tier judges a part of the cells the nightly tier judges, and the nightly tier every cell."""
    pr, nightly = TIERS["pr"], TIERS["all"]
    assert [
        (name, shape.params)
        for name, family in FAMILIES.items()
        for shape in pr.shapes(family)
        if shape.params not in {every.params for every in nightly.shapes(family)}
    ] == []
    assert {name: len(nightly.shapes(family)) for name, family in FAMILIES.items()} == STATED_BOUND
    assert {name: nightly.variant_backends(family) for name, family in FAMILIES.items()} == {
        name: family.backends for name, family in FAMILIES.items()
    }
    assert [
        name for name, family in FAMILIES.items() if not set(pr.variant_backends(family)) <= set(family.backends)
    ] == []
    assert set(pr.contracts) <= set(nightly.contracts) == set(CONTRACTS)
    assert set(pr.client_backends) <= set(nightly.client_backends) == set(FAMILIES["container"].backends)


@pytest.mark.parametrize(("raw", "expected"), [(None, "pr"), ("", "pr"), ("all", "all")], ids=["unset", "empty", "all"])
@pytest.mark.allow_direct_assert("The tier is a pure function of the environment variable's value.")
def test_tier_reads_the_environment(raw: str | None, expected: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The tier variable selects the tier; the nightly workflow sets it to ``all``."""
    monkeypatch.delenv(TIER_VARIABLE, raising=False)
    assert _tier_from_env(raw) == expected


def test_tier_rejects_other_values() -> None:
    """A value other than empty or ``all`` fails loudly instead of running the pull request tier."""
    with pytest.raises(ValueError, match=TIER_VARIABLE):
        _tier_from_env("nightly")


FAMILY_BACKENDS = [
    pytest.param(family, backend, marks=needs("typed-dict-validation") if backend == TYPED_DICT else ())
    for family, definition in FAMILIES.items()
    for backend in definition.backends
]
"""Each family with each of its backends: TypedDict cells need a Pydantic that validates ``typing.TypedDict``."""


def _differences(family: str, backend: str, contract: str, directory: Path) -> Differences:
    """Judge every shape of a family for one backend under one contract, with its twins, and hold it to the rules."""
    cells = with_twins(
        judge(shape, backend, contract, directory / str(index)) for index, shape in enumerate(FAMILIES[family].shapes())
    )
    return _hold(cells, (*KINDS, *TWIN_RELATIONS))


UNCOVERED = "differences no rule of shape_families/kept.py covers:\n"
STALE = "rules to narrow or remove:\n"


@pytest.mark.allow_direct_assert(
    "The verdicts of a family are held to its rule list both ways; each line names a cell."
)
@pytest.mark.parametrize(("family", "backend"), FAMILY_BACKENDS)
def test_default_output_differences_are_frozen(family: str, backend: str, tmp_path: Path) -> None:
    """Under the default output every difference is a known one, and every known one still differs: the set is frozen.

    A difference is one from the validator or one between two spellings of a meaning (``TWINS``). A change that adds
    a difference fails, and so does a fix until it narrows or removes its rule.
    """
    found = _differences(family, backend, "default", tmp_path)
    assert not found.uncovered, UNCOVERED + "\n".join(found.uncovered)
    assert not found.stale, STALE + "\n".join(found.stale)


@pytest.mark.allow_direct_assert(
    "The verdicts of a family are held to its rule list both ways; each line names a cell."
)
@pytest.mark.parametrize(("family", "backend"), FAMILY_BACKENDS)
def test_newest_preset_differences_are_the_to_do_list(family: str, backend: str, tmp_path: Path) -> None:
    """Under the newest dated preset every difference is a rule of the to-do list, and every rule still differs.

    A fix that clears a rule's cells under the preset (an option the preset turns on) removes or narrows the rule.
    """
    found = _differences(family, backend, "preset", tmp_path)
    assert not found.uncovered, UNCOVERED + "\n".join(found.uncovered)
    assert not found.stale, STALE + "\n".join(found.stale)


MULTI_BACKEND_FAMILIES = [
    pytest.param(family, contract, marks=needs("typed-dict-validation") if TYPED_DICT in definition.backends else ())
    for family, definition in FAMILIES.items()
    if len(definition.backends) > 1
    for contract in CONTRACTS
]


@pytest.mark.allow_direct_assert("The backends' verdicts are held to the rule list both ways; each line names a cell.")
@pytest.mark.parametrize(("family", "contract"), MULTI_BACKEND_FAMILIES)
def test_backends_agree_on_accepted_payloads(family: str, contract: str, tmp_path: Path) -> None:
    """Every backend of a family accepts, in strict mode, the payloads its Pydantic v2 model accepts, and no other.

    The shapes are those of the tier; every difference is a ``backends`` rule, and every such rule still differs.
    """
    cells = with_backends(
        judge(shape, backend, contract, tmp_path / f"{index}-{position}", strict=True)
        for index, shape in enumerate(TIER.shapes(FAMILIES[family]))
        for position, backend in enumerate(FAMILIES[family].backends)
    )
    found = _hold(cells, [BACKENDS])
    assert not found.uncovered, UNCOVERED + "\n".join(found.uncovered)
    assert not found.stale, STALE + "\n".join(found.stale)


CLIENT_CELLS = [
    pytest.param(backend, contract, marks=needs("typed-dict-validation") if backend == TYPED_DICT else ())
    for backend in TIER.client_backends
    for contract in CONTRACTS
]


@pytest.mark.allow_direct_assert("The client's arguments are held to the rule list both ways; each line names a cell.")
@pytest.mark.parametrize(("backend", "contract"), CLIENT_CELLS)
def test_client_arguments_accept_what_the_models_accept(backend: str, contract: str, tmp_path: Path) -> None:
    """The httpx2 client's field argument of every container shape accepts each valid value its model field accepts.

    The shapes are the fields of one request body; every difference is a ``client`` rule, and every such rule still
    differs.
    """
    found = _hold(client_findings(backend, contract, tmp_path), [CLIENT])
    assert not found.uncovered, UNCOVERED + "\n".join(found.uncovered)
    assert not found.stale, STALE + "\n".join(found.stale)


VARIANT_CELLS = [
    pytest.param(family, backend, contract, marks=needs("typed-dict-validation") if backend == TYPED_DICT else ())
    for family, definition in FAMILIES.items()
    for backend in TIER.variant_backends(definition)
    for contract in TIER.contracts
]
"""Each family with the backends and contracts the tier generates its variants for: the rewrites and the options."""


def _base(shapes: tuple[Shape, ...], backend: str, contract: str, directory: Path) -> list[Cell]:
    return [judge(shape, backend, contract, directory / "base" / str(index)) for index, shape in enumerate(shapes)]


def _hold_variants(variants: Mapping[str, list[Cell]]) -> Differences:
    """Hold the cells of each variant (a rewrite, an option) to the rules of its own relation, both ways.

    The cells of two variants share their parameters, so each kind is held apart from the cells of the others.
    """
    held = [_hold(cells, [kind]) for kind, cells in variants.items()]
    return Differences(
        tuple(line for found in held for line in found.uncovered), tuple(line for found in held for line in found.stale)
    )


@pytest.mark.allow_direct_assert("The rewrite relations are held to the rule list both ways; each line names a cell.")
@pytest.mark.parametrize(("family", "backend", "contract"), VARIANT_CELLS)
def test_rewrites_keep_the_verdicts(family: str, backend: str, contract: str, tmp_path: Path) -> None:
    """Each rewrite of ``REWRITES`` (definition order, allOf member order, union member order) keeps every verdict.

    The shapes are those of the tier; every difference is a rule of its rewrite, and every such rule still differs.
    """
    shapes = TIER.shapes(FAMILIES[family])
    base = _base(shapes, backend, contract, tmp_path)
    variants: dict[str, list[Cell]] = {}
    for rewrite in REWRITES:
        changed = [(index, shape) for index, shape in enumerate(map(rewritten, shapes, repeat(rewrite))) if shape]
        variants[rewrite] = with_base(
            rewrite,
            (judge(shape, backend, contract, tmp_path / rewrite / str(index)) for index, shape in changed),
            base,
        )
    found = _hold_variants(variants)
    assert not found.uncovered, UNCOVERED + "\n".join(found.uncovered)
    assert not found.stale, STALE + "\n".join(found.stale)


@pytest.mark.allow_direct_assert("The option relations are held to the rule list both ways; each line names a cell.")
@pytest.mark.parametrize(("family", "backend", "contract"), VARIANT_CELLS)
def test_presentation_options_keep_the_verdicts(family: str, backend: str, contract: str, tmp_path: Path) -> None:
    """Each presentation option of the option-effect table leaves every verdict of the tier's cells as it was.

    Every difference is a rule of its ``option:<name>`` relation, and every such rule still differs.
    """
    shapes = TIER.shapes(FAMILIES[family])
    base = _base(shapes, backend, contract, tmp_path)
    variants = {
        option_kind(option): with_base(
            option_kind(option),
            (
                judge(shape, backend, contract, tmp_path / option / str(index), extra=arguments)
                for index, shape in enumerate(shapes)
            ),
            base,
        )
        for option, arguments in PRESENTATION.items()
    }
    found = _hold_variants(variants)
    assert not found.uncovered, UNCOVERED + "\n".join(found.uncovered)
    assert not found.stale, STALE + "\n".join(found.stale)
