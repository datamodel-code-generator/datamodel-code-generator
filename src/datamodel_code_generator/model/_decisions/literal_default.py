"""Literal defaults: whether a field fixed to one value takes it as its default, stays required, and admits null.

A field is fixed to one value by its ``const``, by a type of one ``Literal`` under ``--use-one-literal-as-default``,
or as the tag field of a discriminated union variant. Every input is an immutable fact passed by keyword, so the
functions read nothing else. The adapters read the facts from the live objects and write the decision with
``model/base.py:_apply_literal_default``: ``model/base.py:DataModelFieldBase._process_const_as_literal`` (the
``process_const`` hook of the Pydantic, dataclass and TypedDict fields, at L1 and again when L5 collapse inlines a
root model's extras), ``model/msgspec.py:DataModelField.process_const``, and at L5 in ``parser/_passes/modules.py``
``set_one_literal_on_default`` and ``set_tag_literal_default``, for the tags fields hold (also those nested choices
give a union) and those ``apply_discriminator_type`` writes (with the default from
``_get_single_discriminator_default``). ``const_type`` gives the type of a const, for the field hook and for
``parser/jsonschema.py:JsonSchemaParser._get_const_data_type`` (L1), which returns it before the null a ``type`` list
or ``nullable`` (under ``--strict-nullable``) adds beside it.

The rows named ``legacy-*`` keep today's output where sources or backends disagree on a fixed value's default or
nullability; the fix of each (G12b, which also moves the decision after the facts it reads are final) deletes its row.
"""

from __future__ import annotations

from typing import Any, Literal, TypeAlias, final

from datamodel_code_generator._records import record
from datamodel_code_generator.model._decisions.enum_kind import is_literal_kind, literal_value_kind

__all__ = [
    "ConstType",
    "LiteralDefault",
    "LiteralDefaultRule",
    "LiteralSource",
    "const_type",
    "decide_literal_default",
]

ConstType: TypeAlias = Literal["literal", "none", "value"]
"""The type a const takes: a ``Literal`` of it, the None type, or the type of its value."""

LiteralSource: TypeAlias = Literal["const", "msgspec-const", "one-literal", "tag", "written-tag"]
"""What fixes a field to its value, and so which rows of ``decide_literal_default`` apply.

* ``const``: the field's ``const``, for outputs that write it as a ``Literal`` (Pydantic, dataclasses, TypedDict).
* ``msgspec-const``: the field's ``const``, for msgspec output.
* ``one-literal``: a type of one ``Literal`` under ``--use-one-literal-as-default``.
* ``tag``: a discriminator tag that its field already holds as its one ``Literal``.
* ``written-tag``: a discriminator tag whose type L5 writes on the field; the value is the default that type allows
  (``_get_single_discriminator_default``), or None when it allows none.
"""

LiteralDefaultRule: TypeAlias = Literal[
    "const-null",
    "const-optional",
    "const-default",
    "legacy-const-drops-null",
    "legacy-const-without-literal-not-null",
    "legacy-msgspec-const-not-null",
    "one-literal-optional",
    "one-literal-default",
    "one-literal-keeps-nullable",
    "legacy-one-literal-drops-type-null",
    "tag-optional-allowed",
    "tag-forced-optional",
    "tag-optional",
    "tag-kept",
    "tag-required",
]
"""The rows of ``decide_literal_default``.

* ``const-null``: a ``const: null`` field, whose type is None, is not nullable and takes no default.
* ``const-optional``: an optional const field without a default follows the optional path of its type: it keeps its
  nullability and takes no default, so an omitted field stays unset (#3434).
* ``const-default``: a required const field, or one with a default, is not nullable and takes the const as its
  default when it has none (which a required field does not render).
* ``legacy-const-drops-null``: the same for a field that the schema lets be null (``nullable`` under
  ``--strict-nullable`` or ``--use-missing-sentinel``, or a schema type admitting null), whose null it drops, while a
  field of the plain type keeps it (#4873).
* ``legacy-const-without-literal-not-null``: a const no ``Literal`` holds (a float, an array, an object) keeps the
  type of its value and is not nullable, also when optional, so an optional one writes a None default its type does
  not hold, where an optional ``Literal`` const keeps its nullability (``const-optional``).
* ``legacy-msgspec-const-not-null``: a msgspec const field is not nullable, whatever the schema declares, and takes
  no default from the const, where the other outputs keep an optional const's nullability.
* ``one-literal-optional``: an optional field keeps its default.
* ``one-literal-default``: a required field takes its one literal as its default, is no longer required and is not
  nullable.
* ``one-literal-keeps-nullable``: the same, but a field the schema declares nullable stays nullable.
* ``legacy-one-literal-drops-type-null``: the same as ``one-literal-default`` for a field whose type list admits null,
  whose null it drops, while the tag rows keep it.
* ``tag-optional-allowed``: the output takes an optional tag (it writes no ``discriminator`` argument), so the field
  keeps its default.
* ``tag-forced-optional``: under ``--force-optional``, the tag is the default of a field that is no longer required
  and not nullable, as an optional tag would add None or the ``MISSING`` sentinel to its type, which Pydantic rejects
  as a discriminator.
* ``tag-optional``: the same for an optional tag field that the schema does not let be null and whose nullability is
  not decided, or that is declared not nullable but would take the ``MISSING`` sentinel as its default.
* ``tag-kept``: the field keeps its default and nullability when the tag is required, when the schema lets it be
  null, or when it is declared not nullable and keeps its default.
* ``tag-required``: a tag field whose type L5 writes is required, unless ``--force-optional`` gives it its default
  (``tag-forced-optional``).
"""


