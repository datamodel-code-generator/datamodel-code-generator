"""Check that every merge core writes a schema that accepts exactly what the conjunction of its parts accepts.

The generator merges two schema parts into one schema at five sites, each through the pure cores of
``parser/_intersection.py``: a ``$ref`` with sibling keywords, a property that overrides an inherited one, the
property schemas of ``allOf`` members merged into one object, a property several ``allOf`` members declare, and a
branch of an ``anyOf`` or ``oneOf`` beside keywords. Each core here calls them as the site's adapter does, with the
adapter's own null rules (the reason this module imports private names). For every ordered pair ``(A, B)`` of
``PARTS``, so both orders, under every option set of ``OPTION_ROWS``, the test checks
``valid(x, {"allOf": [A, B]}) == valid(x, merge(A, B))`` for every ``x`` of ``VALUES``, with the source validator
of the payload suite as the oracle.

The domain is the keywords whose intersection the cores write: ``type`` (a subtype, disjoint types, type lists),
``enum`` and ``const`` (``1``, ``1.0`` and ``true``, a ``null`` value), numeric bounds in inclusive and exclusive
form (equal values of both forms, two values of one keyword, bounds that cross), ``multipleOf`` (two integers whose
least common multiple is neither, a fraction), length bounds of strings and arrays, nullability (a type list, OpenAPI
``nullable``, ``type: null``), ``required`` and ``pattern``: two literal patterns, the pair of #7095 whose conjunction
was decided differently in the two orders (a member that Python's ``re`` rejects, such as ``(?<n>b)``, is outside the
domain, since the oracle runs Python's ``re``), an anchored pattern, and a backreference that not every engine runs.
A few parts hold two keywords, so a merge also meets a part whose keywords constrain each other. ``VALUES`` holds the
values on each side of every bound, the multiples, every JSON kind, and ``1``, ``1.0`` and ``true``.

A difference is no failure where no value of ``VALUES`` meets both parts (no schema-valid payload exists), where the
core reports it (a keyword without a common value, or a pattern left out, which the site warns about), where only null
differs and the site's own part declares null (``OWN_NULL_SITES``), or where removing one legacy row from the option
set changes the merge into one of these (``KEPT_ROWS`` names the rows, those of the legacy rows table of
docs/architecture.md). Every other difference is listed in ``KNOWN_DIFFERENCES`` with its issue, and the
list only shrinks: a listed pair that no longer differs fails the test until its entry is removed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator._regex_syntax import conjoin_patterns
from datamodel_code_generator.parser._inherited_schemas import _dump_drops_inherited_null
from datamodel_code_generator.parser._intersection import (
    all_of_member_row,
    concatenating_row,
    inherited_override_row,
    kept_legacy_rows,
)
from datamodel_code_generator.parser._null_facts import _declares_null, dump_null_sources
from datamodel_code_generator.parser._schema_reading import _intersect_dumps
from datamodel_code_generator.parser.jsonschema import JsonSchemaObject, _without_nullable
from tests.main.payload_validation import SchemaCase, source_schema_validator
from tests.main.payload_validation.schema import _apply_nullable

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence

DIALECT = "https://json-schema.org/draft/2020-12/schema"
EXTRAS_KEY = JsonSchemaObject.__extra_key__

PARTS: tuple[dict[str, object], ...] = (
    {"type": "integer"},
    {"type": "number"},
    {"type": "string"},
    {"type": ["integer", "string"]},
    {"type": "object"},
    {"type": ["integer", "null"]},
    {"type": "integer", "nullable": True},
    {"type": "null"},
    {"enum": [1, 2, "a"]},
    {"enum": [2, 3]},
    {"enum": [1, None]},
    {"const": 2},
    {"const": 1.0},
    {"const": True},
    {"const": "a"},
    {"minimum": 2},
    {"minimum": 3},
    {"exclusiveMinimum": 2},
    {"maximum": 3},
    {"maximum": 2},
    {"exclusiveMaximum": 3},
    {"multipleOf": 2},
    {"multipleOf": 3},
    {"multipleOf": 1.5},
    {"minLength": 2},
    {"maxLength": 2},
    {"maxLength": 3},
    {"minItems": 2},
    {"maxItems": 1},
    {"required": ["a"]},
    {"required": ["b"]},
    {"pattern": "^a"},
    {"pattern": "b"},
    {"type": "string", "pattern": "a"},
    {"pattern": r"(a)\1"},
    {"type": "integer", "minimum": 2},
    {"type": "string", "maxLength": 3},
    {"type": ["number", "null"], "maximum": 3},
    {"type": "object", "required": ["a"]},
)
"""The parts, each merged with every part in both orders."""

VALUES: tuple[object, ...] = (
    None,
    True,
    False,
    -1,
    0,
    1,
    1.0,
    1.5,
    2,
    2.0,
    2.5,
    3,
    4,
    4.5,
    6,
    "",
    "a",
    "b",
    "ab",
    "ba",
    "aa",
    "abc",
    "abcd",
    [],
    [1],
    [1, 2],
    {},
    {"a": 1},
    {"b": 1},
    {"a": 1, "b": 1},
)
"""The values each merged schema is checked on: both sides of every bound of ``PARTS`` and every JSON kind."""

OPTION_ROWS: dict[str, frozenset[str]] = {
    "default": kept_legacy_rows({}),
    "--intersect-allof-keywords": kept_legacy_rows({"intersect_allof_keywords": True}),
    "--intersect-combined-schema-keywords": kept_legacy_rows({
        "intersect_allof_keywords": True,
        "intersect_combined_schema_keywords": True,
    }),
}
"""The legacy rows of ``parser/_intersection.py:LEGACY_ROWS`` each option set keeps."""


@dataclass(frozen=True)
class Merge:
    """The schema a core writes for two parts, and whether the site warns that it does not check them all."""

    schema: dict[str, object]
    warned: bool


def _enum_outside_type(rows: Collection[str]) -> bool:
    return "legacy-enum-outside-type-kept" not in rows


def ref_siblings(target: dict[str, object], siblings: dict[str, object], rows: Collection[str]) -> Merge:
    """``parser/_schema_refs.py:intersect_ref_siblings``: the target, then the keywords beside its ``$ref``."""
    intersection = _intersect_dumps(
        [target, siblings],
        concatenating_row(target, siblings),
        legacy_rows=rows,
        removes_enum_outside_type=_enum_outside_type(rows),
    )
    return Merge(intersection.keywords, bool(intersection.unsatisfiable or intersection.untaken_patterns))


def inherited_override(parent: dict[str, object], child: dict[str, object], rows: Collection[str]) -> Merge:
    """``parser/_inherited_schemas.py:inherited_override_schema`` under ``--allof-merge-mode constraints``."""
    row = inherited_override_row(
        parent,
        child,
        merge_mode="constraints",
        drops_null=_dump_drops_inherited_null,
        maps_by_name="legacy-schema-maps-merged-as-schemas" not in rows,
    )
    intersection = _intersect_dumps(
        [parent, child], row, legacy_rows=rows, removes_enum_outside_type=_enum_outside_type(rows)
    )
    return Merge(intersection.keywords, bool(intersection.unsatisfiable or intersection.untaken_patterns))


def all_of_members(first: dict[str, object], second: dict[str, object], rows: Collection[str]) -> Merge:
    """``parser/_all_of_values.py:merge_all_of_object``: the members, after the allOf's own keywords (none here)."""
    untaken: list[str] = []

    def conjoin(patterns: Sequence[str]) -> str:
        conjunction = conjoin_patterns(patterns)
        untaken.extend(conjunction.untaken)
        return conjunction.pattern

    conjoined: Callable[[Sequence[str]], str] | None = None if "legacy-lookahead-patterns" in rows else conjoin
    merged = all_of_member_row({}, first, conjoin_patterns=conjoined)
    return Merge(all_of_member_row(merged, second, conjoin_patterns=conjoined), bool(untaken))


