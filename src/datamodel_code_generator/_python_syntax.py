"""How generated Python spells a call, a subscript, a keyword argument, a display, an import statement and a string.

Every writer of generated code that joins a variable number of expression texts into a call, a subscript or a tuple,
list, set or dict display, passes a value text under a keyword its data names, or imports names, goes through this
leaf, so the separator, the ``()`` of a subscript without arguments, the comma a lone tuple item needs, the `set()` of
a set without items, the colon of a dict entry, the equals sign of a keyword argument and the words of an import
statement are written once. A value becomes its text first (``python_literal.represent_python_value`` or a target's
own writer), a type renderer decides which types a subscript holds, a keyword written in a call's source text is that
text's own, and a writer of imports decides which names it imports, in which order, and when one takes another name.
The text between the quotes of a single-quoted string literal is escaped here, once per kind of literal; the caller
or its template writes the quotes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

QUOTE_AND_CONTROL_ESCAPES = {
    "'": r"\'",
    "\b": r"\b",
    "\f": r"\f",
    "\n": r"\n",
    "\r": r"\r",
    "\t": r"\t",
}
"""The escapes the text of any single-quoted literal needs: the quote and the control characters. The text of a raw
literal (a Pydantic pattern) needs these only, as it reads a backslash as itself."""

SINGLE_QUOTED_ESCAPES = str.maketrans({"\u0000": r"\x00", "\\": r"\\", **QUOTE_AND_CONTROL_ESCAPES})
"""The escapes of the text of a single-quoted literal that is not raw, as ``str.translate`` reads them (an enum value,
a ``TypedDict`` key): the escapes above, the backslash, and the null byte, which a module cannot hold."""


def call_text(head: str, arguments: Iterable[str], /) -> str:
    """Return the call of ``head`` with its argument texts, each spelled with its keyword where it has one."""
    return f"{head}({', '.join(arguments)})"


def subscript_text(head: str, arguments: Iterable[str], /) -> str:
    """Return the subscript of ``head`` with its argument texts, or ``head[()]`` when they write none."""
    return f"{head}[{', '.join(arguments) or '()'}]"


def keyword_argument(name: str, value: str, /) -> str:
    """Return the keyword argument that passes the expression text ``value`` as ``name``."""
    return f"{name}={value}"


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


def module_import(name: str, /) -> str:
    """Return the import statement of a module, ``name`` written as an import statement lists it."""
    return f"import {name}"


def from_import(module: str, names: Iterable[str], /) -> str:
    """Return the import statement of names from a module, each written as an import statement lists it."""
    return f"from {module} import {', '.join(names)}"


def aliased(name: str, alias: str, /) -> str:
    """Return an imported module or name as an import statement lists it when the import binds it to ``alias``."""
    return f"{name} as {alias}"
