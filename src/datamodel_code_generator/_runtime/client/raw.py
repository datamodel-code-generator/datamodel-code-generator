"""Raw responses: the status, headers, and body of a call, without decoding the declared value.

A buffered response holds its body and stays readable after its client closes. A streaming response is a handle
whose body is read once, into memory by `read()` or by one iteration; closing it releases the connection.
"""

from __future__ import annotations

import errno
import os
import threading
from contextlib import aclosing
from functools import partial
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Final, Generic, Literal, TypeAlias

from typing_extensions import Self, TypeIs, TypeVar

from ..model_codecs.errors import CodecError
from ..model_codecs.media import decode_json
from ..model_codecs.wire import thaw_wire
from .coding import CHUNK, ContentDecoder
from .errors import (
    CleanupError,
    DecodeError,
    DeliveryState,
    HTTPStatusError,
    ProtocolError,
    ResponseConsumedError,
    ResponseTooLargeError,
    SDKError,
    add_secondary,
)
from .lifecycle import cleanup_secondary
from .media import charset

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Generator, Iterator
    from concurrent.futures import Future
    from types import TracebackType

    from ..model_codecs.wire import JSONValue
    from .disk import DiskWorker
    from .errors import RetryStopReason
    from .events import CallEvents
    from .lifecycle import Scope
    from .logical import LogicalCallContext
    from .operations import ResponseDecoder
    from .options import Settings
    from .responses import ResponseInfo

_BINARY: Final[int] = getattr(os, "O_BINARY", 0)
_UMASK: Final = threading.Lock()
Action: TypeAlias = Literal["read", "text", "json", "iter_bytes", "iter_raw_bytes", "stream_to"]
State: TypeAlias = Literal["buffered", "open", "streaming", "consumed", "closed", "failed"]
SourceT = TypeVar("SourceT")
HandleT = TypeVar("HandleT")
T = TypeVar("T")


def _status_error(error: Exception) -> TypeIs[HTTPStatusError[object]]:
    return isinstance(error, HTTPStatusError)


def _pieces(data: bytes) -> Generator[bytes, None, None]:
    for start in range(0, len(data), CHUNK):
        yield data[start : start + CHUNK]


class _SavedPieces:
    """Yield saved bytes to an async reader in pieces of at most CHUNK bytes."""

    __slots__ = ("_pieces",)

    def __init__(self, data: bytes) -> None:
        self._pieces = _pieces(data)

    def __aiter__(self) -> _SavedPieces:
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._pieces)
        except StopIteration:
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        """Stop yielding; saved bytes hold nothing to release."""
        self._pieces.close()


def _temporary(path: Path) -> tuple[int, Path]:
    """Create a new file beside the target that only its owner can read while the download is written."""
    temporary = path.with_name(f".{path.name}.{os.urandom(8).hex()}.part")
    return os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY, 0o600), temporary


def _umask() -> int:
    """Return the process umask, which reading sets for an instant to one that no new file is looser under.

    Downloads read it on their own threads, so one lock keeps two reads from restoring each other's instant value.
    """
    with _UMASK:
        umask = os.umask(0o077)
        os.umask(umask)
    return umask


def _commit(temporary: Path, path: Path, *, overwrite: bool) -> None:
    """Give a completed download its name at once, never over a file that appeared meanwhile unless overwriting.

    The download first takes the permissions a plain new file gets under the umask. Without overwriting, a new link
    gives the complete file its name, refusing a name that exists, before the temporary name goes, so a failed move
    leaves no file under the target's name.
    """
    temporary.chmod(0o666 & ~_umask())
    if overwrite:
        temporary.replace(path)
        return
    os.link(temporary, path)
    temporary.unlink()


class _Budget:
    """Count the bytes of one representation against its limit, refusing the chunk that passes it."""

    __slots__ = ("info", "limit", "operation_id", "representation", "size")

    def __init__(
        self,
        limit: int | None,
        representation: Literal["decoded", "content_coded"],
        info: ResponseInfo,
        operation_id: str | None,
    ) -> None:
        self.limit = limit
        self.representation: Literal["decoded", "content_coded"] = representation
        self.info = info
        self.operation_id = operation_id
        self.size = 0

    def spend(self, amount: int) -> None:
        self.size += amount
        if self.limit is not None and self.size > self.limit:
            raise ResponseTooLargeError(
                info=self.info,
                representation=self.representation,
                limit=self.limit,
                observed_bytes=self.size,
                operation_id=self.operation_id,
                call_id=self.info.call_id,
            )


