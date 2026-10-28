"""RFC 8288 Link header fields: the targets of the links of one relation, read strictly.

A field value is a comma-separated list of links, each a `<URI-Reference>` and its `;`-separated parameters, a token or
a quoted string; empty list elements are skipped. Parameter names and relation types compare without regard to ASCII
case, and only the first `rel` parameter of a link counts. A link with an `anchor` parameter describes another context
than the response, so it never counts.
"""

from __future__ import annotations

import re
import string
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ("related",)

_TCHARS: Final = r"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
_QUOTED: Final = r'"(?:[\t \x21\x23-\x5b\x5d-\x7e\x80-\xff]|\\[\t \x21-\x7e\x80-\xff])*"'
_TARGET: Final = re.compile(r"<([^<>]*)>")
_PARAMETER: Final = re.compile(rf"[ \t]*;[ \t]*({_TCHARS})(?:[ \t]*=[ \t]*({_TCHARS}|{_QUOTED}))?")
_END: Final = re.compile(r"[ \t]*(?:,|\Z)")
_ESCAPED: Final = re.compile(r"\\(.)")
_LOWER: Final = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def _skipped(value: str, position: int) -> int:
    """Return the position after the whitespace and the commas of empty list elements at a position."""
    while position < len(value) and value[position] in " \t,":
        position += 1
    return position


def _links(value: str) -> list[tuple[str, dict[str, str]]] | None:
    """Return each link of one field value with its parameters by lowercase name, or None when it is malformed."""
    found: list[tuple[str, dict[str, str]]] = []
    position = _skipped(value, 0)
    while position < len(value):
        if (target := _TARGET.match(value, position)) is None:
            return None
        parameters: dict[str, str] = {}
        position = target.end()
        while (parameter := _PARAMETER.match(value, position)) is not None:
            raw = parameter[2] or ""
            text = _ESCAPED.sub(r"\1", raw[1:-1]) if raw.startswith('"') else raw
            parameters.setdefault(parameter[1].translate(_LOWER), text)
            position = parameter.end()
        if (end := _END.match(value, position)) is None:
            return None
        found.append((target[1], parameters))
        position = _skipped(value, end.end())
    return found


def related(values: Iterable[str], relation: str) -> list[str] | None:
    """Return the target of every link of the field values whose relation types include one, or None if malformed.

    The relation types of a `rel` parameter are separated by spaces.
    """
    wanted = relation.translate(_LOWER)
    targets: list[str] = []
    for value in values:
        if (links := _links(value)) is None:
            return None
        targets.extend(
            target
            for target, parameters in links
            if "anchor" not in parameters and wanted in parameters.get("rel", "").translate(_LOWER).split(" ")
        )
    return targets
