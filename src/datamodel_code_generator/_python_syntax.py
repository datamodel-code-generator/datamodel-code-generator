"""How generated Python spells a call and a tuple, list, set or dict display from the texts of their items.

Every writer of generated code that joins a variable number of expression texts into one of these goes through this
leaf, so the separator, the comma a lone tuple item needs, the `set()` of a set without items and the colon of a dict
entry are written once. A value becomes its text first (``python_literal.represent_python_value`` or a target's own
writer); type subscripts are the type renderers' own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


def call_text(head: str, arguments: Iterable[str], /) -> str:
    """Return the call of ``head`` with its argument texts, each spelled with its keyword where it has one."""
    return f"{head}({', '.join(arguments)})"


def tuple_display(items: Iterable[str], /) -> str:
    """Return the tuple display of expression texts, with the comma a lone item needs."""
    texts = tuple(items)
    return f"({', '.join(texts)}{',' if len(texts) == 1 else ''})"


def list_display(items: Iterable[str], /) -> str:
    """Return the list display of expression texts."""
    return f"[{', '.join(items)}]"


def set_display(items: Iterable[str], /) -> str:
    """Return the set display of expression texts, or ``set()`` without one, since ``{}`` is an empty dict."""
    texts = tuple(items)
    return f"{{{', '.join(texts)}}}" if texts else "set()"


def dict_display(entries: Iterable[tuple[str, str]], /) -> str:
    """Return the dict display of the texts of each entry's key and value."""
    return f"{{{', '.join(f'{key}: {value}' for key, value in entries)}}}"
