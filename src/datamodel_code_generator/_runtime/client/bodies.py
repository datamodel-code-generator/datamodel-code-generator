"""Request bodies of a generated client: the inputs a call sends, and the attempt a transport reads for each send.

Bytes are sent as they are. Every other input builds the attempt of each send itself, as a body factory does: a file
is read from its position when the attempt begins, or opened from its path and checked against the file first found
there; a stream is read once; a factory builds a new attempt. Attempts are read in chunks of at most 64 KiB.
"""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Literal, Protocol, TypeAlias

from .coding import CHUNK
from .errors import BodyChangedError, BodyFactoryError, BodyNotReplayableError, add_secondary

if TYPE_CHECKING:
    from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Iterable, Iterator
    from concurrent.futures import ThreadPoolExecutor
    from os import PathLike
    from typing import TypeVar

    T = TypeVar("T")
    Ownership: TypeAlias = Literal["borrowed", "owned"]
    _Identity: TypeAlias = tuple[int, int, int, int]
    _Checked: TypeAlias = Literal["file", "factory"]


class BodyAttemptContext:
    """The attempt a factory builds a body for: its call, attempt, and redirect hop, and the time left then."""

    __slots__ = ("_attempt_index", "_call_id", "_hop_index", "_remaining_timeout")

    def __init__(self, *, call_id: str, attempt_index: int, hop_index: int, remaining_timeout: float | None) -> None:
        """Keep the call, attempt, hop, and remaining time, which never change."""
        self._call_id = call_id
        self._attempt_index = attempt_index
        self._hop_index = hop_index
        self._remaining_timeout = remaining_timeout

    @property
    def call_id(self) -> str:
        """Return the identifier of the call."""
        return self._call_id

    @property
    def attempt_index(self) -> int:
        """Return the position of the attempt in its call, from 0."""
        return self._attempt_index

    @property
    def hop_index(self) -> int:
        """Return the position of the redirect hop in its attempt, from 0."""
        return self._hop_index

    @property
    def remaining_timeout(self) -> float | None:
        """Return the seconds the call had left when the attempt began, or None without a deadline."""
        return self._remaining_timeout


class BodyAttempt(Protocol):
    """One send's body, read once in chunks and then closed."""

    @property
    def content_length(self) -> int | None:
        """Return the number of body bytes, or None when unknown."""
        ...

    @property
    def content_type(self) -> str | None:
        """Return the body's media type, or None when the request names none."""
        ...

    def iter_bytes(self) -> Iterator[bytes]:
        """Yield the body in chunks."""
        ...

    def close(self) -> None:
        """Release what the attempt holds; closing twice does nothing."""
        ...


class AsyncBodyAttempt(Protocol):
    """One async send's body, read once in chunks and then closed."""

    @property
    def content_length(self) -> int | None:
        """Return the number of body bytes, or None when unknown."""
        ...

    @property
    def content_type(self) -> str | None:
        """Return the body's media type, or None when the request names none."""
        ...

    def aiter_bytes(self) -> AsyncIterator[bytes]:
        """Yield the body in chunks."""
        ...

    async def aclose(self) -> None:
        """Release what the attempt holds; closing twice does nothing."""
        ...


class BodyAttemptFactory(Protocol):
    """Build the body of one attempt; every attempt of a call must carry the same bytes."""

    def __call__(self, context: BodyAttemptContext, /) -> BodyAttempt:
        """Return a new attempt, which the client closes once it is sent or fails."""
        ...


class AsyncBodyAttemptFactory(Protocol):
    """Build the body of one async attempt; every attempt of a call must carry the same bytes."""

    async def __call__(self, context: BodyAttemptContext, /) -> AsyncBodyAttempt:
        """Return a new attempt, which the client closes once it is sent or fails."""
        ...


class EncodedAttempt:
    """A body encoded once for its call, sent as it is by every attempt of either client."""

    __slots__ = ("content", "content_type")

    def __init__(self, content: bytes, content_type: str | None) -> None:
        """Keep the encoded bytes and their media type, if the request names one."""
        self.content = content
        self.content_type = content_type

    @property
    def content_length(self) -> int:
        """Return the number of body bytes."""
        return len(self.content)

    def iter_bytes(self) -> Iterator[bytes]:
        """Yield the whole body."""
        yield self.content

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        """Yield the whole body."""
        yield self.content

    def close(self) -> None:
        """Hold nothing to release."""

    async def aclose(self) -> None:
        """Hold nothing to release."""


