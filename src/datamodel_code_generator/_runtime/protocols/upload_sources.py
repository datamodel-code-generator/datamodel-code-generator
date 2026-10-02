"""Builtin upload sources: bytes held in memory and files at a path, each range read by an independent reader.

A path source hashes its file once and refuses each range of a file whose device, inode, size, or modification time
changed since then; it is not a snapshot, so the caller keeps the file unchanged while it is uploaded. The asyncio path
source runs every open, read, and close on one worker thread of its own, which closing the source stops.
"""

from __future__ import annotations

import os
from contextlib import ExitStack, asynccontextmanager, contextmanager
from hashlib import sha256
from pathlib import Path
from stat import S_ISREG
from typing import TYPE_CHECKING, BinaryIO, Final, TypeAlias

from typing_extensions import Self

from ..client.bodies import _close_file  # pyright: ignore[reportPrivateUsage]
from ..client.disk import DiskWorker, raise_late
from .errors import NonResumableSourceError, ProtocolStateError, UploadSourceChangedError
from .sources import UploadIdentity

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator
    from types import TracebackType

    from .sources import AsyncRangeReader, RangeReader

__all__ = ("AsyncBytesUploadSource", "AsyncFileUploadSource", "BytesUploadSource", "FileUploadSource")

_READ: Final = 65536
_RANGES: Final = 4
_Stat: TypeAlias = tuple[int, int, int, int]


def _checked_range(offset: int, length: int, size: int) -> None:
    """Refuse a range that is not a pair of nonnegative integers inside the content."""
    if any(type(value) is not int or value < 0 for value in (offset, length)) or offset + length > size:
        msg = "offset and length must be nonnegative integers of a range within the content"
        raise ValueError(msg)


def _checked_read(max_bytes: object) -> int:
    """Return a read size, refusing anything but a positive integer."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        msg = "max_bytes must be a positive integer"
        raise ValueError(msg)
    return max_bytes


def _identity(data: bytes) -> UploadIdentity:
    return UploadIdentity(size=len(data), sha256=sha256(data).digest())


class _BytesReader:
    """A reader of one range of bytes, with a position of its own."""

    __slots__ = ("_view",)

    def __init__(self, view: memoryview) -> None:
        self._view = view

    def read(self, max_bytes: int) -> bytes:
        """Return up to max_bytes of the range's next bytes, b'' at its end or once closed."""
        size = _checked_read(max_bytes)
        data, self._view = self._view[:size], self._view[size:]
        return bytes(data)

    def close(self) -> None:
        """End the range; later reads return b''."""
        self._view = memoryview(b"")


class _AsyncBytesReader:
    """An asyncio reader of one range of bytes, with a position of its own."""

    __slots__ = ("_reader",)

    def __init__(self, view: memoryview) -> None:
        self._reader = _BytesReader(view)

    async def read(self, max_bytes: int) -> bytes:
        """Return up to max_bytes of the range's next bytes, b'' at its end or once closed."""
        return self._reader.read(max_bytes)

    async def aclose(self) -> None:
        """End the range; later reads return b''."""
        self._reader.close()


class BytesUploadSource:
    """Upload content held in memory; any number of independent readers may read its ranges."""

    __slots__ = ("_data", "_identity")

    def __init__(self, data: bytes) -> None:
        """Keep the bytes and their identity; `from_bytes` copies any bytes-like value first."""
        self._data = data
        self._identity = _identity(data)

    @classmethod
    def from_bytes(cls, data: bytes | bytearray | memoryview) -> Self:
        """Return a source of a copy of the bytes, so later changes to a mutable buffer never reach the upload."""
        return cls(bytes(data))

    @property
    def identity(self) -> UploadIdentity:
        """Return the size and SHA-256 digest of the bytes."""
        return self._identity

    @property
    def max_parallel_ranges(self) -> int:
        """Return how many ranges may be open at once."""
        return _RANGES

    @contextmanager
    def open_range(self, offset: int, length: int) -> Generator[RangeReader, None, None]:
        """Open a reader of `length` bytes from `offset`, closed when the block ends."""
        _checked_range(offset, length, len(self._data))
        reader = _BytesReader(memoryview(self._data)[offset : offset + length])
        try:
            yield reader
        finally:
            reader.close()

    def __repr__(self) -> str:
        """Name the size only, never the content or its digest."""
        return f"{type(self).__name__}(size={self._identity.size})"