def conjoined_declarations(first: dict[str, object], second: dict[str, object], rows: Collection[str]) -> Merge:
    """``parser/_inherited_schemas.py:_conjoined_declarations``: a property two allOf members declare."""
    intersection = _intersect_dumps(
        [first, second],
        all_of_member_row(first, second),
        removes_enum_outside_type=_enum_outside_type(rows),
        own_last=False,
    )
    return Merge(intersection.keywords, bool(intersection.unsatisfiable or intersection.untaken_patterns))


def combined_branch(outer: dict[str, object], branch: dict[str, object], rows: Collection[str]) -> Merge:
    """``parser/jsonschema.py:JsonSchemaParser._combined_branch_row``: the keywords beside a branch, then the branch."""
    row = all_of_member_row(outer, branch, intersect_bounds=False)
    if "legacy-combined-branch-row" in rows:
        return Merge(row, warned=False)
    intersection = _intersect_dumps(
        [_without_nullable(outer), _without_nullable(branch)],
        _without_nullable(row),
        removes_enum_outside_type=_enum_outside_type(rows),
        own_last=False,
    )
    return Merge(
        intersection.keywords | ({"nullable": row["nullable"]} if "nullable" in row else {}),
        bool(intersection.unsatisfiable or intersection.untaken_patterns),
    )


