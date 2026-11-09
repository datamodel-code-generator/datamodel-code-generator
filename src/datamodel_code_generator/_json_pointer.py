"""RFC 6901 JSON Pointers: the one place that escapes, joins, splits and decodes their reference tokens.

A token is escaped ``~`` as ``~0``, then ``/`` as ``~1``, and unescaped in the reverse order. Percent-encoding is not
part of a pointer but of the URI fragment that writes one, and ``reference_tokens`` reads the fragment of a ``$ref``
the way the parser does: it splits at each ``/`` first, then decodes each token, strictly or leniently. A caller whose
text is already decoded hands it to ``fragment_tokens``. Whether a fragment opens a pointer or is a plain name, which
names an anchor, is told here too (``is_pointer_fragment``, ``pointer_opening``). The generated runtime (``_runtime/``)
keeps its own copy, as it never imports the generator. This module imports only the standard library, so every layer
may use it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final, Literal, TypeAlias
from urllib.parse import unquote

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "NONCANONICAL_ESCAPE",
    "NONCANONICAL_PERCENT",
    "OpeningRow",
    "decode_token",
    "escape_token",
    "fragment_pointer",
    "fragment_tokens",
    "is_array_index",
    "is_pointer_fragment",
    "join_pointer",
    "pointer_opening",
    "pointer_tokens",
    "reference_tokens",
    "unescape_token",
]

NONCANONICAL_PERCENT: Final = re.compile(r"%(?![0-9a-fA-F]{2})")
"""A ``%`` that starts no percent-encoded octet."""
NONCANONICAL_ESCAPE: Final = re.compile(r"~(?![01])")
"""A ``~`` that starts no escape sequence of a decoded pointer."""
_ARRAY_INDEX: Final = re.compile(r"0|[1-9][0-9]*")
_POINTER_OPENING: Final = re.compile(r"/|%2[fF]")
"""The start of a pointer in a URI fragment: a slash, or its percent-encoded octet."""

OpeningRow: TypeAlias = Literal["uri-fragment", "decoded"]
"""What opens a pointer in a fragment (``pointer_opening``): ``/`` or its octet ``%2F`` in a URI fragment, and ``/`` in
a decoded one."""


def escape_token(token: str | int, /) -> str:
    """Return one reference token as a pointer writes it; an integer (a list index or YAML key) as its decimal text."""
    return str(token).replace("~", "~0").replace("/", "~1")


def unescape_token(token: str, /) -> str:
    """Return the reference token an escaped pointer token stands for."""
    return token.replace("~1", "/").replace("~0", "~")


def decode_token(token: str, /) -> str:
    """Return the reference token a token of a URI fragment stands for: percent-decoded, then unescaped."""
    return unescape_token(unquote(token))


def fragment_pointer(tokens: Iterable[str | int], /) -> str:
    """Return the pointer of reference tokens in the fewest characters that ``reference_tokens`` reads back as them.

    Each token is escaped, its ``%`` written as ``%25`` and its ``#``, which would end the fragment of the reference,
    as ``%23``; every other character stays as written, so a token without ``~``, ``/``, ``%`` or ``#`` keeps its text.
    """
    return "".join(f"/{escape_token(token).replace('%', '%25').replace('#', '%23')}" for token in tokens)


def join_pointer(tokens: Iterable[str | int], /) -> str:
    """Return the pointer of reference tokens: ``""`` for none, else ``/`` before each escaped token."""
    return "".join(f"/{escape_token(token)}" for token in tokens)


def pointer_tokens(pointer: str, /) -> tuple[str, ...]:
    """Return the reference tokens of a pointer, which is ``""`` or starts with ``/``."""
    return tuple(unescape_token(token) for token in pointer[1:].split("/")) if pointer else ()


def pointer_opening(fragment: str, /, *, row: OpeningRow = "uri-fragment") -> int | None:
    """Return the length of the opening of the pointer a fragment writes, or None when none opens it."""
    if row == "uri-fragment":
        return opening.end() if (opening := _POINTER_OPENING.match(fragment)) else None
    return 1 if fragment.startswith("/") else None


def is_pointer_fragment(fragment: str, /, *, row: OpeningRow = "uri-fragment") -> bool:
    """Return whether a fragment locates by pointer, an empty one the whole document, instead of naming an anchor."""
    return not fragment or pointer_opening(fragment, row=row) is not None


def fragment_tokens(fragment: str, /) -> tuple[str, ...] | None:
    """Return the reference tokens of a decoded URI fragment: none when empty, None when it is a plain name."""
    if not is_pointer_fragment(fragment, row="decoded"):
        return None
    return pointer_tokens(fragment)


def reference_tokens(fragment: str, /, *, strict: bool = False) -> tuple[str, ...] | None:
    """Return the reference tokens of the URI fragment of a ``$ref``: none when empty, None when it is a plain name.

    A pointer starts with ``/`` or its octet ``%2F``. The fragment is split at each ``/`` before the tokens are
    decoded, so ``a%2Fb`` is the one token ``a/b`` and a decoded ``/`` is never a separator. Under ``strict``, a ``%``
    that starts no octet, octets that are no UTF-8 and a ``~`` that starts no escape raise ``ValueError``.
    """
    if strict:
        if NONCANONICAL_PERCENT.search(fragment):
            raise ValueError(fragment)
        unquote(fragment, errors="strict")
    if not fragment:
        return ()
    if (opening := pointer_opening(fragment)) is None:
        return None
    tokens = tuple(unquote(token) for token in fragment[opening:].split("/"))
    if strict and any(NONCANONICAL_ESCAPE.search(token) for token in tokens):
        raise ValueError(fragment)
    return tuple(map(unescape_token, tokens))


def is_array_index(token: str, /) -> bool:
    """Return whether a token is an RFC 6901 array index: ``0``, or ASCII digits without a leading zero."""
    return _ARRAY_INDEX.fullmatch(token) is not None
