"""The names a client target reserves, and the rules its explicit names and header tokens follow."""

from __future__ import annotations

import keyword
import re
import unicodedata
from typing import TYPE_CHECKING, Final

from datamodel_code_generator._target_naming import WINDOWS_DEVICES, explicit_name

if TYPE_CHECKING:
    from collections.abc import Iterator

_TOKEN: Final = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
RESERVED_MEMBERS: Final = frozenset({
    "aclose",
    "close",
    "protocols",
    "request_raw",
    "with_options",
    "with_raw_response",
    "with_response",
    "with_streaming_response",
})
RESERVED_ARGUMENTS: Final = frozenset({"body", "media_type", "options", "response_media_type", "self"})
VIEW_KEYWORDS: Final = (
    "base_url",
    "server",
    "timeout",
    "total_timeout",
    "max_retries",
    "retry",
    "default_headers",
    "default_query",
    "follow_redirects",
    "auth",
)
CLIENT_KEYWORDS: Final = (
    *VIEW_KEYWORDS,
    "compression",
    "clock",
    "http_client",
    "helper_defaults",
    "cache_stores",
    "allowed_origins",
)
HELPER_ARGUMENTS: Final = frozenset({
    "cache_options",
    "items",
    "pagination_options",
    "poll_options",
    "source",
    "state",
    "stream_options",
    "upload_options",
    "ws_options",
})


def namespace_problem(value: object) -> str | None:
    """Return why a resource namespace is invalid, or None for dotted identifiers that name no member or device."""
    if not isinstance(value, str) or not all(explicit_name(part) for part in value.split(".")):
        return "A resource namespace must be dotted Python identifiers"
    if reserved := next(
        (part for part in value.split(".") if part in RESERVED_MEMBERS or part.casefold() in WINDOWS_DEVICES), None
    ):
        return f"A resource namespace cannot use the reserved name {reserved!r}"
    return None


def token(value: object) -> bool:
    """Return whether a value is an HTTP token, such as a header name."""
    return isinstance(value, str) and _TOKEN.fullmatch(value) is not None


def folded(name: str) -> str:
    """Return the form two helper names are compared in: NFC-normalized, then case-folded."""
    return unicodedata.normalize("NFC", name).casefold()


def helper_name_problem(value: str) -> str | None:
    """Return why a helper name is invalid, or None for dotted public Python identifiers in NFKC form."""
    for part in value.split("."):
        if not part.isidentifier() or keyword.iskeyword(part):
            return f"The helper name {value!r} must be Python identifiers separated by dots, without keywords"
        if unicodedata.normalize("NFKC", part) != part or part.startswith("_") or part.casefold() in WINDOWS_DEVICES:
            return f"The helper name {value!r} cannot use the part {part!r}"
    return None


def helper_classes(name: str, kind: str) -> Iterator[tuple[tuple[str, ...], str]]:
    """Yield the class of each namespace a helper's dotted name opens, then the helper's own class, with their parts.

    A namespace class ends in `Protocols` and a helper class in its kind, both after the PascalCase parts.
    """
    from datamodel_code_generator.reference import snake_to_upper_camel  # noqa: PLC0415

    parts = tuple(name.split("."))
    for end in range(1, len(parts)):
        yield parts[:end], f"{''.join(map(snake_to_upper_camel, parts[:end]))}Protocols"
    yield parts, f"{''.join(map(snake_to_upper_camel, parts))}{snake_to_upper_camel(kind)}"
