"""Native TypedDict for the scoped alias runtime contract."""

from __future__ import annotations

from typing import TypedDict

from typing_extensions import NotRequired


class NativeChild(TypedDict):
    """Child fields with the final Python field name."""

    base: int
    renamed: str
    y: NotRequired[int]
    z: NotRequired[bool]


class NativeRoot(TypedDict):
    """Native root used to check Pydantic's TypedDict support."""

    child: NativeChild
