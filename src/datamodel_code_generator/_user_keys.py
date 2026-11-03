"""User class keys: which model or field an option keyed by a class name, ``Class.field`` or a field name names.

Every option the user keys by a class name resolves its keys through the functions here, at the pass its row names
and against the names models have at that pass (keys most specific first):

* ``--model-name-map``: schema path, ``#fragment``, generated class name, original name; at L1 ``ModelResolver.add``
  under the name a model is created under, and at L2 ``settle_yielding_names`` under the final name of a yielding
  model.
* ``--base-class-map``: class name (``mapped_base_class``); at L1 model construction (``Parser._resolve_base_class``),
  under row ``predicted`` (the root models and the OpenAPI parameter and GraphQL models) or ``reference-name`` (the
  nullable enum root, the root models of a union's variants and the read-only/write-only models).
* ``--extra-template-data``: schema path, then class name (``template_data_keys``, row ``reference-name``); at L1
  model construction (``DataModel.__init__``); a GraphQL scalar reads its ``py_type`` under row ``original-name``.
* ``--validators``: class name, as the template data of that name; at L1 model construction
  (``model/output.py:prepare_output_model_config``).
* ``--aliases``: ``Class.field``, then ``field`` (``field_keys``, ``first_given``); at L1 field naming, under the name
  a model is created under or its original name, and again under the allocated name
  (``Parser._apply_final_class_field_aliases``).
* ``--serialization-aliases``: ``Class.original``, ``Class.name``, ``original``, ``name`` (``field_keys``); at L1
  field naming.
* ``--default-value-overrides``: ``Class.field``, then ``field`` (``field_keys``); at L1 field defaults and inherited
  defaults in the derived class.
* ``--enum-field-as-literal-map``: field name only, no class part; at L1 enum parsing.
* ``--type-overrides``: class name (``model_level_type_override``) or ``Class.field`` (``class_field_key``); at L3
  reuse (``type_override_classes``), and at L5 ``remove_overridden_models``, ``apply_field_type_overrides`` and
  ``apply_base_class_type_overrides`` under the name a model has at L5.

Which of a model's names the class part of a key is compared with is the row of ``class_key`` that the place looking
the model up names. The field-keyed options read row ``predicted`` while allOf, the OpenAPI parameters, GraphQL and
Avro name fields before the class name is allocated, ``reference-name`` once it is (``parse_object``, the parameter
defaults and serialization aliases, the forward schemas of allOf, ``Parser._apply_final_class_field_aliases``),
``original-name`` for the explicit aliases of a field that recorded no class (``prepare_explicit_field_aliases``) and
``class-name`` after construction (the aliases of a reused tag field, whose serialization alias reads
``reference-name``, the tag field names and the Avro default overrides).

Rows that keep today's output where the options disagree (each one waits for the option that resolves every key once,
after names are final):

* ``legacy-reparse-for-final-names`` (``ConstructionKeys``): the L1 rows read the name a model is created under, but a
  yielding name settles at L2. When a yielding name changed and a key of an L1 row names the created or the final
  name, ``__init__.py:_parse_generation`` parses the whole input again with the final names assigned. Its key list
  leaves out ``--type-overrides`` and ``--enum-field-as-literal-map``.
* ``type-overrides-first-dot``: the class part of a ``--type-overrides`` key is what precedes its first dot
  (``type_override_classes``), while the class part of the other ``Class.field`` keys is what precedes their last dot
  (``ConstructionKeys``); ``scoped_key`` splits every key, by these rows and at every dot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, Protocol, TypeAlias, TypeVar

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from collections.abc import Set as AbstractSet

__all__ = [
    "ClassKeyRow",
    "ConstructionKeys",
    "ModelNames",
    "ScopedKeyRow",
    "class_field_key",
    "class_key",
    "field_keys",
    "first_given",
    "first_present",
    "mapped_base_class",
    "model_level_type_override",
    "scoped_key",
    "template_data_keys",
    "type_override_classes",
]

_ValueT = TypeVar("_ValueT")

_SCOPE_SEPARATOR: Final = "."
ScopedKeyRow: TypeAlias = Literal["first-dot", "legacy-last-dot", "every-dot"]
ClassKeyRow: TypeAlias = Literal["predicted", "reference-name", "original-name", "class-name"]


class ModelNames(Protocol):
    """The names of a model's reference (``reference.Reference``) that a class key reads."""

    path: str
    name: str
    original_name: str

    @property
    def short_name(self) -> str:
        """The class name: the last part of the dotted ``name``."""


def class_key(row: ClassKeyRow, model: ModelNames | str) -> str:
    """Return the name of a model that the class part of the keys of a class-keyed option is compared with, by row.

    Row ``predicted`` is the name a parser has for a model before the resolver allocates its class name: the name it
    adds the model under (the GraphQL type name, before its affixes), the allOf name before ``--use-title-as-name``,
    or a class name ``ModelResolver.get_class_name`` gives without allocating it; it is passed as ``model`` itself.
    The other rows read the reference ``model``: ``reference-name`` its name (the one it was created under while L1
    builds it), ``original-name`` the name it was added under, and ``class-name`` its class name without the module
    (``DataModel.class_name``). The rows differ where the resolver numbered, mapped or affixed a name, where a module
    prefixes it, and after a model was renamed.
    """
    if isinstance(model, str):
        return model
    match row:
        case "original-name":
            return model.original_name
        case "class-name":
            return model.short_name
    return model.name


