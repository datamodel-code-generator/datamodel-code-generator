"""Constants the passes and ``parser/base.py`` share, in a module without imports so importing them closes no cycle."""

from __future__ import annotations

from typing import Final

__all__ = [
    "_DEFERRED_INHERITED_CLASS_KEY",
    "_DEFERRED_INHERITED_FIELD_KEY",
    "_DEFERRED_INHERITED_TYPE_KEY",
    "_MODEL_MODULE_PREFIX",
    "_RAW_SCHEMA_DEFAULT_KEY",
    "_RAW_SCHEMA_DEFAULT_UNDEFINED",
]

_MODEL_MODULE_PREFIX: Final = "datamodel_code_generator.model."
_DEFERRED_INHERITED_CLASS_KEY: Final = "_deferred_inherited_class"
_DEFERRED_INHERITED_FIELD_KEY: Final = "_deferred_inherited_field"
_DEFERRED_INHERITED_TYPE_KEY: Final = "_deferred_inherited_type"
_RAW_SCHEMA_DEFAULT_KEY: Final = "_raw_schema_default"
_RAW_SCHEMA_DEFAULT_UNDEFINED: Final = object()