def _failed(context: BodyAttemptContext, cause: BaseException) -> BodyFactoryError:
    return BodyFactoryError(attempt_index=context.attempt_index, hop_index=context.hop_index, cause=cause)


class _Counter:
    """Check a body's chunks: bytes only, and within the length its source declared, if it declared one."""

    __slots__ = ("context", "kind", "length", "size")

    def __init__(self, context: BodyAttemptContext, kind: _Checked, length: int | None) -> None:
        self.context = context
        self.kind: _Checked = kind
        self.length = length
        self.size = 0

    def chunk(self, chunk: object) -> bytes:
        """Return a chunk that is bytes and does not pass the declared length."""
        if type(chunk) is not bytes:
            raise _failed(self.context, TypeError("A body chunk must be bytes"))
        self.size += len(chunk)
        if self.length is not None and self.size > self.length:
            raise BodyChangedError(source_kind=self.kind, check="length")
        return chunk

    def end(self) -> None:
        """Refuse a body that ended short of its declared length."""
        if self.length is not None and self.size != self.length:
            raise BodyChangedError(source_kind=self.kind, check="length")


def _read(chunks: Callable[[], Iterable[object]], counter: _Counter) -> Iterator[bytes]:
    """Yield the non-empty chunks of a source, turning its failures into BodyFactoryError."""
    try:
        iterator = iter(chunks())
    except Exception as error:  # noqa: BLE001
        raise _failed(counter.context, error) from None
    while True:
        try:
            chunk = next(iterator)
        except StopIteration:
            break
        except Exception as error:  # noqa: BLE001
            raise _failed(counter.context, error) from None
        if chunk := counter.chunk(chunk):
            yield chunk
    counter.end()


async def _aread(chunks: Callable[[], AsyncIterable[object]], counter: _Counter) -> AsyncIterator[bytes]:
    """Yield the non-empty chunks of an async source, turning its failures into BodyFactoryError."""
    try:
        iterator = aiter(chunks())
    except Exception as error:  # noqa: BLE001
        raise _failed(counter.context, error) from None
    while True:
        try:
            chunk = await anext(iterator)
        except StopAsyncIteration:
            break
        except Exception as error:  # noqa: BLE001
            raise _failed(counter.context, error) from None
        if chunk := counter.chunk(chunk):
            yield chunk
    counter.end()


def _identity(stat: os.stat_result) -> _Identity:
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _file_chunks(file: BinaryIO) -> Iterator[bytes]:
    while chunk := file.read(CHUNK):
        yield chunk


def _remaining(file: BinaryIO) -> int | None:
    """Return the bytes from an open file's position to its end, leaving it there; None if it cannot seek.

    A closed file, such as an owned one after its call, cannot be sent again.
    """
    if file.closed:
        raise BodyNotReplayableError(source_kind="file", condition="consumed")
    if not file.seekable():
        return None
    offset = file.tell()
    end = file.seek(0, os.SEEK_END)
    file.seek(offset)
    return end - offset


def _unchanged(file: BinaryIO, identity: _Identity) -> None:
    if _identity(os.fstat(file.fileno())) != identity:
        raise BodyChangedError(source_kind="file", check="stat")


def _opened(path: Path, identity: _Identity) -> BinaryIO:
    """Open a path's file, refusing one that is not the file first found there."""
    file = path.open("rb")
    try:
        _unchanged(file, identity)
    except BaseException as error:
        try:
            file.close()
        except BaseException as failure:  # noqa: BLE001
            add_secondary(error, failure)
        raise
    return file


def _close_file(file: BinaryIO) -> None:
    file.close()


def _declared(length: int | None, attempt_length: int | None) -> int | None:
    """Return the length a factory's attempt is sent with, refusing one that contradicts its factory."""
    if length is None:
        return attempt_length
    if attempt_length not in {None, length}:
        raise BodyChangedError(source_kind="factory", check="length")
    return length


