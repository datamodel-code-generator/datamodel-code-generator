"""Serialize finite projected type values for independent E2E fixture comparisons."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal


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
