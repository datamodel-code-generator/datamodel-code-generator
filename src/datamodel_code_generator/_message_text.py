"""How the generator's messages write English: lists, counts, install hints and the lines of a paragraph.

A message lists items with a conjunction (``english_list``), names the first items of a longer list and how many more
there are (``listed_with_more``), writes a word or a counted noun in its form for a number (``for_count``,
``counted``), names an extra of the generator or the pip command that installs it (``extra_requirement``,
``install_command``), and a text report wraps a paragraph into lines (``wrapped_lines``). The module imports only the
standard library, so every layer can import it.
"""

from __future__ import annotations

from textwrap import wrap
from typing import TYPE_CHECKING, Literal, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = [
    "Conjunction",
    "InstallPrompt",
    "InstallRow",
    "ListForm",
    "counted",
    "english_list",
    "extra_requirement",
    "for_count",
    "install_command",
    "listed_with_more",
    "wrapped_lines",
]

Conjunction: TypeAlias = Literal["and", "or"]
ListForm: TypeAlias = Literal["final", "serial", "each"]
InstallPrompt: TypeAlias = Literal["$", "$ ", ""]
InstallRow: TypeAlias = Literal["code", "plain", "legacy-graphql-quote"]
"""How ``install_command`` writes the command: as a code span, as plain text, or as the GraphQL hint of 0.87.0 does."""

_REPORT_WIDTH = 100


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


def for_count(count: float, one: str, many: str | None = None) -> str:
    """Return the form of a word for ``count`` things: ``one`` for exactly one, else ``many``.

    ``many`` defaults to ``one`` with ``s``, or with ``es`` after an ``s`` (``key``, ``keys``; ``class``, ``classes``).
    """
    if count == 1:
        return one
    return f"{one}{'es' if one.endswith('s') else 's'}" if many is None else many


def counted(count: int, noun: str) -> str:
    """Return ``count`` followed by ``noun`` in its form for that count: ``1 key``, ``3 keys``, ``0 classes``."""
    return f"{count} {for_count(count, noun)}"


def extra_requirement(extra: str) -> str:
    """Return the requirement that installs the generator with an extra: ``datamodel-code-generator[extra]``."""
    return f"datamodel-code-generator[{extra}]"


def install_command(extra: str, *, prompt: InstallPrompt = "$", row: InstallRow = "code") -> str:
    """Return the pip command that installs the generator with ``extra``, written after ``prompt``.

    Row ``code`` writes it as a code span (`` `$pip install 'datamodel-code-generator[x]'` ``), ``plain`` without the
    backticks. Row ``legacy-graphql-quote`` writes the closing backtick before the closing quote.
    """
    command = f"{prompt}pip install '{extra_requirement(extra)}"
    if row == "plain":
        return f"{command}'"
    return f"`{command}`'" if row == "legacy-graphql-quote" else f"`{command}'`"


def wrapped_lines(text: str, indent: str, subsequent_indent: str | None = None) -> list[str]:
    """Wrap a paragraph of a text report into lines of at most 100 columns, never broken at a hyphen.

    The first line starts with ``indent``, the others with ``subsequent_indent`` (``indent`` when it is None).
    """
    subsequent = indent if subsequent_indent is None else subsequent_indent
    return wrap(text, width=_REPORT_WIDTH, initial_indent=indent, subsequent_indent=subsequent, break_on_hyphens=False)
