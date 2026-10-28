"""Upload sources and the progress of an upload."""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, TypeAlias

from .records import record_instance

__all__ = ("UploadProgress", "UploadSource")

UploadSource: TypeAlias = bytes | bytearray | memoryview | BinaryIO
"""Upload content: bytes, or a seekable binary file read from its position at `start` or `resume` to its end."""


def _count(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{name} must be an integer"
        raise TypeError(msg)
    if value < 0:
        msg = f"{name} must be at least 0"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadProgress:
    """How far an upload is: the bytes the server confirmed, the total, and whether it completed."""

    confirmed_bytes: int
    total_bytes: int
    complete: bool = False

    def __post_init__(self) -> None:
        """Require counts within the total and a boolean completion flag."""
        _count(self.total_bytes, "total_bytes")
        _count(self.confirmed_bytes, "confirmed_bytes")
        if self.confirmed_bytes > self.total_bytes:
            msg = "confirmed_bytes must not exceed total_bytes"
            raise ValueError(msg)
        record_instance(self.complete, bool, "complete must be a bool")