CORES: dict[str, Callable[[dict[str, object], dict[str, object], Collection[str]], Merge]] = {
    "ref_siblings": ref_siblings,
    "inherited_override": inherited_override,
    "all_of_members": all_of_members,
    "conjoined_declarations": conjoined_declarations,
    "combined_branch": combined_branch,
}
DEFERRED_KEYWORDS: dict[str, frozenset[str]] = {"combined_branch": frozenset({"nullable"})}
"""Keywords a core leaves to a later decision: a branch's ``nullable`` is read by the null rule ``combined-branch``."""
OWN_NULL_SITES = frozenset({"ref_siblings", "inherited_override"})
"""The cores whose last part is the site's own keywords, whose declared null the merge keeps (null rule
``all-of-own-declares-null`` of ``parser/_null_facts.py``: null comes from the site's own ``nullable``, ``type`` list or
``null`` value, else only if every part admits it)."""

KEPT_ROWS: dict[str, frozenset[str]] = {
    "ref_siblings": frozenset({"legacy-merge-row"}),
    "inherited_override": frozenset({"legacy-merge-row"}),
    "all_of_members": frozenset({"legacy-lookahead-patterns"}),
    "conjoined_declarations": frozenset(),
    "combined_branch": frozenset({"legacy-combined-branch-row"}),
}
"""The legacy rows of ``LEGACY_ROWS`` (the legacy rows table of docs/architecture.md) that keep a difference."""