def _refuse_existing(path: Path, *, overwrite: bool) -> None:
    if not overwrite and path.exists():
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(path))


def _created(path: Path, overwrite: bool) -> tuple[BinaryIO, Path]:  # noqa: FBT001
    """Refuse an existing target unless overwriting, then open a new temporary file beside it."""
    _refuse_existing(path, overwrite=overwrite)
    handle, temporary = _temporary(path)
    try:
        return os.fdopen(handle, "wb"), temporary
    except BaseException:
        os.close(handle)
        temporary.unlink(missing_ok=True)
        raise


def _discarded(created: tuple[BinaryIO, Path]) -> None:
    """Close and remove an unfinished download."""
    file, temporary = created
    try:
        file.close()
    finally:
        temporary.unlink(missing_ok=True)


def _committed(created: tuple[BinaryIO, Path], tail: bytes, path: Path, overwrite: bool) -> None:  # noqa: FBT001
    """Write the last bytes of a completed download, close it, and give it its name."""
    file, temporary = created
    file.write(tail)
    file.close()
    _commit(temporary, path, overwrite=overwrite)


async def _waited(future: Future[int]) -> None:
    """Wait on the event loop for a write on the worker."""
    import asyncio  # noqa: PLC0415

    await asyncio.wrap_future(future)


class _Download:
    """A download to a path: its disk worker, its temporary file once created, its unwritten bytes, and its write.

    Chunks are kept until they fill a write of at least CHUNK bytes, which runs while the next ones are read. A write
    whose outcome was taken gives way to the always finished `done`.
    """

    __slots__ = ("created", "done", "future", "parts", "size", "worker")

    def __init__(self, worker: DiskWorker) -> None:
        from concurrent.futures import Future  # noqa: PLC0415

        self.worker = worker
        self.created: tuple[BinaryIO, Path] | None = None
        self.parts: list[bytes] = []
        self.size = 0
        self.done: Future[int] = Future()
        self.done.set_result(0)
        self.future = self.done

    async def create(self, path: Path, overwrite: bool) -> tuple[BinaryIO, Path]:  # noqa: FBT001
        """Open the temporary file on the worker, keeping it to discard even when the call stops right after."""
        self.created = created = await self.worker.run(_created, path, overwrite, discard=_discarded)
        return created

    def add(self, chunk: bytes) -> bytes | None:
        """Keep a chunk, and return the kept bytes once they fill a write."""
        self.parts.append(chunk)
        self.size += len(chunk)
        if self.size < CHUNK:
            return None
        data = b"".join(self.parts)
        self.parts, self.size = [], 0
        return data

    async def discard(self) -> None:
        """Let the last write settle, close and remove the unfinished file on the worker, then release the worker.

        Every late failure of the download's disk work fails the discarding, so the call keeps it as secondary.
        """
        from .disk import raise_late  # noqa: PLC0415

        try:
            if (future := self.future) is not self.done:
                self.worker.abandon(future)
            late = await self.worker.settled(every=True)
            if (created := self.created) is not None:
                await self.worker.run(_discarded, created, cleanup=True)
        finally:
            self.worker.release()
        raise_late(late)


