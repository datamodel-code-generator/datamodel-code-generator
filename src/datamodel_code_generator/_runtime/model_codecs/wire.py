"""Immutable JSON-domain wire snapshots, independent mutable copies, and presence trees."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from types import MappingProxyType
from typing import Final, Literal

from typing_extensions import TypeAliasType

JSONScalar = TypeAliasType("JSONScalar", "bool | str | int | float | Decimal | None")
JSONValue = TypeAliasType("JSONValue", "JSONScalar | list[JSONValue] | tuple[JSONValue, ...] | dict[str, JSONValue]")
WireValue = TypeAliasType("WireValue", "JSONScalar | tuple[WireValue, ...] | Mapping[str, WireValue]")

_SURROGATE: Final = re.compile(r"[\ud800-\udfff]")


@dataclass(frozen=True, slots=True)
class PresenceTree:
    """Record every present wire node by RFC 6901 pointer, keeping container order."""

    pointer: str
    container: Literal["value", "array", "object"]
    members: tuple[tuple[str | int, PresenceTree], ...] = ()

    def child(self, key: str | int) -> PresenceTree | None:
        """Return the present member at an object key or array index, if any."""
        for member_key, member in self.members:
            if member_key == key and type(member_key) is type(key):
                return member
        return None

    def pointers(self) -> tuple[str, ...]:
        """Return every present pointer in document order, starting with this node."""
        pointers = [self.pointer]
        for _, member in self.members:
            pointers.extend(member.pointers())
        return tuple(pointers)


def escape_pointer_token(token: str | int) -> str:
    """Escape one RFC 6901 reference token."""
    return token.replace("~", "~0").replace("/", "~1") if isinstance(token, str) else str(token)


def freeze_wire(value: JSONValue | WireValue) -> WireValue:
    """Copy a JSON-domain value into an immutable snapshot, preserving order."""
    return _freeze(value, set())


def thaw_wire(value: WireValue) -> JSONValue:
    """Copy a wire snapshot into independent mutable lists and dictionaries."""
    return _thaw(value, set())


def presence_of(value: JSONValue | WireValue) -> PresenceTree:
    """Build presence from the actual keys and indices of a JSON-domain value."""
    return _presence(value, "", set())


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


def _freeze(value: JSONValue | WireValue, active: set[int]) -> WireValue:
    if isinstance(value, (list, tuple)):
        identity = enter(value, active)
        frozen = tuple(_freeze(item, active) for item in value)
        active.discard(identity)
        return frozen
    if isinstance(value, Mapping):
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


def _presence(value: JSONValue | WireValue, pointer: str, active: set[int]) -> PresenceTree:
    if isinstance(value, (list, tuple)):
        identity = enter(value, active)
        items = tuple((index, _presence(item, f"{pointer}/{index}", active)) for index, item in enumerate(value))
        active.discard(identity)
        return PresenceTree(pointer, "array", items)
    if isinstance(value, Mapping):
        identity = enter(value, active)
        members = tuple(
            (name, _presence(item, f"{pointer}/{escape_pointer_token(name)}", active))
            for name, item in ((checked_key(key), item) for key, item in value.items())
        )
        active.discard(identity)
        return PresenceTree(pointer, "object", members)
    checked_scalar(value)
    return PresenceTree(pointer, "value")
