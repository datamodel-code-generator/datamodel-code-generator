"""Rendered nullability: whether a type's hint, or the annotation a field writes around it, includes ``None``.

Every input is an immutable fact passed by keyword, so the functions read nothing else. The adapters
``types.py:DataType.admits_none`` and ``model/base.py:DataModelFieldBase._field_null_source`` read the facts from the
live objects; ``parser/generation.py:GenerationStore.write_rendered_null`` writes it once at render preparation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "FieldNullSource",
    "NullSource",
    "field_renders_none",
    "member_null",
    "rendered_nullability",
    "takes_member_null",
]

NullSource: TypeAlias = Literal["own", "reference", "member"]
FieldNullSource: TypeAlias = Literal["reference", "nullable", "type-has-null", "fall-back"]


def rendered_nullability(*, own: bool, reference: bool, member_nulls: Iterable[bool] = ()) -> NullSource | None:
    """Return why the rendered hint of a type includes None, or None when it does not.

    ``own`` is the type's ``is_optional`` as written so far. ``reference`` tells that the type names a nullable model
    that is not a type alias and that its None was not stripped. ``member_nulls`` tells, for each member of a union
    that takes over the None of its members (``takes_member_null``), whether the member gives one (``member_null``);
    it is read only when neither the type nor its model gives the None, so a caller that only asks which of those two
    gives it leaves it out.
    """
    if own:
        return "own"
    if reference:
        return "reference"
    if any(member_nulls):
        return "member"
    return None


def takes_member_null(*, is_tuple: bool, keeps_member_order: bool, member_count: int) -> bool:
    """Return whether a type takes over the None of its members: a union that is no tuple and keeps no member order."""
    return member_count > 1 and not is_tuple and not keeps_member_order


def member_null(*, is_none: bool, is_any: bool) -> bool | None:
    """Return whether a union member gives the union its None, or None when the member's own rendered hint decides.

    The bare None type gives it, and a member rendered as ``Any`` keeps none.
    """
    return True if is_none else False if is_any else None


def field_renders_none(  # noqa: PLR0913
    *,
    hint_has_none: bool,
    has_default_factory: bool,
    nullable: bool | None,
    required: bool,
    type_has_null: bool | None,
    fall_back_to_nullable: bool,
    pending_reference_null: bool = False,
) -> FieldNullSource | None:
    """Return why a field writes None around the hint of its type, or None when it does not.

    ``hint_has_none`` tells that the type's hint includes None already (its ``is_optional``, unless it renders
    ``Any``); a default factory always gives a value, and such a hint keeps its None as it is.
    ``pending_reference_null`` tells that the type names a nullable model whose None ``hint_has_none`` leaves out,
    which only import collection that skips rendering the hint reads. A ``nullable`` that is not a bool counts as
    not nullable. Without ``nullable``, a required field asks whether its schema type admits null (``type_has_null``)
    and an optional field asks its output backend (``fall_back_to_nullable``), so a caller passes only the one that
    applies.
    """
    if has_default_factory or hint_has_none:
        return None
    if pending_reference_null:
        return "reference"
    if nullable is not None:
        return "nullable" if nullable is True else None
    if required:
        return "type-has-null" if type_has_null else None
    return "fall-back" if fall_back_to_nullable else None
