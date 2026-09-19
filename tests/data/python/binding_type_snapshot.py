"""Serialize finite projected type values for independent E2E fixture comparisons."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datamodel_code_generator._generation_contract import SymbolId
    from datamodel_code_generator.model.binding_fields import FieldOwnershipProjection
    from datamodel_code_generator.parser.base import Result
    from datamodel_code_generator.parser.openapi_contract import ContractOpenAPIParser


def type_snapshot(value: object) -> object:
    """Keep node kinds and literal kinds distinct without rendering model annotations."""
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "node": type(value).__name__,
            **{field.name: type_snapshot(getattr(value, field.name)) for field in fields(value)},
        }
    if isinstance(value, tuple):
        return [type_snapshot(item) for item in value]
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, bytes):
        return {"bytes": list(value)}
    return value


def ownership_snapshot(projection: FieldOwnershipProjection, names: dict[SymbolId, str]) -> dict[str, object]:
    """Present stable declaration names while keeping field positions and overrides."""
    if projection.value is None:
        return {"reason": projection.reason}
    return {
        "consumer": names[projection.value.consumer],
        "fields": [
            {
                "owner": names[field.slot.symbol],
                "name": field.slot.name,
                "index": field.slot.index,
                "wire_name": field.wire_name,
            }
            for field in projection.value.fields
        ],
        "overrides": [
            {
                "key": override.key,
                "original": names[override.original.symbol],
                "replacement": names[override.replacement.symbol],
            }
            for override in projection.value.overrides
        ],
    }


def field_source_snapshot(parser: ContractOpenAPIParser) -> dict[str, object]:
    """Present actual copied field origins and their original default producers."""
    sources = parser._resolve_field_sources()
    result: dict[str, object] = {}
    for model in parser.results:
        model_fields: dict[str | None, object] = {}
        for field in model.fields:
            identity = parser.binding_ledger.identity(field)
            original_ids = sources.get(identity, (identity,))
            model_fields[field.name] = {
                "locations": [
                    origin.location.pointer
                    for source in original_ids
                    if (observation := parser.field_origins.get(source)) is not None
                    for origin in observation.origins
                ],
                "defaults": [
                    construction.default_policy.has_default
                    for source in original_ids
                    if (construction := parser.field_constructions.get(source)) is not None
                    and construction.default_policy is not None
                ],
            }
        result[model.name] = model_fields
    return result


def inherited_default_snapshot(parser: ContractOpenAPIParser) -> dict[str, object]:
    """Expose scoped override producers reachable from each final copied field."""
    sources = parser._resolve_field_sources()
    result: dict[str, object] = {}
    for model in parser.results:
        model_fields: dict[str | None, object] = {}
        for field in model.fields:
            identity = parser.binding_ledger.identity(field)
            observations = [
                observation.resolution
                for source in sources.get(identity, (identity,))
                if (observation := parser.inherited_defaults.get(source)) is not None
            ]
            if observations:
                model_fields[field.name] = {
                    "scopes": [observation.class_name for observation in observations],
                    "schema_defaults": [observation.had_default for observation in observations],
                    "producers": [observation.producer for observation in observations],
                    "selected": [observation.result[0] for observation in observations],
                    "field_default": field.default,
                }
        if model_fields:
            result[model.name] = model_fields
    return result


def module_output_snapshot(
    parser: ContractOpenAPIParser, results: str | dict[tuple[str, ...], Result]
) -> list[dict[str, object]]:
    """Keep actual result identity separate from generated model names."""
    return [
        {
            "module": list(output.module),
            "models": [model.name for model in output.models],
            "actual_return": output.result.body is results
            if isinstance(results, str)
            else any(result is output.result for result in results.values()),
            "global_imports": output.imports[0] is parser.imports,
        }
        for output in parser.module_outputs
    ]
