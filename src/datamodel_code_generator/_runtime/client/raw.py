"""Raw responses: the status, headers, and body of a call, without decoding the declared value.

A buffered response holds its body and stays readable after its client closes. A streaming response is a handle
whose body is read once, into memory by `read()` or by one iteration; closing it releases the connection.
"""

from __future__ import annotations

import errno
import os
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Final, Generic, Literal, TypeAlias

from typing_extensions import Self, TypeVar

from ..model_codecs.errors import CodecError
from ..model_codecs.media import decode_json
from ..model_codecs.wire import thaw_wire
from .coding import CHUNK, ContentDecoder
from .errors import (
    DecodeError,
    ProtocolError,
    ResponseConsumedError,
    ResponseTooLargeError,
    SDKError,
    add_secondary,
)
from .operations import charset

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
    from types import TracebackType

    from ..model_codecs.wire import JSONValue
    from .lifecycle import Scope
    from .operations import ResponseDecoder
    from .options import Settings
    from .responses import ResponseInfo

_BINARY: Final[int] = getattr(os, "O_BINARY", 0)
Action: TypeAlias = Literal["read", "text", "json", "iter_bytes", "iter_raw_bytes", "stream_to"]
State: TypeAlias = Literal["buffered", "open", "streaming", "consumed", "closed", "failed"]
SourceT = TypeVar("SourceT")
HandleT = TypeVar("HandleT")


def _pieces(data: bytes) -> Iterator[bytes]:
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


def _temporary(path: Path) -> tuple[int, Path]:
    """Create a new file beside the target that only its owner can read while the download is written."""
    temporary = path.with_name(f".{path.name}.{os.urandom(8).hex()}.part")
    return os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY, 0o600), temporary


def _umask() -> int:
    """Return the process umask, which reading sets for an instant to one that no new file is looser under."""
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


class _Raw(Generic[SourceT, HandleT]):
    """What buffered and streaming, sync and async raw responses share: metadata, saved bytes, and state."""

    __slots__ = (
        "_body",
        "_decoder",
        "_failure",
        "_info",
        "_limits",
        "_operation_id",
        "_raw",
        "_scope",
        "_source",
        "_state",
    )

    def __init__(  # noqa: PLR0913
        self,
        info: ResponseInfo,
        decoder: ResponseDecoder[object, object],
        limits: Settings,
        operation_id: str | None,
        failure: Callable[[Exception], SDKError],
        *,
        source: SourceT,
        scope: Scope[HandleT],
    ) -> None:
        """Keep the response metadata, how its status is classified, its call's limits, its body source, and scope."""
        self._info = info
        self._decoder = decoder
        self._limits = limits
        self._operation_id = operation_id
        self._failure = failure
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

    def _consumed(self, *, action: Action) -> SDKError:
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

    def _enter(self) -> None:
        """Start reading a streaming body, unless its client started closing."""
        self._check()
        self._state = "streaming"

    def _closing(self) -> SDKError | None:
        if (closed := self._scope.closing()) is None:
            return None
        closed.info = self._info
        return self._failure(closed)

    def _budget(self, limit: int | None, representation: Literal["decoded", "content_coded"]) -> _Budget:
        return _Budget(limit, representation, self._info, self._operation_id)

    def _check(self) -> None:
        """Stop reading a streaming body once its client or view started closing."""
        if (closed := self._closing()) is not None:
            raise closed

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

    def _saved_failure(self) -> SDKError:
        """Return the typed failure of a buffered response from its bounded error prefix."""
        limit, body = self._limits.max_error_body_bytes, self._body
        return self._failure(self._decoder.failure(self._info, body[:limit], truncated=len(body) > limit))

    def _unread_failure(self) -> SDKError:
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


