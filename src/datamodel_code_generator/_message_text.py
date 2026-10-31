"""How the generator's messages write English: a list of items, the items left out of one, and a word for a count.

A message lists items with a conjunction (``english_list``), names the first items of a longer list and how many more
there are (``listed_with_more``), and writes a word or a counted noun in its form for a number (``for_count``,
``counted``). The module imports only the standard library, so every layer can import it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = ["Conjunction", "ListForm", "counted", "english_list", "for_count", "listed_with_more"]

Conjunction: TypeAlias = Literal["and", "or"]
ListForm: TypeAlias = Literal["final", "serial", "each"]


def english_list(items: Iterable[str], conjunction: Conjunction, form: ListForm = "final") -> str:
    """Write ``items`` as an English list joined by ``conjunction``.

    ``final`` writes the conjunction before the last item only (``a, b or c``), ``serial`` also a comma before it from
    three items on (``a, b, or c``), and ``each`` writes it between every two items (``a or b or c``). One item is
    written alone. No items write nothing under ``each`` and raise ``IndexError`` under the other forms.
    """
    listed = list(items)
    if form == "each":
        return f" {conjunction} ".join(listed)
    if len(listed) == 1:
        return listed[0]
    comma = "," if form == "serial" and len(listed) > 2 else ""  # noqa: PLR2004
    return f"{', '.join(listed[:-1])}{comma} {conjunction} {listed[-1]}"


def listed_with_more(shown: Sequence[str], total: int) -> str:
    """Write the ``shown`` items of a list of ``total`` items, comma-separated, then ``and N more`` for the rest."""
    rest = total - len(shown)
    return f"{', '.join(shown)}{f' and {rest} more' if rest > 0 else ''}"


def for_count(count: int, one: str, many: str | None = None) -> str:
    """Return the form of a word for ``count`` things: ``one`` for exactly one, else ``many``.

    ``many`` defaults to ``one`` with ``s``, or with ``es`` after an ``s`` (``key``, ``keys``; ``class``, ``classes``).
    """
    if count == 1:
        return one
    return f"{one}{'es' if one.endswith('s') else 's'}" if many is None else many


def counted(count: int, noun: str) -> str:
    """Return ``count`` followed by ``noun`` in its form for that count: ``1 key``, ``3 keys``, ``0 classes``."""
    return f"{count} {for_count(count, noun)}"
