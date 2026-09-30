"""One native URL interpretation for configured origins and received redirect targets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

import httpx2

Origin = tuple[str, str, int]
URLValidationError = httpx2.InvalidURL
_PORT_MAX: Final = 65535


@dataclass(frozen=True, slots=True)
class URLTarget:
    """An effective HTTP URL and the origin used by the redirect safety policy."""

    url: str = field(repr=False)
    origin: Origin = field(repr=False)


def _origin(url: httpx2.URL) -> Origin:
    if url.scheme not in {"http", "https"} or not url.is_absolute_url or url.userinfo:
        msg = "Expected an absolute HTTP URL without user information"
        raise URLValidationError(msg)
    port = url.port
    if port is not None and not 0 < port <= _PORT_MAX:
        msg = "Invalid origin port"
        raise URLValidationError(msg)
    return url.scheme, url.raw_host.decode("ascii"), port if port is not None else 443 if url.scheme == "https" else 80


def canonical_origin(value: str) -> Origin:
    """Canonicalize a structurally validated configured origin using native URL semantics."""
    return _origin(httpx2.URL(value))


def absolute_target(url: str) -> URLTarget:
    """Interpret an absolute request URL natively, dropping the fragment it never sends."""
    parsed = httpx2.URL(url)
    origin = _origin(parsed)
    return URLTarget(str(parsed.copy_with(fragment=None)) if "#" in url else str(parsed), origin)


def redirect_target(current_url: str, location: str) -> URLTarget:
    """Resolve a Location once, preserving encoded path/query and removing non-request fragments."""
    url = httpx2.URL(current_url).join(location)
    origin = _origin(url)
    return URLTarget(str(url.copy_with(fragment=None)), origin)
