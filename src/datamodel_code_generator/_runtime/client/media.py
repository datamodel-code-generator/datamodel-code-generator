"""Media types as a client sends them: dispatched to their most specific declaration, and written in their charset.

Text is written in the charset its media type names, and refused when that charset cannot represent it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..model_codecs import media
from ..model_codecs.errors import ParameterEncodingError
from ..model_codecs.media import charset

if TYPE_CHECKING:
    from collections.abc import Iterable


def most_specific(received: str, declared: Iterable[str]) -> str | None:
    """Return the declared media type a concrete one falls under: itself, without parameters, type/*, then */*.

    The first declaration of the most specific of these wins, in declared order (row `declared` until #5596).
    """
    return media.most_specific(received, declared, order="declared")


def with_charset(media_type: str, declared: str) -> str:
    """Return a normalized media type with the charset of its normalized declared type when it names none."""
    if (start := declared.find("; charset=")) < 0 or "; charset=" in media_type:
        return media_type
    end = declared.find("; ", start + 2)
    return media_type + declared[start : None if end < 0 else end]


def encode_text(text: str, media_type: str) -> bytes:
    """Return text in the charset its media type names, refusing text that charset cannot represent."""
    try:
        return text.encode(charset(media_type))
    except UnicodeEncodeError:
        msg = "The text cannot be represented in its media type's charset"
        raise ParameterEncodingError(msg) from None
