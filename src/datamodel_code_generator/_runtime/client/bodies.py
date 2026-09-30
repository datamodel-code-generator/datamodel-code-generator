"""Request bodies of a generated client: the inputs a call sends, and the attempt a transport reads for each send.

Bytes are sent as they are. Every other input builds the attempt of each send itself, as a body factory does: a file
is read from its position at call entry, or opened from its path and checked against the file first found there;
a stream is read once; a factory builds a new attempt. Attempts are read in chunks of at most 64 KiB.
"""

from __future__ import annotations

import asyncio
import os
import threading
from io import UnsupportedOperation
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Literal, Protocol, TypeAlias

from .coding import CHUNK
from .errors import BodyChangedError, BodyFactoryError, BodyNotReplayableError, SDKError, add_secondary
from .lifecycle import LEFT_WORK, TaskInterruptionError, task_failure

if TYPE_CHECKING:
    from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable, Coroutine, Iterable, Iterator
    from concurrent.futures import Future, ThreadPoolExecutor
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


class AsyncBodyCleanup(Protocol):
    """Retain abnormal body cleanup under the logical call's existing cleanup budget."""

    async def __call__(self, operation: Callable[[], Awaitable[None]], *, error: BaseException | None = None) -> bool:
        """Join cleanup or retain it for the owning client's later drain."""
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


def _remaining(file: BinaryIO, context: BodyAttemptContext | None = None) -> int | None:
    """Return the bytes from an open file's position to its end, leaving it there; None if it cannot seek.

    A closed file, such as an owned one after its call, cannot be sent again.
    """
    return _snapshot(file, context)[1]


def _snapshot(file: BinaryIO, context: BodyAttemptContext | None = None) -> tuple[int | None, int | None]:
    try:
        if not file.closed:
            return _positioned(file)
    except OSError:
        raise
    except Exception as error:  # noqa: BLE001
        failure = BodyFactoryError(cause=error) if context is None else _failed(context, error)
        raise failure from None
    raise BodyNotReplayableError(source_kind="file", condition="consumed")


def _positioned(file: BinaryIO) -> tuple[int | None, int | None]:
    try:
        offset = file.tell() if file.seekable() else None
    except (AttributeError, UnsupportedOperation):
        offset = None
    if offset is None:
        return None, None
    try:
        end = file.seek(0, os.SEEK_END)
    except (AttributeError, UnsupportedOperation):
        return None, None
    file.seek(offset)
    return offset, max(0, end - offset)


def _rewound(file: BinaryIO, offset: int | None, length: int | None, context: BodyAttemptContext) -> None:
    try:
        if offset is not None:
            file.seek(offset)
    except OSError:
        raise
    except Exception as error:  # noqa: BLE001
        raise _failed(context, error) from None
    if _remaining(file, context) != length:
        raise BodyChangedError(source_kind="file", check="length")


def _kept() -> None:
    """Leave a call-owned input alive after its individual attempt closes."""


async def _akept() -> None:
    """Leave an async call-owned input alive after its individual attempt closes."""


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
            body_secondary(error, failure)
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

    __slots__ = ("_consume", "_context", "_file", "_length", "_release")

    def __init__(
        self,
        file: BinaryIO,
        context: BodyAttemptContext,
        length: int | None,
        release: Callable[[], None],
        consume: Callable[[], None] | None = None,
    ) -> None:
        self._file = file
        self._context = context
        self._length = length
        self._release: Callable[[], None] | None = release
        self._consume = consume

    @property
    def content_length(self) -> int | None:
        """Return the bytes left in the file, or None when it cannot seek."""
        return self._length

    @property
    def content_type(self) -> None:
        """Name no media type; the request does."""

    def iter_bytes(self) -> Iterator[bytes]:
        """Yield the file from its position to its end."""
        return _read(self._chunks, _Counter(self._context, "file", self._length))

    def _chunks(self) -> Iterator[bytes]:
        if self._consume is not None:
            self._consume()
        return _file_chunks(self._file)

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
            length = _remaining(self.file, context)
        except BaseException as error:  # noqa: BLE001
            failure = _failed(context, error) if isinstance(error, OSError) else error
            try:
                self.release()
            except BaseException as secondary:  # noqa: BLE001
                body_secondary(failure, secondary)
            raise failure from None
        self.claim.used = length is None
        return _FileAttempt(self.file, context, length, self.release)

    def release(self) -> None:
        """End a call's read: close an owned file, then let the next call read."""
        try:
            if self.ownership == "owned":
                self.claim.used = True
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

    An open file is read from its position at call entry, by one call at a time; one that cannot seek is
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

    def _bind(self) -> _FileCall | _PathCall:
        return _FileCall(self._source) if isinstance(self._source, _OpenFile) else _PathCall(self._source)