class _Raw(Generic[SourceT, HandleT]):
    """What buffered and streaming, sync and async raw responses share: metadata, saved bytes, and state."""

    __slots__ = (
        "_body",
        "_call",
        "_classify",
        "_decoder",
        "_events",
        "_info",
        "_limits",
        "_operation_id",
        "_raw",
        "_retry_stop_reason",
        "_scope",
        "_source",
        "_state",
        "_status_secondary_errors",
    )

    def __init__(  # noqa: PLR0913
        self,
        info: ResponseInfo,
        decoder: ResponseDecoder[object, object],
        limits: Settings,
        operation_id: str | None,
        failure: Callable[[Exception], BaseException],
        *,
        source: SourceT,
        scope: Scope[HandleT],
        events: CallEvents | None,
        call: LogicalCallContext,
        retry_stop_reason: RetryStopReason | None = None,
        status_secondary_errors: tuple[Exception, ...] = (),
    ) -> None:
        """Keep the response metadata, how its status is classified, its call's limits, its body source, and scope.

        A streaming handle of a call with hooks keeps its events, which report the stream's end once it was handed over.
        """
        self._events = events
        self._call = call
        self._retry_stop_reason = retry_stop_reason
        self._status_secondary_errors = status_secondary_errors
        self._info = info
        self._decoder = decoder
        self._limits = limits
        self._operation_id = operation_id
        self._classify = failure
        self._source = source
        self._scope = scope
        self._raw = self._body = b""
        self._state: State = "open"

    @property
    def info(self) -> ResponseInfo:
        """Return the response metadata."""
        return self._info

    @property
    def body_bytes(self) -> bytes:
        """Return the body with its content codings removed, once it is in memory."""
        if self._state != "buffered":
            raise self._consumed(action="read")
        return self._body

    @property
    def raw_body_bytes(self) -> bytes:
        """Return the body as it arrived, content codings included, once it is in memory."""
        if self._state != "buffered":
            raise self._consumed(action="read")
        return self._raw

    def _consumed(self, *, action: Action) -> BaseException:
        """Return why a body that is not in memory cannot be read: its client closing, or its earlier consumption."""
        if (closed := self._closing()) is not None:
            return closed
        state = self._state
        match state:
            case "consumed" | "closed" | "failed":
                reported: Literal["streaming", "consumed", "closed", "failed"] = state
            case _:
                reported = "streaming"
        return ResponseConsumedError(
            state=reported, action=action, operation_id=self._operation_id, call_id=self._info.call_id, info=self._info
        )

    def _enter(self) -> ContentDecoder:
        """Check the call before starting to read and interpret its content codings."""
        self._check()
        self._state = "streaming"
        return ContentDecoder(self._info, self._operation_id)

    def _closing(self) -> BaseException | None:
        if (closed := self._scope.closing()) is None:
            return None
        closed.info = self._info
        return self._failure(closed)

    def _failure(self, error: Exception) -> BaseException:
        if _status_error(error):
            error.retry_stop_reason = self._retry_stop_reason
            for secondary in self._status_secondary_errors:
                add_secondary(error, secondary)
        failure = self._classify(error)
        if isinstance(failure, SDKError):
            failure.info = self._info
        return failure

    def _downloadable(self) -> None:
        """Refuse a download to a path once the body is being read or is gone, before any disk work."""
        if self._state not in {"open", "buffered"}:
            raise self._consumed(action="stream_to")

    def _budget(self, limit: int | None, representation: Literal["decoded", "content_coded"]) -> _Budget:
        return _Budget(limit, representation, self._info, self._operation_id)

    def _check(self) -> None:
        """Observe cancellation, client closing, and the active acquisition or stream deadline."""
        try:
            self._call.check("stream" if self._call.streaming else "send", DeliveryState.RESPONSE_STARTED)
        except SDKError as error:
            error.info = self._info
            raise

    def _text(self, body: bytes) -> str:
        try:
            return body.decode(charset(self._info.content_type or ""))
        except UnicodeDecodeError as error:
            raise self._undecodable(body, error) from None

    def _json(self, body: bytes) -> JSONValue:
        try:
            return thaw_wire(decode_json(body))
        except CodecError as error:
            raise self._undecodable(body, error) from None

    def _undecodable(self, body: bytes, cause: BaseException) -> DecodeError:
        return DecodeError(
            info=self._info, body_bytes=body, operation_id=self._operation_id, call_id=self._info.call_id, cause=cause
        )

    def _native(self) -> bool:
        """Return whether the call reads model bodies through their converters alone."""
        return self._limits.validation.response == "native"

    def _saved_failure(self) -> BaseException:
        """Return the typed failure of a buffered response from its bounded error prefix."""
        limit, body = self._limits.max_error_body_bytes, self._body
        error = self._decoder.failure(self._info, body[:limit], truncated=len(body) > limit, native=self._native())
        if _status_error(error):
            error.retry_stop_reason = self._retry_stop_reason
            for secondary in self._status_secondary_errors:
                add_secondary(error, secondary)
        return self._call.snapshot_error(error)

    def _unread_failure(self) -> BaseException:
        """Return the typed failure of a response whose body was partly or wholly read: no body, truncated."""
        return self._failure(self._decoder.failure(self._info, b"", truncated=True))

    def _prefix(self, parts: list[bytes], chunk: bytes, size: int) -> bool:
        """Keep a chunk of an error body within its prefix limit and return whether reading continues."""
        limit = self._limits.max_error_body_bytes
        if size <= limit:
            parts.append(chunk)
            return True
        parts.append(chunk[: len(chunk) - (size - limit)])
        return False

    def _save(self, raw: list[bytes], decoded: list[bytes]) -> None:
        self._raw = content = b"".join(raw)
        self._body = content if decoded is raw else b"".join(decoded)
        self._check()


