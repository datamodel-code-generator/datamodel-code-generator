"""Values protocol helpers read from decoded responses and write into requests: RFC 6901 pointers and their absence.

A value written into a querystring or a JSON body is a patch of the caller's value: the argument is encoded and checked
as any call's, and the values the server chose are written into its wire value afterwards, unchecked; the parameters
and media that apply patches live in `writes`, which only a helper's plan loads.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final

from ..model_codecs.unset import Unset

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..model_codecs.wire import WireValue

__all__ = ("MISSING", "Missing", "Patch", "resolve")


class Missing(Enum):
    """The absence of the member a selector names, which is neither JSON null nor an empty string."""

    MISSING = "missing"


MISSING: Final = Missing.MISSING


def _tokens(pointer: str) -> list[str]:
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer.split("/")[1:]]


def resolve(value: WireValue, pointer: str) -> WireValue | Missing:
    """Return the member of a decoded JSON value an RFC 6901 pointer names, or MISSING when there is none.

    Generated helpers point only through object members, so an absent member and a step into anything but an object,
    null included, both name nothing.
    """
    for key in _tokens(pointer):
        if not isinstance(value, Mapping) or key not in value:
            return MISSING
        value = value[key]
    return value


def _patched(value: WireValue, tokens: list[str], new: WireValue) -> WireValue:
    """Return a copy of a wire value with the member the tokens name set, the new value itself for none.

    Only the objects along the tokens are copied; a missing or null one on the way becomes a new object.
    """
    if not tokens:
        return new
    key, *rest = tokens
    members = dict(value) if isinstance(value, Mapping) else {}
    members[key] = _patched(members.get(key), rest, new)
    return members


@dataclass(frozen=True, slots=True)
class Patch:
    """A caller's argument or body, UNSET when omitted, and the wire values to write into it by pointer, in order."""

    value: object
    writes: tuple[tuple[str, WireValue], ...]

    def applied(self, encode: Callable[[object], WireValue]) -> WireValue:
        """Return the encoded value, an empty object when omitted, with every write applied."""
        wire: WireValue = {} if isinstance(self.value, Unset) else encode(self.value)
        for pointer, new in self.writes:
            wire = _patched(wire, _tokens(pointer), new)
        return wire
