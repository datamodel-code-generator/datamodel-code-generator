"""Rendered nullability: whether a type's hint, or the annotation a field writes around it, includes ``None``.

Every input is an immutable fact passed by keyword, so the functions read nothing else. The adapters
``types.py:DataType.admits_none`` and ``model/base.py:DataModelFieldBase._field_null_source`` read the facts from the
live objects; ``parser/generation.py:GenerationStore.write_rendered_null`` writes it once at render preparation. The
client and server targets read the None of a model type from its frozen view (``_target_types.py``), and that of an
annotation the document writes from ``written_annotation_null``.

Where the None of a type comes from is one record, `TypeNull`, that the generation store keeps on the type: the node
of the reference site (``parser/_sites.py:RefSite.node``), so copies of the node keep it. ``reference_null`` decides
from it the None a reference keeps after model merges.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal, TypeAlias, final

from datamodel_code_generator._records import record

if TYPE_CHECKING:
    from collections.abc import Iterable

    from datamodel_code_generator._python_type_annotation import PythonTypeExpr

__all__ = [
    "NO_TYPE_NULL",
    "FieldNullSource",
    "NullSource",
    "TypeNull",
    "field_renders_none",
    "imported_name_null",
    "member_null",
    "reference_adds_null",
    "reference_null",
    "rendered_nullability",
    "takes_member_null",
    "written_annotation_null",
]

NullSource: TypeAlias = Literal["own", "reference", "member"]
FieldNullSource: TypeAlias = Literal["reference", "nullable", "type-has-null", "fall-back"]


@final
@record
class TypeNull:
    """Where the None of one data type comes from; only the generation store's operations write it.

    A type without a record holds `NO_TYPE_NULL`.
    """

    named: bool | None = None
    """Whether the type admits the null of the schema it named, recorded each time a merge points the type at an
    equal model (``GenerationStore.redirect_reference_users``); None while it names the model built for that schema."""
    target: bool = False
    """The type's own None is the one the schema it references admits (``GenerationStore.mark_target_null``)."""
    reference_written: bool = False
    """``is_optional`` holds the None of the nullable model the type names, written before rendering."""
    rendered: bool = False
    """Render preparation wrote into ``is_optional`` the None the type hint renders (``write_rendered_null``)."""
    held_dropped: bool = False
    """``--omit-redundant-reference-null`` dropped the None the type restates of the model it names."""
    stripped: bool = False
    """A msgspec field stripped the type's None from a copy of its type, which writes None itself."""


NO_TYPE_NULL: Final = TypeNull()
_TYPING: Final = frozenset({"typing", "typing_extensions"})


def reference_null(*, named: bool | None, names_nullable: bool) -> bool:
    """Return whether a reference admits the None of the schema it names.

    ``names_nullable`` tells that the model the type names now admits null and is not a type alias; ``named`` is
    `TypeNull.named`. A reference whose schema rejects null keeps rejecting it after its model is merged into an equal
    one that admits null (#5410). Row ``legacy-merged-reference-null``: a reference whose schema admits null takes the
    null of the model it names after the merge, so it loses the None when that model rejects null (#4816, #5411).
    """
    return names_nullable and named is not False


def reference_adds_null(*, null: TypeNull, names_nullable: bool) -> bool:
    """Return whether the hint of a type adds the None of the schema it references (``rendered_nullability``).

    It does not after a msgspec field stripped the type's None or ``--omit-redundant-reference-null`` dropped it.
    """
    return not (null.stripped or null.held_dropped) and reference_null(named=null.named, names_nullable=names_nullable)


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


def imported_name_null(*, module: str | None, name: str) -> bool:
    """Return whether a name imported from ``module`` accepts None: ``Any`` under any alias does."""
    return name == "Any" and module in _TYPING


def written_annotation_null(expression: PythonTypeExpr) -> bool:
    """Return whether an annotation the document writes (``x-python-type``) accepts None, by its names' imports.

    None, object, Any under any alias, a union with such a member, and a `Literal` with a None value do. The model
    path never asks, so it renders no None from this row.
    """
    from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
        PythonTypeBoundName,
        PythonTypeName,
        PythonTypeSubscript,
        PythonTypeUnion,
    )

    none = PythonTypeName("None")
    null = expression in {none, PythonTypeName("object")}
    match expression:
        case PythonTypeUnion():
            null = any(map(written_annotation_null, expression.items))
        case PythonTypeBoundName():
            null = imported_name_null(module=expression.import_from, name=expression.import_name)
        case PythonTypeSubscript() if (
            isinstance(base := expression.base, PythonTypeBoundName)
            and base.import_name == "Literal"
            and base.import_from in _TYPING
        ):
            null = none in expression.arguments
        case _:
            pass
    return null
