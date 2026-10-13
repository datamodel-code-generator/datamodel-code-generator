"""Encode and decode Pydantic v2 dataclasses, which serialize every field."""

from __future__ import annotations

from typing_extensions import TypeVar

from .pydantic_adapters import AdapterCodec, adapter

T = TypeVar("T")


class PydanticDataclassCodec(AdapterCodec[T]):
    """Serialize Pydantic dataclasses without fields-set tracking, which their backend does not provide."""

    __slots__ = ()

    def encode(self, value: T) -> bytes:
        """Serialize every field with the backend's JSON alias rules."""
        return adapter(self.type).dump_json(value, by_alias=True)

    def dump(self, value: T) -> object:
        """Return JSON builtins including defaults, as Pydantic dataclasses serialize them."""
        return adapter(self.type).dump_python(value, mode="json", by_alias=True)