class _PathFileBody(FileBody):
    __slots__ = ()

    def __init__(self, path: Path) -> None:
        self._source = _PathFile(path)


class _StreamAttempt:
    """The only attempt of a stream, which closes an owned iterator once closed."""

    __slots__ = ("_chunks", "_consume", "_context", "_owned")

    def __init__(
        self,
        chunks: Iterable[bytes],
        context: BodyAttemptContext,
        *,
        owned: bool,
        consume: Callable[[], None] | None = None,
    ) -> None:
        self._chunks = chunks
        self._context = context
        self._owned = owned
        self._consume = consume

    @property
    def content_length(self) -> None:
        """Know no length: the body is sent in chunks."""

    @property
    def content_type(self) -> None:
        """Name no media type; the request does."""

    def iter_bytes(self) -> Iterator[bytes]:
        """Yield the stream's chunks."""
        return _read(self._begin, _Counter(self._context, "factory", None))

    def _begin(self) -> Iterable[bytes]:
        if self._consume is not None:
            self._consume()
        return self._chunks

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
        self._ownership: Ownership = ownership
        self._claim = _Claim()

    def __call__(self, context: BodyAttemptContext, /) -> BodyAttempt:
        """Begin the only attempt, refusing a stream already read."""
        self._claim.take("stream")
        self._claim.used = True
        self._claim.lock.release()
        return _StreamAttempt(self._chunks, context, owned=self._ownership == "owned")

    def _bind(self) -> _StreamCall:
        return _StreamCall(self._chunks, self._claim, self._ownership)


class _FactoryAttempt:
    """A factory's attempt, read within the length it or its factory declared."""

    __slots__ = ("_attempt", "_closed", "_context", "_length")

    def __init__(self, attempt: BodyAttempt, context: BodyAttemptContext, length: int | None) -> None:
        self._attempt = attempt
        self._context = context
        self._length = length
        self._closed = False

    @property
    def content_length(self) -> int | None:
        """Return the declared length."""
        return self._length

    @property
    def content_type(self) -> str | None:
        """Return the media type the attempt names."""
        return _attempt_type(self._attempt, self._context)

    def iter_bytes(self) -> Iterator[bytes]:
        """Yield the attempt's chunks."""
        return _read(self._attempt.iter_bytes, _Counter(self._context, "factory", self._length))

    def close(self) -> None:
        """Close the attempt, turning its failure into BodyFactoryError."""
        if self._closed:
            return
        self._closed = True
        try:
            self._attempt.close()
        except Exception as error:  # noqa: BLE001
            raise _failed(self._context, error) from None

    def check_length(self, length: int | None) -> int | None:
        """Retain a length already observed by this call for subsequent chunk checks."""
        self._length = _declared(length, self._length)
        return self._length


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
        return self._open(context)

    def _open(self, context: BodyAttemptContext, history: dict[int, BodyAttempt] | None = None) -> _FactoryAttempt:
        try:
            attempt = self._factory(context)
        except Exception as error:  # noqa: BLE001
            raise _failed(context, error) from None
        previous, self._last = self._last, attempt
        if attempt is previous or (history is not None and history.get(id(attempt)) is attempt):
            raise BodyNotReplayableError(source_kind="factory", condition="same_attempt")
        if history is not None:
            history[id(attempt)] = attempt
        try:
            length = _declared(self._content_length, _attempt_length(attempt, context))
        except BaseException as error:  # noqa: BLE001
            failure = _factory_failure(context, error)
            try:
                attempt.close()
            except BaseException as secondary:  # noqa: BLE001
                body_secondary(failure, secondary)
            raise failure from None
        return _FactoryAttempt(attempt, context, length)

    def _bind(self, history: dict[int, BodyAttempt]) -> _FactoryCall:
        return _FactoryCall(self, self._open, history)