class RawResponse(_Raw["Callable[[], Iterator[bytes]]", "RawResponse"]):
    """A raw response of a synchronous client: buffered, or a streaming handle whose body is read at most once."""

    __slots__ = ("_close",)

    def __init__(  # noqa: PLR0913
        self,
        info: ResponseInfo,
        decoder: ResponseDecoder[object, object],
        limits: Settings,
        operation_id: str | None,
        failure: Callable[[Exception], SDKError],
        *,
        source: Callable[[], Iterator[bytes]],
        close: Callable[[], None],
        scope: Scope[RawResponse],
    ) -> None:
        """Keep the response metadata, its body source, the close that releases it, and its client's scope."""
        super().__init__(info, decoder, limits, operation_id, failure, source=source, scope=scope)
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
            for chunk in self._iterate(action="stream_to", decoded=True):
                target.write(chunk)
            return
        path = Path(target)
        _refuse_existing(path, overwrite=overwrite)
        handle, temporary = _temporary(path)
        try:
            with os.fdopen(handle, "wb") as file:
                for chunk in self._iterate(action="stream_to", decoded=True):
                    file.write(chunk)
            _commit(temporary, path, overwrite=overwrite)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def raise_for_status(self) -> None:
        """Return for a success; close and raise the typed failure of any other status from its error prefix."""
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
        self.close()
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
        self._enter()
        decoder = ContentDecoder(self._info, self._operation_id)
        limit = self._limits.max_response_bytes
        raw: list[bytes] = []
        decoded = raw if decoder.identity else []
        budget = self._budget(limit, "decoded")
        try:
            for chunk in decoder.decoded(self._source() if decoder.identity else self._recorded(raw, limit)):
                budget.spend(len(chunk))
                decoded.append(chunk)
        except Exception as error:  # noqa: BLE001
            failure = self._failure(error)
            self._end("failed", failure)
            raise failure from None
        self._raw = content = b"".join(raw)
        self._body = content if decoded is raw else b"".join(decoded)
        self._end("buffered")

    def _recorded(self, parts: list[bytes], limit: int | None) -> Iterator[bytes]:
        """Yield the coded chunks while keeping them, refusing more coded bytes than the buffer holds."""
        budget = self._budget(limit, "content_coded")
        for chunk in self._source():
            budget.spend(len(chunk))
            parts.append(chunk)
            yield chunk

    def _iterate(self, *, action: Action, decoded: bool) -> Iterator[bytes]:
        match self._state:
            case "buffered":
                return _pieces(self._body if decoded else self._raw)
            case "open":
                self._enter()
                return self._stream(decoded=decoded)
            case _:
                pass
        raise self._consumed(action=action)

    def _stream(self, *, decoded: bool) -> Iterator[bytes]:
        budget = self._budget(self._limits.max_stream_bytes, "decoded" if decoded else "content_coded")
        try:
            source = self._source()
            for chunk in ContentDecoder(self._info, self._operation_id).decoded(source) if decoded else source:
                self._check()
                budget.spend(len(chunk))
                yield chunk
        except Exception as error:  # noqa: BLE001
            failure = self._failure(error)
            self._end("failed", failure)
            raise failure from None
        self._end("consumed")

    def _error_prefix(self) -> SDKError:
        """Read the error prefix of a streaming handle and return its typed failure."""
        self._state = "streaming"
        parts: list[bytes] = []
        size = 0
        problem: ProtocolError | None = None
        try:
            for chunk in ContentDecoder(self._info, self._operation_id).decoded(self._source()):
                size += len(chunk)
                if not self._prefix(parts, chunk, size):
                    break
        except ProtocolError as error:
            problem = error
        except Exception as error:  # noqa: BLE001
            failure = self._failure(error)
            self._end("failed", failure)
            return failure
        truncated = size > self._limits.max_error_body_bytes or problem is not None
        return self._failure(self._decoder.failure(self._info, b"".join(parts), truncated=truncated, problem=problem))

    def _end(self, state: State, error: BaseException | None = None) -> None:
        """Enter a final state, releasing the connection and the handle's place in its scope once.

        The close is taken under the scope's lock, as closing the client may end the handle while its reader does.
        """
        self._state = state
        with self._scope.lock:
            close, self._close = self._close, None
        if close is None:
            return
        try:
            close()
        except Exception as failure:  # noqa: BLE001
            if error is None:
                raise self._failure(failure) from None
            add_secondary(error, failure)
        finally:
            self._scope.release_handle(self)