class _Claim:
    """An input one call at a time may read: taken when an attempt begins, and refused once a one-shot one was read."""

    __slots__ = ("lock", "used")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.used = False

    def take(self, kind: Literal["file", "stream"]) -> None:
        """Take the input, or refuse one another call reads or a one-shot one already read."""
        if not self.lock.acquire(blocking=False):
            raise BodyNotReplayableError(source_kind=kind, condition="concurrent")
        if self.used:
            self.lock.release()
            raise BodyNotReplayableError(source_kind=kind, condition="consumed")


class _FileAttempt:
    """One attempt's read of a file, which releases the file once closed."""

    __slots__ = ("_context", "_file", "_length", "_release")

    def __init__(
        self, file: BinaryIO, context: BodyAttemptContext, length: int | None, release: Callable[[], None]
    ) -> None:
        self._file = file
        self._context = context
        self._length = length
        self._release: Callable[[], None] | None = release

    @property
    def content_length(self) -> int | None:
        """Return the bytes left in the file, or None when it cannot seek."""
        return self._length

    @property
    def content_type(self) -> None:
        """Name no media type; the request does."""

    def iter_bytes(self) -> Iterator[bytes]:
        """Yield the file from its position to its end."""
        return _read(lambda: _file_chunks(self._file), _Counter(self._context, "file", self._length))

    def close(self) -> None:
        """Release the file once."""
        if (release := self._release) is not None:
            self._release = None
            release()


class _OpenFile:
    """An open file read by one call at a time from its position, closed after its call when the client owns it."""

    __slots__ = ("claim", "file", "ownership")

    def __init__(self, file: BinaryIO, ownership: Ownership) -> None:
        self.file = file
        self.ownership = ownership
        self.claim = _Claim()

    def attempt(self, context: BodyAttemptContext) -> BodyAttempt:
        """Begin reading the file, refusing one another call reads, one that is closed, or one read once."""
        self.claim.take("file")
        try:
            length = _remaining(self.file)
        except OSError as error:
            self.claim.lock.release()
            raise _failed(context, error) from None
        except BaseException:
            self.claim.lock.release()
            raise
        self.claim.used = length is None
        return _FileAttempt(self.file, context, length, self.release)

    def release(self) -> None:
        """End a call's read: close an owned file, then let the next call read."""
        try:
            if self.ownership == "owned":
                self.file.close()
        finally:
            self.claim.lock.release()


class _PathFile:
    """A path opened for each attempt, which must stay the file first found there."""

    __slots__ = ("identity", "path")

    def __init__(self, path: Path) -> None:
        self.path = path
        self.identity = _identity(path.stat())

    def attempt(self, context: BodyAttemptContext) -> BodyAttempt:
        """Open the file, refusing one that changed since it was first found."""
        try:
            file = _opened(self.path, self.identity)
        except OSError as error:
            raise _failed(context, error) from None
        return _FileAttempt(file, context, self.identity[2], file.close)


class FileBody:
    """A file sent as a whole body: an open binary file, or a path opened for each attempt.

    An open file is read from its position when the attempt begins, by one call at a time; one that cannot seek is
    read once. A borrowed file is never closed and is left where reading stopped; an owned one is closed after its
    call. A path must still name the file first found there: the same device, inode, size, and modification time.
    """

    __slots__ = ("_source",)

    def __init__(self, file: BinaryIO, *, ownership: Ownership = "borrowed") -> None:
        """Send an open binary file, borrowed unless its ownership moves to the client."""
        self._source: _OpenFile | _PathFile = _OpenFile(file, ownership)

    @classmethod
    def from_path(cls, path: str | PathLike[str]) -> FileBody:
        """Send the file at a path, opened for each attempt, while it stays the file found there now."""
        return _PathFileBody(Path(path))

    def __call__(self, context: BodyAttemptContext, /) -> BodyAttempt:
        """Begin one attempt's read of the file."""
        return self._source.attempt(context)


class _PathFileBody(FileBody):
    __slots__ = ()

    def __init__(self, path: Path) -> None:
        self._source = _PathFile(path)