async def _carried(work: Awaitable[None]) -> None:
    """Keep an interruption of deferred work as its failure, so the call it belongs to still reports it."""
    try:
        await work
    except BaseException as error:
        if isinstance(error, Exception):
            raise
        raise TaskInterruptionError(error) from None


def _raise_late(failures: tuple[BaseException, ...]) -> None:
    """Raise the first late failure of interrupted disk work, carrying the rest as its secondary errors."""
    if failures:
        add_secondary(failures[0], *failures[1:])
        raise failures[0]


class _Worker:
    """The one thread an async file body reads on, started by its first read and stopped by closing."""

    __slots__ = ("closed", "failed", "lock", "pool", "releasing", "settling", "uses")

    def __init__(self) -> None:
        self.pool: ThreadPoolExecutor | None = None
        self.lock = threading.Lock()
        self.closed = False
        self.uses = 0
        self.settling: dict[asyncio.Task[None], list[asyncio.Task[None]] | None] = {}
        self.releasing: set[asyncio.Task[None]] = set()
        self.failed: list[tuple[list[asyncio.Task[None]] | None, BaseException]] = []

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
        """Run one disk operation; an interrupted caller returns at once, and the work settles on a retained task.

        Cleanup, such as closing the file, first waits for the work interrupted callers left; a caller interrupted in
        that wait leaves the cleanup to a retained task that holds the worker. An interrupted cleanup settles before it
        ends, redoing a close its cancellation dropped.
        """
        with self.lock:
            if self.closed and not cleanup:
                raise BodyNotReplayableError(source_kind="file", condition="consumed")
            if (pool := self.pool) is None:
                from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415 - Only async file bodies start one.

                pool = self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="AsyncFileBody")
        late: tuple[BaseException, ...] = ()
        if cleanup:
            try:
                late = await self.settled()
            except asyncio.CancelledError:
                with self.lock:
                    self.uses += 1
                self.defer(_carried(self._finish(function, *arguments)), release=True)
                raise
        future = pool.submit(function, *arguments)
        try:
            result = await asyncio.wrap_future(future)
        except asyncio.CancelledError as error:
            if not cleanup:
                self.defer(self._settle(future, discard))
                raise
            try:
                await self._settle(future, discard, lambda: pool.submit(function, *arguments))
            except BaseException as failure:  # noqa: BLE001
                add_secondary(error, failure)
            add_secondary(error, *late)
            raise
        except BaseException as error:
            add_secondary(error, *late)
            raise
        _raise_late(late)
        return result

    async def _finish(self, function: Callable[..., object], *arguments: object) -> None:
        """Run the cleanup an interrupted caller left before it began, holding the worker until it ends."""
        try:
            await self.run(function, *arguments, cleanup=True)
        finally:
            self.release()

    def defer(self, work: Coroutine[object, object, None], *, release: bool = False) -> None:
        """Finish work an interrupted caller left on a retained task, which belongs to the caller's call.

        Disk work settles before any file is released; a deferred release is awaited by the interrupted call instead.
        """
        task = asyncio.ensure_future(work)
        left = LEFT_WORK.get()
        if release:
            self.releasing.add(task)
            task.add_done_callback(self.releasing.discard)
        else:
            self.settling[task] = left
            task.add_done_callback(self._settled)
        if release and left is not None:
            left.append(task)

    def _settled(self, task: asyncio.Task[None]) -> None:
        left = self.settling.pop(task)
        if (failure := task_failure(task)) is not None:
            self.failed.append((left, failure))

    async def settled(self) -> tuple[BaseException, ...]:
        """Wait for the disk work interrupted callers left, and return what the current call's share of it failed late.

        Settling work waits for nothing else, so two interrupted opens of one path never wait for each other. A late
        failure stays with the worker until its call asks, even when its work finished before that call's release.
        """
        if asyncio.current_task() in self.settling:
            return ()
        if pending := tuple(self.settling):
            await asyncio.wait(pending)
        if not self.failed:
            return ()
        owner = LEFT_WORK.get()
        failures = tuple(failure for left, failure in self.failed if left is owner)
        self.failed = [entry for entry in self.failed if entry[0] is not owner]
        return failures

    async def _settle(
        self,
        future: Future[T],
        discard: Callable[[T], None] | None,
        resubmit: Callable[[], Future[T]] | None = None,
    ) -> None:
        """Wait for interrupted disk work, dispose a late file on this same worker, and raise what failed late."""
        settled = asyncio.wrap_future(future)
        while not settled.done():
            try:
                await asyncio.wait((settled,))
            except asyncio.CancelledError:  # noqa: PERF203 - Repeated cancellation must not abandon disk work.
                continue
        if future.cancelled():
            if resubmit is not None:
                await self._settle(resubmit(), None)
            return
        result = settled.result()
        if discard is not None:
            await self.run(discard, result, cleanup=True)

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

    __slots__ = ("_consume", "_context", "_file", "_length", "_release", "_worker")

    def __init__(  # noqa: PLR0913
        self,
        file: BinaryIO,
        context: BodyAttemptContext,
        length: int | None,
        worker: _Worker,
        release: Callable[[], Awaitable[None]],
        *,
        consume: Callable[[], None] | None = None,
    ) -> None:
        self._file = file
        self._context = context
        self._length = length
        self._worker = worker
        self._release: Callable[[], Awaitable[None]] | None = release
        self._consume = consume

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
        if self._consume is not None:
            self._consume()
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
            length = await self.worker.run(_remaining, self.file, context)
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError):
                self.worker.defer(_carried(self.release()), release=True)
                raise
            failure = _failed(context, error) if isinstance(error, OSError) else error
            try:
                await self.release()
            except BaseException as secondary:  # noqa: BLE001
                body_secondary(failure, secondary)
            raise failure from None
        self.claim.used = length is None
        return _AsyncFileAttempt(self.file, context, length, self.worker, self.release)

    async def release(self) -> None:
        """End a call's read once its disk work settles: close an owned file, then let the next call read."""
        late: tuple[BaseException, ...] = ()
        try:
            late = await self.worker.settled()
            if self.ownership == "owned":
                self.claim.used = True
                await self.worker.run(self.file.close, cleanup=True)
        finally:
            self.claim.lock.release()
            self.worker.release()
        _raise_late(late)


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
            if isinstance(error, asyncio.CancelledError):
                self.worker.defer(_carried(self._settled_release()), release=True)
                raise
            self.worker.release()
            if isinstance(error, OSError):
                raise _failed(context, error) from None
            raise
        return _AsyncFileAttempt(file, context, self.identity[2], self.worker, lambda: self.release(file))

    async def _settled_release(self) -> None:
        late: tuple[BaseException, ...] = ()
        try:
            late = await self.worker.settled()
        finally:
            self.worker.release()
        _raise_late(late)

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

    async def _bind(self) -> _AsyncFileCall | _AsyncPathCall:
        if isinstance(self._source, _AsyncPathFile):
            return _AsyncPathCall(self._source)
        binding = _AsyncFileCall(self._source)
        await binding.capture()
        return binding

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

    __slots__ = ("_chunks", "_consume", "_context", "_owned")

    def __init__(
        self,
        chunks: AsyncIterable[bytes],
        context: BodyAttemptContext,
        *,
        owned: bool,
        consume: Callable[[], None] | None = None,
    ) -> None:
        self._chunks = chunks
        self._context = context
        self._owned = owned
        self._consume = consume

    @property
    def content_length(self) -> None:
        """Know no length: the body is sent in chunks."""

    @property
    def content_type(self) -> None:
        """Name no media type; the request does."""

    def aiter_bytes(self) -> AsyncIterator[bytes]:
        """Yield the stream's chunks."""
        return _aread(self._begin, _Counter(self._context, "factory", None))

    def _begin(self) -> AsyncIterable[bytes]:
        if self._consume is not None:
            self._consume()
        return self._chunks

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
        self._ownership: Ownership = ownership
        self._claim = _Claim()

    async def __call__(self, context: BodyAttemptContext, /) -> AsyncBodyAttempt:
        """Begin the only attempt, refusing a stream already read."""
        self._claim.take("stream")
        self._claim.used = True
        self._claim.lock.release()
        return _AsyncStreamAttempt(self._chunks, context, owned=self._ownership == "owned")

    def _bind(self) -> _AsyncStreamCall:
        return _AsyncStreamCall(self._chunks, self._claim, self._ownership)