def class_field_key(class_name: str, field_name: str | None) -> str:
    """Return the key of the field ``field_name`` of the class ``class_name`` (``Class.field``)."""
    return f"{class_name}{_SCOPE_SEPARATOR}{field_name}"


def field_keys(class_name: str | None, *field_names: str) -> tuple[str, ...]:
    """Return the keys of a field option, most specific first: each name under the class, then each name alone."""
    if class_name is None:
        return field_names
    return (*(class_field_key(class_name, field_name) for field_name in field_names), *field_names)


def scoped_key(key: str, *, row: ScopedKeyRow) -> tuple[tuple[str, str], ...]:
    """Return the (class part, field part) pairs that a ``Class.field`` key splits into at the dots the row reads.

    ``class_field_key`` joins names that may hold a dot, so a key names one pair at each of its dots. Row ``every-dot``
    gives them all, none for a key without a dot (``model_level_type_override``); the ``--aliases`` keys of a class
    after name allocation (``Parser._apply_final_class_field_aliases``) and those of a wire name in the targets
    (``_target_naming.py:TargetNames.aliased``) read it too. Row ``first-dot`` (``type_override_classes``) gives the
    pair at the first dot, ``(key, "")`` for a key without one, and row ``legacy-last-dot`` (``ConstructionKeys``) the
    pair at the last dot, ``("", key)`` for a key without one.
    """
    if row == "every-dot":
        return tuple(
            (key[:index], key[index + 1 :]) for index, character in enumerate(key) if character == _SCOPE_SEPARATOR
        )
    class_part, _, field_part = (
        key.partition(_SCOPE_SEPARATOR) if row == "first-dot" else key.rpartition(_SCOPE_SEPARATOR)
    )
    return ((class_part, field_part),)


def first_present(mapping: Mapping[str, _ValueT], keys: Iterable[str]) -> tuple[_ValueT | None, bool]:
    """Return the value of the first of ``keys`` that ``mapping`` holds and True, or None and False."""
    for key in keys:
        if key in mapping:
            return mapping[key], True
    return None, False


def first_given(mapping: Mapping[str, _ValueT], keys: Iterable[str]) -> _ValueT | None:
    """Return the value of the first of ``keys`` that ``mapping`` gives a string or a non-empty value."""
    return next((value for key in keys if isinstance(value := mapping.get(key), str) or value), None)


def mapped_base_class(base_class_map: Mapping[str, _ValueT] | None, class_name: str) -> tuple[_ValueT | None, bool]:
    """Return the ``--base-class-map`` value of ``class_name`` and True, or None and False."""
    return first_present(base_class_map or {}, (class_name,))


def template_data_keys(reference: ModelNames) -> tuple[str, str]:
    """Return the keys of the ``--extra-template-data`` entries of a model: its schema path, then its name.

    The name is row ``reference-name`` of ``class_key``; ``--validators`` entries join the template data of their key.
    """
    return reference.path, class_key("reference-name", reference)


def model_level_type_override(key: str) -> bool:
    """Return whether a ``--type-overrides`` key names a model (no class part) rather than one field of a class."""
    return not scoped_key(key, row="every-dot")


def type_override_classes(type_overrides: Iterable[str]) -> frozenset[str]:
    """Return the class names ``--type-overrides`` keys name, by the part before the first dot (row ``first-dot``)."""
    return frozenset(class_name for key in type_overrides for class_name, _ in scoped_key(key, row="first-dot"))


@dataclass(frozen=True, slots=True)
class ConstructionKeys:
    """The class names the L1 rows key, for row ``legacy-reparse-for-final-names``."""

    class_names: frozenset[str] = frozenset()
    """The keys of ``--base-class-map``, ``--extra-template-data`` and ``--validators``."""
    field_scopes: frozenset[str] = frozenset()
    """The class parts, before the last dot, of the ``--aliases``, ``--serialization-aliases`` and
    ``--default-value-overrides`` keys."""

    @classmethod
    def of(
        cls,
        class_keyed: Iterable[Iterable[str] | None],
        field_keyed: Iterable[Iterable[str] | None],
    ) -> ConstructionKeys:
        """Collect the class names that the class-keyed and the field-keyed options of L1 name."""
        return cls(
            frozenset(key for keys in class_keyed for key in keys or ()),
            frozenset(
                class_name
                for keys in field_keyed
                for key in keys or ()
                for class_name, _ in scoped_key(key, row="legacy-last-dot")
            ),
        )

    def name_any(self, names: AbstractSet[str]) -> bool:
        """Return whether a key names one of ``names``, so the models of these names are built again."""
        return not names.isdisjoint(self.class_names) or not names.isdisjoint(self.field_scopes)
