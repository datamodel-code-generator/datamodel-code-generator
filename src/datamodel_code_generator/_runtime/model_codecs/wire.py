"""JSON-domain copies and pointer utilities used by generation and lexical wire planning."""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal
from math import isfinite
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, overload

from typing_extensions import TypeAliasType, TypeIs

if TYPE_CHECKING:
    from typing import TypeAlias

    JSONScalar: TypeAlias = bool | str | int | float | Decimal | None
    JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | tuple["JSONValue", ...] | dict[str, "JSONValue"]
    PlainJSON: TypeAlias = bool | int | float | str | list["PlainJSON"] | dict[str, "PlainJSON"] | None
    WireValue: TypeAlias = PlainJSON | JSONScalar | tuple["WireValue", ...] | Mapping[str, "WireValue"]
else:
    JSONScalar = TypeAliasType("JSONScalar", "bool | str | int | float | Decimal | None")
    JSONValue = TypeAliasType(
        "JSONValue", "JSONScalar | list[JSONValue] | tuple[JSONValue, ...] | dict[str, JSONValue]"
    )
    WireValue = TypeAliasType(
        "WireValue", "JSONScalar | list[WireValue] | tuple[WireValue, ...] | Mapping[str, WireValue]"
    )

_SURROGATE: Final = re.compile(r"[\ud800-\udfff]")


def escape_pointer_token(token: str | int) -> str:
    """Escape one RFC 6901 reference token."""
    return token.replace("~", "~0").replace("/", "~1") if isinstance(token, str) else str(token)


def pointer_tokens(pointer: str) -> list[str]:
    """Split an RFC 6901 pointer into its unescaped reference tokens."""
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")] if pointer else []


@overload
def freeze_wire(value: JSONValue) -> WireValue: ...
@overload
def freeze_wire(value: WireValue) -> WireValue: ...
def freeze_wire(value: JSONValue | WireValue) -> WireValue:
    """Copy a JSON-domain value into an immutable snapshot, preserving order."""
    return _freeze(value, set())


def checked_wire(value: object) -> WireValue:
    """Validate a value of unknown static type, such as a handler's return, and freeze it as a wire snapshot."""
    return _freeze(value, set())


def thaw_wire(value: WireValue) -> JSONValue:
    """Copy a wire snapshot into independent mutable lists and dictionaries."""
    return _thaw(value, set())


def checked_text(value: str) -> str:
    """Reject strings that cannot be encoded as UTF-8 wire text."""
    if value.isascii() or _SURROGATE.search(value) is None:
        return value
    msg = "A wire string must not contain lone surrogates"
    raise ValueError(msg)


def checked_scalar(value: object) -> JSONScalar:
    """Return one exact JSON scalar, rejecting subclasses, non-finite numbers, and other objects."""
    match value:
        case None | bool():
            return value
        case str() if type(value) is str:
            return checked_text(value)
        case int() if type(value) is int:
            return value
        case float() if type(value) is float and isfinite(value):
            return value
        case Decimal() if type(value) is Decimal and value.is_finite():
            return value
        case float() | Decimal() if type(value) in {float, Decimal}:
            msg = "A wire number must be finite"
            raise ValueError(msg)
        case _:
            msg = f"{type(value).__name__} is not a JSON value"
            raise TypeError(msg)


def checked_key(key: object) -> str:
    """Return an object member name, requiring an exact encodable string."""
    if type(key) is not str:
        msg = "A wire object key must be a string"
        raise TypeError(msg)
    return checked_text(key)


def enter(value: object, active: set[int]) -> int:
    """Mark a container as being copied, rejecting reference cycles."""
    if (identity := id(value)) in active:
        msg = "A wire value must not contain cycles"
        raise ValueError(msg)
    active.add(identity)
    return identity


def _is_array(value: object) -> TypeIs[list[object] | tuple[object, ...]]:
    return isinstance(value, (list, tuple))


def _is_object(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _freeze(value: object, active: set[int]) -> WireValue:
    if _is_array(value):
        identity = enter(value, active)
        frozen = tuple(_freeze(item, active) for item in value)
        active.discard(identity)
        return frozen
    if _is_object(value):
        identity = enter(value, active)
        frozen_items = {checked_key(key): _freeze(item, active) for key, item in value.items()}
        active.discard(identity)
        return MappingProxyType(frozen_items)
    return checked_scalar(value)


def _thaw(value: JSONValue | WireValue, active: set[int]) -> JSONValue:
    if isinstance(value, (list, tuple)):
        identity = enter(value, active)
        thawed = [_thaw(item, active) for item in value]
        active.discard(identity)
        return thawed
    if isinstance(value, Mapping):
        identity = enter(value, active)
        thawed_items = {checked_key(key): _thaw(item, active) for key, item in value.items()}
        active.discard(identity)
        return thawed_items
    return checked_scalar(value)
