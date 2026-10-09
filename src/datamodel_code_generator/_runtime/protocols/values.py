"""Values protocol helpers read from decoded responses and write into requests: RFC 6901 pointers and their absence.

A value written into a querystring or a JSON body is a patch of the caller's value: the argument is encoded and checked
as any call's, and the values the server chose are written into its wire value afterwards, unchecked; the parameters
and media that apply patches live in `writes`, which only a helper's plan loads.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING, Final, cast

from ..model_codecs.unset import UNSET
from .records import BodySelector, HeaderSelector

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from ..client.responses import ResponseInfo
    from ..model_codecs.media import JSONValue
    from .records import Selector

__all__ = ("MISSING", "Missing", "Patch", "RepeatedValueError", "resolve", "selected", "server_expiry", "written")

_RFC3339: Final = re.compile(
    r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:)([0-9]{2})(?:\.([0-9]+))?(Z|[+-][0-9]{2}:[0-9]{2})"
)
_LEAP_SECOND: Final = "60"


class Missing(Enum):
    """The absence of the member a selector names, which is neither JSON null nor an empty string."""

    MISSING = "missing"


MISSING: Final = Missing.MISSING


def _tokens(pointer: str) -> list[str]:
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer.split("/")[1:]]


def resolve(value: JSONValue, pointer: str) -> JSONValue | Missing:
    """Return the member of a decoded JSON value an RFC 6901 pointer names, or MISSING when there is none.

    Generated helpers point only through object members, so an absent member and a step into anything but an object,
    null included, both name nothing.
    """
    for key in _tokens(pointer):
        if not isinstance(value, Mapping) or key not in value:
            return MISSING
        value = value[key]
    return value


class RepeatedValueError(Exception):
    """A header a selector reads once that the response repeats; each helper raises its own data error instead."""


def selected(read: Selector, wire: JSONValue, info: ResponseInfo) -> JSONValue | Missing:
    """Return what a selector reads from a response, or MISSING; every occurrence of a header is an array of them.

    A header selected once that the response repeats raises RepeatedValueError.
    """
    if isinstance(read, BodySelector):
        return resolve(wire, read.pointer)
    if isinstance(read, HeaderSelector):
        values = info.headers.get_all(read.name)
        if read.occurrence == "all":
            return list(values) if values else MISSING
        if len(values) > 1:
            raise RepeatedValueError
        return values[0] if values else MISSING
    return info.status_code


def _patched(value: JSONValue, tokens: list[str], new: JSONValue) -> JSONValue:
    """Return a copy of a JSON value with the member the tokens name set, the new value itself for none.

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
    writes: tuple[tuple[str, JSONValue], ...]

    def applied(self, encode: Callable[[object], JSONValue]) -> JSONValue:
        """Return the encoded value, an empty object when omitted, with every write applied."""
        wire: JSONValue = {} if self.value is UNSET else encode(self.value)
        for pointer, new in self.writes:
            wire = _patched(wire, _tokens(pointer), new)
        return wire


def server_expiry(value: str, now: float) -> datetime | None:
    """Return the UTC time an RFC 3339 date-time with an offset or an HTTP date gives, or None for another value.

    A leap second is the second after the one before it, as in an HTTP date, and `now` is the receipt wall time that
    places an HTTP date's two-digit year. The zone `Z` and fractions of any length are spelled as Python 3.10 parses
    them, fractions past microseconds cut.
    """
    try:
        if (matched := _RFC3339.fullmatch(value.upper())) is not None:
            head, second, fraction, zone = matched.groups()
            leap = second == _LEAP_SECOND
            micro = (fraction or "")[:6].ljust(6, "0")
            offset = "+00:00" if zone == "Z" else zone
            parsed = datetime.fromisoformat(f"{head}{'59' if leap else second}.{micro}{offset}")
            return (parsed + timedelta(seconds=leap)).astimezone(timezone.utc)
        from ..client.retry import http_date  # noqa: PLC0415 - Only a helper with an expiry parses HTTP dates.

        return http_date(value, now)
    except (ValueError, OverflowError):
        return None


def written(
    writes: tuple[tuple[int | None, str | None], ...],
    arguments: tuple[object, ...],
    body: object,
    values: Iterable[JSONValue],
) -> tuple[tuple[object, ...], object]:
    """Return a request's arguments and body with each value written where its write goes, in order.

    A write without a pointer replaces the argument at its position, and one with a pointer patches the argument at its
    position, or the body without one, so the value is written into its encoded value.
    """
    given = list(arguments)
    patches: dict[int | None, list[tuple[str, JSONValue]]] = {}
    for (position, pointer), value in zip(writes, values, strict=True):
        if pointer is None:
            given[cast("int", position)] = value
        else:
            patches.setdefault(position, []).append((pointer, value))
    for position, patched in patches.items():
        if position is None:
            body = Patch(body, tuple(patched))
        else:
            given[position] = Patch(given[position], tuple(patched))
    return tuple(given), body