class _AsyncFactoryAttempt:
    """An async factory's attempt, read within the length it or its factory declared."""

    __slots__ = ("_attempt", "_closed", "_context", "_length")

    def __init__(self, attempt: AsyncBodyAttempt, context: BodyAttemptContext, length: int | None) -> None:
        self._attempt = attempt
        self._context = context
        self._length = length
        self._closed = False

    @property
    def content_length(self) -> int | None:
        """Return the declared length."""
        return self._length

    @property
    def content_type(self) -> str | None:
        """Return the media type the attempt names."""
        return _attempt_type(self._attempt, self._context)

    def aiter_bytes(self) -> AsyncIterator[bytes]:
        """Yield the attempt's chunks."""
        return _aread(self._attempt.aiter_bytes, _Counter(self._context, "factory", self._length))

    async def aclose(self) -> None:
        """Close the attempt, turning its failure into BodyFactoryError."""
        if self._closed:
            return
        self._closed = True
        try:
            await self._attempt.aclose()
        except Exception as error:  # noqa: BLE001
            raise _failed(self._context, error) from None

    def check_length(self, length: int | None) -> int | None:
        """Retain the call's previously observed length for later reads."""
        self._length = _declared(length, self._length)
        return self._length


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
        return await self._open(context)

    async def _open(
        self,
        context: BodyAttemptContext,
        history: dict[int, AsyncBodyAttempt] | None = None,
        cleanup: AsyncBodyCleanup | None = None,
    ) -> _AsyncFactoryAttempt:
        try:
            attempt = await self._factory(context)
        except Exception as error:  # noqa: BLE001
            raise _failed(context, error) from None
        previous, self._last = self._last, attempt
        if attempt is previous or (history is not None and history.get(id(attempt)) is attempt):
            raise BodyNotReplayableError(source_kind="factory", condition="same_attempt")
        if history is not None:
            history[id(attempt)] = attempt
        try:
            length = _declared(self._content_length, _attempt_length(attempt, context))
        except BaseException as error:  # noqa: BLE001
            failure = _factory_failure(context, error)
            if cleanup is None:
                try:
                    await attempt.aclose()
                except BaseException as secondary:  # noqa: BLE001
                    body_secondary(failure, secondary)
            else:
                await cleanup(attempt.aclose, error=failure)
            raise failure from None
        return _AsyncFactoryAttempt(attempt, context, length)

    def _bind(self, history: dict[int, AsyncBodyAttempt], cleanup: AsyncBodyCleanup) -> _AsyncFactoryCall:
        return _AsyncFactoryCall(self, self._open, history, cleanup)


