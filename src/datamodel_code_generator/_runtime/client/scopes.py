"""Canonical scope tokens shared by public auth values and errors."""

from __future__ import annotations

import re
from typing import Final, TypeGuard

_TOKEN: Final = re.compile(r"[\x21\x23-\x5b\x5d-\x7e]+")


def _scope_items(value: object) -> TypeGuard[tuple[object, ...] | list[object]]:
    return isinstance(value, (tuple, list))


def scope_tuple(value: object) -> tuple[str, ...]:
    """Copy explicit scope collections into sorted, distinct ASCII tokens."""
    if not _scope_items(value):
        msg = "Scopes must be a tuple or list."
        raise ValueError(msg)
    normalized: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not _TOKEN.fullmatch(item):
            msg = "Scopes must contain valid ASCII scope tokens."
            raise ValueError(msg)
        normalized.add(item)
    return tuple(sorted(normalized))
