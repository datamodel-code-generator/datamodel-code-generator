"""Generated model metadata payload types."""

from __future__ import annotations

import json
from typing import Literal

from typing_extensions import TypedDict

JSON_SCHEMA_DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
MODEL_METADATA_SCHEMA_RESOURCE = "model_metadata.schema.json"


class ModelFieldMetadata(TypedDict, closed=True):
    """One generated model field entry in --emit-model-metadata output."""

    name: str
    alias: str
    original_name: str | None
    type: str
    required: bool


class GeneratedModelMetadata(TypedDict, closed=True):
    """One generated model entry in --emit-model-metadata output."""

    class_name: str
    name: str
    module: str
    source_ref: str
    source_path: list[str] | None
    title: str | None
    fields: list[ModelFieldMetadata]


class ModelMetadata(TypedDict, closed=True):
    """JSON payload emitted by --emit-model-metadata."""

    version: Literal[1]
    models: list[GeneratedModelMetadata]


_EMPTY_MODEL_METADATA: ModelMetadata = {"version": 1, "models": []}


def dump_model_metadata(metadata: ModelMetadata | None) -> str:
    """Serialize model metadata as pretty JSON."""
    payload = metadata if metadata is not None else _EMPTY_MODEL_METADATA
    return json.dumps(payload, indent=2, ensure_ascii=False)


def model_metadata_json_schema() -> str:
    """Return the static JSON Schema for --emit-model-metadata output."""
    from importlib.resources import files  # noqa: PLC0415

    return (
        files("datamodel_code_generator.resources")
        .joinpath(MODEL_METADATA_SCHEMA_RESOURCE)
        .read_text(encoding="utf-8")
        .rstrip("\n")
    )