def checked(response: _Raw[SourceT, HandleT]) -> None:
    """Raise what stops a response's call now, before using bytes it already read: cancellation, closing, or expiry.

    A stream's expiry is its call's stream deadline, as on every read.
    """
    response._check()  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001


def held(response: RawResponse) -> Generator[bytes, None, None]:
    """Read an open streaming handle's decoded body, leaving its end to the reader, which reports it with `finished`.

    A reader that stops at a terminal event of the body, or finds its end premature, decides how the stream ended.
    """
    response._state = "streaming"  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    return response._stream(decoded=True, held=True)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001


def aheld(response: AsyncRawResponse) -> AsyncGenerator[bytes, None]:
    """Read an open asyncio streaming handle's decoded body, leaving its end to the reader, as `held` does."""
    response._state = "streaming"  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    return response._stream(decoded=True, held=True)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001


def finished(response: RawResponse, error: BaseException | None = None) -> None:
    """End a held stream as read, or as failed with the reader's error, releasing it and reporting its end once."""
    response._end("consumed" if error is None else "failed", error)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001


async def afinished(response: AsyncRawResponse, error: BaseException | None = None) -> None:
    """End a held asyncio stream as read, or as failed with the reader's error, as `finished` does."""
    await response._end("consumed" if error is None else "failed", error)  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001