class _FileCall:
    """An open file claimed from entry through final cleanup, with one retained offset and length."""

    __slots__ = ("_length", "_offset", "_source")

    def __init__(self, source: _OpenFile) -> None:
        source.claim.take("file")
        self._source = source
        try:
            self._offset, self._length = _snapshot(source.file)
        except BaseException as error:  # noqa: BLE001
            failure = BodyFactoryError(cause=error) if isinstance(error, OSError) else error
            try:
                self.close()
            except BaseException as secondary:  # noqa: BLE001
                body_secondary(failure, secondary)
            raise failure from None

    @property
    def replayable(self) -> bool:
        return self._offset is not None or not self._source.claim.used

    def open(self, context: BodyAttemptContext) -> BodyAttempt:
        try:
            _rewound(self._source.file, self._offset, self._length, context)
        except OSError as error:
            raise _failed(context, error) from None
        return _FileAttempt(self._source.file, context, self._length, _kept, self._consumed)

    def _consumed(self) -> None:
        if self._offset is None:
            self._source.claim.used = True

    def close(self) -> None:
        self._source.release()


class _AsyncFileCall:
    """One async call's file claim, retained through disk settlement and owned final close."""

    __slots__ = ("_length", "_offset", "_source")

    def __init__(self, source: _AsyncOpenFile) -> None:
        source.claim.take("file")
        try:
            source.worker.acquire()
        except BaseException:
            source.claim.lock.release()
            raise
        self._source = source
        self._offset: int | None = None
        self._length: int | None = None

    async def capture(self) -> None:
        try:
            self._offset, self._length = await self._source.worker.run(_snapshot, self._source.file)
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError):
                self._source.worker.defer(_carried(self.aclose()), release=True)
                raise
            failure = BodyFactoryError(cause=error) if isinstance(error, OSError) else error
            try:
                await self.aclose()
            except BaseException as secondary:  # noqa: BLE001
                body_secondary(failure, secondary)
            raise failure from None

    @property
    def replayable(self) -> bool:
        return self._offset is not None or not self._source.claim.used

    async def aopen(self, context: BodyAttemptContext) -> AsyncBodyAttempt:
        source = self._source
        try:
            await source.worker.run(_rewound, source.file, self._offset, self._length, context)
        except OSError as error:
            raise _failed(context, error) from None
        return _AsyncFileAttempt(source.file, context, self._length, source.worker, _akept, consume=self._consumed)

    def _consumed(self) -> None:
        if self._offset is None:
            self._source.claim.used = True

    async def aclose(self) -> None:
        await self._source.release()


