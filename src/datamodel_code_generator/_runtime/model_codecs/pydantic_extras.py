"""Serialize the extra members of Pydantic dataclasses, which Pydantic keeps on validation but leaves out of dumps."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any, Final, cast

from pydantic_core import SchemaSerializer, core_schema

if TYPE_CHECKING:
    from pydantic import SerializerFunctionWrapHandler, TypeAdapter

_SERIALIZERS: Final[dict[int, tuple[TypeAdapter[Any], SchemaSerializer]]] = {}


def extras_serializer(adapter: TypeAdapter[Any]) -> SchemaSerializer:
    """Return the serializer of an adapter's type that writes each dataclass's extra members after its fields.

    It is built on first use and kept with its adapter; a concurrent first use builds an equal one.
    """
    if (cached := _SERIALIZERS.get(id(adapter))) is None:
        schema = cast("core_schema.CoreSchema", _keeping(adapter.core_schema))
        _SERIALIZERS[id(adapter)] = cached = (adapter, SchemaSerializer(schema))
    return cached[1]


def extras_json(adapter: TypeAdapter[Any], value: object) -> bytes:
    """Write a value as JSON by its adapter's aliases without unset fields, and its dataclasses' extra members."""
    return extras_serializer(adapter).to_json(value, by_alias=True, exclude_unset=True)


def _keeping(schema: object) -> object:
    """Return a copy of a core schema whose dataclasses that allow extra members serialize them too.

    Each dataclass names its class as its generic origin, so pydantic-core builds it from this schema instead of
    reusing the serializer of its class, which writes no extra members of the dataclasses it holds.
    """
    if isinstance(schema, list):
        return [_keeping(item) for item in cast("list[object]", schema)]
    if not isinstance(schema, dict):
        return schema
    kept = {key: _keeping(value) for key, value in cast("dict[str, object]", schema).items()}
    if kept.get("type") != "dataclass":
        return kept
    kept.setdefault("generic_origin", kept["cls"])
    if (
        "serialization" not in kept
        and isinstance(config := kept.get("config"), dict)
        and cast("dict[str, object]", config).get("extra_fields_behavior") == "allow"
    ):
        fields = frozenset(cast("list[str]", kept["fields"]))
        kept["serialization"] = core_schema.wrap_serializer_function_ser_schema(partial(_with_extras, fields))
    return kept


def _with_extras(fields: frozenset[str], value: object, handler: SerializerFunctionWrapHandler) -> object:
    """Serialize a dataclass's fields, then the members it holds besides them."""
    dumped = handler(value)
    if extras := {name: item for name, item in getattr(value, "__dict__", {}).items() if name not in fields}:
        return {**dumped, **extras}
    return dumped
