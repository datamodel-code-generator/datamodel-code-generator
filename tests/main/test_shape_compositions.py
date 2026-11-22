"""Compose the shape families and hold a fixed sample of compositions to the known differences.

The composition library (``tests/main/shape_families/compose.py``) places a family's shape inside another's; the
nightly random tier (``scripts/shape_families_random.py``) draws them with Hypothesis and compares each with the last
release. Here every placement holds the first plain shape of every family once (``SAMPLE``), so a change of the
library, a rule or the generator that moves a sampled composition fails in the PR suite, and the helpers the random
tier compares two generators with (``shape_families/moves.py``) run on generated modules.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import jsonschema
import pytest
from hypothesis import HealthCheck, given, settings

from scripts.legacy_rows import LEGACY_ROWS
from tests.capabilities import needs
from tests.main.shape_families.compose import (
    COMPOSED,
    COMPOSITION,
    MAX_LEVELS,
    PLACEMENTS,
    PREFIXES,
    backends,
    compose,
    covering,
    describe,
    fits,
    levels,
    placed_levels,
    plain_levels,
)
from tests.main.shape_families.families import FAMILIES, MSGSPEC, PYDANTIC_V2, REF, TYPED_DICT
from tests.main.shape_families.kept import ISSUE, KEPT, LEGACY_ROW
from tests.main.shape_families.moves import moved, verdicts
from tests.main.shape_families.verdict import CONTRACTS, KINDS, judge

if TYPE_CHECKING:
    from pathlib import Path

    from tests.main.shape_families.compose import Level
    from tests.main.shape_families.kept import Kept

PLACED_BOUND = {"container": 24, "extend": 16, "union": 48, "tagged": 32, "mixin": 18}
"""The number of placed levels of each family, as the module docstring of the composition library states them."""


def _first(family: str, levels_: tuple[Level, ...]) -> Level:
    return next(level for level in levels_ if level.family == family)


SAMPLE = tuple(
    (_first(outer, placed_levels()), _first(inner, plain_levels())) for outer in PLACEMENTS for inner in FAMILIES
)
"""Each placement's first level around each family's first plain shape: 25 compositions of two levels."""


@pytest.mark.allow_direct_assert("The level counts are compared with the bound the library's docstring states.")
def test_compositions_enumerate_their_stated_bound() -> None:
    """Each placement yields the levels the docstring states, and every plain shape of the families is a level."""
    assert {name: sum(level.family == name for level in placed_levels()) for name in PLACEMENTS} == PLACED_BOUND
    assert len(plain_levels()) == sum(len(family.shapes()) for family in FAMILIES.values())


@pytest.mark.allow_direct_assert("Each check lists the definitions or references of a drawn composition that break it.")
@settings(derandomize=True, database=None, max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(chain=levels())
def test_drawn_compositions_are_closed_documents(chain: tuple[Level, ...]) -> None:
    """A drawn composition is a valid 2020-12 document whose references all resolve inside it.

    It keeps only the definitions its checked definition reaches, and the plain shape's under its level's prefix.
    """
    shape = compose(chain)
    assert isinstance(definitions := shape.document["$defs"], dict)
    jsonschema.Draft202012Validator.check_schema(shape.schema)
    assert 2 <= len(chain) <= MAX_LEVELS
    assert shape.family == COMPOSITION
    assert shape.name == chain[0].name
    assert shape.name in definitions
    assert [name for name in _referenced(definitions) if name not in definitions] == []
    assert [name for name in definitions if name != shape.name and name not in _referenced(definitions)] == []
    assert PREFIXES[len(chain) - 1] + chain[-1].name in definitions


def _referenced(value: object) -> set[str]:
    if isinstance(value, dict):
        return set().union(*map(_referenced, value.values()))
    if isinstance(value, list):
        return set().union(*map(_referenced, value))
    return {value.removeprefix(REF)} if isinstance(value, str) and value.startswith(REF) else set()


SAMPLE_CELLS = [
    pytest.param(contract, backend, marks=needs("typed-dict-validation") if backend == TYPED_DICT else ())
    for contract in CONTRACTS
    for backend in dict.fromkeys(backend for chain in SAMPLE for backend in backends(chain))
]
"""Each contract with each backend of the sample; TypedDict needs a Pydantic that validates ``typing.TypedDict``."""


@pytest.mark.allow_direct_assert("The sampled cells are held to the rules; each line names a cell.")
@pytest.mark.parametrize(("contract", "backend"), SAMPLE_CELLS)
def test_sampled_compositions_differ_only_as_known(contract: str, backend: str, tmp_path: Path) -> None:
    """Every difference of a sampled composition from the validator is one a family rule or a composition rule covers.

    Family rules cover the part of the level they describe (``compose.covering``), composition rules the rest.
    """
    uncovered = [
        f"{describe(chain)}: {kind} {witness}"
        for index, chain in enumerate(SAMPLE)
        if backend in backends(chain)
        for kind, witness in judge(compose(chain), backend, contract, tmp_path / str(index)).findings.items()
        if covering((*KEPT, *COMPOSED), chain, backend, contract, kind) is None
    ]
    assert uncovered == []


def _view_domain(family: str) -> dict[str, tuple[object, ...]]:
    """Return the values each parameter of a placed level's view takes (``compose.views``), the backend among them."""
    placement = PLACEMENTS[family]
    return {
        **{name: (value,) for name, value in placement.shown.items()},
        **dict(placement.dimensions),
        "inner": tuple(FAMILIES),
        "inner union": (False, True),
        "inner declares": (False, True),
        "inner requires": (False, True),
        "backend": FAMILIES[family].backends,
    }