class _StreamAttempt:
    """The only attempt of a stream, which closes an owned iterator once closed."""

    __slots__ = ("_chunks", "_context", "_owned")

    def __init__(self, chunks: Iterable[bytes], context: BodyAttemptContext, *, owned: bool) -> None:
        self._chunks = chunks
        self._context = context
        self._owned = owned

    @property
    def content_length(self) -> None:
        """Know no length: the body is sent in chunks."""

    @property
    def content_type(self) -> None:
        """Name no media type; the request does."""

    def iter_bytes(self) -> Iterator[bytes]:
        """Yield the stream's chunks."""
        return _read(lambda: self._chunks, _Counter(self._context, "factory", None))

    def close(self) -> None:
        """Close an owned iterator once."""
        if self._owned and (close := getattr(self._chunks, "close", None)) is not None:
            self._owned = False
            close()


class StreamBody:
    """Byte chunks sent once as a whole body; the client closes an owned iterator after its call."""

    __slots__ = ("_chunks", "_claim", "_ownership")

    def __init__(self, chunks: Iterable[bytes], *, ownership: Ownership = "borrowed") -> None:
        """Send the chunks once, borrowed unless their ownership moves to the client."""
        self._chunks = chunks
        self._ownership = ownership
        self._claim = _Claim()

    def __call__(self, context: BodyAttemptContext, /) -> BodyAttempt:
        """Begin the only attempt, refusing a stream already read."""
        self._claim.take("stream")
        self._claim.used = True
        self._claim.lock.release()
        return _StreamAttempt(self._chunks, context, owned=self._ownership == "owned")


class _FactoryAttempt:
    """A factory's attempt, read within the length it or its factory declared."""

    __slots__ = ("_attempt", "_context", "_length")

    def __init__(self, attempt: BodyAttempt, context: BodyAttemptContext, length: int | None) -> None:
        self._attempt = attempt
        self._context = context
        self._length = length

    @property
    def content_length(self) -> int | None:
        """Return the declared length."""
        return self._length

    @property
    def content_type(self) -> str | None:
        """Return the media type the attempt names."""
        return self._attempt.content_type

    def iter_bytes(self) -> Iterator[bytes]:
        """Yield the attempt's chunks."""
        return _read(self._attempt.iter_bytes, _Counter(self._context, "factory", self._length))

    def close(self) -> None:
        """Close the attempt, turning its failure into BodyFactoryError."""
        try:
            self._attempt.close()
        except Exception as error:  # noqa: BLE001
            raise _failed(self._context, error) from None


class BodyFactory:
    """A factory building a new attempt of the same bytes for each send, with what it declares about them."""

    __slots__ = ("_content_length", "_content_type", "_factory", "_fingerprint", "_last")

    def __init__(
        self,
        factory: BodyAttemptFactory,
        *,
        content_length: int | None = None,
        content_type: str | None = None,
        fingerprint: bytes | None = None,
    ) -> None:
        """Keep the factory and the length, media type, and fingerprint its attempts share."""
        self._factory = factory
        self._content_length = content_length
        self._content_type = content_type
        self._fingerprint = fingerprint
        self._last: BodyAttempt | None = None

    @property
    def content_length(self) -> int | None:
        """Return the length every attempt has, or None when undeclared."""
        return self._content_length

    @property
    def content_type(self) -> str | None:
        """Return the media type of the bytes, or None when undeclared."""
        return self._content_type

    @property
    def fingerprint(self) -> bytes | None:
        """Return the declared identity of the bytes, or None."""
        return self._fingerprint

    def __call__(self, context: BodyAttemptContext, /) -> BodyAttempt:
        """Build one attempt, refusing the attempt the factory returned last time or one of another length."""
        try:
            attempt = self._factory(context)
        except Exception as error:  # noqa: BLE001
            raise _failed(context, error) from None
        if attempt is self._last:
            raise BodyNotReplayableError(source_kind="factory", condition="same_attempt")
        self._last = attempt
        try:
            length = _declared(self._content_length, attempt.content_length)
        except BodyChangedError as error:
            try:
                attempt.close()
            except Exception as failure:  # noqa: BLE001
                add_secondary(error, failure)
            raise
        return _FactoryAttempt(attempt, context, length)