class RawResponse(_Raw["Callable[[], Iterator[bytes]]", "RawResponse"]):
    """A raw response of a synchronous client: buffered, or a streaming handle whose body is read at most once."""

    __slots__ = ("_close",)

    def __init__(  # noqa: PLR0913
        self,
        info: ResponseInfo,
        decoder: ResponseDecoder[object, object],
        limits: Settings,
        operation_id: str | None,
        failure: Callable[[Exception], BaseException],
        *,
        source: Callable[[], Iterator[bytes]],
        close: Callable[[], None],
        scope: Scope[RawResponse],
        call: LogicalCallContext,
        events: CallEvents | None = None,
        retry_stop_reason: RetryStopReason | None = None,
        status_secondary_errors: tuple[Exception, ...] = (),
    ) -> None:
        """Keep the response metadata, its body source, the close that releases it, and its client's scope."""
        super().__init__(
            info,
            decoder,
            limits,
            operation_id,
            failure,
            source=source,
            scope=scope,
            events=events,
            call=call,
            retry_stop_reason=retry_stop_reason,
            status_secondary_errors=status_secondary_errors,
        )
        self._close: Callable[[], None] | None = close

    def read(self) -> bytes:
        """Return the decoded body, reading a streaming handle into memory first."""
        return self._read(action="read")

    def text(self) -> str:
        """Return the body as text in its declared charset, UTF-8 when it declares none."""
        return self._text(self._read(action="text"))

    def json(self) -> JSONValue:
        """Return the body parsed as JSON."""
        return self._json(self._read(action="json"))

    def iter_bytes(self) -> Iterator[bytes]:
        """Yield the decoded body: the saved one of a buffered response, else the stream, once."""
        return self._iterate(action="iter_bytes", decoded=True)

    def iter_raw_bytes(self) -> Iterator[bytes]:
        """Yield the body as it arrived, content codings included: the saved one, else the stream, once."""
        return self._iterate(action="iter_raw_bytes", decoded=False)

    def stream_to(self, target: str | PathLike[str] | BinaryIO, *, overwrite: bool = False) -> None:
        """Write the decoded body to a file object, or to a path through a temporary file moved there on success."""
        if not isinstance(target, (str, PathLike)):
            self._write(target.write)
            return
        self._downloadable()
        path = Path(target)
        file, temporary = _created(path, overwrite)
        try:
            with file:
                self._write(file.write)
            _commit(temporary, path, overwrite=overwrite)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def _write(self, write: Callable[[bytes], object]) -> None:
        """Write each decoded chunk; a failed write ends the stream it started before its failure propagates."""
        chunks = self._iterate(action="stream_to", decoded=True)
        try:
            for chunk in chunks:
                write(chunk)
        except BaseException as error:
            if self._state == "streaming":
                self._end("failed", error)
            raise

    def raise_for_status(self) -> None:
        """Return for a success; close and raise the typed failure of any other status from its error prefix.

        A handed-over stream ends in that failure, which keeps any hook failure of its end as a secondary error.
        """
        if (state := self._state) != "buffered":
            self._check()
        if self._decoder.success(self._info.status_code):
            return
        match state:
            case "buffered":
                raise self._saved_failure()
            case "open":
                failure = self._error_prefix()
            case _:
                failure = self._unread_failure()
        if self._state in {"open", "streaming"}:
            self._end("closed", failure)
        raise failure

    def close(self) -> None:
        """Release a streaming handle's connection; saved bytes stay readable, and closing twice does nothing."""
        if self._state in {"open", "streaming"}:
            self._end("closed")

    def __enter__(self) -> Self:
        """Return this response, which leaving the block closes."""
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close this response."""
        self.close()

    def discard(self, error: BaseException) -> None:
        """Close the handle while an error propagates, keeping a close failure beside that error."""
        if self._state in {"open", "streaming"}:
            self._end("closed", error)

    def _read(self, *, action: Action) -> bytes:
        if self._state == "open":
            self._buffer()
        if self._state != "buffered":
            raise self._consumed(action=action)
        return self._body

    def _buffer(self) -> None:
        """Read the body into memory; an identity body is kept once, as it arrived."""
        limit = self._limits.max_response_bytes
        raw: list[bytes] = []
        budget = self._budget(limit, "decoded")
        try:
            decoder = self._enter()
            decoded = raw if decoder.identity else []
            for chunk in decoder.decoded(self._chunks() if decoder.identity else self._recorded(raw, limit)):
                budget.spend(len(chunk))
                decoded.append(chunk)
            self._save(raw, decoded)
        except Exception as error:  # noqa: BLE001
            failure = self._failure(error)
            self._end("failed", failure)
            raise failure from None
        except BaseException as error:
            self._end("failed", error)
            raise
        self._end("buffered")

    def _chunks(self) -> Iterator[bytes]:
        """Check both sides of every native read before yielding its bytes to the decoder."""
        self._check()
        source = self._source()
        while True:
            self._check()
            try:
                chunk = next(source)
            except StopIteration:
                self._check()
                return
            self._check()
            yield chunk

    def _recorded(self, parts: list[bytes], limit: int | None) -> Iterator[bytes]:
        """Yield the coded chunks while keeping them, refusing more coded bytes than the buffer holds."""
        budget = self._budget(limit, "content_coded")
        for chunk in self._chunks():
            budget.spend(len(chunk))
            parts.append(chunk)
            yield chunk

    def _iterate(self, *, action: Action, decoded: bool) -> Iterator[bytes]:
        match self._state:
            case "buffered":
                return _pieces(self._body if decoded else self._raw)
            case "open":
                self._state = "streaming"
                return self._stream(decoded=decoded)
            case _:
                pass
        raise self._consumed(action=action)

    def _stream(self, *, decoded: bool, held: bool = False) -> Generator[bytes, None, None]:
        budget = self._budget(self._limits.max_stream_bytes, "decoded" if decoded else "content_coded")
        try:
            source = self._chunks()
            for chunk in ContentDecoder(self._info, self._operation_id).decoded(source) if decoded else source:
                self._check()
                budget.spend(len(chunk))
                yield chunk
        except Exception as error:  # noqa: BLE001
            failure = self._failure(error)
            self._end("failed", failure)
            raise failure from None
        except BaseException as error:
            if not isinstance(error, GeneratorExit):
                self._end("failed", error)
            raise
        if not held:
            self._end("consumed")

    def _error_prefix(self) -> BaseException:
        """Read the error prefix of a streaming handle and return its typed failure."""
        self._state = "streaming"
        parts: list[bytes] = []
        size = 0
        problem: ProtocolError | None = None
        try:
            for chunk in ContentDecoder(self._info, self._operation_id).decoded(self._chunks()):
                size += len(chunk)
                if not self._prefix(parts, chunk, size):
                    break
        except ProtocolError as error:
            problem = error
        except Exception as error:  # noqa: BLE001
            failure = self._failure(error)
            self._end("failed", failure)
            return failure
        except BaseException as error:
            self._end("failed", error)
            raise
        truncated = size > self._limits.max_error_body_bytes or problem is not None
        return self._failure(
            self._decoder.failure(
                self._info, b"".join(parts), truncated=truncated, problem=problem, native=self._native()
            )
        )

    def _end(self, state: State, error: BaseException | None = None) -> None:
        """Enter a final state, releasing the connection and the handle's place in its scope once.

        The close is taken under the scope's lock, as closing the client may end the handle while its reader does. A
        handed-over stream then reports its end to its call's hooks.
        """
        self._state = state
        with self._scope.lock:
            close, self._close = self._close, None
        if close is None:
            return
        failed: BaseException | None = None
        try:
            close()
        except Exception as failure:  # noqa: BLE001
            if error is None:
                failed = error = self._call.snapshot_error(
                    failure if isinstance(failure, CleanupError) else CleanupError(cause=failure)
                )
                failed.info = self._info
            else:
                self._call.retry_blocked = True
                cleanup_secondary(error, failure)
        except BaseException as interruption:
            if error is not None and not isinstance(error, Exception):
                add_secondary(error, CleanupError(cause=interruption))
                self._released(error, early=state == "closed")
                return
            self._released(interruption, early=state == "closed")
            raise
        self._released(error, early=state == "closed")
        if failed is not None:
            raise failed

    def _released(self, error: BaseException | None, *, early: bool) -> None:
        """Give up the handle's place in its scope, then report a handed-over stream's end to its call's hooks."""
        try:
            self._scope.release_handle(self)
            if (events := self._events) is not None:
                events.streamed(error, early=early)
        finally:
            if self._call.streaming:
                self._call.finish()


