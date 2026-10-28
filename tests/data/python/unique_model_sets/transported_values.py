"""Foreign same-spelled date type for import provenance controls."""

from __future__ import annotations

from enum import Enum


class date(Enum):  # ruff: ignore[invalid-class-name]
    """A foreign enum must not be treated as datetime.date."""

    one = 1
