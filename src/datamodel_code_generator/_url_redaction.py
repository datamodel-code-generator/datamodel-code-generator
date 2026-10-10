"""Redact the secrets a URL may carry before a message prints it."""

from __future__ import annotations

import re

_URL = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)(?P<authority>[^/?#]*)(?P<path>[^?#]*)")


def redact_url(text: str) -> str:
    """Return a URL with its scheme, host, port and path only, without userinfo, query or fragment.

    Text that is not a `scheme://` URL is returned unchanged. The URL is cut at its delimiters instead of parsed, so
    a malformed URL loses the same parts rather than being printed as written.
    """
    if (url := _URL.match(text)) is None:
        return text
    return f"{url['scheme']}{url['authority'].rpartition('@')[2]}{url['path']}"
