"""Immutable JSON-domain wire snapshots, independent mutable copies, and presence trees."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, overload

from typing_extensions import TypeAliasType

from .errors import CodecBindingError

if TYPE_CHECKING:
    from typing import TypeAlias

    JSONScalar: TypeAlias = bool | str | int | float | Decimal | None
    JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | tuple["JSONValue", ...] | dict[str, "JSONValue"]
    WireValue: TypeAlias = JSONScalar | tuple["WireValue", ...] | Mapping[str, "WireValue"]
else:
    JSONScalar = TypeAliasType("JSONScalar", "bool | str | int | float | Decimal | None")
    JSONValue = TypeAliasType(
        "JSONValue", "JSONScalar | list[JSONValue] | tuple[JSONValue, ...] | dict[str, JSONValue]"
    )
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


def thaw_wire(value: WireValue) -> JSONValue:
    """Copy a wire snapshot into independent mutable lists and dictionaries."""
    return _thaw(value, set())


@overload
def presence_of(value: JSONValue) -> PresenceTree: ...
@overload
def presence_of(value: WireValue) -> PresenceTree: ...
def presence_of(value: JSONValue | WireValue) -> PresenceTree:
    """Build presence from the actual keys and indices of a JSON-domain value."""
    return _presence(value, "", set())


def snapshot_presence(value: WireValue) -> PresenceTree:
    """Build presence for a snapshot that freeze_wire produced, without repeating its domain checks."""
    return _snapshot_presence(value, "")


def without_pointers(value: WireValue, pointers: tuple[str, ...]) -> WireValue:
    """Copy a snapshot without the object members at the given pointers; other pointers are ignored."""
    thawed = thaw_wire(value)
    for *parents, name in (pointer_tokens(pointer) for pointer in pointers if pointer):
        container: JSONValue = thawed
        for token in parents:
            container = (
                container.get(token)
                if isinstance(container, dict)
                else container[int(token)]
                if isinstance(container, list) and _index(token, len(container))
                else None
            )
        if isinstance(container, dict):
            container.pop(name, None)
    return freeze_wire(thawed)


def _index(digits: str, size: int) -> bool:
    return digits.isascii() and digits.isdecimal() and (digits == "0" or digits[0] != "0") and int(digits) < size


def select_present(value: WireValue, presence: PresenceTree) -> WireValue:
    """Copy only the members and elements a presence tree names, rejecting trees that do not fit the value."""
    return freeze_wire(_selected(value, presence))


def check_array_presence(presence: PresenceTree | None, length: int, pointer: str) -> None:
    """Require a presence tree for an array to name every element in order."""
    if presence is not None and (
        presence.container != "array" or [key for key, _ in presence.members] != list(range(length))
    ):
        msg = f"The presence tree does not select the whole array at {pointer or '/'}"
        raise CodecBindingError(msg)


def check_object_presence(presence: PresenceTree | None, available: set[str], pointer: str) -> None:
    """Require a presence tree for an object to name only existing members."""
    if presence is not None and (
        presence.container != "object" or any(key not in available for key, _ in presence.members)
    ):
        msg = f"The presence tree does not select existing object members at {pointer or '/'}"
        raise CodecBindingError(msg)


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


def _snapshot_presence(value: WireValue, pointer: str) -> PresenceTree:
    if isinstance(value, tuple):
        return PresenceTree(
            pointer,
            "array",
            tuple((index, _snapshot_presence(item, f"{pointer}/{index}")) for index, item in enumerate(value)),
        )
    if isinstance(value, Mapping):
        return PresenceTree(
            pointer,
            "object",
            tuple(
                (name, _snapshot_presence(item, f"{pointer}/{escape_pointer_token(name)}"))
                for name, item in value.items()
            ),
        )
    return PresenceTree(pointer, "value")


def _selected(value: WireValue, presence: PresenceTree) -> JSONValue:
    match value:
        case tuple():
            check_array_presence(presence, len(value), presence.pointer)
            return [_selected(item, child) for item, (_, child) in zip(value, presence.members, strict=True)]
        case Mapping():
            check_object_presence(presence, set(value), presence.pointer)
            return {str(key): _selected(value[str(key)], child) for key, child in presence.members}
        case _ if presence.container == "value":
            return value
        case _:
            msg = f"The presence tree does not describe a scalar at {presence.pointer or '/'}"
            raise CodecBindingError(msg)


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
