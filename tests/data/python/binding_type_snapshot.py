"""Serialize finite projected type values for independent E2E fixture comparisons."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datamodel_code_generator._generation_contract import SymbolId
    from datamodel_code_generator.model.binding_fields import FieldOwnershipProjection


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