class AsyncBytesUploadSource:
    """Upload content held in memory for asyncio clients; any number of independent readers may read its ranges."""

    __slots__ = ("_data", "_identity")

    def __init__(self, data: bytes) -> None:
        """Keep the bytes and their identity; `from_bytes` copies any bytes-like value first."""
        self._data = data
        self._identity = _identity(data)

    @classmethod
    def from_bytes(cls, data: bytes | bytearray | memoryview) -> Self:
        """Return a source of a copy of the bytes, so later changes to a mutable buffer never reach the upload."""
        return cls(bytes(data))

    @property
    def identity(self) -> UploadIdentity:
        """Return the size and SHA-256 digest of the bytes."""
        return self._identity

    @property
    def max_parallel_ranges(self) -> int:
        """Return how many ranges may be open at once."""
        return _RANGES

    @asynccontextmanager
    async def open_range(self, offset: int, length: int) -> AsyncGenerator[AsyncRangeReader, None]:
        """Open a reader of `length` bytes from `offset`, closed when the block ends."""
        _checked_range(offset, length, len(self._data))
        reader = _AsyncBytesReader(memoryview(self._data)[offset : offset + length])
        try:
            yield reader
        finally:
            await reader.aclose()

    def __repr__(self) -> str:
        """Name the size only, never the content or its digest."""
        return f"{type(self).__name__}(size={self._identity.size})"


def _stat(file: BinaryIO) -> _Stat:
    stat = os.fstat(file.fileno())
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _irregular(stat: os.stat_result) -> None:
    """Refuse a file that is not a regular file, such as a FIFO, a device, or a directory."""
    if not S_ISREG(stat.st_mode):
        raise NonResumableSourceError(source_kind="stream")


def _nonblocking(path: Path) -> BinaryIO:
    """Open a binary file without waiting for a FIFO writer, closing the descriptor if wrapping fails."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    with ExitStack() as stack:
        stack.callback(os.close, descriptor)
        file = os.fdopen(descriptor, "rb")
        stack.pop_all()
    return file


def _regular(path: Path) -> BinaryIO:
    """Open a regular file, refusing anything else before opening it and the descriptor it opened.

    The open does not wait for a writer, even when the path became a FIFO after it was checked, and the descriptor is
    checked again; nonblocking mode changes nothing for a regular file's reads.
    """
    _irregular(path.stat())
    with ExitStack() as stack:
        file = stack.enter_context(_nonblocking(path))
        _irregular(os.fstat(file.fileno()))
        stack.pop_all()
    return file


def _digested(file: BinaryIO, size: int) -> UploadIdentity:
    """Hash at most the size a file had when it was opened, in bounded reads."""
    digest, left = sha256(), size
    while chunk := file.read(min(_READ, left)):
        digest.update(chunk)
        left -= len(chunk)
    return UploadIdentity(size=size - left, sha256=digest.digest())


def _opened(path: Path, stat: _Stat, identity: UploadIdentity, offset: int) -> BinaryIO:
    """Open a file at a range's offset, refusing one whose status changed since it was hashed."""
    with ExitStack() as stack:
        file = stack.enter_context(_nonblocking(path))
        if _stat(file) != stat:
            raise UploadSourceChangedError(expected=identity, actual=None, offset=offset)
        file.seek(offset)
        stack.pop_all()
    return file


class _FileReader:
    """A reader of one range of a file, through a descriptor of its own."""

    __slots__ = ("_file", "_left")

    def __init__(self, file: BinaryIO, length: int) -> None:
        self._file = file
        self._left = length

    def read(self, max_bytes: int) -> bytes:
        """Return up to max_bytes of the range's next bytes, b'' at its end or once closed."""
        size = _checked_read(max_bytes)
        if not self._left or self._file.closed:
            return b""
        data = self._file.read(min(size, self._left))
        self._left -= len(data)
        return data

    def close(self) -> None:
        """Close the descriptor; closing again does nothing."""
        self._file.close()


