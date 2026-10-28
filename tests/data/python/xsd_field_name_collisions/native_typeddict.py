"""Independent native TypedDict matching the ordinary XSD field contract."""

from __future__ import annotations

from typing import TypedDict


class NativeRoot(TypedDict):
    """Native TypedDict used as the runtime reference model."""

    item: int
    code: str
