"""Constants the passes and ``parser/base.py`` share, in a module without imports so importing them closes no cycle."""

from __future__ import annotations

from typing import Final

from typing_extensions import Sentinel

__all__ = [
    "_DEFERRED_INHERITED_CLASS_KEY",
    "_DEFERRED_INHERITED_FIELD_KEY",
    "_DEFERRED_INHERITED_TYPE_KEY",
    "_DERIVED_FROM_KEY",
    "_MODEL_MODULE_PREFIX",
    "_RAW_SCHEMA_DEFAULT_KEY",
    "_RAW_SCHEMA_DEFAULT_UNDEFINED",
]

_MODEL_MODULE_PREFIX: Final = "datamodel_code_generator.model."
_DEFERRED_INHERITED_CLASS_KEY: Final = "_deferred_inherited_class"
_DEFERRED_INHERITED_FIELD_KEY: Final = "_deferred_inherited_field"
_DEFERRED_INHERITED_TYPE_KEY: Final = "_deferred_inherited_type"
_DERIVED_FROM_KEY: Final = "_derived_from"
"""The Reference of the one ``$ref`` target a site model is built from with the site's other parts."""
_RAW_SCHEMA_DEFAULT_KEY: Final = "_raw_schema_default"
_RAW_SCHEMA_DEFAULT_UNDEFINED: Final = Sentinel("_RAW_SCHEMA_DEFAULT_UNDEFINED")
