"""Native binary inputs and small adapters for seekable request bodies."""

from __future__ import annotations

from collections.abc import AsyncIterable, Iterable, Mapping
from inspect import iscoroutinefunction
from io import TextIOBase
from os import SEEK_END, PathLike, fsdecode
from pathlib import Path
from typing import IO, TYPE_CHECKING, Final, TypeAlias, cast

from typing_extensions import TypeIs

from .coding import CHUNK
from .errors import body_failure
from .logical import in_thread

if TYPE_CHECKING:
    from abc import abstractmethod
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
    from typing import Protocol

    class SyncContent(Protocol):
        @property
        def content_length(self) -> int | None:
            """Return the native stream's known length."""

        @property
        def content_type(self) -> str | None:
            """Return an encoded media type if present."""

        @abstractmethod
        def iter_bytes(self) -> Iterator[bytes]:
            """Yield bytes for one native request stream."""

    class AsyncContent(Protocol):
        @property
        def content_length(self) -> int | None:
            """Return the native stream's known length."""

        @property
        def content_type(self) -> str | None:
            """Return an encoded media type if present."""

        @abstractmethod
        def aiter_bytes(self) -> AsyncIterator[bytes]:
            """Yield bytes for one asynchronous request stream."""


SyncBinaryBody: TypeAlias = bytes | IO[bytes] | PathLike[str] | Iterable[bytes]
AsyncBinaryBody: TypeAlias = SyncBinaryBody | AsyncIterable[bytes]
_NOT_BINARY: Final = (str, bytearray, memoryview, Mapping, TextIOBase)


def _is_file(value: object) -> bool:
    return callable(read := getattr(value, "read", None)) and not iscoroutinefunction(read)


def _async_read(value: object) -> Callable[[int], Awaitable[bytes]] | None:
    return (
        cast("Callable[[int], Awaitable[bytes]]", read)
        if iscoroutinefunction(read := getattr(value, "read", None))
        else None
    )


def is_file_input(value: object) -> bool:
    """Identify paths and synchronously read file objects without opening or consuming them."""
    return isinstance(value, PathLike) or _is_file(value)


def is_binary_input(value: object) -> TypeIs[SyncBinaryBody]:
    """Accept native synchronous binary inputs, never text or a mutable buffer."""
    return isinstance(value, bytes) or (
        not isinstance(value, _NOT_BINARY) and (is_file_input(value) or isinstance(value, Iterable))
    )


def is_async_binary_input(value: object) -> TypeIs[AsyncBinaryBody]:
    """Accept synchronous inputs, async files with an awaitable read, and native async iterables."""
    return isinstance(value, AsyncIterable) or _async_read(value) is not None or is_binary_input(value)


class EncodedAttempt:
    """Immutable bytes encoded once and reused across attempts."""

    __slots__ = ("content", "content_type")

    def __init__(self, content: bytes, content_type: str | None) -> None:
        self.content = content
        self.content_type = content_type

    @property
    def content_length(self) -> int:
        """Return the encoded length."""
        return len(self.content)

    def iter_bytes(self) -> Iterator[bytes]:
        """Yield the retained bytes."""
        yield self.content

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        """Yield the retained bytes."""
        yield self.content


def _step(chunks: Iterator[bytes]) -> bytes | None:
    return next(chunks, None)


class BinarySource:
    """Keep an input's entry offset and own only files opened from paths."""

    __slots__ = ("_aread", "_file", "_input", "_offset", "_owned", "_used", "content_length")

    def __init__(self, content: AsyncBinaryBody) -> None:
        self._input = content
        self._file = cast("IO[bytes]", content) if _is_file(content) else None
        self._aread = _async_read(content)
        self._owned = False
        self._used = False
        self._offset: int | None = None
        self.content_length: int | None = None
        if self._file is not None:
            self._measure(self._file)

    def _measure(self, file: IO[bytes]) -> None:
        """Keep a seekable file's offset and remaining length, as HTTPX2 frames a file."""
        try:
            offset = file.tell()
            end = file.seek(0, SEEK_END)
            file.seek(offset)
        except (AttributeError, OSError):
            return
        except ValueError as error:
            raise body_failure(reason="unencodable", cause=error) from error
        self._offset, self.content_length = offset, max(0, end - offset)

    @property
    def replayable(self) -> bool:
        """Replay unopened paths, seekable files and unconsumed inputs."""
        return not self._used or self._offset is not None

    def open(self) -> BinaryContent:
        """Open a path lazily and rewind a seekable file before sending."""
        if isinstance(self._input, PathLike) and self._file is None:
            try:
                file = Path(fsdecode(self._input)).open("rb")  # noqa: SIM115 - The call owns it until its end.
            except OSError as error:
                raise body_failure(reason="unencodable", cause=error) from error
            self._file, self._owned = file, True
            self._measure(file)
        if self._file is not None and self._offset is not None:
            try:
                self._file.seek(self._offset)
            except (OSError, ValueError) as error:
                raise body_failure(reason="body_not_replayable", cause=error) from error
        return BinaryContent(self)

    async def aopen(self) -> BinaryContent:
        """Open and rewind a path's file in a thread; a caller's file is rewound where the call runs."""
        return await in_thread(self.open) if isinstance(self._input, PathLike) else self.open()

    def chunks(self) -> Iterator[bytes]:
        """Read a file's measured bytes in bounded chunks, or consume the caller's iterable."""
        self._used = True
        if (file := self._file) is None:
            yield from cast("Iterable[bytes]", self._input)
            return
        if self._offset is not None:
            file.seek(self._offset)
        left = self.content_length
        while left != 0 and (chunk := file.read(CHUNK if left is None else min(CHUNK, left))):
            yield chunk
            if left is not None:
                left -= len(chunk)

    async def achunks(self) -> AsyncIterator[bytes]:
        """Read an async file or a path's file in bounded chunks, or consume the caller's iterable or file.

        A file this call opened from a path is read in a thread, one chunk at a time; a caller's file is read where
        the call runs.
        """
        if (read := self._aread) is not None:
            self._used = True
            while chunk := await read(CHUNK):
                yield chunk
        elif self._owned:
            chunks, left = self.chunks(), self.content_length
            while left != 0:
                if (piece := await in_thread(_step, chunks)) is None:
                    break
                yield piece
                left = None if left is None else left - len(piece)
        elif isinstance(self._input, AsyncIterable):
            self._used = True
            async for chunk in self._input:
                yield chunk
        else:
            for chunk in self.chunks():
                yield chunk

    def close(self) -> OSError | None:
        """Close an SDK-opened path and return its failure, while leaving caller files open."""
        if self._owned and self._file is not None:
            file, self._file = self._file, None
            self._owned = False
            try:
                file.close()
            except OSError as error:
                return error
        return None

    @property
    def owned(self) -> bool:
        """Report whether this call opened the file from a path and has yet to close it."""
        return self._owned


class BinaryContent:
    """One native request stream backed by its call's input."""

    __slots__ = ("_source", "content_length", "content_type")

    def __init__(self, source: BinarySource) -> None:
        self._source = source
        self.content_length = source.content_length
        self.content_type: str | None = None

    def iter_bytes(self) -> Iterator[bytes]:
        """Return the native binary chunks."""
        return self._source.chunks()

    def aiter_bytes(self) -> AsyncIterator[bytes]:
        """Return the native binary chunks in async mode."""
        return self._source.achunks()
