"""Serialize finite projected type values for independent E2E fixture comparisons."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from datamodel_code_generator._generation_contract import GeneratedEnumMember, GeneratedSymbolType

if TYPE_CHECKING:
    from datamodel_code_generator._generation_contract import GeneratedTypeContractBatch, SymbolId
    from datamodel_code_generator.model.binding_fields import FieldOwnershipProjection


def final_import_snapshot(batch: GeneratedTypeContractBatch) -> str:
    """Report explicit external import identities and bound expressions from frozen fields."""
    from datamodel_code_generator._generation_contract import BoundType
    from datamodel_code_generator._python_type_annotation import render_python_type_expr
    from datamodel_code_generator.imports import Import

    def imports(value: object) -> list[str]:
        if isinstance(value, Import):
            return [f"{value.from_}.{value.import_}" if value.from_ else value.import_]
        if isinstance(value, tuple):
            return [name for child in value for name in imports(child)]
        if is_dataclass(value) and not isinstance(value, type):
            return [name for field in fields(value) for name in imports(getattr(value, field.name))]
        return []

    wanted = {"moment", "external", "native", "bound_native", "handler", "record"}
    lines = []
    for field in batch.fields:
        if field.slot is None or field.model_facts is None or field.slot.name not in wanted:
            continue
        value = field.model_facts.type
        lines.append(f"{field.slot.name}: {','.join(sorted(set(imports(value))))}")
        if isinstance(value, BoundType):
            lines.append(f"  expression: {render_python_type_expr(value.binding.expression)}")
    return "\n".join(lines) + "\n"


def type_snapshot(value: object, names: dict[SymbolId, str] | None = None) -> object:
    """Keep node kinds and literal kinds distinct without rendering model annotations."""
    if names is not None and isinstance(value, GeneratedSymbolType):
        return {"node": "GeneratedSymbolType", "symbol": names[value.symbol]}
    if names is not None and isinstance(value, GeneratedEnumMember):
        return {"node": "GeneratedEnumMember", "symbol": names[value.symbol], "name": value.name}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "node": type(value).__name__,
            **{field.name: type_snapshot(getattr(value, field.name), names) for field in fields(value)},
        }
    if isinstance(value, tuple):
        return [type_snapshot(item, names) for item in value]
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