def _reference_holds(rule: Kept) -> bool:
    """Return whether a composition rule's reference is an issue (``#n``) or a legacy row of its basis."""
    if rule.basis == LEGACY_ROW:
        return rule.reference in {row.name for row in LEGACY_ROWS}
    return rule.basis == ISSUE and re.fullmatch(r"#[1-9][0-9]*", rule.reference) is not None


@pytest.mark.allow_direct_assert("The composition rules are a static table; each check lists the rules that break it.")
def test_composition_rules_are_well_formed() -> None:
    """Every composition rule names a placement, kind, contracts, issue or legacy row and view values that exist."""
    assert [
        rule.reference
        for rule in COMPOSED
        if rule.family not in PLACEMENTS
        or rule.kind not in KINDS
        or not _reference_holds(rule)
        or not rule.contracts <= CONTRACTS.keys()
    ] == []
    assert [
        (rule.reference, name, values)
        for rule in COMPOSED
        for box in rule.boxes
        for name, values in box.items()
        if not set(values) <= set(_view_domain(rule.family).get(name, ()))
    ] == []


@pytest.mark.allow_direct_assert("The filter's answers on two compositions are compared.")
def test_compositions_without_a_common_value_are_left_out() -> None:
    """A mix declaring ``p`` an integer around a mix whose ``p`` is a string has no valid ``p``: no author writes it.

    The same mix around a string ``p`` is kept, as is a level that declares nothing beside its inner (``container``).
    """
    mixes = {dict(level.params)["second"]: level for level in placed_levels() if level.family == "mixin"}
    strings = next(level for level in plain_levels() if level.family == "mixin")
    assert (
        fits((mixes["int"], strings)),
        fits((mixes["str"], strings)),
        fits((_first("container", placed_levels()), strings)),
    ) == (
        False,
        True,
        True,
    )


@pytest.mark.allow_direct_assert("The backends and the description of a composition are compared with its levels'.")
def test_composition_backends_are_shared_by_every_level() -> None:
    """A composition is generated for the backends every level's family is, in the first level's order.

    It is described one level per line, outermost first, a placed level saying that it holds the next.
    """
    container, tagged, extend = (_first(name, plain_levels()) for name in ("container", "tagged", "extend"))
    held = _first("container", placed_levels())
    assert backends((held, container)) == FAMILIES["container"].backends
    assert backends((held, tagged)) == (PYDANTIC_V2, TYPED_DICT, MSGSPEC)
    assert backends((held, extend)) == (PYDANTIC_V2,)
    assert describe((held, tagged)) == (
        "container holds (container=items, nullable=False, required=False)\n"
        "  tagged (tag=const-str, nullable=False, mapping=False, kind=oneOf)"
    )


@pytest.mark.allow_direct_assert("The verdict vectors of generated modules are compared.")
def test_moves_compare_the_verdicts_of_two_modules(tmp_path: Path) -> None:
    """Two modules of one composition move where they decide a payload differently or only one of them imports.

    The default output and the newest preset are two generators that differ on a nullable map's values (#6305) and on
    the import of a Pydantic discriminated union whose tag admits null (#7102).
    """
    container = _first("container", placed_levels())
    nullable_map = next(
        level
        for level in plain_levels()
        if level.family == "container"
        and dict(level.params) == {"container": "map", "element": "str", "nullable": True, "required": False}
    )
    nullable_tag = next(
        level for level in plain_levels() if level.family == "tagged" and dict(level.params)["nullable"]
    )
    outputs = {}
    for name, chain in (("map", (container, nullable_map)), ("tag", (container, nullable_tag))):
        shape = compose(chain)
        for contract in CONTRACTS:
            judge(shape, PYDANTIC_V2, contract, tmp_path / name / contract)
            outputs[name, contract] = shape, verdicts(tmp_path / name / contract / "model.py", shape, PYDANTIC_V2)
    shape, before = outputs["map", "default"]
    _, after = outputs["map", "preset"]
    assert moved(shape, before, before) is None
    assert moved(shape, before, after) == (
        '{"f": [{"f": {"zzz": 1}}]}',
        "before: accepts, strict accepts; after: rejects, strict rejects",
    )
    shape, before = outputs["tag", "default"]
    _, after = outputs["tag", "preset"]
    witness, detail = moved(shape, before, after) or ("-", "")
    assert (
        witness,
        detail.startswith("before: import-fails: PydanticUserError"),
        detail.endswith("; after: imports"),
    ) == (
        "",
        True,
        True,
    )
    assert moved(shape, before, (before[0] + "!",)) is None