class _Worker:
    """The one thread an async file body reads on, started by its first read and stopped by closing."""

    __slots__ = ("closed", "lock", "pool", "uses")

    def __init__(self) -> None:
        self.pool: ThreadPoolExecutor | None = None
        self.lock = threading.Lock()
        self.closed = False
        self.uses = 0

    def acquire(self) -> None:
        """Keep the worker available until an admitted file attempt finishes its cleanup."""
        with self.lock:
            if self.closed:
                raise BodyNotReplayableError(source_kind="file", condition="consumed")
            self.uses += 1

    def release(self) -> None:
        """Release one file attempt's use after all its disk work has settled."""
        with self.lock:
            self.uses -= 1
        self._stop()

    async def run(
        self,
        function: Callable[..., T],
        *arguments: object,
        discard: Callable[[T], None] | None = None,
        cleanup: bool = False,
    ) -> T:
        """Run one disk operation, settling it before an interrupted owner releases its file."""
        with self.lock:
            if self.closed and not cleanup:
                raise BodyNotReplayableError(source_kind="file", condition="consumed")
            if (pool := self.pool) is None:
                from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415 - Only async file bodies start one.

                pool = self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="AsyncFileBody")
        future = asyncio.get_running_loop().run_in_executor(pool, function, *arguments)
        try:
            await asyncio.wait((future,))
        except asyncio.CancelledError as error:
            await self._settle(future, error, discard)
            raise
        return future.result()

    async def _settle(
        self, future: asyncio.Future[T], error: BaseException, discard: Callable[[T], None] | None
    ) -> None:
        """Retain interrupted disk work and dispose a late file on this same worker."""
        while not future.done():
            try:
                await asyncio.wait((future,))
            except asyncio.CancelledError:  # noqa: PERF203 - Repeated cancellation must not abandon disk work.
                continue
        try:
            result = future.result()
        except BaseException as failure:  # noqa: BLE001
            add_secondary(error, failure)
        else:
            if discard is not None:
                try:
                    await self.run(discard, result, cleanup=True)
                except BaseException as failure:  # noqa: BLE001
                    add_secondary(error, failure)

    def close(self) -> None:
        """Refuse new reads and stop after admitted attempts finish their owned cleanup."""
        with self.lock:
            self.closed = True
        self._stop()

    def _stop(self) -> None:
        with self.lock:
            pool = None
            if self.closed and not self.uses:
                pool, self.pool = self.pool, None
        if pool is not None:
            pool.shutdown(wait=False)


class _AsyncFileAttempt:
    """One async attempt's read of a file on its body's worker, which releases the file once closed."""

    __slots__ = ("_context", "_file", "_length", "_release", "_worker")

    def __init__(
        self,
        file: BinaryIO,
        context: BodyAttemptContext,
        length: int | None,
        worker: _Worker,
        release: Callable[[], Awaitable[None]],
    ) -> None:
        self._file = file
        self._context = context
        self._length = length
        self._worker = worker
        self._release: Callable[[], Awaitable[None]] | None = release

    @property
    def content_length(self) -> int | None:
        """Return the bytes left in the file, or None when it cannot seek."""
        return self._length

    @property
    def content_type(self) -> None:
        """Name no media type; the request does."""

    def aiter_bytes(self) -> AsyncIterator[bytes]:
        """Yield the file from its position to its end, one chunk read on the worker at a time."""
        return _aread(self._chunks, _Counter(self._context, "file", self._length))

    async def _chunks(self) -> AsyncIterator[bytes]:
        while chunk := await self._worker.run(self._file.read, CHUNK):
            yield chunk

    async def aclose(self) -> None:
        """Release the file once."""
        if (release := self._release) is not None:
            self._release = None
            await release()


