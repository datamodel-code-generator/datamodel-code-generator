"""RFC 6901 JSON Pointers: the one place that escapes, joins, splits and decodes their reference tokens.

A token is escaped ``~`` as ``~0``, then ``/`` as ``~1``, and unescaped in the reverse order. Percent-encoding is not
part of a pointer but of the URI fragment that writes one: each caller decodes a fragment as its own contract says
(strictly, leniently or not at all) and hands the decoded text to ``fragment_tokens``. The generated runtime
(``_runtime/``) keeps its own copy, as it never imports the generator. This module imports only the standard library,
so every layer may use it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "NONCANONICAL_ESCAPE",
    "NONCANONICAL_PERCENT",
    "escape_token",
    "fragment_tokens",
    "is_array_index",
    "join_pointer",
    "pointer_tokens",
    "unescape_token",
]

NONCANONICAL_PERCENT: Final = re.compile(r"%(?![0-9a-fA-F]{2})")
"""A ``%`` that starts no percent-encoded octet."""
NONCANONICAL_ESCAPE: Final = re.compile(r"~(?![01])")
"""A ``~`` that starts no escape sequence of a decoded pointer."""
_ARRAY_INDEX: Final = re.compile(r"0|[1-9][0-9]*")


def escape_token(token: str | int, /) -> str:
    """Return one reference token as a pointer writes it; an integer (a list index or YAML key) as its decimal text."""
    return str(token).replace("~", "~0").replace("/", "~1")


def unescape_token(token: str, /) -> str:
    """Return the reference token an escaped pointer token stands for."""
    return token.replace("~1", "/").replace("~0", "~")


def join_pointer(tokens: Iterable[str | int], /) -> str:
    """Return the pointer of reference tokens: ``""`` for none, else ``/`` before each escaped token."""
    return "".join(f"/{escape_token(token)}" for token in tokens)


def pointer_tokens(pointer: str, /) -> tuple[str, ...]:
    """Return the reference tokens of a pointer, which is ``""`` or starts with ``/``."""
    return tuple(unescape_token(token) for token in pointer[1:].split("/")) if pointer else ()


def fragment_tokens(fragment: str, /) -> tuple[str, ...] | None:
    """Return the reference tokens of a decoded URI fragment: none when empty, None when it is a plain name."""
    if fragment and not fragment.startswith("/"):
        return None
    return pointer_tokens(fragment)


def is_array_index(token: str, /) -> bool:
    """Return whether a token is an RFC 6901 array index: ``0``, or ASCII digits without a leading zero."""
    return _ARRAY_INDEX.fullmatch(token) is not None
