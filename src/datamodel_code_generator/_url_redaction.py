"""Redact the secrets a URL may carry before a message prints it."""

from __future__ import annotations

import re

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