class _PathCall:
    """Reopen a path against its original identity at each hop."""

    __slots__ = ("_source",)

    def __init__(self, source: _PathFile) -> None:
        self._source = source

    @property
    def replayable(self) -> bool:
        return True

    def open(self, context: BodyAttemptContext) -> BodyAttempt:
        return self._source.attempt(context)

    def close(self) -> None:
        """Leave no open handle between attempts."""


class _AsyncPathCall:
    """Reopen a path on its worker, with per-attempt owned descriptor cleanup."""

    __slots__ = ("_source",)

    def __init__(self, source: _AsyncPathFile) -> None:
        self._source = source

    @property
    def replayable(self) -> bool:
        return not self._source.worker.closed

    async def aopen(self, context: BodyAttemptContext) -> AsyncBodyAttempt:
        return await self._source.attempt(context)

    async def aclose(self) -> None:
        """Leave the caller's explicitly created worker alive."""


class _StreamCall:
    """Hold a stream's call claim and mark consumption only when iteration begins."""

    __slots__ = ("_chunks", "_claim", "_ownership")

    def __init__(self, chunks: Iterable[bytes], claim: _Claim, ownership: Ownership) -> None:
        claim.take("stream")
        self._chunks, self._claim, self._ownership = chunks, claim, ownership

    @property
    def replayable(self) -> bool:
        return not self._claim.used

    def open(self, context: BodyAttemptContext) -> BodyAttempt:
        return _StreamAttempt(self._chunks, context, owned=False, consume=self._consumed)

    def _consumed(self) -> None:
        self._claim.used = True

    def close(self) -> None:
        try:
            if self._ownership == "owned":
                self._claim.used = True
                if (close := getattr(self._chunks, "close", None)) is not None:
                    close()
        finally:
            self._claim.lock.release()


class _AsyncStreamCall:
    """Hold an async stream's claim through its final owned cleanup."""

    __slots__ = ("_chunks", "_claim", "_ownership")

    def __init__(self, chunks: AsyncIterable[bytes], claim: _Claim, ownership: Ownership) -> None:
        claim.take("stream")
        self._chunks, self._claim, self._ownership = chunks, claim, ownership

    @property
    def replayable(self) -> bool:
        return not self._claim.used

    async def aopen(self, context: BodyAttemptContext) -> AsyncBodyAttempt:
        return _AsyncStreamAttempt(self._chunks, context, owned=False, consume=self._consumed)

    def _consumed(self) -> None:
        self._claim.used = True

    async def aclose(self) -> None:
        try:
            if self._ownership == "owned":
                self._claim.used = True
                if (close := getattr(self._chunks, "aclose", None)) is not None:
                    await close()
        finally:
            self._claim.lock.release()


class _FactoryCall:
    """A factory's immutable declarations and the shared identity history of its logical call."""

    __slots__ = ("_body", "_fingerprint", "_history", "_length", "_open")

    def __init__(
        self,
        body: BodyFactory,
        open_attempt: Callable[[BodyAttemptContext, dict[int, BodyAttempt]], _FactoryAttempt],
        history: dict[int, BodyAttempt],
    ) -> None:
        self._body = body
        self._history = history
        self._length, self._fingerprint = _declarations(body)
        self._open = open_attempt

    @property
    def replayable(self) -> bool:
        return True

    def open(self, context: BodyAttemptContext) -> BodyAttempt:
        attempt = self._open(context, self._history)
        try:
            _fingerprint(_current_fingerprint(self._body, context), self._fingerprint)
            self._length = attempt.check_length(self._length)
        except BaseException as error:  # noqa: BLE001
            failure = _factory_failure(context, error)
            try:
                attempt.close()
            except BaseException as secondary:  # noqa: BLE001
                body_secondary(failure, secondary)
            raise failure from None
        return attempt

    def close(self) -> None:
        """Leave history clearing to the whole call's owner."""


