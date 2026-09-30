"""Recognize Bearer rejection challenges without retaining their descriptive values."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from .responses import HeadersView

_WORD: Final = r"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
_TEXT: Final = r"[^\x00-\x08\x0a-\x1f\x7f]"
_PARAMETER: Final = re.compile(rf'({_WORD})[ \t]*=[ \t]*({_WORD}|"(?:(?![\\"]){_TEXT}|\\{_TEXT})*")')
_CHALLENGE: Final = re.compile(rf"({_WORD})(?: +(.+))?")
_TOKEN68: Final = re.compile(r"[A-Za-z0-9._~+/-]+=*")
_INVALID: Final = 1
_SCOPE: Final = 2


def _parts(value: str) -> tuple[str, ...] | None:
    parts: list[str] = []
    quoted = escaped = False
    start = 0
    for index, char in enumerate(value):
        if escaped:
            escaped = False
        elif quoted and char == "\\":
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif char == "," and not quoted:
            parts.append(value[start:index].strip(" \t"))
            start = index + 1
    if quoted:
        return None
    parts.append(value[start:].strip(" \t"))
    return tuple(part for part in parts if part)


def _parameter(value: str) -> tuple[str, str] | None:
    match = _PARAMETER.fullmatch(value)
    if match is None:
        return None
    name, text = match.groups()
    if text.startswith('"'):
        text = re.sub(r"\\(.)", r"\1", text[1:-1])
    return name.lower(), text


def _flags(value: str) -> int:
    parts = _parts(value)
    if parts is None:
        return 0
    scheme = ""
    names: set[str] = set()
    parameters = True
    flags = 0
    for part in parts:
        parameter = _parameter(part)
        if parameter is None:
            match = _CHALLENGE.fullmatch(part)
            if match is None:
                return 0
            scheme, rest = match.groups()
            scheme = scheme.lower()
            names.clear()
            parameters = True
            if rest is None:
                continue
            parameter = _parameter(rest)
            if parameter is None:
                if _TOKEN68.fullmatch(rest) is None:
                    return 0
                parameters = False
                continue
        name, text = parameter
        if not scheme or not parameters or name in names:
            return 0
        names.add(name)
        if scheme == "bearer" and name == "error":
            if text == "invalid_token":
                flags |= _INVALID
            elif text == "insufficient_scope":
                flags |= _SCOPE
    return flags


def invalid_token(headers: HeadersView, *, challenge_less: bool) -> bool:
    """Accept a valid invalid_token challenge, with an explicit insufficient_scope veto."""
    values = headers.get_all("WWW-Authenticate")
    if not values:
        return challenge_less
    flags = 0
    for value in values:
        flags |= _flags(value)
    return bool(flags & _INVALID) and not flags & _SCOPE
