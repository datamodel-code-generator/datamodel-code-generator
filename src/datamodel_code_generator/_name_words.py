"""How a class name is made from the words of a free text: its ASCII letter and digit runs, each titled, joined.

The schema converters (Avro, XML Schema, MCP tools, AsyncAPI), the class name of a JSON Schema title and the class
name the recommender proposes for an input file all build a name from a text this way. What each feature does beyond
that is released output, so it stays with the caller as a row: how a word is titled (``first_upper``, ``str.title``
for a schema title, or a converter's own titler), the prefix of a name that would start with a digit, and the name of a
text that holds no word. This module imports only the standard library, so every layer may use it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["ASCII_WORD", "first_upper", "pascal_words"]

ASCII_WORD: Final = re.compile(r"[A-Za-z0-9]+")
"""A word of a text: a run of ASCII letters and digits."""


def first_upper(text: str) -> str:
    """Return text with its first character upper-cased and the others as written."""
    return f"{text[:1].upper()}{text[1:]}"


def pascal_words(*texts: str, title: Callable[[str], str] = first_upper, digit_prefix: str = "") -> str:
    """Return the words of ``texts``, each titled by ``title``, joined; "" when they hold no word.

    A name that does not start with a letter takes ``digit_prefix`` in front.
    """
    name = "".join(title(word) for text in texts for word in ASCII_WORD.findall(text))
    return f"{digit_prefix}{name}" if name and not name[0].isalpha() else name
