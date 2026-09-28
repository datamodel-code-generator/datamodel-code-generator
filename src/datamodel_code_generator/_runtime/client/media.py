"""Media types as a client reads them: normalized, dispatched to their most specific declaration, and charsets."""

from __future__ import annotations

import codecs
from typing import TYPE_CHECKING

from ..model_codecs.media import normalize_media_type

if TYPE_CHECKING:
    from collections.abc import Iterable


def essence(media_type: str) -> str:
    """Return a media type without its parameters."""
    return media_type.partition(";")[0]


def normalized(media_type: str) -> str | None:
    """Return a media type in its normalized form, or None when it is not a media type."""
    try:
        return normalize_media_type(media_type)
    except ValueError:
        return None


def most_specific(received: str, declared: Iterable[str]) -> str | None:
    """Return the declared media type a concrete one falls under: itself, without parameters, type/*, then */*."""
    declared = tuple(declared)
    if received in declared:
        return received
    bare = essence(received)
    return next(
        (
            media
            for candidate in (bare, f"{bare.partition('/')[0]}/*", "*/*")
            for media in declared
            if essence(media) == candidate
        ),
        None,
    )


def with_charset(media_type: str, declared: str) -> str:
    """Return a normalized media type with the charset of its normalized declared type when it names none."""
    if (start := declared.find("; charset=")) < 0 or "; charset=" in media_type:
        return media_type
    end = declared.find("; ", start + 2)
    return media_type + declared[start : None if end < 0 else end]


def charset(media_type: str) -> str:
    """Return the Python codec a media type's charset parameter names, UTF-8 when it names none or an unknown one."""
    for parameter in media_type.split(";")[1:]:
        name, _, value = parameter.strip().partition("=")
        if name == "charset":
            try:
                return codecs.lookup(value.strip('"')).name
            except LookupError:
                break
    return "utf-8"
