"""User class keys: which model or field an option keyed by a class name, ``Class.field`` or a field name names.

Every option the user keys by a class name resolves its keys through the functions here, at the pass its row names
and against the names models have at that pass (keys most specific first):

* ``--model-name-map``: schema path, ``#fragment``, generated class name, original name; at L1 ``ModelResolver.add``
  under the name a model is created under, and at L2 ``settle_yielding_names`` under the final name of a yielding
  model.
* ``--base-class-map``: class name (``mapped_base_class``); at L1 model construction (``Parser._resolve_base_class``).
* ``--extra-template-data``: schema path, then class name (``template_data_keys``); at L1 model construction
  (``DataModel.__init__``).
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

Rows that keep today's output where the options disagree (each one waits for the option that resolves every key once,
after names are final):

* ``legacy-reparse-for-final-names`` (``ConstructionKeys``): the L1 rows read the name a model is created under, but a
  yielding name settles at L2. When a yielding name changed and a key of an L1 row names the created or the final
  name, ``__init__.py:_parse_generation`` parses the whole input again with the final names assigned. Its key list
  leaves out ``--type-overrides`` and ``--enum-field-as-literal-map``.
* ``type-overrides-first-dot``: the class part of a ``--type-overrides`` key is what precedes its first dot
  (``type_override_classes``), while the class part of the other ``Class.field`` keys is what precedes their last dot
  (``ConstructionKeys``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, TypeVar

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from collections.abc import Set as AbstractSet

__all__ = [
    "ConstructionKeys",
    "class_field_key",
    "field_keys",
    "first_given",
    "first_present",
    "mapped_base_class",
    "model_level_type_override",
    "template_data_keys",
    "type_override_classes",
]

_ValueT = TypeVar("_ValueT")

_SCOPE_SEPARATOR: Final = "."


def class_field_key(class_name: str, field_name: str | None) -> str:
    """Return the key of the field ``field_name`` of the class ``class_name`` (``Class.field``)."""
    return f"{class_name}{_SCOPE_SEPARATOR}{field_name}"


def field_keys(class_name: str | None, *field_names: str) -> tuple[str, ...]:
    """Return the keys of a field option, most specific first: each name under the class, then each name alone."""
    if class_name is None:
        return field_names
    return (*(class_field_key(class_name, field_name) for field_name in field_names), *field_names)


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


def template_data_keys(reference_path: str, class_name: str) -> tuple[str, str]:
    """Return the keys of the ``--extra-template-data`` entries of a model: its schema path, then its class name."""
    return reference_path, class_name


def model_level_type_override(key: str) -> bool:
    """Return whether a ``--type-overrides`` key names a model (no class part) rather than one field of a class."""
    return _SCOPE_SEPARATOR not in key


def type_override_classes(type_overrides: Iterable[str]) -> frozenset[str]:
    """Return the class names ``--type-overrides`` keys name, by the part before the first dot (row ``first-dot``)."""
    return frozenset(key.partition(_SCOPE_SEPARATOR)[0] for key in type_overrides)


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
            frozenset(key.rpartition(_SCOPE_SEPARATOR)[0] for keys in field_keyed for key in keys or ()),
        )

    def name_any(self, names: AbstractSet[str]) -> bool:
        """Return whether a key names one of ``names``, so the models of these names are built again."""
        return not names.isdisjoint(self.class_names) or not names.isdisjoint(self.field_scopes)
