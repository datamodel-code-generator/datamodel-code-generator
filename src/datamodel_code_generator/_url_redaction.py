"""Redact the secrets a URL may carry before a message prints it, and name a schema location in a message."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["LocationRow", "named_location", "redact_path", "redact_reference", "redact_url"]

LocationRow: TypeAlias = Literal["redacted", "legacy-unredacted-locations"]
"""How ``named_location`` writes a location: redacted, or as written, as the warnings of 0.87.0 print it."""

_URL = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)(?P<authority>[^/?#]*)(?P<path>[^?#]*)(?P<rest>.*)", re.DOTALL
)
_HOST = re.compile(r"(?:\[[^\]]*\]|[^@:\[\]]*)(?::[0-9]+)?")
_DELIMITER = re.compile(r"[?#]")


def redact_url(text: str) -> str:
    """Return a URL with its scheme, host, port and path only, without userinfo, query or fragment.

    Text that is not a `scheme://` URL is returned unchanged. The URL is cut at its delimiters instead of parsed, so
    a malformed URL never raises. An authority that reads as `host` or `host:port` carries no userinfo, so an `@`
    after it is part of the path, query or fragment. Otherwise, when an `@` follows the authority, where the userinfo
    ends is ambiguous: everything up to the last `@` is shown as `***`, and nothing more when that `@` may lie in the
    query or fragment.
    """
    if (url := _URL.match(text)) is None:
        return text
    if _HOST.fullmatch(url["authority"]) is None and ("@" in url["path"] or "@" in url["rest"]):
        before, _, tail = text.rpartition("@")
        if _DELIMITER.search(before) is not None:
            return f"{url['scheme']}***"
        return f"{url['scheme']}***@{_DELIMITER.split(tail, maxsplit=1)[0]}"
    return f"{url['scheme']}{url['authority'].rpartition('@')[2]}{url['path']}"


def redact_reference(text: str) -> str:
    """Return a reference with its document URL redacted and its `#` fragment, such as a JSON pointer, kept."""
    document, delimiter, fragment = text.partition("#")
    return f"{redact_url(document)}{delimiter}{fragment}"


def redact_path(parts: Iterable[str]) -> str:
    """Join a schema path whose parts may start with a document URL, redacting each part."""
    return "/".join(redact_reference(part) for part in parts)


def named_location(location: object, fallback: str, *, row: LocationRow = "redacted") -> str:
    """Return how a message names a schema location: ``document#pointer``, or ``fallback`` when it is None.

    Row ``redacted`` redacts the document URL of the text (``redact_reference``). Row ``legacy-unredacted-locations``
    keeps the location as written, and the fallback as the caller gives it.
    """
    text = fallback if location is None else str(location)
    return redact_reference(text) if row == "redacted" else text
