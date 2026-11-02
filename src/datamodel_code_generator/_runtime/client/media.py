"""Media types as a client sends them, written in their charset.

Text is written in the charset its media type names, and refused when that charset cannot represent it; received text
is read in the charset its content type names.
"""

from __future__ import annotations

from ..model_codecs.errors import ParameterEncodingError
from ..model_codecs.media import charset


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


def charset_text(data: bytes, content_type: str | None) -> str:
    """Return received bytes as text in the charset their raw content type names, else UTF-8.

    UTF-8 reads bytes whose content type names no charset or an unknown one. Bytes the charset cannot decode raise
    UnicodeDecodeError, which each reader turns into its own failure.
    """
    return data.decode(charset(content_type or ""))