class AsyncRawResponse(_Raw["Callable[[], AsyncIterator[bytes]]", "AsyncRawResponse"]):
    """A raw response of an asyncio client: buffered, or a streaming handle whose body is read at most once."""

    __slots__ = ("_close",)

    def __init__(  # noqa: PLR0913
        self,
        info: ResponseInfo,
        decoder: ResponseDecoder[object, object],
        limits: Settings,
        operation_id: str | None,
        failure: Callable[[Exception], BaseException],
        *,
        source: Callable[[], AsyncIterator[bytes]],
        close: Callable[[], Awaitable[None]],
        scope: Scope[AsyncRawResponse],
        call: LogicalCallContext,
        events: CallEvents | None = None,
        retry_stop_reason: RetryStopReason | None = None,
        status_secondary_errors: tuple[Exception, ...] = (),
    ) -> None:
        """Keep the response metadata, its body source, the close that releases it, and its client's scope."""
        super().__init__(
            info,
            decoder,
            limits,
            operation_id,
            failure,
            source=source,
            scope=scope,
            events=events,
            call=call,
            retry_stop_reason=retry_stop_reason,
            status_secondary_errors=status_secondary_errors,
        )
        self._close: Callable[[], Awaitable[None]] | None = close

    async def read(self) -> bytes:
        """Return the decoded body, reading a streaming handle into memory first."""
        return await self._read(action="read")

    async def text(self) -> str:
        """Return the body as text in its declared charset, UTF-8 when it declares none."""
        return self._text(await self._read(action="text"))

    async def json(self) -> JSONValue:
        """Return the body parsed as JSON."""
        return self._json(await self._read(action="json"))

    def iter_bytes(self) -> AsyncIterator[bytes]:
        """Yield the decoded body: the saved one of a buffered response, else the stream, once."""
        return self._iterate(action="iter_bytes", decoded=True)

    def iter_raw_bytes(self) -> AsyncIterator[bytes]:
        """Yield the body as it arrived, content codings included: the saved one, else the stream, once."""
        return self._iterate(action="iter_raw_bytes", decoded=False)

    async def stream_to(self, target: str | PathLike[str] | BinaryIO, *, overwrite: bool = False) -> None:
        """Write the decoded body to a file object, or to a path through a temporary file moved there on success.

        A file object is written on the event loop. A path's file is created, written, and moved on a disk thread of
        the handle, which writes about CHUNK bytes at a time while the next ones are read and stops once the download
        ends.
        """
        if isinstance(target, (str, PathLike)):
            await self._download(Path(target), overwrite=overwrite)
            return
        chunks = self._iterate(action="stream_to", decoded=True)
        try:
            async with aclosing(chunks):
                async for chunk in chunks:
                    target.write(chunk)
        except BaseException as error:
            await self._write_failed(error)
            raise

    async def _write_failed(self, error: BaseException) -> None:
        """End the stream a failed write of this call left behind, before the failure propagates."""
        if self._state == "streaming":
            await self._end("failed", error)

    async def _download(self, path: Path, *, overwrite: bool) -> None:
        """Write the body to a path on a disk thread, reading the next chunks while one write runs.

        Opening the file and waiting for a write count against the stream's total limit and the call's deadline, but
        never against its idle or read limits; the final write and move run to their end. A failure ends the stream
        and removes the unfinished file before it propagates. Until then the download holds its client's close.
        """
        self._downloadable()
        import asyncio  # noqa: PLC0415

        from .disk import DiskWorker  # noqa: PLC0415 - Only a download to a path starts a disk thread.

        held = asyncio.Event()
        self._scope.retain_cleanup(asyncio.create_task(held.wait()), owner=self._call)
        worker = DiskWorker("AsyncRawResponse")
        worker.acquire()
        download = _Download(worker)
        chunks: AsyncGenerator[bytes, None] | _SavedPieces | None = None
        try:
            created = await self._disk(partial(download.create, path, overwrite))
            chunks = self._iterate(action="stream_to", decoded=True)
            async with aclosing(chunks):
                async for chunk in chunks:
                    if (data := download.add(chunk)) is not None:
                        await self._flushed(download)
                        download.future = worker.submit(created[0].write, data)
            await self._flushed(download)
            await worker.run(_committed, created, b"".join(download.parts), path, overwrite)
        except BaseException as error:
            try:
                if chunks is not None:
                    await self._write_failed(error)
            finally:
                await self._call.cleanup(download.discard, error=error)
            raise
        finally:
            worker.close()
            held.set()
        worker.release()

    async def _flushed(self, download: _Download) -> None:
        """Wait for the download's last write, at once when it already ended, and raise its failure."""
        if not (future := download.future).done():
            await self._disk(partial(_waited, future))
        download.future = download.done
        future.result()

    async def _disk(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Await disk work: a saved body's at once, a stream's under its limits, except the idle one, and closing."""
        if self._state == "buffered":
            return await operation()
        try:
            return await self._call.bounded(
                operation, phase="stream", delivery_state=DeliveryState.RESPONSE_STARTED, idle=False
            )
        except SDKError as error:
            error.info = self._info
            raise

    async def raise_for_status(self) -> None:
        """Return for a success; close and raise the typed failure of any other status from its error prefix.

        A handed-over stream ends in that failure, which keeps any hook failure of its end as a secondary error.
        """
        if (state := self._state) != "buffered":
            self._check()
        if self._decoder.success(self._info.status_code):
            return
        match state:
            case "buffered":
                raise self._saved_failure()
            case "open":
                failure = await self._error_prefix()
            case _:
                failure = self._unread_failure()
        if self._state in {"open", "streaming"}:
            await self._end("closed", failure)
        raise failure

    async def aclose(self) -> None:
        """Release a streaming handle's connection; saved bytes stay readable, and closing twice does nothing."""
        if self._state in {"open", "streaming"}:
            await self._end("closed")

    async def __aenter__(self) -> Self:
        """Return this response, which leaving the block closes."""
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Close this response."""
        await self.aclose()

    async def discard(self, error: BaseException) -> None:
        """Close the handle while an error propagates, keeping a close failure beside that error."""
        if self._state in {"open", "streaming"}:
            await self._end("closed", error)

    async def _read(self, *, action: Action) -> bytes:
        if self._state == "open":
            await self._buffer()
        if self._state != "buffered":
            raise self._consumed(action=action)
        return self._body

    async def _buffer(self) -> None:
        """Read the body into memory; an identity body is kept once, as it arrived."""
        limit = self._limits.max_response_bytes
        raw: list[bytes] = []
        budget = self._budget(limit, "decoded")
        try:
            decoder = self._enter()
            decoded = raw if decoder.identity else []
            async for chunk in decoder.adecoded(self._chunks() if decoder.identity else self._recorded(raw, limit)):
                budget.spend(len(chunk))
                decoded.append(chunk)
            self._save(raw, decoded)
        except Exception as error:  # noqa: BLE001
            failure = self._failure(error)
            await self._end("failed", failure)
            raise failure from None
        except BaseException as error:
            await self._end("failed", error)
            raise
        await self._end("buffered")

    async def _chunks(self) -> AsyncIterator[bytes]:
        """Bound each native read by cancellation, closing, and the active call or stream limits."""
        self._check()
        source = self._source()
        while True:
            try:
                chunk = await self._call.bounded(
                    source.__anext__,
                    phase="stream" if self._call.streaming else "send",
                    delivery_state=DeliveryState.RESPONSE_STARTED,
                )
            except StopAsyncIteration:
                return
            yield chunk

    async def _recorded(self, parts: list[bytes], limit: int | None) -> AsyncIterator[bytes]:
        """Yield the coded chunks while keeping them, refusing more coded bytes than the buffer holds."""
        budget = self._budget(limit, "content_coded")
        async for chunk in self._chunks():
            budget.spend(len(chunk))
            parts.append(chunk)
            yield chunk

    def _iterate(self, *, action: Action, decoded: bool) -> AsyncGenerator[bytes, None] | _SavedPieces:
        match self._state:
            case "buffered":
                return _SavedPieces(self._body if decoded else self._raw)
            case "open":
                self._state = "streaming"
                return self._stream(decoded=decoded)
            case _:
                pass
        raise self._consumed(action=action)

    async def _stream(self, *, decoded: bool, held: bool = False) -> AsyncGenerator[bytes, None]:
        budget = self._budget(self._limits.max_stream_bytes, "decoded" if decoded else "content_coded")
        try:
            source = self._chunks()
            async for chunk in ContentDecoder(self._info, self._operation_id).adecoded(source) if decoded else source:
                self._check()
                budget.spend(len(chunk))
                yield chunk
        except Exception as error:  # noqa: BLE001
            failure = self._failure(error)
            await self._end("failed", failure)
            raise failure from None
        except BaseException as error:
            if not isinstance(error, GeneratorExit):
                await self._end("failed", error)
            raise
        if not held:
            await self._end("consumed")

    async def _error_prefix(self) -> BaseException:
        """Read the error prefix of a streaming handle and return its typed failure."""
        self._state = "streaming"
        parts: list[bytes] = []
        size = 0
        problem: ProtocolError | None = None
        try:
            async for chunk in ContentDecoder(self._info, self._operation_id).adecoded(self._chunks()):
                size += len(chunk)
                if not self._prefix(parts, chunk, size):
                    break
        except ProtocolError as error:
            problem = error
        except Exception as error:  # noqa: BLE001
            failure = self._failure(error)
            await self._end("failed", failure)
            return failure
        except BaseException as error:
            await self._end("failed", error)
            raise
        truncated = size > self._limits.max_error_body_bytes or problem is not None
        return self._failure(
            self._decoder.failure(
                self._info, b"".join(parts), truncated=truncated, problem=problem, native=self._native()
            )
        )

    async def _end(self, state: State, error: BaseException | None = None) -> None:
        """Enter a final state, releasing the connection and the handle's place in its scope once.

        A handed-over stream then reports its end to its call's hooks.
        """
        self._state = state
        with self._scope.lock:
            close, self._close = self._close, None
        if close is None:
            return
        failed: BaseException | None = None
        try:
            await self._call.cleanup(close, error=error)
        except Exception as failure:  # noqa: BLE001
            failed = error = self._failure(failure)
        except BaseException as interruption:
            if error is not None and not isinstance(error, Exception):
                add_secondary(error, CleanupError(cause=interruption))
                await self._released(error, early=state == "closed")
                return
            await self._released(interruption, early=state == "closed")
            raise
        await self._released(error, early=state == "closed")
        if failed is not None:
            raise failed

    async def _released(self, error: BaseException | None, *, early: bool) -> None:
        """Give up the handle's place in its scope, then report a handed-over stream's end to its call's hooks."""
        try:
            self._scope.release_handle(self)
            if (events := self._events) is not None:
                await events.astreamed(error, early=early)
        finally:
            if self._call.streaming:
                self._call.finish()
