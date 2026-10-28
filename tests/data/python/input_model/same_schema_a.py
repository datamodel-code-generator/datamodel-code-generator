"""Distinct runtime types whose JSON schemas are equal."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, field_validator


class Node(BaseModel):
    """A value with a runtime-only prefix constraint."""

    value: str

    @field_validator("value")
    @classmethod
    def validate_prefix(cls, value: str) -> str:
        """Keep the module-specific validator through runtime type reuse."""
        if not value.startswith("a"):
            msg = "expected a prefix"
            raise ValueError(msg)
        return value


class Recursive(Node):
    """A recursively nested value."""

    child: Recursive | None = None


class Marker(Enum):
    """A native enum identity."""

    VALUE = "ok"


class OriginalA(Node):
    """A runtime class available through a module-level alias."""


Alias = OriginalA
Alias.__name__ = "Alias"
