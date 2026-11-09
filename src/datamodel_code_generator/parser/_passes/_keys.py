"""Constants the passes and ``parser/base.py`` share, in a module without imports so importing them closes no cycle.

``raw_schema_default`` reads the schema default a field keeps under ``_RAW_SCHEMA_DEFAULT_KEY``.
"""

from __future__ import annotations

from typing import Any, Final

from typing_extensions import Sentinel

__all__ = [
    "_DEFERRED_INHERITED_CLASS_KEY",
    "_DEFERRED_INHERITED_FIELD_KEY",
    "_DEFERRED_INHERITED_TYPE_KEY",
    "_DERIVED_FROM_KEY",
    "_RAW_SCHEMA_DEFAULT_KEY",
    "_RAW_SCHEMA_DEFAULT_UNDEFINED",
    "_SITE_CONSTRAINTS_KEY",
    "_TYPE_LIST_UNIQUE_ITEMS_KEY",
    "_UNIQUE_ITEMS_SET_KEY",
    "declares_raw_schema_default",
    "declares_type_list_unique_items",
    "is_unique_items_set",
    "raw_schema_default",
    "site_constraints",
    "with_type_list_unique_items",
    "with_unique_items_set",
]

_DEFERRED_INHERITED_CLASS_KEY: Final = "_deferred_inherited_class"
_DEFERRED_INHERITED_FIELD_KEY: Final = "_deferred_inherited_field"
_DEFERRED_INHERITED_TYPE_KEY: Final = "_deferred_inherited_type"
_DERIVED_FROM_KEY: Final = "_derived_from"
"""The Reference of the one ``$ref`` target a site model is built from with the site's other parts."""
_RAW_SCHEMA_DEFAULT_KEY: Final = "_raw_schema_default"
_RAW_SCHEMA_DEFAULT_UNDEFINED: Final = Sentinel("_RAW_SCHEMA_DEFAULT_UNDEFINED")
_SITE_CONSTRAINTS_KEY: Final = "_site_constraints"
"""The constraint keywords an allOf site adds to the ``$ref`` target its type names (``site_constraints``)."""
_TYPE_LIST_UNIQUE_ITEMS_KEY: Final = "_type_list_unique_items"
_UNIQUE_ITEMS_SET_KEY: Final = "_unique_items_set"


def raw_schema_default(field: object) -> tuple[Any, bool] | None:
    """Return the ``(default, has_default)`` the schema property of a field declares, None for a field that keeps none.

    A field keeps it where a run has default value overrides and a property builds the field, or a copy takes it;
    ``default`` is None where the property declares none (``_RAW_SCHEMA_DEFAULT_UNDEFINED``).
    """
    if _RAW_SCHEMA_DEFAULT_KEY not in (values := field.__dict__):
        return None
    if (default := values[_RAW_SCHEMA_DEFAULT_KEY]) is _RAW_SCHEMA_DEFAULT_UNDEFINED:
        return None, False
    return default, True


def site_constraints(data_type: object) -> dict[str, Any]:
    """Return the constraint keywords that the allOf site a type is built for adds to the target the type names.

    The parse records them on the type of the site, typed by its first ``$ref`` alone; root-model collapse carries them
    to the type it inlines in its place. Empty for every other type.
    """
    return data_type.__dict__.get(_SITE_CONSTRAINTS_KEY, {})


def declares_raw_schema_default(field: object) -> bool:
    """Return whether a field keeps a schema default its property declares (``raw_schema_default``)."""
    return (raw := raw_schema_default(field)) is not None and raw[1]


def with_type_list_unique_items(field: Any, *, declares: bool = True) -> Any:
    """Record on ``field``, where ``declares``, that its ``uniqueItems`` is the one a type list declares; return it."""
    if declares:
        field.__dict__[_TYPE_LIST_UNIQUE_ITEMS_KEY] = True
    return field


def declares_type_list_unique_items(field: object) -> bool:
    """Return whether the ``uniqueItems`` of a field is the one its schema declares beside a type list.

    The array member of ``type: [array, string]`` reads it since 0.89.0 (#6139); 0.88.0 took no keyword there. A copy
    of the field keeps the record.
    """
    return _TYPE_LIST_UNIQUE_ITEMS_KEY in field.__dict__


def with_unique_items_set(data_type: Any, *, wrote: bool = True) -> Any:
    """Record on ``data_type`` that --use-unique-items-as-set wrote it as a set from a uniqueItems list; return it.

    ``wrote=False`` removes the record, from the list copy that such a set turns back into.
    """
    if wrote:
        data_type.__dict__[_UNIQUE_ITEMS_SET_KEY] = True
    else:
        del data_type.__dict__[_UNIQUE_ITEMS_SET_KEY]
    return data_type


def is_unique_items_set(data_type: object) -> bool:
    """Return whether --use-unique-items-as-set wrote ``data_type`` as a set from a uniqueItems list.

    A copy of the node keeps the record, such as the copy of a root type that collapse inlines.
    """
    return _UNIQUE_ITEMS_SET_KEY in data_type.__dict__
