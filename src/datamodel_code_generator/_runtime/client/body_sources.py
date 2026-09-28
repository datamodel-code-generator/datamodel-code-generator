"""Call-owned body bindings, distinct from the attempts opened for each resource hop."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from .bodies import (
    AsyncBodyFactory,
    AsyncFileBody,
    AsyncStreamBody,
    BodyFactory,
    FileBody,
    StreamBody,
    bind_async_factory,
    bind_async_input,
    bind_factory,
    bind_input,
    body_secondary,
)
from .errors import RequestEncodingError
from .multipart import (
    AsyncMultipartAttempt,
    MultipartAttempt,
    MultipartSource,
    close_async_attempts,
    close_attempts,
    is_file_part,
    is_multipart,
    quiet_aclose,
    quiet_close,
    raise_cleanup,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .bodies import AsyncBodyAttempt, AsyncBodyCleanup, BodyAttempt, BodyAttemptContext


class BodySource(Protocol):
    """One synchronous call's replay eligibility, attempt opening, and final ownership."""

    @property
    def replayable(self) -> bool:
        """Return whether another attempt can recreate the body without consuming it."""
        ...

    def open(self, context: BodyAttemptContext) -> BodyAttempt:
        """Open one owned attempt after admission."""
        ...

    def close(self) -> None:
        """Release the call's inputs exactly once."""
        ...


class AsyncBodySource(Protocol):
    """One async call's replay eligibility, attempt opening, and retained final cleanup."""

    @property
    def replayable(self) -> bool:
        """Return whether another attempt can recreate the body without consuming it."""
        ...

    async def aopen(self, context: BodyAttemptContext) -> AsyncBodyAttempt:
        """Open one owned attempt after admission."""
        ...

    async def aclose(self) -> None:
        """Release the call's inputs exactly once."""
        ...


def _inputs(body: object) -> Iterator[object]:
    if is_multipart(body):
        for part in body.parts:
            if is_file_part(part):
                yield part.content
    else:
        yield body


class BodyBindings:
    """Claims captured before encoding and later transferred to a bound body source."""

    __slots__ = ("_entries", "_history", "_sources")

    def __init__(self) -> None:
        """Allocate ownership state only for a resource body."""
        self._entries: dict[int, tuple[object, BodySource]] = {}
        self._sources: list[BodySource] = []
        self._history: dict[int, BodyAttempt] | None = None

    def capture(self, body: FileBody | StreamBody) -> None:
        """Claim an explicit file or stream before callbacks and encoding."""
        source = bind_input(body)
        self._sources.append(source)
        self._entries[id(body)] = body, source

    def bind(self, body: object) -> BodySource:
        """Reuse an entry claim or adopt an input created by encoding."""
        if (entry := self._entries.pop(id(body), None)) is not None:
            return entry[1]
        if isinstance(body, (FileBody, StreamBody)):
            source: BodySource = bind_input(body)
        elif isinstance(body, BodyFactory):
            if self._history is None:
                self._history = {}
            source = bind_factory(body, self._history)
        else:
            raise RequestEncodingError(
                location=("body",),
                cause=TypeError(
                    "A body must be bytes or a file, stream, factory, or multipart body of the client's mode"
                ),
            )
        self._sources.append(source)
        return source

    def close(self) -> None:
        """Release every captured input, including ones encoding omitted, and clear call history."""
        sources, self._sources = self._sources, []
        try:
            raise_cleanup([failure for source in sources if (failure := quiet_close(source.close)) is not None])
        finally:
            self._entries.clear()
            if self._history is not None:
                self._history.clear()


class AsyncBodyBindings:
    """Async claims retained through capture, encoding, attempts, and final cleanup."""

    __slots__ = ("_entries", "_history", "_sources", "cleanup")

    def __init__(self, cleanup: AsyncBodyCleanup) -> None:
        """Allocate ownership state only for an asynchronous resource body."""
        self.cleanup = cleanup
        self._entries: dict[int, tuple[object, AsyncBodySource]] = {}
        self._sources: list[AsyncBodySource] = []
        self._history: dict[int, AsyncBodyAttempt] | None = None

    async def capture(self, body: AsyncFileBody | AsyncStreamBody) -> None:
        """Claim an explicit source, settling pending disk preparation before returning."""
        source = await bind_async_input(body)
        self._sources.append(source)
        self._entries[id(body)] = body, source

    async def bind(self, body: object) -> AsyncBodySource:
        """Reuse a captured claim or adopt the asynchronously encoded source."""
        if (entry := self._entries.pop(id(body), None)) is not None:
            return entry[1]
        if isinstance(body, AsyncFileBody):
            source: AsyncBodySource = await bind_async_input(body)
        elif isinstance(body, AsyncStreamBody):
            source = await bind_async_input(body)
        elif isinstance(body, AsyncBodyFactory):
            if self._history is None:
                self._history = {}
            source = bind_async_factory(body, self._history, self.cleanup)
        else:
            raise RequestEncodingError(
                location=("body",),
                cause=TypeError(
                    "A body must be bytes or a file, stream, factory, or multipart body of the client's mode"
                ),
            )
        self._sources.append(source)
        return source

    async def aclose(self) -> None:
        """Join all final source cleanup before releasing call-owned identity references."""
        sources, self._sources = self._sources, []
        try:
            raise_cleanup([failure for source in sources if (failure := await quiet_aclose(source.aclose)) is not None])
        finally:
            self._entries.clear()
            if self._history is not None:
                self._history.clear()


