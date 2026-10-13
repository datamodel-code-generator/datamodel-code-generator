# Derived from stdlib.py.jinja2 by datamodel-code-generator's scripts/build_stdlib_codecs.py; do not edit directly.
"""Rename keys, build nested dataclasses and convert JSON-incompatible leaves without validating."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import TYPE_CHECKING, Final, Generic, Literal, TypeAlias, cast
from uuid import UUID

from typing_extensions import TypeVar

from .media import plain

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

T = TypeVar("T")
JSONKind: TypeAlias = Literal["object", "array", "string", "number", "boolean"]
Shape: TypeAlias = "type | Items | Fixed | Values | Choice | None"
_CONTAINERS: Final[dict[str, Callable[[Iterable[object]], object]]] = {
    "list": list,
    "set": set,
    "frozenset": frozenset,
    "tuple": tuple,
}


@dataclass(frozen=True, slots=True)
class Field:
    """One field's wire name, Python name, and conversions needed by its nested type."""

    wire: str
    name: str
    shape: Shape = None
    omit_none: bool = False


@dataclass(frozen=True, slots=True)
class Model:
    """The static field map of a dataclass."""

    fields: tuple[Field, ...]


@dataclass(frozen=True, slots=True)
class Items:
    """Construct a sequence or set, converting only its elements."""

    item: Shape
    kind: Literal["list", "set", "frozenset", "tuple"] = "list"


@dataclass(frozen=True, slots=True)
class Fixed:
    """Construct a tuple with one conversion per position."""

    items: tuple[Shape, ...]


@dataclass(frozen=True, slots=True)
class Values:
    """Convert the values of a string-keyed mapping."""

    value: Shape


@dataclass(frozen=True, slots=True)
class Choice:
    """Select a conversion by JSON kind or the union's declared discriminator."""

    kinds: tuple[tuple[JSONKind, Shape], ...] = ()
    tag: str | None = None
    tags: tuple[tuple[str, type], ...] = ()


def _kind(value: object) -> JSONKind:
    kind: JSONKind
    if isinstance(value, bool):
        kind = "boolean"
    elif isinstance(value, (int, float)):
        kind = "number"
    elif isinstance(value, str):
        kind = "string"
    elif isinstance(value, (list, tuple, set, frozenset)):
        kind = "array"
    else:
        kind = "object"
    return kind


class DataclassCodec(Generic[T]):
    """Use static names and discriminator facts to construct dataclasses without validation."""

    __slots__ = ("models", "shape")

    @property
    def errors(self) -> tuple[type[Exception], ...]:
        """The construction and leaf-conversion errors of the stdlib mapper."""
        return (
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
            IndexError,
            RecursionError,
            InvalidOperation,
        )

    def __init__(self, shape: Shape, models: Mapping[type, Model]) -> None:
        """Keep the conversion shape and acquired model field maps."""
        self.shape, self.models = shape, models

    def decode(self, content: bytes) -> T:
        """Parse ordinary JSON, then construct its native containers and leaves."""
        return self.convert(json.loads(content))

    def convert(self, value: object) -> T:
        """Apply construction and leaf conversion without checking schema or field constraints."""
        return cast("T", self._load(value, self.shape))

    def text(self, value: object) -> T:
        """Construct from header, form, or part text; nothing validates later, so text that is no JSON literal fails."""
        return cast("T", self._load(plain(value, strict=True), self.shape))

    def assemble(self, fields: Mapping[str, object]) -> object:
        """Construct a model from already-native field values addressed by their wire names."""
        shape = dict(self.shape.kinds)["object"] if isinstance(self.shape, Choice) else self.shape
        type_ = cast("type", shape)
        model = self.models[type_]
        return type_(**{field.name: fields[field.wire] for field in model.fields if field.wire in fields})

    def encode(self, value: T) -> bytes:
        """Serialize compact UTF-8 JSON, refusing non-finite JSON numbers."""
        return json.dumps(self.dump(value), separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()

    def dump(self, value: T) -> object:
        """Rename present keys and serialize values through their native leaf representations."""
        return self._dump(value, self.shape)

    @staticmethod
    def malformed(error: Exception) -> bool:
        """Return whether received bytes could not be parsed as JSON."""
        return isinstance(error, (json.JSONDecodeError, UnicodeDecodeError, RecursionError))

    def _load(self, value: object, shape: Shape) -> object:  # ruff: ignore[too-many-return-statements]
        if value is None or shape is None:
            return value
        if isinstance(shape, Choice):
            kind = _kind(value)
            selected = dict(shape.kinds).get(kind)
            if kind == "object" and shape.tag is not None:
                tag = cast("Mapping[str, object]", value)[shape.tag]
                selected = dict(shape.tags)[cast("str", tag)]
            return self._load(value, selected)
        if isinstance(shape, Items):
            return _CONTAINERS[shape.kind](self._load(item, shape.item) for item in cast("list[object]", value))
        if isinstance(shape, Fixed):
            return tuple(
                self._load(item, shape.items[index] if index < len(shape.items) else None)
                for index, item in enumerate(cast("list[object]", value))
            )
        if isinstance(shape, Values):
            return {key: self._load(item, shape.value) for key, item in cast("Mapping[str, object]", value).items()}
        if (model := self.models.get(shape)) is not None:
            mapping = cast("Mapping[str, object]", value)
            entries = {
                field.name: self._load(mapping[field.wire], field.shape)
                for field in model.fields
                if field.wire in mapping
            }
            return shape(**entries)
        if shape in {datetime, date, time}:
            return cast("type[datetime]", shape).fromisoformat(cast("str", value))
        if shape is UUID:
            return UUID(cast("str", value))
        if shape is Decimal:
            return Decimal(str(value))
        if shape is bytes:
            return base64.b64decode(cast("str", value), validate=True)
        return shape(value)

    def _dump(self, value: object, shape: Shape = None) -> object:  # ruff: ignore[too-many-return-statements]
        if value is None:
            return None
        if isinstance(shape, Choice):
            selected = dict(shape.kinds).get(_kind(value))
            if isinstance(selected, (Items, Fixed, Values)):
                shape = selected
        if (model := self.models.get(type(value))) is not None:
            return {
                field.wire: self._dump(item, field.shape)
                for field in model.fields
                if (item := getattr(value, field.name)) is not None or not field.omit_none
            }
        if isinstance(value, Mapping):
            mapping = cast("Mapping[str, object]", value)
            value_shape = shape.value if isinstance(shape, Values) else None
            return {
                str(key.value if isinstance(key, Enum) else key): self._dump(item, value_shape)
                for key, item in mapping.items()
            }
        if isinstance(value, (list, tuple, set, frozenset)):
            return [
                self._dump(
                    item,
                    shape.items[index]
                    if isinstance(shape, Fixed) and index < len(shape.items)
                    else shape.item
                    if isinstance(shape, Items)
                    else None,
                )
                for index, item in enumerate(cast("Iterable[object]", value))
            ]
        if isinstance(value, Enum):
            return self._dump(value.value)
        if isinstance(value, (datetime, date, time)):
            return value.isoformat()
        if isinstance(value, (UUID, Decimal)):
            return str(value)
        if isinstance(value, bytes):
            return base64.b64encode(value).decode("ascii")
        return value