KNOWN_DIFFERENCES: dict[str, tuple[str, frozenset[tuple[str, str]]]] = {
    "#7171: an allOf merge of two refs gives a shared property the later member's type and multipleOf": (
        "all_of_members",
        frozenset({
            ('{"multipleOf":1.5}', '{"multipleOf":2}'),
            ('{"multipleOf":2}', '{"multipleOf":1.5}'),
            ('{"multipleOf":2}', '{"multipleOf":3}'),
            ('{"multipleOf":3}', '{"multipleOf":1.5}'),
            ('{"multipleOf":3}', '{"multipleOf":2}'),
            ('{"type":"integer","minimum":2}', '{"type":"integer","nullable":true}'),
            ('{"type":"integer","minimum":2}', '{"type":"number"}'),
            ('{"type":"integer","minimum":2}', '{"type":["integer","null"]}'),
            ('{"type":"integer","minimum":2}', '{"type":["integer","string"]}'),
            ('{"type":"integer","minimum":2}', '{"type":["number","null"],"maximum":3}'),
            ('{"type":"integer","nullable":true}', '{"type":"integer","minimum":2}'),
            ('{"type":"integer","nullable":true}', '{"type":"integer"}'),
            ('{"type":"integer","nullable":true}', '{"type":"number"}'),
            ('{"type":"integer","nullable":true}', '{"type":["integer","string"]}'),
            ('{"type":"integer","nullable":true}', '{"type":["number","null"],"maximum":3}'),
            ('{"type":"integer"}', '{"type":"integer","nullable":true}'),
            ('{"type":"integer"}', '{"type":"number"}'),
            ('{"type":"integer"}', '{"type":["integer","null"]}'),
            ('{"type":"integer"}', '{"type":["integer","string"]}'),
            ('{"type":"integer"}', '{"type":["number","null"],"maximum":3}'),
            ('{"type":"null"}', '{"type":"integer","nullable":true}'),
            ('{"type":"null"}', '{"type":["integer","null"]}'),
            ('{"type":"null"}', '{"type":["number","null"],"maximum":3}'),
            ('{"type":"number"}', '{"type":"integer","nullable":true}'),
            ('{"type":"number"}', '{"type":["integer","null"]}'),
            ('{"type":"number"}', '{"type":["integer","string"]}'),
            ('{"type":"number"}', '{"type":["number","null"],"maximum":3}'),
            ('{"type":"string","maxLength":3}', '{"type":["integer","string"]}'),
            ('{"type":"string","pattern":"a"}', '{"type":["integer","string"]}'),
            ('{"type":"string"}', '{"type":["integer","string"]}'),
            ('{"type":["integer","null"]}', '{"type":"number"}'),
            ('{"type":["integer","null"]}', '{"type":["integer","string"]}'),
            ('{"type":["integer","null"]}', '{"type":["number","null"],"maximum":3}'),
            ('{"type":["integer","string"]}', '{"type":"integer","nullable":true}'),
            ('{"type":["integer","string"]}', '{"type":"number"}'),
            ('{"type":["integer","string"]}', '{"type":["integer","null"]}'),
            ('{"type":["integer","string"]}', '{"type":["number","null"],"maximum":3}'),
            ('{"type":["number","null"],"maximum":3}', '{"type":["integer","null"]}'),
            ('{"type":["number","null"],"maximum":3}', '{"type":["integer","string"]}'),
        }),
    ),
}
"""Differences the generator keeps by mistake: per issue (its draft until it is filed), the core and the ``(A, B)``
pairs it covers."""


def _label(part: dict[str, object]) -> str:
    return json.dumps(part, separators=(",", ":"))


def _dump(part: dict[str, object]) -> dict[str, object]:
    return JsonSchemaObject.model_validate(part).model_dump(exclude_unset=True, by_alias=True)


def _json_schema(dump: dict[str, object]) -> dict[str, object]:
    """Return a dumped schema as JSON Schema: its kept keywords such as ``const`` back in place, ``nullable`` read.

    A ``type`` or ``required`` list that names a value twice (two lists concatenated) means what it means once.
    """
    schema = {key: value for key, value in dump.items() if key != EXTRAS_KEY}
    if isinstance(extras := dump.get(EXTRAS_KEY), dict):
        schema.update(extras)
    if schema.pop("nullable", None) is True:
        _apply_nullable(schema)
    for keyword in ("type", "required"):
        if isinstance(names := schema.get(keyword), list):
            schema[keyword] = list(dict.fromkeys(names))
    return schema


_VALIDITY: dict[str, tuple[bool, ...]] = {}


def _validity(schema: dict[str, object]) -> tuple[bool, ...]:
    """Return which of ``VALUES`` the source validator of the payload suite accepts."""
    key = json.dumps(schema, sort_keys=True)
    if (validity := _VALIDITY.get(key)) is None:
        document = {"$schema": DIALECT, **schema}
        case = SchemaCase(
            id=key,
            input_file_type="jsonschema",
            source_path=Path("merge_equivalence.json"),
            source_schema=document,
            codegen_schema=document,
            temp_input_suffix=".json",
        )
        validator = source_schema_validator(case)
        validity = _VALIDITY[key] = tuple(validator.is_valid(value) for value in VALUES)
    return validity


