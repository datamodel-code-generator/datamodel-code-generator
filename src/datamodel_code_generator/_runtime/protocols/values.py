"""Values protocol helpers read from decoded responses: RFC 6901 pointers, and the absence of a member."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from ..model_codecs.wire import WireValue

__all__ = ("MISSING", "Missing", "resolve")


class Missing(Enum):
    """The absence of the member a selector names, which is neither JSON null nor an empty string."""

    MISSING = "missing"


MISSING: Final = Missing.MISSING


def resolve(value: WireValue, pointer: str) -> WireValue | Missing:
    """Return the member of a decoded JSON value an RFC 6901 pointer names, or MISSING when there is none.

    Generated helpers point only through object members, so an absent member and a step into anything but an object,
    null included, both name nothing.
    """
    for token in pointer.split("/")[1:]:
        if not isinstance(value, Mapping) or (key := token.replace("~1", "/").replace("~0", "~")) not in value:
            return MISSING
        value = value[key]
    return value
