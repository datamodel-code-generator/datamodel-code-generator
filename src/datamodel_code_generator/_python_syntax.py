"""How generated Python spells calls, subscripts, keyword arguments, displays, imports, strings and docstrings.

Every writer of generated code that joins a variable number of expression texts into a call, a subscript or a tuple,
list, set or dict display, passes a value text under a keyword its data names, or imports names, goes through this
leaf, so the separator, the ``()`` of a subscript without arguments, the comma a lone tuple item needs, the `set()` of
a set without items, the colon of a dict entry, the equals sign of a keyword argument and the words of an import
statement are written once. A value becomes its text first (``python_literal.represent_python_value`` or a target's
own writer), a type renderer decides which types a subscript holds, a keyword written in a call's source text is that
text's own, and a writer of imports decides which names it imports, in which order, and when one takes another name.
The text between the quotes of a single-quoted string literal is escaped here, once per kind of literal; the caller
or its template writes the quotes. So are the text of a docstring and of a comment: the escapes a triple-quoted
docstring needs, its one-line and block forms, and the line endings a comment block keeps; templates read the public
functions as filters through ``model.base``, which keeps their names. Every reader of text that ends its lines as
Python's universal newlines do (CRLF and a lone CR read as LF), source text and comments alike, reads it through
``normalize_newlines``.
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

RAW_SINGLE_QUOTED_ESCAPES = str.maketrans(QUOTE_AND_CONTROL_ESCAPES)
"""The escapes of the text of a raw single-quoted literal (a Pydantic pattern), as ``str.translate`` reads them."""


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


def normalize_newlines(text: str) -> str:
    """Return ``text`` with every CRLF and lone CR as LF, as Python's universal newlines read a file."""
    return text.replace("\r\n", "\n").replace("\r", "\n") if "\r" in text else text


def _normalize_string(value: str) -> str:
    """Return the underlying built-in string without invoking subclass overrides."""
    return value if type(value) is str else str.__str__(value)  # noqa: PLC2801 - bypass untrusted subclass overrides


class _EscapedDocstring(str):  # noqa: FURB189
    """Marker for values already escaped for a generated Python docstring."""

    __slots__ = ()


def escape_docstring(value: str | None) -> str | None:
    r"""Escape special characters in a docstring to prevent syntax errors.

    Handles:
    - Backslashes: `\\` -> `\\\\` (must be escaped first)
    - Triple quotes: `\"\"\"` -> `\\\"\\\"\\\"` (would terminate docstring)
    - Null bytes: written as `\\x00`, as a module cannot hold one

    Args:
        value: The string to escape, or None.

    Returns:
        The escaped string, or None if input was None.
    """
    if value is None:
        return None
    if type(value) is _EscapedDocstring:
        return value
    value = _normalize_string(value)
    # Escape backslashes first, then triple quotes. Retain the original string
    # when no escaping is needed so custom-template fast paths stay allocation-free.
    escaped = value.replace("\\", "\\\\").replace('"""', '\\"\\"\\"').replace("\0", "\\x00")
    return _EscapedDocstring(escaped) if escaped != value else value


def _ends_with_unescaped_quote(value: str) -> bool:
    """Return whether *value* ends with a double quote that is not escaped."""
    if not value.endswith('"'):
        return False

    backslash_count = 0
    for char in reversed(value[:-1]):
        if char != "\\":
            break
        backslash_count += 1
    return backslash_count % 2 == 0


def _single_line_docstring(escaped: str) -> str:
    """Return the one-line docstring of escaped text, with its last double quote escaped so it cannot close it."""
    if _ends_with_unescaped_quote(escaped):
        escaped = f'{escaped[:-1]}\\"'
    return f'"""{escaped}"""'


def format_docstring(value: str | None, indent_spaces: int = 0, *, use_single_line_docstring: bool = False) -> str:
    """Format *value* as a docstring as per PEP 257.

    PEP 257 recommends that docstrings that can fit on one line should be formatted on a
    single line, for consistency and readability. When use_single_line_docstring is
    false, docstrings retain the historical multi-line formatting. It is assumed that
    the opening triple-quotes are indented appropriately in the template. If it's a
    multi-line docstring, each line including the closing triple-quotes will be indented
    as per indent_spaces.

    Args:
        value: docstring text
        indent_spaces: Spaces to indent for all lines after the opening triple-quotes
        use_single_line_docstring: Use one-line docstrings when possible

    Returns:
        Empty string when `value` is falsy; otherwise the docstring block.
    """
    if value is None:
        return ""
    if type(value) is not _EscapedDocstring:
        value = _normalize_string(value)
    if not value.strip():
        return ""

    escaped = escape_docstring(value) or ""

    if use_single_line_docstring and "\n" not in value and "\r" not in value:
        return _single_line_docstring(escaped)

    indent = max(indent_spaces, 0) * " "
    if indent:
        escaped = "\n".join(f"{indent}{line}" if line else "" for line in escaped.split("\n"))
        return f'"""\n{escaped}\n{indent}"""'
    return f'"""\n{escaped}\n"""'


def comment_safe(value: str | None) -> str | None:
    """Normalize line endings before rendering text in Python comments.

    Built-in union templates already prefix LF continuation lines with ``# ``.
    This helper converts CRLF and bare CR into LF so that existing template
    behavior keeps the whole description inside the comment block.
    """
    if value is None:
        return None
    return normalize_newlines(_normalize_string(value))


def inline_comment_safe(value: str | None) -> str | None:
    """Make a value safe for a generated inline Python comment."""
    if value is None:
        return None
    safe_value = comment_safe(value) or ""
    return safe_value.replace("\v", "\n").replace("\f", "\n").replace("\n", "\n# ")