class _AsyncFactoryCall:
    """An async factory's declarations and the call-owned raw-attempt identity history."""

    __slots__ = ("_body", "_cleanup", "_fingerprint", "_history", "_length", "_open")

    def __init__(
        self,
        body: AsyncBodyFactory,
        open_attempt: Callable[
            [BodyAttemptContext, dict[int, AsyncBodyAttempt], AsyncBodyCleanup], Awaitable[_AsyncFactoryAttempt]
        ],
        history: dict[int, AsyncBodyAttempt],
        cleanup: AsyncBodyCleanup,
    ) -> None:
        self._body = body
        self._history = history
        self._length, self._fingerprint = _declarations(body)
        self._open = open_attempt
        self._cleanup = cleanup

    @property
    def replayable(self) -> bool:
        return True

    async def aopen(self, context: BodyAttemptContext) -> AsyncBodyAttempt:
        attempt = await self._open(context, self._history, self._cleanup)
        try:
            _fingerprint(_current_fingerprint(self._body, context), self._fingerprint)
            self._length = attempt.check_length(self._length)
        except BaseException as error:  # noqa: BLE001
            failure = _factory_failure(context, error)
            await self._cleanup(attempt.aclose, error=failure)
            raise failure from None
        return attempt

    async def aclose(self) -> None:
        """Leave history clearing to the whole call's owner."""


def _attempt_length(attempt: BodyAttempt | AsyncBodyAttempt, context: BodyAttemptContext) -> int | None:
    try:
        return attempt.content_length
    except Exception as error:  # noqa: BLE001
        raise _failed(context, error) from None


def _attempt_type(attempt: BodyAttempt | AsyncBodyAttempt, context: BodyAttemptContext) -> str | None:
    try:
        return attempt.content_type
    except Exception as error:  # noqa: BLE001
        raise _failed(context, error) from None


def _declarations(body: BodyFactory | AsyncBodyFactory) -> tuple[int | None, bytes | None]:
    try:
        return body.content_length, body.fingerprint
    except Exception as error:  # noqa: BLE001
        raise BodyFactoryError(cause=error) from None


def _current_fingerprint(body: BodyFactory | AsyncBodyFactory, context: BodyAttemptContext) -> bytes | None:
    try:
        return body.fingerprint
    except Exception as error:  # noqa: BLE001
        raise _failed(context, error) from None


def _factory_failure(context: BodyAttemptContext, error: BaseException) -> BaseException:
    return _failed(context, error) if isinstance(error, Exception) and not isinstance(error, SDKError) else error


def body_secondary(error: BaseException, failure: BaseException) -> None:
    """Keep the first native interruption, otherwise retain cleanup beside the body failure."""
    if isinstance(error, Exception) and not isinstance(failure, Exception):
        raise failure from None
    add_secondary(error, failure)


def _fingerprint(actual: bytes | None, expected: bytes | None) -> None:
    if actual != expected:
        raise BodyChangedError(source_kind="factory", check="fingerprint")


def bind_input(body: FileBody | StreamBody) -> _FileCall | _PathCall | _StreamCall:
    """Claim one known synchronous input for the private call executor."""
    return body._bind()  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001


async def bind_async_input(body: AsyncFileBody | AsyncStreamBody) -> _AsyncFileCall | _AsyncPathCall | _AsyncStreamCall:
    """Claim one known async input and settle any disk preparation."""
    return await body._bind() if isinstance(body, AsyncFileBody) else body._bind()  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001


def bind_factory(body: BodyFactory, history: dict[int, BodyAttempt]) -> _FactoryCall:
    """Bind a synchronous factory to the call's raw-attempt identity ledger."""
    return body._bind(history)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001


def bind_async_factory(
    body: AsyncBodyFactory, history: dict[int, AsyncBodyAttempt], cleanup: AsyncBodyCleanup
) -> _AsyncFactoryCall:
    """Bind an async factory to the call's raw-attempt identity ledger."""
    return body._bind(history, cleanup)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001


SyncBinaryBody: TypeAlias = bytes | FileBody | StreamBody | BodyFactory
AsyncBinaryBody: TypeAlias = bytes | AsyncFileBody | AsyncStreamBody | AsyncBodyFactory
