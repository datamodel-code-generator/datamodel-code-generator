"""The JSON text of the command line's machine-readable outputs and of the JSON Schemas that describe them.

Also the keys every structured output starts with and the JSON Schemas ``--output-format-json-schema`` prints. A leaf
of the standard library: the payload modules, the registry listings, the remote lock, the argument parser and the argv
fast paths read it without loading pydantic.
"""

from __future__ import annotations

import json
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, NamedTuple, TypeAlias, TypedDict

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

JSON_SCHEMA_DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"


class OutputHead(TypedDict):
    """The keys a structured JSON output starts with, before its ``kind``: the payload version and format."""

    version: Literal[1]
    format: Literal["json"]


OUTPUT_HEAD: Final[OutputHead] = {"version": 1, "format": "json"}

JsonSchemaPath: TypeAlias = Literal["argv", "parsed", "main"]


class _JsonSchemaSource(NamedTuple):
    module: str
    function: str
    path: JsonSchemaPath


_JSON_SCHEMAS: Final[Mapping[str, _JsonSchemaSource]] = MappingProxyType({
    "config": _JsonSchemaSource("json_config", "json_config_json_schema", "argv"),
    "generate-prompt": _JsonSchemaSource("prompt", "generate_prompt_json_schema", "parsed"),
    "generation": _JsonSchemaSource("_structured_output", "generation_output_json_schema", "argv"),
    "model-metadata": _JsonSchemaSource("model_metadata", "model_metadata_json_schema", "argv"),
    "recommend-options": _JsonSchemaSource("_structured_output", "recommend_options_json_schema", "main"),
    "structured-output": _JsonSchemaSource("_structured_output", "structured_output_json_schema", "argv"),
})
JSON_SCHEMA_NAMES: Final = tuple(_JSON_SCHEMAS)

JsonStyle: TypeAlias = Literal["ordered", "sorted", "sorted-ascii"]
_STYLES: Final[Mapping[JsonStyle, tuple[bool, bool]]] = MappingProxyType({
    "ordered": (False, False),
    "sorted": (False, True),
    "sorted-ascii": (True, True),
})


def dump_json(value: object, style: JsonStyle) -> str:
    """Return the JSON text an output writes for ``value``: indented by two spaces, in the style it was released with.

    ``ordered`` keeps the key order and non-ASCII text (the generation, check, batch, command and recommendation
    payloads, model metadata), ``sorted`` sorts the keys and keeps non-ASCII text (the prompt payload and its schema,
    the remote lock), ``sorted-ascii`` sorts the keys and escapes non-ASCII text (the registry and preset listings, the
    config schema).
    """
    ensure_ascii, sort_keys = _STYLES[style]
    return json.dumps(value, ensure_ascii=ensure_ascii, indent=2, sort_keys=sort_keys)


def write_streamed_json(
    write: Callable[[str], object], document: Mapping[str, object], key: str, items: Iterable[object], style: JsonStyle
) -> None:
    """Write ``dump_json({**document, key: [*items]}, style)`` and a newline, dumping one item of the array at a time.

    For a document whose array is not held in memory: the jobs of a batch run, read back from their spool, and the
    resources of the remote lock. ``key`` is a key of the document's top level, so each item is two levels deep.
    """
    array = f"\n  {dump_json(key, style)}: ["
    head, _, tail = dump_json({**document, key: []}, style).partition(f"{array}]")
    write(f"{head}{array}")
    closing = "]"
    for index, item in enumerate(items):
        write(f"{',' if index else ''}\n" + "\n".join(f"    {line}" for line in dump_json(item, style).splitlines()))
        closing = "\n  ]"
    write(f"{closing}{tail}\n")


def schema_document(schema: Mapping[str, object], style: JsonStyle) -> str:
    """Return the JSON text of the JSON Schema of an output: ``$schema`` names draft 2020-12, then the schema's keys."""
    return dump_json({"$schema": JSON_SCHEMA_DRAFT_2020_12, **schema}, style)


def json_schema_path(name: str) -> JsonSchemaPath | None:
    """Return where the command line prints the JSON Schema ``name`` of ``--output-format-json-schema``, None for none.

    ``argv`` before the argument parser loads when the request is the whole command, ``parsed`` once the parser has read
    the command, before ``--version`` and the skill options, ``main`` only where the command handles those options.
    """
    return None if (source := _JSON_SCHEMAS.get(name)) is None else source.path


def json_schema_text(name: str) -> str:
    """Return the JSON Schema ``--output-format-json-schema`` prints for ``name``, importing only the module with it."""
    from importlib import import_module  # noqa: PLC0415

    source = _JSON_SCHEMAS[name]
    return getattr(import_module(f"datamodel_code_generator.{source.module}"), source.function)()


__all__ = [
    "JSON_SCHEMA_DRAFT_2020_12",
    "JSON_SCHEMA_NAMES",
    "OUTPUT_HEAD",
    "JsonSchemaPath",
    "JsonStyle",
    "OutputHead",
    "dump_json",
    "json_schema_path",
    "json_schema_text",
    "schema_document",
    "write_streamed_json",
]