class AsyncRawResponse(_Raw["Callable[[], AsyncIterator[bytes]]", "AsyncRawResponse"]):
    """A raw response of an asyncio client: buffered, or a streaming handle whose body is read at most once."""

    __slots__ = ("_close",)

    def __init__(  # noqa: PLR0913
        self,
        info: ResponseInfo,
        decoder: ResponseDecoder[object, object],
        limits: Settings,
        operation_id: str | None,
        failure: Callable[[Exception], SDKError],
        *,
        source: Callable[[], AsyncIterator[bytes]],
        close: Callable[[], Awaitable[None]],
        scope: Scope[AsyncRawResponse],
    ) -> None:
        """Keep the response metadata, its body source, the close that releases it, and its client's scope."""
        super().__init__(info, decoder, limits, operation_id, failure, source=source, scope=scope)
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
        """Write the decoded body to a file object, or to a path through a temporary file moved there on success."""
        if not isinstance(target, (str, PathLike)):
            async for chunk in self._iterate(action="stream_to", decoded=True):
                target.write(chunk)
            return
        path = Path(target)
        _refuse_existing(path, overwrite=overwrite)
        handle, temporary = _temporary(path)
        try:
            with os.fdopen(handle, "wb") as file:
                async for chunk in self._iterate(action="stream_to", decoded=True):
                    file.write(chunk)
            _commit(temporary, path, overwrite=overwrite)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    async def raise_for_status(self) -> None:
        """Return for a success; close and raise the typed failure of any other status from its error prefix."""
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
        await self.aclose()
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
        self._enter()
        decoder = ContentDecoder(self._info, self._operation_id)
        limit = self._limits.max_response_bytes
        raw: list[bytes] = []
        decoded = raw if decoder.identity else []
        budget = self._budget(limit, "decoded")
        try:
            async for chunk in decoder.adecoded(self._source() if decoder.identity else self._recorded(raw, limit)):
                budget.spend(len(chunk))
                decoded.append(chunk)
        except Exception as error:  # noqa: BLE001
            failure = self._failure(error)
            await self._end("failed", failure)
            raise failure from None
        self._raw = content = b"".join(raw)
        self._body = content if decoded is raw else b"".join(decoded)
        await self._end("buffered")

    async def _recorded(self, parts: list[bytes], limit: int | None) -> AsyncIterator[bytes]:
        """Yield the coded chunks while keeping them, refusing more coded bytes than the buffer holds."""
        budget = self._budget(limit, "content_coded")
        async for chunk in self._source():
            budget.spend(len(chunk))
            parts.append(chunk)
            yield chunk

    def _iterate(self, *, action: Action, decoded: bool) -> AsyncIterator[bytes]:
        match self._state:
            case "buffered":
                return _SavedPieces(self._body if decoded else self._raw)
            case "open":
                self._enter()
                return self._stream(decoded=decoded)
            case _:
                pass
        raise self._consumed(action=action)

    async def _stream(self, *, decoded: bool) -> AsyncIterator[bytes]:
        budget = self._budget(self._limits.max_stream_bytes, "decoded" if decoded else "content_coded")
        try:
            source = self._source()
            async for chunk in ContentDecoder(self._info, self._operation_id).adecoded(source) if decoded else source:
                self._check()
                budget.spend(len(chunk))
                yield chunk
        except Exception as error:  # noqa: BLE001
            failure = self._failure(error)
            await self._end("failed", failure)
            raise failure from None
        await self._end("consumed")

    async def _error_prefix(self) -> SDKError:
        """Read the error prefix of a streaming handle and return its typed failure."""
        self._state = "streaming"
        parts: list[bytes] = []
        size = 0
        problem: ProtocolError | None = None
        try:
            async for chunk in ContentDecoder(self._info, self._operation_id).adecoded(self._source()):
                size += len(chunk)
                if not self._prefix(parts, chunk, size):
                    break
        except ProtocolError as error:
            problem = error
        except Exception as error:  # noqa: BLE001
            failure = self._failure(error)
            await self._end("failed", failure)
            return failure
        truncated = size > self._limits.max_error_body_bytes or problem is not None
        return self._failure(self._decoder.failure(self._info, b"".join(parts), truncated=truncated, problem=problem))

    async def _end(self, state: State, error: BaseException | None = None) -> None:
        """Enter a final state, releasing the connection and the handle's place in its scope once."""
        self._state = state
        with self._scope.lock:
            close, self._close = self._close, None
        if close is None:
            return
        try:
            await close()
        except Exception as failure:  # noqa: BLE001
            if error is None:
                raise self._failure(failure) from None
            add_secondary(error, failure)
        finally:
            self._scope.release_handle(self)
