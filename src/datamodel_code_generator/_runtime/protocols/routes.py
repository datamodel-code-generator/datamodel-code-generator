"""Route a received JSON body by the string its discriminator member holds: stream events and WebSocket messages alike.

A helper that receives several JSON schemas on one stream or connection tells them apart by a body member an RFC 6901
pointer names, whose string value is the key of the schema that decodes the body.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, final

from ..model_codecs.media import json_value
from .values import resolve, unfit

if TYPE_CHECKING:
    from ..model_codecs.media import JSONValue
    from .records import BodySelector

__all__ = ("Unrouted", "route")


@final
@dataclass(frozen=True, slots=True)
class Unrouted:
    """Why a body names no key: it is not JSON, or its discriminator member is absent, null, or not a string."""

    reason: Literal["malformed", "missing", "null", "type"]
    cause: BaseException | None = None


def route(data: bytes, selector: BodySelector) -> tuple[JSONValue, str] | Unrouted:
    """Return a JSON body parsed, with the string its discriminator member holds, or why it names no key."""
    try:
        wire = json_value(data)
    except (ValueError, UnicodeDecodeError, RecursionError) as error:
        return Unrouted("malformed", error)
    found = resolve(wire, selector.pointer)
    return (wire, found) if isinstance(found, str) else Unrouted(unfit(found))
