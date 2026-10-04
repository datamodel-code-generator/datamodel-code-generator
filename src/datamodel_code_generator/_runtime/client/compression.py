"""Gzip declared request bodies unless the client disables compression.

A body encoded once is compressed once and replayed as those bytes; a body that builds its own attempts is compressed
as each attempt streams, so a one-shot body stays one-shot. The builtin encoder uses level 6, no file name, and a zero
modification time, so every attempt of a replayable body sends the same bytes.
"""

from __future__ import annotations

import zlib
from typing import TYPE_CHECKING, Final

from .bodies import EncodedAttempt
from .coding import CHUNK

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterable, Iterator

    from .bodies import AsyncBodyAttempt, BodyAttempt, BodyAttemptContext
    from .body_sources import AsyncBodySource, BodySource

_LEVEL: Final = 6
_WBITS: Final = 16 + zlib.MAX_WBITS


def _compressor() -> zlib._Compress:  # pyright: ignore[reportPrivateUsage]
    return zlib.compressobj(_LEVEL, zlib.DEFLATED, _WBITS)


def _gzipped(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """Compress chunks incrementally, feeding at most CHUNK bytes at a time."""
    compressor = _compressor()
    for chunk in chunks:
        for offset in range(0, len(chunk), CHUNK):
            if output := compressor.compress(chunk[offset : offset + CHUNK]):
                yield output
    yield compressor.flush()


def gzipped_attempt(attempt: EncodedAttempt, check: Callable[[], None]) -> EncodedAttempt:
    """Return the compressed attempt of a body encoded once, compressed once for every attempt.

    `check` runs before each step, so a cancelled or expired call stops compressing a large body.
    """
    content = attempt.content
    output: list[bytes] = []
    compressor = _compressor()
    for offset in range(0, len(content), CHUNK):
        check()
        output.append(compressor.compress(content[offset : offset + CHUNK]))
    output.append(compressor.flush())
    return EncodedAttempt(b"".join(output), attempt.content_type)


class _GzipAttempt:
    """A body attempt whose bytes are compressed as they stream; its length is unknown in advance."""

    __slots__ = ("_attempt", "content_type")

    def __init__(self, attempt: BodyAttempt) -> None:
        self._attempt = attempt
        self.content_type = attempt.content_type

    @property
    def content_length(self) -> None:
        """Return None: the compressed length is known only once sent."""

    def iter_bytes(self) -> Iterator[bytes]:
        """Yield the compressed bytes of the attempt."""
        return _gzipped(self._attempt.iter_bytes())

    def close(self) -> None:
        """Release the uncompressed attempt."""
        self._attempt.close()


class _AsyncGzipAttempt:
    """An asynchronous body attempt whose bytes are compressed as they stream."""

    __slots__ = ("_attempt", "content_type")

    def __init__(self, attempt: AsyncBodyAttempt) -> None:
        self._attempt = attempt
        self.content_type = attempt.content_type

    @property
    def content_length(self) -> None:
        """Return None: the compressed length is known only once sent."""

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        """Yield the compressed bytes of the attempt."""
        compressor = _compressor()
        async for chunk in self._attempt.aiter_bytes():
            for offset in range(0, len(chunk), CHUNK):
                if output := compressor.compress(chunk[offset : offset + CHUNK]):
                    yield output
        yield compressor.flush()

    async def aclose(self) -> None:
        """Release the uncompressed attempt."""
        await self._attempt.aclose()


class GzipSource:
    """A body source whose attempts are compressed; it replays exactly as the source it wraps."""

    __slots__ = ("_source",)

    def __init__(self, source: BodySource) -> None:
        """Wrap the call's own source."""
        self._source = source

    @property
    def replayable(self) -> bool:
        """Return whether the wrapped source replays."""
        return self._source.replayable

    def open(self, context: BodyAttemptContext) -> BodyAttempt:
        """Open an attempt of the wrapped source, compressed as it streams."""
        return _GzipAttempt(self._source.open(context))

    def close(self) -> None:
        """Release the wrapped source."""
        self._source.close()


class AsyncGzipSource:
    """An asynchronous body source whose attempts are compressed."""

    __slots__ = ("_source",)

    def __init__(self, source: AsyncBodySource) -> None:
        """Wrap the call's own source."""
        self._source = source

    @property
    def replayable(self) -> bool:
        """Return whether the wrapped source replays."""
        return self._source.replayable

    async def aopen(self, context: BodyAttemptContext) -> AsyncBodyAttempt:
        """Open an attempt of the wrapped source, compressed as it streams."""
        return _AsyncGzipAttempt(await self._source.aopen(context))

    async def aclose(self) -> None:
        """Release the wrapped source."""
        await self._source.aclose()
