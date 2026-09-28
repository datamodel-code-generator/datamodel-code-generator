"""Media types as a client reads them: the charsets their texts are in."""

from __future__ import annotations

import codecs


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