class _AsyncOpenFile:
    """An open file an asyncio client reads on its body's worker, one call at a time."""

    __slots__ = ("claim", "file", "ownership", "worker")

    def __init__(self, file: BinaryIO, ownership: Ownership, worker: _Worker) -> None:
        self.file = file
        self.ownership = ownership
        self.worker = worker
        self.claim = _Claim()

    async def attempt(self, context: BodyAttemptContext) -> AsyncBodyAttempt:
        """Begin reading the file, refusing one another call reads, one that is closed, or one read once."""
        self.claim.take("file")
        try:
            self.worker.acquire()
        except BaseException:
            self.claim.lock.release()
            raise
        try:
            length = await self.worker.run(_remaining, self.file)
        except BaseException as error:
            try:
                await self.release()
            except BaseException as failure:  # noqa: BLE001
                add_secondary(error, failure)
            if isinstance(error, OSError):
                raise _failed(context, error) from None
            raise
        self.claim.used = length is None
        return _AsyncFileAttempt(self.file, context, length, self.worker, self.release)

    async def release(self) -> None:
        """End a call's read: close an owned file on the worker, then let the next call read."""
        try:
            if self.ownership == "owned":
                self.claim.used = True
                await self.worker.run(self.file.close, cleanup=True)
        finally:
            self.claim.lock.release()
            self.worker.release()


class _AsyncPathFile:
    """A path an asyncio client opens on its body's worker for each attempt."""

    __slots__ = ("identity", "path", "worker")

    def __init__(self, path: Path, worker: _Worker) -> None:
        self.path = path
        self.worker = worker
        self.identity = _identity(path.stat())

    async def attempt(self, context: BodyAttemptContext) -> AsyncBodyAttempt:
        """Open the file on the worker, refusing one that changed since it was first found."""
        self.worker.acquire()
        try:
            file = await self.worker.run(_opened, self.path, self.identity, discard=_close_file)
        except BaseException as error:
            self.worker.release()
            if isinstance(error, OSError):
                raise _failed(context, error) from None
            raise
        return _AsyncFileAttempt(file, context, self.identity[2], self.worker, lambda: self.release(file))

    async def release(self, file: BinaryIO) -> None:
        """Close the attempt's file before a stopped worker can shut down."""
        try:
            await self.worker.run(file.close, cleanup=True)
        finally:
            self.worker.release()


class AsyncFileBody:
    """A file an asyncio client sends as a whole body, read as FileBody reads, on one worker thread of its own.

    The worker reads one chunk at a time. Closing the body stops it; closing a client does not.
    """

    __slots__ = ("_source", "_worker")

    def __init__(self, file: BinaryIO, *, ownership: Ownership = "borrowed") -> None:
        """Send an open binary file, borrowed unless its ownership moves to the client."""
        self._worker = _Worker()
        self._source: _AsyncOpenFile | _AsyncPathFile = _AsyncOpenFile(file, ownership, self._worker)

    @classmethod
    def from_path(cls, path: str | PathLike[str]) -> AsyncFileBody:
        """Send the file at a path, opened for each attempt, while it stays the file found there now."""
        return _AsyncPathFileBody(Path(path))

    async def __call__(self, context: BodyAttemptContext, /) -> AsyncBodyAttempt:
        """Begin one attempt's read of the file."""
        return await self._source.attempt(context)

    def close(self) -> None:
        """Stop the worker once its current read ends; the body cannot be sent after that."""
        self._worker.close()

    async def aclose(self) -> None:
        """Stop the worker once its current read ends; the body cannot be sent after that."""
        self._worker.close()


class _AsyncPathFileBody(AsyncFileBody):
    __slots__ = ()

    def __init__(self, path: Path) -> None:
        self._worker = _Worker()
        self._source = _AsyncPathFile(path, self._worker)


class _AsyncStreamAttempt:
    """The only attempt of an async stream, which closes an owned iterator once closed."""

    __slots__ = ("_chunks", "_context", "_owned")

    def __init__(self, chunks: AsyncIterable[bytes], context: BodyAttemptContext, *, owned: bool) -> None:
        self._chunks = chunks
        self._context = context
        self._owned = owned

    @property
    def content_length(self) -> None:
        """Know no length: the body is sent in chunks."""

    @property
    def content_type(self) -> None:
        """Name no media type; the request does."""

    def aiter_bytes(self) -> AsyncIterator[bytes]:
        """Yield the stream's chunks."""
        return _aread(lambda: self._chunks, _Counter(self._context, "factory", None))

    async def aclose(self) -> None:
        """Close an owned iterator once."""
        if self._owned and (close := getattr(self._chunks, "aclose", None)) is not None:
            self._owned = False
            await close()


