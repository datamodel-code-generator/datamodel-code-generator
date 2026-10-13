"""Encode and decode Pydantic v2 dataclasses, which leave out only the fields that hold MISSING."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from typing_extensions import TypeVar

from .pydantic_adapters import AdapterCodec, adapter

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import TypeAdapter
    from pydantic_core import SchemaSerializer

T = TypeVar("T")


class PydanticDataclassCodec(AdapterCodec[T]):
    """Serialize Pydantic dataclasses, which track no fields set: an unset field holds MISSING, which they leave out.

    `serializer` returns the serializer that writes a value from its type's adapter, for a type whose adapter leaves
    out what its values hold, such as the extra members of a dataclass that allows them.
    """

    __slots__ = ("serializer",)

    def __init__(self, type_: object, serializer: Callable[[TypeAdapter[Any]], SchemaSerializer] | None = None) -> None:
        """Keep the generated type and the serializer that writes its values, if its adapter's own does not."""
        super().__init__(type_)
        self.serializer = serializer

    def encode(self, value: T) -> bytes:
        """Serialize every field but those holding MISSING, with the backend's JSON alias rules."""
        if (serializer := self.serializer) is None:
            return adapter(self.type).dump_json(value, by_alias=True)
        return serializer(adapter(self.type)).to_json(value, by_alias=True)

    def dump(self, value: T) -> object:
        """Return JSON builtins including defaults but not MISSING, as Pydantic dataclasses serialize them."""
        if (serializer := self.serializer) is None:
            return adapter(self.type).dump_python(value, mode="json", by_alias=True)
        return serializer(adapter(self.type)).to_python(value, mode="json", by_alias=True)
