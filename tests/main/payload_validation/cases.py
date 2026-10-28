"""Discover runnable schema-derived payload validation cases."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from datamodel_code_generator import load_yaml
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, OPEN_API_DATA_PATH

from .constants import (
    EXCLUDED_CASES,
    EXCLUDED_FILES,
    JSON_SCHEMA_KEYS,
    PAYLOAD_BACKEND_EXTRA_ARGS_BY_CASE_ID,
    PAYLOAD_CLASS_NAME,
    SCHEMA_FILE_SUFFIXES,
)
from .models import SchemaCase
from .schema import (
    _apply_component_discriminator_payload_constraints,
    _apply_discriminator_payload_constraints,
    _has_dotted_openapi_component_name,
    _normalize_schema_dialect,
    _openapi_components,
    _referenced_openapi_components,
    _schema_exclusion_reason,
    _to_json_schema,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _load_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text) if path.suffix == ".json" else load_yaml(text)
    if not isinstance(data, dict):
        msg = f"{path} did not contain an object"
        raise TypeError(msg)
    return data


def _is_openapi_document(data: dict[str, Any]) -> bool:
    return isinstance(data.get("openapi"), str) or isinstance(data.get("swagger"), str)


def _looks_like_json_schema(data: dict[str, Any]) -> bool:
    return not _is_openapi_document(data) and bool(JSON_SCHEMA_KEYS & data.keys())


def _iter_schema_files(base_path: Path) -> Iterator[Path]:
    for path in sorted(base_path.rglob("*")):
        if path.suffix in SCHEMA_FILE_SUFFIXES:
            yield path


def _relative_case_path(path: Path) -> str:
    for base_name, base_path in (("jsonschema", JSON_SCHEMA_DATA_PATH), ("openapi", OPEN_API_DATA_PATH)):
        if path.is_relative_to(base_path):
            return f"{base_name}/{path.relative_to(base_path).as_posix()}"
    return path.as_posix()


def _iter_openapi_json_body_schemas(document: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    match document.get("paths"):
        case dict() as paths:
            for path_name, path_item in paths.items():
                if not isinstance(path_item, dict):
                    continue
                for method, operation in path_item.items():
                    if method.startswith("x-") or not isinstance(operation, dict):
                        continue
                    if isinstance(request_body := operation.get("requestBody"), dict):
                        yield from _iter_media_type_schemas(f"paths.{path_name}.{method}.requestBody", request_body)
                    if isinstance(responses := operation.get("responses"), dict):
                        for status_code, response in responses.items():
                            if isinstance(response, dict):
                                yield from _iter_media_type_schemas(
                                    f"paths.{path_name}.{method}.responses.{status_code}",
                                    response,
                                )


def _iter_media_type_schemas(prefix: str, container: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    match container.get("content"):
        case dict() as content:
            for media_type, media_type_object in content.items():
                if (
                    media_type == "application/json"
                    and isinstance(media_type_object, dict)
                    and isinstance(schema := media_type_object.get("schema"), dict)
                ):
                    yield f"{prefix}.content.application/json", schema


def _iter_schema_documents(base_path: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    for path in _iter_schema_files(base_path):
        if _relative_case_path(path) not in EXCLUDED_FILES:
            yield path, _load_mapping(path)


def _make_jsonschema_case(path: Path, schema: dict[str, Any]) -> SchemaCase:
    case_id = _relative_case_path(path)
    normalized_schema = _normalize_schema_dialect(schema)
    return SchemaCase(
        id=case_id,
        input_file_type="jsonschema",
        source_path=path,
        source_schema=deepcopy(normalized_schema),
        codegen_schema=deepcopy(normalized_schema),
        temp_input_suffix=path.suffix,
        backend_extra_args=PAYLOAD_BACKEND_EXTRA_ARGS_BY_CASE_ID.get(case_id, {}),
    )


def _make_openapi_case(path: Path, document: dict[str, Any], candidate_name: str, schema: dict[str, Any]) -> SchemaCase:
    components = _openapi_components(document)
    referenced_components = _referenced_openapi_components(schema, components)
    json_schema = {
        **_to_json_schema(schema),
        "components": {"schemas": _to_json_schema(referenced_components)},
    }
    _apply_discriminator_payload_constraints(json_schema, schema)
    _apply_component_discriminator_payload_constraints(json_schema)
    codegen_schema = {
        "openapi": document.get("openapi", "3.0.0"),
        "info": (
            document.get("info") if isinstance(document.get("info"), dict) else {"title": "Payload", "version": "1"}
        ),
        "paths": {},
        "components": {
            "schemas": {
                PAYLOAD_CLASS_NAME: schema,
                **referenced_components,
            }
        },
    }
    case_id = f"{_relative_case_path(path)}::{candidate_name}"
    return SchemaCase(
        id=case_id,
        input_file_type="openapi",
        source_path=path,
        source_schema=json_schema,
        codegen_schema=codegen_schema,
        temp_input_suffix=".yaml",
        backend_extra_args=PAYLOAD_BACKEND_EXTRA_ARGS_BY_CASE_ID.get(case_id, {}),
    )


def _schema_is_supported(schema: dict[str, Any], *, is_openapi: bool = False) -> bool:
    return _schema_exclusion_reason(schema, is_openapi=is_openapi) is None


def _case_is_supported(case: SchemaCase) -> bool:
    return _schema_is_supported(case.source_schema, is_openapi=case.input_file_type == "openapi")


def _iter_openapi_schema_candidates(document: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for name, schema in _openapi_components(document).items():
        if isinstance(schema, dict):
            yield f"components.schemas.{name}", schema
    yield from _iter_openapi_json_body_schemas(document)


def _iter_jsonschema_case_candidates() -> Iterator[SchemaCase]:
    for path, schema in _iter_schema_documents(JSON_SCHEMA_DATA_PATH):
        if _looks_like_json_schema(schema) and _schema_is_supported(schema):
            yield _make_jsonschema_case(path, schema)


def _iter_openapi_case_candidates() -> Iterator[SchemaCase]:
    for path, document in _iter_schema_documents(OPEN_API_DATA_PATH):
        if not _is_openapi_document(document) or _has_dotted_openapi_component_name(document):
            continue
        for name, schema in _iter_openapi_schema_candidates(document):
            if not _schema_is_supported(schema, is_openapi=True):
                continue
            case = _make_openapi_case(path, document, name, schema)
            if _case_is_supported(case):
                yield case


def _iter_case_candidates() -> Iterator[SchemaCase]:
    yield from _iter_jsonschema_case_candidates()
    yield from _iter_openapi_case_candidates()


def _iter_cases() -> Iterator[SchemaCase]:
    for case in _iter_case_candidates():
        if case.id not in EXCLUDED_CASES:
            yield case


def discover_unaccounted_files(cases: list[SchemaCase]) -> list[str]:
    """Return schema fixture files that are runnable but neither tested nor excluded."""
    accounted = {_relative_case_path(case.source_path) for case in cases} | set(EXCLUDED_FILES)
    excluded_case_files = {case_id.split("::", 1)[0] for case_id in EXCLUDED_CASES}
    accounted.update(excluded_case_files)
    return sorted({
        case_path
        for case in _iter_case_candidates()
        if (case_path := _relative_case_path(case.source_path)) not in accounted
    })


SCHEMA_CASES = list(_iter_cases())
