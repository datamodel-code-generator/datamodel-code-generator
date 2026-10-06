"""Build the codec declaration records of the generation targets from JSON fixture data."""

from __future__ import annotations

from datamodel_code_generator import api_types
from datamodel_code_generator.api_types import (
    CodecAdapterRegistration,
    OperationRef,
    SchemaDirectionalUse,
    SchemaRef,
    TypeUseRef,
)


def _tuples(data: dict[str, object]) -> dict[str, object]:
    return {key: tuple(value) if isinstance(value, list) else value for key, value in data.items()}


def _selector(data: dict[str, object]) -> TypeUseRef | SchemaDirectionalUse:
    if "schema" in data:
        return SchemaDirectionalUse(schema=SchemaRef(**data["schema"]), direction=data["direction"])
    operation = data["operation"]
    return TypeUseRef(**{**data, "operation": OperationRef(**operation) if isinstance(operation, dict) else operation})


def _registration(data: dict[str, object]) -> CodecAdapterRegistration:
    record = dict(data["capabilities"])
    return CodecAdapterRegistration(
        kind=data["kind"],
        name=data["name"],
        import_ref=data["import_ref"],
        dependencies=tuple(data.get("dependencies", ())),
        python_requires=data.get("python_requires", ">=3.10"),
        capabilities=getattr(api_types, record.pop("record"))(**_tuples(record)),
        uses=tuple(_selector(use) for use in data["uses"]),
        api_version=data.get("api_version", 1),
    )


def declaration(kind: str, data: dict[str, object]) -> object:
    """Build one declaration record of a kind: an adapter registration, or a use selector."""
    return _registration(data) if kind == "adapters" else _selector(data)
