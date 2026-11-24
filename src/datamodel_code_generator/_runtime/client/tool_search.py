"""Rank tools for a query by the words of their names, tags, routes, descriptions, and signatures, deterministically.

A query word scores in each text it is a word of, and half as much in one where it begins a word; a tool's score sums
each query word's best text, weighted name first, then tags and route, then description and signature. Ties keep the
order of the table.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

_WORD: Final = re.compile(r"[^\W_]+")
_CAMEL: Final = re.compile(r"(?<=[0-9a-z])(?=[A-Z])")
_PREFIX: Final = 3


def words(text: str) -> tuple[str, ...]:
    """Return the lowercase words of a text, a camelCase word split at each capital."""
    return tuple(_WORD.findall(_CAMEL.sub(" ", text).lower()))


def score(query: tuple[str, ...], texts: Iterable[tuple[int, str]]) -> int:
    """Return how well weighted texts match the words of a query: each word's best text, summed; 0 for no match."""
    found = [(weight, frozenset(words(text))) for weight, text in texts]
    return sum(max((_match(word, known) * weight for weight, known in found), default=0) for word in query)


def _match(word: str, known: frozenset[str]) -> int:
    """Return 2 for a word a text holds, 1 for one that begins a word of it and has 3 letters or more, else 0."""
    if word in known:
        return 2
    return int(len(word) >= _PREFIX and any(item.startswith(word) for item in known))
