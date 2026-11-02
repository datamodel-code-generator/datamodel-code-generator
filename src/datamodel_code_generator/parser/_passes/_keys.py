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
    "declares_raw_schema_default",
    "raw_schema_default",
]

_DEFERRED_INHERITED_CLASS_KEY: Final = "_deferred_inherited_class"
_DEFERRED_INHERITED_FIELD_KEY: Final = "_deferred_inherited_field"
_DEFERRED_INHERITED_TYPE_KEY: Final = "_deferred_inherited_type"
_DERIVED_FROM_KEY: Final = "_derived_from"
"""The Reference of the one ``$ref`` target a site model is built from with the site's other parts."""
_RAW_SCHEMA_DEFAULT_KEY: Final = "_raw_schema_default"
_RAW_SCHEMA_DEFAULT_UNDEFINED: Final = Sentinel("_RAW_SCHEMA_DEFAULT_UNDEFINED")


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


def declares_raw_schema_default(field: object) -> bool:
    """Return whether a field keeps a schema default its property declares (``raw_schema_default``)."""
    return (raw := raw_schema_default(field)) is not None and raw[1]
