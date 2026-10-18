"""Rendered nullability: whether a type's hint, or the annotation a field writes around it, includes ``None``.

The adapters ``types.py:DataType.admits_none`` and ``model/base.py:DataModelFieldBase._field_null_source`` build the
fact records from the live objects; ``types.py:DataType._materialize_render_state`` writes what rendering decides.
"""

from __future__ import annotations

from typing import Literal, NamedTuple, TypeAlias, final

__all__ = [
    "FieldNullSource",
    "FieldNullabilityFacts",
    "MemberNullabilityFacts",
    "NullSource",
    "NullabilityFacts",
    "field_renders_none",
    "rendered_nullability",
]

NullSource: TypeAlias = Literal["own", "reference", "member"]
FieldNullSource: TypeAlias = Literal["reference", "nullable", "type-has-null", "fall-back"]


@final
class NullabilityFacts(NamedTuple):
    """The facts that decide whether the rendered hint of a type includes None.

    ``own`` is the type's ``is_optional`` as written so far. ``reference`` tells that the type names a nullable model
    that is not a type alias and that its None was not stripped. ``members`` holds the members of a union, and is
    empty for any other type.
    """

    own: bool
    reference: bool
    is_tuple: bool
    keeps_member_order: bool
    members: tuple[MemberNullabilityFacts, ...]


@final
class MemberNullabilityFacts(NamedTuple):
    """A union member: whether it is the bare None type, whether it renders as ``Any``, and its own facts."""

    is_none: bool
    is_any: bool
    facts: NullabilityFacts


@final
class FieldNullabilityFacts(NamedTuple):
    """The facts that decide whether a field writes None around the hint of its type.

    ``hint_has_none`` tells that the type's hint includes None already (its ``is_optional``, unless it renders
    ``Any``). ``pending_reference_null`` tells that the type names a nullable model whose None no rendered hint has
    written yet, which only import collection that skips rendering the hint reads. ``fall_back_to_nullable`` is the
    output backend's answer for an optional field without ``nullable``.
    """

    hint_has_none: bool
    has_default_factory: bool
    nullable: bool | None
    required: bool
    type_has_null: bool
    fall_back_to_nullable: bool
    pending_reference_null: bool = False


def rendered_nullability(facts: NullabilityFacts, /) -> NullSource | None:
    """Return why the rendered hint of a type includes None, or None when it does not.

    A union that is not a tuple and does not keep its member order takes over the None of a member that is the bare
    None type or whose own hint includes None, except a member rendered as ``Any``, which keeps no None.
    """
    if facts.own:
        return "own"
    if facts.reference:
        return "reference"
    if (
        not facts.is_tuple
        and not facts.keeps_member_order
        and len(facts.members) > 1
        and any(
            member.is_none or (not member.is_any and rendered_nullability(member.facts) is not None)
            for member in facts.members
        )
    ):
        return "member"
    return None


def field_renders_none(facts: FieldNullabilityFacts, /) -> FieldNullSource | None:
    """Return why a field writes None around the hint of its type, or None when it does not.

    A default factory always gives a value, and a hint that includes None already keeps it as it is.
    """
    if facts.has_default_factory or facts.hint_has_none:
        return None
    if facts.pending_reference_null:
        return "reference"
    if facts.nullable is not None:
        return "nullable" if facts.nullable else None
    if facts.required:
        return "type-has-null" if facts.type_has_null else None
    return "fall-back" if facts.fall_back_to_nullable else None