def capture_body(body: object) -> BodyBindings | None:
    """Capture only explicit file/stream inputs without encoding, opening paths, or invoking factories."""
    bindings: BodyBindings | None = None
    try:
        for item in _inputs(body):
            if isinstance(item, (FileBody, StreamBody)):
                if bindings is None:
                    bindings = BodyBindings()
                bindings.capture(item)
    except BaseException as error:
        if bindings is not None and (failure := quiet_close(bindings.close)) is not None:
            body_secondary(error, failure)
        raise
    return bindings


async def capture_async_body(body: object, *, cleanup: AsyncBodyCleanup) -> AsyncBodyBindings | None:
    """Capture explicit async resources under the caller's bounded ownership operation."""
    bindings: AsyncBodyBindings | None = None
    try:
        for item in _inputs(body):
            if isinstance(item, (AsyncFileBody, AsyncStreamBody)):
                if bindings is None:
                    bindings = AsyncBodyBindings(cleanup)
                await bindings.capture(item)
    except BaseException as error:
        if bindings is not None:
            await cleanup(bindings.aclose, error=error)
        raise
    return bindings


class _BoundBody:
    """An encoded layout and its primitive sources sharing one final owner."""

    __slots__ = ("_bindings", "_multipart", "_pieces")

    def __init__(self, pieces: list[bytes | BodySource], bindings: BodyBindings, *, multipart: bool) -> None:
        self._pieces = pieces
        self._bindings = bindings
        self._multipart = multipart

    @property
    def replayable(self) -> bool:
        return all(isinstance(piece, bytes) or piece.replayable for piece in self._pieces)

    def open(self, context: BodyAttemptContext) -> BodyAttempt:
        pieces: list[bytes | BodyAttempt] = []
        try:
            for piece in self._pieces:
                pieces.append(piece if isinstance(piece, bytes) else piece.open(context))  # noqa: PERF401 - Retain partially opened attempts for cleanup.
            if not self._multipart and not isinstance(attempt := pieces[0], bytes):
                return attempt
            return MultipartAttempt(pieces)
        except BaseException as error:
            if (failure := quiet_close(lambda: close_attempts(pieces))) is not None:
                body_secondary(error, failure)
            raise

    def close(self) -> None:
        self._bindings.close()


class _AsyncBoundBody:
    """The encoded async layout and all claims retained by its adopted call pipeline."""

    __slots__ = ("_bindings", "_multipart", "_pieces")

    def __init__(self, pieces: list[bytes | AsyncBodySource], bindings: AsyncBodyBindings, *, multipart: bool) -> None:
        self._pieces = pieces
        self._bindings = bindings
        self._multipart = multipart

    @property
    def replayable(self) -> bool:
        return all(isinstance(piece, bytes) or piece.replayable for piece in self._pieces)

    async def aopen(self, context: BodyAttemptContext) -> AsyncBodyAttempt:
        pieces: list[bytes | AsyncBodyAttempt] = []
        try:
            for piece in self._pieces:
                pieces.append(piece if isinstance(piece, bytes) else await piece.aopen(context))  # noqa: PERF401 - Retain partially opened attempts for cleanup.
            if not self._multipart and not isinstance(attempt := pieces[0], bytes):
                return attempt
            return AsyncMultipartAttempt(pieces)
        except BaseException as error:
            await self._bindings.cleanup(lambda: close_async_attempts(pieces), error=error)
            raise

    async def aclose(self) -> None:
        await self._bindings.aclose()


def bind_body(content: object, *, entry: BodyBindings | None = None) -> BodySource:
    """Bind once-encoded deferred content, transferring every entry claim to the resulting source."""
    bindings = BodyBindings() if entry is None else entry
    try:
        multipart = isinstance(content, MultipartSource)
        inputs = content.inputs() if isinstance(content, MultipartSource) else (content,)
        pieces = [piece if isinstance(piece, bytes) else bindings.bind(piece) for piece in inputs]
        return _BoundBody(pieces, bindings, multipart=multipart)
    except BaseException as error:
        if (failure := quiet_close(bindings.close)) is not None:
            body_secondary(error, failure)
        raise


async def bind_async_body(
    content: object, *, cleanup: AsyncBodyCleanup, entry: AsyncBodyBindings | None = None
) -> AsyncBodySource:
    """Bind async content once, retaining and releasing partial claims on interruption."""
    bindings = AsyncBodyBindings(cleanup) if entry is None else entry
    try:
        multipart = isinstance(content, MultipartSource)
        inputs = content.ainputs() if isinstance(content, MultipartSource) else (content,)
        pieces = [piece if isinstance(piece, bytes) else await bindings.bind(piece) for piece in inputs]
        return _AsyncBoundBody(pieces, bindings, multipart=multipart)
    except BaseException as error:
        await cleanup(bindings.aclose, error=error)
        raise