class FileUploadSource:
    """Upload content of a file, hashed once; each range is read through a descriptor of its own."""

    __slots__ = ("_identity", "_path", "_stat")

    def __init__(self, path: Path, identity: UploadIdentity, stat: _Stat) -> None:
        """Keep the path, the identity of its content, and its status when it was hashed; use `from_path`."""
        self._path = path
        self._identity = identity
        self._stat = stat

    @classmethod
    def from_path(cls, path: str | os.PathLike[str]) -> Self:
        """Hash the file at a path once and return its source."""
        found = Path(path)
        with _regular(found) as file:
            stat = _stat(file)
            identity = _digested(file, stat[2])
        return cls(found, identity, stat)

    @property
    def identity(self) -> UploadIdentity:
        """Return the size and SHA-256 digest the file had when it was hashed."""
        return self._identity

    @property
    def max_parallel_ranges(self) -> int:
        """Return how many ranges may be open at once."""
        return _RANGES

    @contextmanager
    def open_range(self, offset: int, length: int) -> Generator[RangeReader, None, None]:
        """Open a reader of `length` bytes from `offset`, refusing a file that changed; the block's end closes it."""
        _checked_range(offset, length, self._identity.size)
        reader = _FileReader(_opened(self._path, self._stat, self._identity, offset), length)
        try:
            yield reader
        finally:
            reader.close()

    def __repr__(self) -> str:
        """Name the size only, never the path or the digest."""
        return f"{type(self).__name__}(size={self._identity.size})"


class _AsyncFileReader:
    """An asyncio reader of one range of a file, whose descriptor the source's worker reads and closes."""

    __slots__ = ("_closed", "_file", "_left", "_worker")

    def __init__(self, file: BinaryIO, length: int, worker: DiskWorker) -> None:
        self._file = file
        self._left = length
        self._worker = worker
        self._closed = False

    async def read(self, max_bytes: int) -> bytes:
        """Return up to max_bytes of the range's next bytes, b'' at its end or once closed."""
        size = _checked_read(max_bytes)
        if not self._left or self._closed:
            return b""
        data = await self._worker.run(self._file.read, min(size, self._left))
        self._left -= len(data)
        return data

    async def aclose(self) -> None:
        """Close the descriptor on the worker and release the worker; closing again does nothing."""
        if self._closed:
            return
        self._closed = True
        try:
            await self._worker.run(self._file.close, cleanup=True)
        finally:
            self._worker.release()


class AsyncFileUploadSource:
    """Upload content of a file for asyncio clients, hashed once; one worker thread of its own does every file I/O.

    `aclose`, or leaving an `async with` block, stops the worker once the readers it serves are closed; a source a
    client borrows is never closed by the client.
    """

    __slots__ = ("_identity", "_path", "_stat", "_worker")

    def __init__(self, path: Path, identity: UploadIdentity, stat: _Stat, worker: DiskWorker) -> None:
        """Keep the path, its content's identity, its status when it was hashed, and the worker; use `from_path`."""
        self._path = path
        self._identity = identity
        self._stat = stat
        self._worker = worker

    @classmethod
    async def from_path(cls, path: str | os.PathLike[str]) -> Self:
        """Hash the file at a path once on the source's worker and return the source, which owns that worker."""
        found = Path(path)
        worker = DiskWorker("AsyncFileUploadSource")
        try:
            file = await worker.run(_regular, found, discard=_close_file)
            try:
                stat = await worker.run(_stat, file)
                digest, left = sha256(), stat[2]
                while chunk := await worker.run(file.read, min(_READ, left)):
                    digest.update(chunk)
                    left -= len(chunk)
            finally:
                await worker.run(file.close, cleanup=True)
        except BaseException:
            worker.close()
            raise
        identity = UploadIdentity(size=stat[2] - left, sha256=digest.digest())
        return cls(found, identity, stat, worker)

    @property
    def identity(self) -> UploadIdentity:
        """Return the size and SHA-256 digest the file had when it was hashed."""
        return self._identity

    @property
    def max_parallel_ranges(self) -> int:
        """Return how many ranges may be open at once."""
        return _RANGES

    @asynccontextmanager
    async def open_range(self, offset: int, length: int) -> AsyncGenerator[AsyncRangeReader, None]:
        """Open a reader of `length` bytes from `offset` on the worker, refusing a changed file or a closed source."""
        _checked_range(offset, length, self._identity.size)
        worker = self._worker
        if worker.closed:
            raise ProtocolStateError(state="closed", action="open_range")
        worker.acquire()
        try:
            file = await worker.run(_opened, self._path, self._stat, self._identity, offset, discard=_close_file)
        except BaseException:
            worker.release()
            raise
        reader = _AsyncFileReader(file, length, worker)
        try:
            yield reader
        finally:
            await reader.aclose()

    async def aclose(self) -> None:
        """Stop the worker once its open readers are closed, and wait for the work interrupted callers left."""
        self._worker.close()
        raise_late(await self._worker.settled(every=True))

    async def __aenter__(self) -> Self:
        """Return this source, which leaving the block closes."""
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close the source."""
        await self.aclose()

    def __repr__(self) -> str:
        """Name the size only, never the path or the digest."""
        return f"{type(self).__name__}(size={self._identity.size})"
