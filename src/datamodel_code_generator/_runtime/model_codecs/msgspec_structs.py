"""Encode and decode msgspec Structs through msgspec's own JSON decoder and encoder."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, cast

from msgspec import DecodeError, EncodeError, ValidationError, convert, to_builtins
from msgspec.json import Decoder
from msgspec.json import encode as encode_json
from typing_extensions import TypeVar

from .shared import ValidatingCodec, identity_cached

if TYPE_CHECKING:
    from typing_extensions import TypeForm

T = TypeVar("T")

_DECODERS: Final[dict[int, tuple[TypeForm[Any], Decoder[Any]]]] = {}
_ERRORS: Final = (DecodeError, EncodeError)


def _decoder(type_: TypeForm[Any]) -> Decoder[Any]:
    """Return the cached msgspec JSON decoder of a type expression, building it on first use."""
    return identity_cached(_DECODERS, type_, Decoder)


class MsgspecCodec(ValidatingCodec[T]):
    """Decode and encode through msgspec; renames, tags and defaults belong to the Struct."""

    __slots__ = ("type",)

    def __init__(self, type_: TypeForm[Any]) -> None:
        """Keep the generated type; its JSON decoder is built on first use and shared through the module cache."""
        self.type = type_

    def decode(self, content: bytes) -> T:
        """Decode JSON through the cached msgspec decoder of the type."""
        return cast("T", _decoder(self.type).decode(content))

    def convert(self, value: object) -> T:
        """Construct from a parsed parameter or non-JSON body with native lax conversion."""
        return cast("T", convert(value, type=self.type, strict=False))

    @staticmethod
    def encode(value: T) -> bytes:
        """Serialize through msgspec's native JSON encoder."""
        return encode_json(value)

    @staticmethod
    def dump(value: T) -> object:
        """Return msgspec's builtin representation of the native value."""
        return to_builtins(value)

    @property
    def errors(self) -> tuple[type[Exception], ...]:
        """The backend's decoding and encoding exceptions."""
        return _ERRORS

    @staticmethod
    def malformed(error: Exception) -> bool:
        """Distinguish JSON syntax failures from native value validation failures."""
        return isinstance(error, DecodeError) and not isinstance(error, ValidationError)
