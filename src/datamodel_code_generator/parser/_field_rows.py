"""Which configured field options each kind of field the parsers build takes.

``Parser._build_field`` builds every field a parser writes: the object fields of the schema parsers and GraphQL, the
required-only fields, the appended discriminator tags, the root fields of root models and the members of enums. A
field takes every option of ``Parser._field_option_kwargs`` except those its row leaves out, so a new option reaches
every kind of field unless a row names it. The rows keep the options each builder passed before they shared one: the
``legacy-*`` rows leave out options whose absence the generated code shows (see "Legacy rows" in
``docs/architecture.md``); the other rows leave out options that the field kind does not write, which custom templates
can still read on the raw field.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias

from typing_extensions import TypedDict

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "FIELD_OPTIONS_LEFT_OUT",
    "FIELD_ROWS_BUILT_WITHOUT_VALIDATION",
    "FIELD_ROWS_WITHOUT_VALIDATION_ALIASES",
    "FieldAliasRow",
    "FieldCopyFacts",
    "FieldFacts",
    "FieldIdentity",
    "FieldRow",
    "uses_default_with_required",
]

FieldRow: TypeAlias = Literal[
    "property",
    "legacy-graphql-field",
    "legacy-boolean-property",
    "legacy-alias-option-only",
    "typename",
    "array-root",
    "root",
    "enum-member",
    "graphql-enum-member",
    "bare",
]
"""The kind of a field, by the configured options it takes.

* ``property``: a property, an OpenAPI parameter, or a field built without a schema; every option.
* ``legacy-graphql-field``: a GraphQL field.
* ``legacy-boolean-property``: a property whose schema is ``true`` or ``false``.
* ``legacy-alias-option-only``: a required name without a property and no inherited field, and the tag field the
  discriminator pass appends to a variant.
* ``typename``: the ``__typename`` field of a GraphQL type.
* ``array-root``: the root field of an array root model.
* ``root``: the root field of any other root model.
* ``enum-member``, ``graphql-enum-member``: a member of an enum class.
* ``bare``: the root field of a root model that only names another type, a GraphQL enum alias and a GraphQL union
  member.
"""


FieldAliasRow: TypeAlias = Literal["required-copy", "final-class", "rename"]
"""What a field built earlier takes with a new name in ``Parser._take_field_aliases``, beside its alias.

* ``required-copy``: the copy of an inherited field that a required name without a property takes: also the name as
  its original name, the validation aliases, the serialization alias and ``--use-serialization-alias``, as a field
  built for the name would.
* ``final-class``: a field of a class whose final name differs from the one it was built under: also the validation
  aliases; it keeps the serialization alias it took unless it took none or ``--serialization-aliases`` names the field
  under the final class.
* ``rename``: a field renamed to keep its wire name (GraphQL typename slots): also the serialization alias of the new
  name; it keeps its validation aliases.
"""


class FieldIdentity(TypedDict, total=False):
    """The names and aliases a field takes, the attributes ``GenerationStore.set_field_identity`` writes.

    A field takes exactly the keys given and keeps the others: a copy takes all of them from the placeholder it
    replaces when it is made (``parser/_field_copies.py:_copy_data_model_field``), a field built earlier takes those its
    ``FieldAliasRow`` names through the store.
    """

    name: str | None
    original_name: str | None
    alias: str | None
    validation_aliases: list[str] | None
    serialization_alias: str | None
    use_serialization_alias: bool


class FieldCopyFacts(TypedDict, total=False):
    """The values a copy of a field is made with besides its names and aliases (``FieldIdentity``).

    A copy takes them when it is made (``parser/_field_copies.py:_copy_data_model_field``) and keeps the source's other
    values; a copy given ``extras`` or ``default`` owns those instead of a copy of the source's.
    """

    required: bool
    nullable: bool | None
    type_has_null: bool | None
    constraints: Any
    extras: dict[str, Any]
    read_only: bool
    write_only: bool
    default: Any
    has_default: bool
    use_default_with_required: bool


class FieldFacts(TypedDict, total=False):
    """The values of one field that its builder passes: its own facts, and the options it sets itself.

    A field is built with exactly the keys its builder passes (they are its ``model_fields_set``); ``alias``, the
    output alias or the list of validation aliases, is passed as the alias and validation aliases it splits into.
    """

    name: str | None
    alias: str | list[str] | None
    default: Any
    required: bool
    nullable: bool | None
    constraints: Any
    extras: dict[str, Any]
    has_default: bool
    original_name: str | None
    type_has_null: bool | None
    read_only: bool
    write_only: bool
    use_default_with_required: bool
    use_one_literal_as_default: bool
    use_annotated: bool
    use_field_description: bool


_DESCRIPTIONS: Final = frozenset({
    "use_field_description",
    "use_field_description_example",
    "use_inline_field_description",
})
_ROOT_LEAVES_OUT: Final = frozenset({
    "use_default_kwarg",
    "use_frozen_field",
    "use_serialization_alias",
    "use_default_factory_for_optional_nested_models",
})

FIELD_OPTIONS_LEFT_OUT: Final[Mapping[FieldRow, frozenset[str]]] = {
    "property": frozenset(),
    "legacy-graphql-field": frozenset({"use_frozen_field", "use_default_factory_for_optional_nested_models"}),
    "legacy-boolean-property": frozenset({
        "use_serialize_as_any",
        "use_default_kwarg",
        "use_frozen_field",
        "use_default_factory_for_optional_nested_models",
    }),
    "legacy-alias-option-only": _DESCRIPTIONS
    | {
        "strip_default_none",
        "order_constructor_fields_by_written_defaults",
        "use_annotated",
        "use_serialize_as_any",
        "use_default_kwarg",
        "use_frozen_field",
        "use_default_factory_for_optional_nested_models",
    },
    "typename": _DESCRIPTIONS
    | {
        "strip_default_none",
        "order_constructor_fields_by_written_defaults",
        "use_serialize_as_any",
        "use_frozen_field",
        "use_default_factory_for_optional_nested_models",
    },
    "array-root": _ROOT_LEAVES_OUT,
    "root": _ROOT_LEAVES_OUT | {"use_serialize_as_any"},
    "enum-member": _ROOT_LEAVES_OUT | {"use_serialize_as_any", "use_annotated"},
    "graphql-enum-member": _ROOT_LEAVES_OUT | _DESCRIPTIONS | {"use_serialize_as_any", "use_annotated"},
    "bare": _ROOT_LEAVES_OUT
    | _DESCRIPTIONS
    | {"strip_default_none", "order_constructor_fields_by_written_defaults", "use_serialize_as_any", "use_annotated"},
}
"""The options of ``Parser._field_option_kwargs`` each row leaves out."""

FIELD_ROWS_BUILT_WITHOUT_VALIDATION: Final[frozenset[FieldRow]] = frozenset({"property"})
"""The rows built with the validation-free constructor the field class declares (``PARSER_CONSTRUCTOR``), if any; the
other rows build through the field class."""

FIELD_ROWS_WITHOUT_VALIDATION_ALIASES: Final[frozenset[FieldRow]] = frozenset({"typename"})
"""The rows whose ``alias`` is one output alias: they leave ``validation_aliases`` unset."""


def uses_default_with_required(*, required: bool, has_default: bool, use_default: bool) -> bool:
    """Return whether a field keeps its default beside ``required`` (``use_default_with_required``).

    ``--use-default`` (``use_default``) keeps the default of a required field that has one, wherever a parser builds
    the field or makes it required later.
    """
    return required and use_default and has_default
