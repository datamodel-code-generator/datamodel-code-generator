"""The JSON text of the command line's machine-readable outputs and of the JSON Schemas that describe them.

A leaf of the standard library: the payload modules, the registry listings, the remote lock and the argv fast paths
read it without loading pydantic.
"""

from __future__ import annotations

import json
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Mapping

JSON_SCHEMA_DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"

JsonStyle: TypeAlias = Literal["ordered", "sorted", "sorted-ascii"]
_STYLES: Final[Mapping[JsonStyle, tuple[bool, bool]]] = MappingProxyType({
    "ordered": (False, False),
    "sorted": (False, True),
    "sorted-ascii": (True, True),
})


def dump_json(value: object, style: JsonStyle, *, depth: int = 0) -> str:
    """Return the JSON text an output writes for ``value``: indented by two spaces, in the style it was released with.

    ``ordered`` keeps the key order and non-ASCII text (the generation, check, batch, command and recommendation
    payloads, model metadata), ``sorted`` sorts the keys and keeps non-ASCII text (the prompt payload and its schema,
    the remote lock), ``sorted-ascii`` sorts the keys and escapes non-ASCII text (the registry and preset listings, the
    config schema). ``depth`` indents every line as a value that many levels into a document: a writer that streams the
    items of a document's array passes 2.
    """
    ensure_ascii, sort_keys = _STYLES[style]
    text = json.dumps(value, ensure_ascii=ensure_ascii, indent=2, sort_keys=sort_keys)
    if not depth:
        return text
    prefix = "  " * depth
    return "\n".join(f"{prefix}{line}" for line in text.splitlines())


def schema_document(schema: Mapping[str, object], style: JsonStyle) -> str:
    """Return the JSON text of the JSON Schema of an output: ``$schema`` names draft 2020-12, then the schema's keys."""
    return dump_json({"$schema": JSON_SCHEMA_DRAFT_2020_12, **schema}, style)


__all__ = ["JSON_SCHEMA_DRAFT_2020_12", "JsonStyle", "dump_json", "schema_document"]
