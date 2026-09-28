"""Name resources, methods, and arguments of a client target by the fixed snake_case rule."""

from __future__ import annotations

import keyword
import re
import unicodedata
from typing import Final

_ACRONYM: Final = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL: Final = re.compile(r"([a-z0-9])([A-Z])")
_SEPARATORS: Final = re.compile(r"_+")
_IDENTIFIER: Final = re.compile(r"[a-z][a-z0-9_]*")
_PLACEHOLDER: Final = re.compile(r"\{([^{}]*)\}")
RESERVED_MEMBERS: Final = frozenset({
    "aclose",
    "close",
    "protocols",
    "request_raw",
    "reset_circuit",
    "with_options",
    "with_raw_response",
    "with_response",
    "with_streaming_response",
})
RESERVED_ARGUMENTS: Final = frozenset({"body", "media_type", "options", "response_media_type", "self"})
WINDOWS_DEVICES: Final = frozenset({
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
})


def snake(value: str) -> str:
    """Return the snake_case Python name of free text, or an empty string when nothing of it remains.

    The text is NFKC-normalized and split at ASCII case boundaries; ASCII letters are lowercased, each other code
    point becomes a `u<hex>` token, and every other character separates tokens.
    """
    text = _CAMEL.sub(r"\1_\2", _ACRONYM.sub(r"\1_\2", unicodedata.normalize("NFKC", value)))
    parts = [(char.lower() if char.isalnum() else "_") if char.isascii() else f"_u{ord(char):x}_" for char in text]
    if not (name := _SEPARATORS.sub("_", "".join(parts)).strip("_")):
        return ""
    if name[0].isdigit():
        name = f"n_{name}"
    return f"{name}_" if keyword.iskeyword(name) else name


def pascal(name: str) -> str:
    """Return the PascalCase form of a snake_case name: its tokens with their first letter capitalized."""
    return "".join(token[:1].upper() + token[1:] for token in name.split("_") if token)


def method_name(method: str, path: str) -> str:
    """Return the name of an operation without operationId: its method, then its path's literal and parameter parts."""
    parts: list[str] = []
    for index, piece in enumerate(_PLACEHOLDER.split(path)):
        if part := (f"by_{snake(piece)}" if index % 2 else snake(piece)):
            parts.append(part)
    return "_".join((method.lower(), *(parts or ["root"])))


def identifier(value: object) -> bool:
    """Return whether an explicit name is a lowercase ASCII identifier that is not a keyword."""
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None and not keyword.iskeyword(value)


def namespace_problem(value: object) -> str | None:
    """Return why a resource namespace is invalid, or None for dotted lowercase identifiers that name no member."""
    if not isinstance(value, str) or not all(identifier(part) for part in value.split(".")):
        return "A resource namespace must be dotted lowercase ASCII identifiers"
    if reserved := next((part for part in value.split(".") if part in RESERVED_MEMBERS | WINDOWS_DEVICES), None):
        return f"A resource namespace cannot use the reserved name {reserved!r}"
    return None
