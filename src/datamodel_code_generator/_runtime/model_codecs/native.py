"""Encode and decode through Pydantic v2 or msgspec, importing the selected backend on first use."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, Generic, cast

from typing_extensions import TypeVar

if TYPE_CHECKING:
    from collections.abc import Mapping

    from msgspec.json import Decoder
    from pydantic import TypeAdapter

T = TypeVar("T")

_ADAPTERS: Final[dict[int, tuple[object, TypeAdapter[Any]]]] = {}
_DECODERS: Final[dict[int, tuple[object, Decoder[Any]]]] = {}


def _adapter(type_: object) -> TypeAdapter[Any]:
    """Return the cached TypeAdapter of a type expression, building it on first use."""
    if (cached := _ADAPTERS.get(id(type_))) is None:
        from pydantic import TypeAdapter  # ruff: ignore[import-outside-top-level] - Only the selected backend is installed.

        adapter: TypeAdapter[Any] = TypeAdapter(type_)
        _ADAPTERS[id(type_)] = cached = (type_, adapter)
    return cached[1]


def _decoder(type_: object) -> Decoder[Any]:
    """Return the cached msgspec JSON decoder of a type expression, building it on first use."""
    if (cached := _DECODERS.get(id(type_))) is None:
        from msgspec.json import Decoder  # ruff: ignore[import-outside-top-level] - Only the selected backend is installed.

        decoder: Decoder[Any] = Decoder(type_)
        _DECODERS[id(type_)] = cached = (type_, decoder)
    return cached[1]


class PydanticCodec(Generic[T]):
    """Use Pydantic's own JSON validation and serialization, including aliases and fields-set tracking."""

    __slots__ = ("type",)

    def __init__(self, type_: object) -> None:
        """Keep the generated type; its TypeAdapter is built on first use and shared through the module cache."""
        self.type = type_

    def decode(self, content: bytes) -> T:
        """Validate JSON directly with the acquired model type."""
        return cast("T", _adapter(self.type).validate_json(content))

    def convert(self, value: object) -> T:
        """Construct from a parsed parameter or non-JSON body through Pydantic."""
        return cast("T", _adapter(self.type).validate_python(value))

    def assemble(self, fields: Mapping[str, object]) -> T:
        """Construct the native model from field arguments addressed by their wire aliases."""
        return self.convert(dict(fields))

    def encode(self, value: T) -> bytes:
        """Serialize in JSON mode with aliases and only explicitly set fields."""
        from pydantic import BaseModel  # ruff: ignore[import-outside-top-level] - Only the selected backend is installed.

        if isinstance(value, BaseModel):
            return value.model_dump_json(by_alias=True, exclude_unset=True).encode()
        return _adapter(self.type).dump_json(value, by_alias=True, exclude_unset=True)

    def dump(self, value: T) -> object:
        """Return JSON builtins through the same native serialization rules."""
        from pydantic import BaseModel  # ruff: ignore[import-outside-top-level] - Only the selected backend is installed.

        if isinstance(value, BaseModel):
            return value.model_dump(mode="json", by_alias=True, exclude_unset=True)
        return _adapter(self.type).dump_python(value, mode="json", by_alias=True, exclude_unset=True)

    @property
    def errors(self) -> tuple[type[Exception], ...]:
        """Backend validation and serialization exceptions."""
        from pydantic import ValidationError  # ruff: ignore[import-outside-top-level] - Only the selected backend is installed.
        from pydantic_core import PydanticSerializationError  # ruff: ignore[import-outside-top-level] - Imported with Pydantic.

        return ValidationError, PydanticSerializationError

    @staticmethod
    def malformed(error: Exception) -> bool:
        """Distinguish invalid JSON bytes from a JSON value the native model refuses."""
        from pydantic import ValidationError  # ruff: ignore[import-outside-top-level] - Only the selected backend is installed.

        return isinstance(error, ValidationError) and all(item["type"] == "json_invalid" for item in error.errors())


class PydanticDataclassCodec(PydanticCodec[T]):
    """Serialize Pydantic dataclasses without fields-set tracking, which their backend does not provide."""

    __slots__ = ()

    def encode(self, value: T) -> bytes:
        """Serialize every field with the backend's JSON alias rules."""
        return _adapter(self.type).dump_json(value, by_alias=True)

    def dump(self, value: T) -> object:
        """Return JSON builtins including defaults, as Pydantic dataclasses serialize them."""
        return _adapter(self.type).dump_python(value, mode="json", by_alias=True)


class MsgspecCodec(Generic[T]):
    """Decode and encode through msgspec; renames, tags and defaults belong to the Struct."""

    __slots__ = ("type",)

    def __init__(self, type_: object) -> None:
        """Keep the generated type; its JSON decoder is built on first use and shared through the module cache."""
        self.type = type_

    def decode(self, content: bytes) -> T:
        """Decode JSON through the cached msgspec decoder of the type."""
        return cast("T", _decoder(self.type).decode(content))

    def convert(self, value: object) -> T:
        """Construct from a parsed parameter or non-JSON body with native lax conversion."""
        from msgspec import convert  # ruff: ignore[import-outside-top-level] - Only the selected backend is installed.

        return cast("T", convert(value, type=self.type, strict=False))

    def assemble(self, fields: Mapping[str, object]) -> T:
        """Construct a Struct from the supplied field arguments and its own defaults."""
        return self.convert(dict(fields))

    @staticmethod
    def encode(value: T) -> bytes:
        """Serialize through msgspec's native JSON encoder."""
        from msgspec.json import encode  # ruff: ignore[import-outside-top-level] - Only the selected backend is installed.

        return encode(value)

    @staticmethod
    def dump(value: T) -> object:
        """Return msgspec's builtin representation of the native value."""
        from msgspec import to_builtins  # ruff: ignore[import-outside-top-level] - Only the selected backend is installed.

        return to_builtins(value)

    @property
    def errors(self) -> tuple[type[Exception], ...]:
        """The backend's decoding and encoding exceptions."""
        from msgspec import DecodeError, EncodeError  # ruff: ignore[import-outside-top-level] - Only the selected backend is installed.

        return DecodeError, EncodeError

    @staticmethod
    def malformed(error: Exception) -> bool:
        """Distinguish JSON syntax failures from native value validation failures."""
        from msgspec import DecodeError, ValidationError  # ruff: ignore[import-outside-top-level] - Only the selected backend is installed.

        return isinstance(error, DecodeError) and not isinstance(error, ValidationError)