@final
@record
class LiteralDefault:
    """What a field fixed to one value writes, and the row of ``LiteralDefaultRule`` it comes from.

    ``default`` is written when ``writes_default`` is set, ``required`` unless it is None, and ``nullable = False``
    when ``not_null`` is set; the field keeps the others.
    """

    rule: LiteralDefaultRule
    default: Any = None
    writes_default: bool = False
    required: bool | None = None
    not_null: bool = False


def const_type(value: object, /, *, bool_literals: bool = True) -> ConstType:
    """Return the type a const takes: a ``Literal`` where one holds it, the None type for null, else its value's type.

    A ``Literal`` holds the kinds ``is_literal_kind`` names; ``bool_literals`` tells that the output writes bool
    literals.
    """
    if (kind := literal_value_kind(value)) == "none":
        return "none"
    return "literal" if is_literal_kind(kind, bool_literals=bool_literals) else "value"


def decide_literal_default(  # ruff: ignore[too-many-arguments]
    source: LiteralSource,
    value: Any,
    /,
    *,
    required: bool,
    has_default: bool = False,
    default: Any = None,
    nullable: bool | None = None,
    type_has_null: bool | None = None,
    force_optional: bool = False,
    missing_sentinel: bool = False,
    discriminator_argument: bool = False,
) -> LiteralDefault:
    """Return what a field fixed to ``value`` writes (``LiteralSource`` and ``LiteralDefaultRule`` name the rows).

    It reads whether the field is ``required``, its default (``has_default``, ``default``), its declared
    ``nullable``, whether its schema type admits null (``type_has_null``) and, for tags, ``--force-optional``, whether
    the field would take the ``MISSING`` sentinel as its default (``missing_sentinel``) and whether the output writes a
    ``discriminator`` argument, which rejects an optional tag (``discriminator_argument``). A ``written-tag`` reads
    neither ``required`` nor the nullability, which its type replaces.
    """
    if source == "const":
        return _const_row(
            value, required=required, has_default=has_default, default=default, nullable=nullable, null=type_has_null
        )
    if source == "msgspec-const":
        return _MSGSPEC_CONST
    if source == "one-literal":
        return _one_literal_row(value, required=required, nullable=nullable, null=type_has_null)
    return _tag_row(
        source,
        value,
        required=required,
        has_default=has_default,
        nullable=nullable,
        null=type_has_null,
        force_optional=force_optional,
        missing_sentinel=missing_sentinel,
        discriminator_argument=discriminator_argument,
    )


def _const_row(  # ruff: ignore[too-many-arguments]
    value: Any, *, required: bool, has_default: bool, default: Any, nullable: bool | None, null: bool | None
) -> LiteralDefault:
    """Return the ``const`` rows of ``decide_literal_default``; ``null``: the schema type admits null."""
    match const_type(value):
        case "none":
            return _CONST_NULL
        case "value":
            return _CONST_WITHOUT_LITERAL
    if not (required or has_default or default is not None):
        return _CONST_OPTIONAL
    rule: LiteralDefaultRule = "legacy-const-drops-null" if nullable is True or null else "const-default"
    return LiteralDefault(rule, value, writes_default=default is None and not has_default, not_null=True)


def _one_literal_row(value: Any, *, required: bool, nullable: bool | None, null: bool | None) -> LiteralDefault:
    """Return the ``one-literal`` rows of ``decide_literal_default``; ``null``: the schema type admits null."""
    if not required:
        return _ONE_LITERAL_OPTIONAL
    if nullable is True:
        return LiteralDefault("one-literal-keeps-nullable", value, writes_default=True, required=False)
    rule: LiteralDefaultRule = "legacy-one-literal-drops-type-null" if null else "one-literal-default"
    return LiteralDefault(rule, value, writes_default=True, required=False, not_null=True)


def _tag_row(  # ruff: ignore[too-many-arguments]
    source: Literal["tag", "written-tag"],
    value: Any,
    *,
    required: bool,
    has_default: bool,
    nullable: bool | None,
    null: bool | None,
    force_optional: bool,
    missing_sentinel: bool,
    discriminator_argument: bool,
) -> LiteralDefault:
    """Return the ``tag`` and ``written-tag`` rows of ``decide_literal_default``; ``null``: the type admits null."""
    if source == "written-tag":
        if discriminator_argument and force_optional and value is not None:
            return _tag_forced_optional(value)
        return _TAG_REQUIRED
    if not discriminator_argument:
        return _TAG_OPTIONAL_ALLOWED
    if force_optional:
        return _tag_forced_optional(value)
    if required or null or not (nullable is None or (nullable is False and missing_sentinel and not has_default)):
        return _TAG_KEPT
    return LiteralDefault("tag-optional", value, writes_default=True, required=False, not_null=True)


def _tag_forced_optional(value: Any) -> LiteralDefault:
    """Return the row that makes a tag the default of a field ``--force-optional`` makes optional, not nullable."""
    return LiteralDefault("tag-forced-optional", value, writes_default=True, required=False, not_null=True)


_CONST_NULL = LiteralDefault("const-null", not_null=True)
_CONST_OPTIONAL = LiteralDefault("const-optional")
_CONST_WITHOUT_LITERAL = LiteralDefault("legacy-const-without-literal-not-null", not_null=True)
_MSGSPEC_CONST = LiteralDefault("legacy-msgspec-const-not-null", not_null=True)
_ONE_LITERAL_OPTIONAL = LiteralDefault("one-literal-optional")
_TAG_OPTIONAL_ALLOWED = LiteralDefault("tag-optional-allowed")
_TAG_KEPT = LiteralDefault("tag-kept")
_TAG_REQUIRED = LiteralDefault("tag-required", required=True)
