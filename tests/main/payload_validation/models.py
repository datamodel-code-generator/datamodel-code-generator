"""Shared models for schema-derived payload validation tests."""

from __future__ import annotations

from collections import UserDict
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

JsonPath = tuple[str | int, ...]


class PayloadBackend(Enum):
    """Generated output backend exercised by payload validation tests."""

    PYDANTIC_V2 = "pydantic_v2"
    PYDANTIC_V2_DATACLASS = "pydantic_v2_dataclass"
    MSGSPEC = "msgspec"
    DATACLASSES = "dataclasses"

    @property
    def output_model_type(self) -> str:
        """Return the datamodel-codegen CLI output model type."""
        match self:
            case PayloadBackend.PYDANTIC_V2:
                return "pydantic_v2.BaseModel"
            case PayloadBackend.PYDANTIC_V2_DATACLASS:
                return "pydantic_v2.dataclass"
            case PayloadBackend.MSGSPEC:
                return "msgspec.Struct"
            case PayloadBackend.DATACLASSES:
                return "dataclasses.dataclass"
            case _:
                msg = f"Unsupported payload backend: {self!r}"
                raise ValueError(msg)

    @property
    def supports_rejection_oracle(self) -> bool:
        """Return whether the backend has runtime validation for invalid payloads."""
        match self:
            case PayloadBackend.PYDANTIC_V2 | PayloadBackend.PYDANTIC_V2_DATACLASS | PayloadBackend.MSGSPEC:
                return True
            case PayloadBackend.DATACLASSES:
                return False
            case _:
                msg = f"Unsupported payload backend: {self!r}"
                raise ValueError(msg)


@dataclass(frozen=True)
class SchemaCase:
    """A schema candidate that should generate a model named Payload."""

    id: str
    input_file_type: str
    source_path: Path
    source_schema: dict[str, Any] = field(repr=False)
    codegen_schema: dict[str, Any] = field(repr=False)
    temp_input_suffix: str
    backend_extra_args: Mapping[PayloadBackend, tuple[str, ...]] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class InvalidPayloadMutation:
    """A schema-invalid payload derived from a valid seed payload."""

    constraint: str
    path: JsonPath
    payload: Any = field(repr=False)
    reason: str


class GeneratedModelCache(UserDict[str, Any]):
    """Cache wrapper with compact Hypothesis failure representation."""

    def __repr__(self) -> str:
        """Return a compact representation for Hypothesis failure output."""
        return "GeneratedModelCache(...)"
