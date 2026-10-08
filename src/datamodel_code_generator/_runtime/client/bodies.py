"""Native binary inputs and small adapters for seekable request bodies."""

from __future__ import annotations

from collections.abc import AsyncIterable, Iterable, Mapping
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, TypeAlias, cast

from typing_extensions import TypeIs

from .coding import CHUNK
from .errors import body_failure

if TYPE_CHECKING:
    from abc import abstractmethod
    from collections.abc import AsyncIterator, Iterator
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


SyncBinaryBody: TypeAlias = bytes | BinaryIO | PathLike[str] | Iterable[bytes]
AsyncBinaryBody: TypeAlias = SyncBinaryBody | AsyncIterable[bytes]


def is_file_input(value: object) -> bool:
    """Identify paths and readable file objects without opening or consuming them."""
    return not isinstance(value, bytes) and (isinstance(value, PathLike) or callable(getattr(value, "read", None)))


def is_binary_input(value: object) -> TypeIs[SyncBinaryBody]:
    """Accept native synchronous binary inputs."""
    return (
        isinstance(value, bytes)
        or is_file_input(value)
        or (isinstance(value, Iterable) and not isinstance(value, (str, Mapping)))
    )


def is_async_binary_input(value: object) -> TypeIs[AsyncBinaryBody]:
    """Accept synchronous inputs and native async iterables."""
    return is_binary_input(value) or isinstance(value, AsyncIterable)


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


class BinarySource:
    """Keep an input's entry offset and own only files opened from paths."""

    __slots__ = ("_file", "_input", "_offset", "_owned", "_used", "content_length")

    def __init__(self, content: AsyncBinaryBody) -> None:
        self._input = content
        self._file = cast("BinaryIO", content) if callable(getattr(content, "read", None)) else None
        self._owned = False
        self._used = False
        self._offset: int | None = None
        self.content_length: int | None = None
        if self._file is not None:
            self._position()

    def _position(self) -> None:
        file = cast("BinaryIO", self._file)
        try:
            self._offset = file.tell()
        except OSError:
            return
        try:
            end = file.seek(0, 2)
        except OSError:
            self._offset = None
            return
        file.seek(self._offset)
        self.content_length = end - self._offset

    @property
    def replayable(self) -> bool:
        """Replay unopened paths, seekable files and unconsumed inputs."""
        return not self._used or self._offset is not None

    def open(self) -> BinaryContent:
        """Open a path lazily and rewind a seekable file before sending."""
        if isinstance(self._input, PathLike) and self._file is None:
            self._file = Path(self._input).open("rb")  # noqa: SIM115 - The call owns the file until its final cleanup.
            self._owned = True
            self._position()
        if self._file is not None and self._offset is not None:
            try:
                self._file.seek(self._offset)
            except OSError as error:
                raise body_failure(reason="body_not_replayable", cause=error) from error
        return BinaryContent(self)

    def chunks(self) -> Iterator[bytes]:
        """Read a file conventionally or consume the caller's iterable."""
        self._used = True
        if self._file is not None:
            if self._offset is not None:
                self._file.seek(self._offset)
            while chunk := self._file.read(CHUNK):
                yield chunk
        else:
            yield from cast("Iterable[bytes]", self._input)

    async def achunks(self) -> AsyncIterator[bytes]:
        """Consume a native async iterable or read conventional binary input."""
        if isinstance(self._input, AsyncIterable):
            self._used = True
            async for chunk in self._input:
                yield chunk
        else:
            for chunk in self.chunks():
                yield chunk

    def close(self) -> None:
        """Close an SDK-opened path while leaving caller files open."""
        if self._owned and self._file is not None:
            file, self._file = self._file, None
            self._owned = False
            file.close()


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
