"""Upload sources, their range readers, content identities, part receipts, and the progress of an upload."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from .records import record_instance, record_string

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager, AbstractContextManager

__all__ = (
    "AsyncRangeReader",
    "AsyncUploadSource",
    "PartReceipt",
    "RangeReader",
    "UploadIdentity",
    "UploadProgress",
    "UploadSource",
)

_DIGEST_BYTES = 32


def _count(value: object, name: str, minimum: int = 0) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{name} must be an integer"
        raise TypeError(msg)
    if value < minimum:
        msg = f"{name} must be at least {minimum}"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadIdentity:
    """The immutable identity of upload content, or of one checked range of it: its size and SHA-256 digest."""

    size: int
    sha256: bytes = field(repr=False)

    def __post_init__(self) -> None:
        """Require a nonnegative size and a 32-byte digest."""
        _count(self.size, "size")
        record_instance(self.sha256, bytes, "sha256 must be bytes")
        if len(self.sha256) != _DIGEST_BYTES:
            msg = "sha256 must be 32 bytes"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class PartReceipt:
    """A part a server confirmed: its index, from 1, and the receipt the server gave for it."""

    index: int
    receipt: str = field(repr=False)

    def __post_init__(self) -> None:
        """Require a positive index and a string receipt."""
        _count(self.index, "index", 1)
        record_string(self.receipt, "receipt")


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadProgress:
    """How far an upload is: the bytes and parts the server confirmed, the total, and whether it completed."""

    confirmed_bytes: int
    total_bytes: int
    confirmed_parts: tuple[PartReceipt, ...] = ()
    complete: bool = False

    def __post_init__(self) -> None:
        """Require counts within the total, a tuple of receipts, and a boolean completion flag."""
        _count(self.total_bytes, "total_bytes")
        _count(self.confirmed_bytes, "confirmed_bytes")
        if self.confirmed_bytes > self.total_bytes:
            msg = "confirmed_bytes must not exceed total_bytes"
            raise ValueError(msg)
        record_instance(self.confirmed_parts, tuple, "confirmed_parts must be a tuple")
        for receipt in self.confirmed_parts:
            record_instance(receipt, PartReceipt, "confirmed_parts must hold PartReceipt values")
        record_instance(self.complete, bool, "complete must be a bool")


class RangeReader(Protocol):
    """A reader of one range of an upload source, positioned independently of every other reader."""

    def read(self, max_bytes: int) -> bytes:
        """Return the next 0 to max_bytes bytes of the range; b'' is its permanent end."""
        ...

    def close(self) -> None:
        """Release the reader; closing again does nothing."""
        ...


class AsyncRangeReader(Protocol):
    """An asyncio reader of one range of an upload source, positioned independently of every other reader."""

    async def read(self, max_bytes: int) -> bytes:
        """Return the next 0 to max_bytes bytes of the range; b'' is its permanent end."""
        ...

    async def aclose(self) -> None:
        """Release the reader; closing again does nothing."""
        ...


class UploadSource(Protocol):
    """Borrowed upload content with an immutable identity that opens independent readers of its ranges."""

    @property
    def identity(self) -> UploadIdentity:
        """Return the size and SHA-256 digest of the whole content."""
        ...

    @property
    def max_parallel_ranges(self) -> int:
        """Return how many ranges may be open at once, at least 1."""
        ...

    def open_range(self, offset: int, length: int) -> AbstractContextManager[RangeReader]:
        """Return a context that opens a reader of `length` bytes from `offset` and closes it on exit."""
        ...


class AsyncUploadSource(Protocol):
    """Borrowed upload content for asyncio clients, opening independent native asyncio readers of its ranges."""

    @property
    def identity(self) -> UploadIdentity:
        """Return the size and SHA-256 digest of the whole content."""
        ...

    @property
    def max_parallel_ranges(self) -> int:
        """Return how many ranges may be open at once, at least 1."""
        ...

    def open_range(self, offset: int, length: int) -> AbstractAsyncContextManager[AsyncRangeReader]:
        """Return a context that opens a reader of `length` bytes from `offset` and closes it on exit."""
        ...
