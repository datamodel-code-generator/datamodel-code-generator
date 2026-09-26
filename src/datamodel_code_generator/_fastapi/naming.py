"""Normalize operation, router, and argument names into unique, non-keyword Python identifiers."""

from __future__ import annotations

import keyword
import re
import unicodedata
from typing import Final

_ACRONYM: Final = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL: Final = re.compile(r"([a-z0-9])([A-Z])")
_OTHER: Final = re.compile(r"[^0-9A-Za-z_]+")
_WINDOWS_DEVICES: Final = frozenset({
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
})


def normalize(value: str, *, empty: str, digit: str) -> str:
    """Normalize free text into a Python identifier: NFKC, CamelCase splits, underscores, and lowercase."""
    text = _CAMEL.sub(r"\1_\2", _ACRONYM.sub(r"\1_\2", unicodedata.normalize("NFKC", value)))
    name = _OTHER.sub("_", text).lower().strip("_") or empty
    if name[0].isdigit():
        name = f"{digit}{name}"
    return f"{name}_" if keyword.iskeyword(name) else name


def explicit(value: str) -> bool:
    """Return whether an explicit name is an ASCII, non-keyword identifier that does not start with `__`."""
    return value.isascii() and value.isidentifier() and not keyword.iskeyword(value) and not value.startswith("__")


def file_stem_conflict(stem: str) -> bool:
    """Return whether a file stem is reserved by the package layout or by Windows device names."""
    return stem.casefold() in {"__init__", *_WINDOWS_DEVICES}