class AsyncStreamBody:
    """Async byte chunks sent once as a whole body; the client closes an owned iterator after its call."""

    __slots__ = ("_chunks", "_claim", "_ownership")

    def __init__(self, chunks: AsyncIterable[bytes], *, ownership: Ownership = "borrowed") -> None:
        """Send the chunks once, borrowed unless their ownership moves to the client."""
        self._chunks = chunks
        self._ownership = ownership
        self._claim = _Claim()

    async def __call__(self, context: BodyAttemptContext, /) -> AsyncBodyAttempt:
        """Begin the only attempt, refusing a stream already read."""
        self._claim.take("stream")
        self._claim.used = True
        self._claim.lock.release()
        return _AsyncStreamAttempt(self._chunks, context, owned=self._ownership == "owned")


class _AsyncFactoryAttempt:
    """An async factory's attempt, read within the length it or its factory declared."""

    __slots__ = ("_attempt", "_context", "_length")

    def __init__(self, attempt: AsyncBodyAttempt, context: BodyAttemptContext, length: int | None) -> None:
        self._attempt = attempt
        self._context = context
        self._length = length

    @property
    def content_length(self) -> int | None:
        """Return the declared length."""
        return self._length

    @property
    def content_type(self) -> str | None:
        """Return the media type the attempt names."""
        return self._attempt.content_type

    def aiter_bytes(self) -> AsyncIterator[bytes]:
        """Yield the attempt's chunks."""
        return _aread(self._attempt.aiter_bytes, _Counter(self._context, "factory", self._length))

    async def aclose(self) -> None:
        """Close the attempt, turning its failure into BodyFactoryError."""
        try:
            await self._attempt.aclose()
        except Exception as error:  # noqa: BLE001
            raise _failed(self._context, error) from None


class AsyncBodyFactory:
    """An async factory building a new attempt of the same bytes for each send, with what it declares about them."""

    __slots__ = ("_content_length", "_content_type", "_factory", "_fingerprint", "_last")

    def __init__(
        self,
        factory: AsyncBodyAttemptFactory,
        *,
        content_length: int | None = None,
        content_type: str | None = None,
        fingerprint: bytes | None = None,
    ) -> None:
        """Keep the factory and the length, media type, and fingerprint its attempts share."""
        self._factory = factory
        self._content_length = content_length
        self._content_type = content_type
        self._fingerprint = fingerprint
        self._last: AsyncBodyAttempt | None = None

    @property
    def content_length(self) -> int | None:
        """Return the length every attempt has, or None when undeclared."""
        return self._content_length

    @property
    def content_type(self) -> str | None:
        """Return the media type of the bytes, or None when undeclared."""
        return self._content_type

    @property
    def fingerprint(self) -> bytes | None:
        """Return the declared identity of the bytes, or None."""
        return self._fingerprint

    async def __call__(self, context: BodyAttemptContext, /) -> AsyncBodyAttempt:
        """Build one attempt, refusing the attempt the factory returned last time or one of another length."""
        try:
            attempt = await self._factory(context)
        except Exception as error:  # noqa: BLE001
            raise _failed(context, error) from None
        if attempt is self._last:
            raise BodyNotReplayableError(source_kind="factory", condition="same_attempt")
        self._last = attempt
        try:
            length = _declared(self._content_length, attempt.content_length)
        except BodyChangedError as error:
            try:
                await attempt.aclose()
            except Exception as failure:  # noqa: BLE001
                add_secondary(error, failure)
            raise
        return _AsyncFactoryAttempt(attempt, context, length)


SyncBinaryBody: TypeAlias = bytes | FileBody | StreamBody | BodyFactory
AsyncBinaryBody: TypeAlias = bytes | AsyncFileBody | AsyncStreamBody | AsyncBodyFactory