@dataclass(frozen=True)
class Outcome:
    """The pairs of one core whose merge differs from their conjunction unexplained, and the rows that keep one."""

    residual: frozenset[tuple[str, str]]
    rows: frozenset[str]


def _explanation(core: str, a: dict[str, object], b: dict[str, object], kept: frozenset[str]) -> str | None:
    """Return why the merge of ``a`` and ``b`` under the legacy rows ``kept`` may differ from their conjunction.

    ``exact`` where it does not differ, ``unsatisfiable`` where no value meets both parts, ``warned`` where the core
    reports it, ``own null`` where only null differs and the site's own part declares it, else None.
    """
    conjunction = _validity({"allOf": [_json_schema(a), _json_schema(b)]})
    merged = CORES[core](a, b, kept)
    validity = _validity(_json_schema(merged.schema))
    differing = [
        index for index, (ok, expected) in enumerate(zip(validity, conjunction, strict=True)) if ok != expected
    ]
    reason = None
    if not differing:
        reason = "exact"
    elif not any(conjunction):
        reason = "unsatisfiable"
    elif merged.warned:
        reason = "warned"
    elif core in OWN_NULL_SITES and differing == [VALUES.index(None)] and _declares_null(dump_null_sources(b)):
        reason = "own null"
    return reason


def _reason(core: str, a: dict[str, object], b: dict[str, object], kept: frozenset[str]) -> str | None:
    """Return the ``_explanation`` of a merge, else the legacy row of ``kept`` without which the merge has one."""
    if (explanation := _explanation(core, a, b, kept)) is not None:
        return explanation
    merged = CORES[core](a, b, kept)
    return next(
        (
            row
            for row in sorted(kept)
            if CORES[core](a, b, kept - {row}) != merged and _explanation(core, a, b, kept - {row}) is not None
        ),
        None,
    )


def _outcome(core: str) -> Outcome:
    deferred = DEFERRED_KEYWORDS.get(core, frozenset())
    parts = [part for part in PARTS if deferred.isdisjoint(part)]
    residual: set[tuple[str, str]] = set()
    reasons: set[str] = set()
    for kept in OPTION_ROWS.values():
        for first in parts:
            for second in parts:
                if (reason := _reason(core, _dump(first), _dump(second), kept)) is None:
                    residual.add((_label(first), _label(second)))
                else:
                    reasons.add(reason)
    return Outcome(frozenset(residual), frozenset(reason for reason in reasons if reason.startswith("legacy-")))


@pytest.mark.allow_direct_assert(
    "A pure core is checked against the validator over a finite domain; the differences are named in this module."
)
@pytest.mark.parametrize("core", CORES)
def test_merge_matches_the_conjunction_of_its_parts(core: str) -> None:
    """Every difference of a merge from the conjunction of its parts is warned, kept by a legacy row, or listed."""
    outcome = _outcome(core)
    listed = {pair: draft for draft, (name, pairs) in KNOWN_DIFFERENCES.items() if name == core for pair in pairs}
    unlisted = sorted(outcome.residual - listed.keys())
    fixed = sorted(listed.keys() - outcome.residual)
    assert not unlisted, f"{core}: merges that differ from the conjunction of their parts: {unlisted}"
    assert not fixed, (
        f"{core}: listed differences that no longer differ, remove them from {listed[fixed[0]]!r}: {fixed}"
    )
    assert outcome.rows == KEPT_ROWS[core], f"{core}: kept by {sorted(outcome.rows)}"


@pytest.mark.allow_direct_assert("The pattern pair of #7095 is pinned exact in both orders, not only explained.")
@pytest.mark.parametrize("core", CORES)
def test_pattern_conjunction_is_exact_in_both_orders(core: str) -> None:
    """Two portable patterns of allOf parts are conjoined alike in both orders where no legacy row keeps the merge."""
    rows = OPTION_ROWS["--intersect-combined-schema-keywords"]
    first, second = _dump({"type": "string", "pattern": "a"}), _dump({"pattern": "b"})
    assert [_reason(core, first, second, rows), _reason(core, second, first, rows)] == ["exact", "exact"]
