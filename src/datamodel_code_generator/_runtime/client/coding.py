"""Response content codings: identity, gzip, and deflate, removed exactly once, incrementally, and within limits."""

from __future__ import annotations

import zlib
from typing import TYPE_CHECKING, Final

from .errors import DecompressionLimitError, ProtocolSizeError, UnsupportedContentCodingError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from ..protocols.errors import ProtocolDataError
    from .responses import ResponseInfo

CHUNK: Final = 64 * 1024
MAX_LAYERS: Final = 2
MAX_RATIO: Final = 100.0
_MIN_EXPANDED: Final = 1024 * 1024
_GZIP: Final = 16 + zlib.MAX_WBITS
_WBITS: Final = {"gzip": _GZIP, "x-gzip": _GZIP, "deflate": zlib.MAX_WBITS}


def codings(info: ResponseInfo) -> tuple[str, ...]:
    """Return the content codings a response applied, in the order applied, leaving out identity."""
    return tuple(
        coding
        for value in info.headers.get_all("content-encoding")
        for item in value.split(",")
        if (coding := item.strip().lower()) and coding != "identity"
    )


class _Layer:
    """Remove one coding, never producing more than CHUNK bytes at a time or expanding past the ratio."""

    __slots__ = ("_coding", "_decoded", "_decompressor", "_encoded", "_index", "_info", "_operation_id")

    def __init__(self, coding: str, index: int, info: ResponseInfo, operation_id: str | None) -> None:
        self._coding = coding
        self._index = index
        self._info = info
        self._operation_id = operation_id
        self._decompressor = zlib.decompressobj(_WBITS[coding])
        self._encoded = 0
        self._decoded = 0

    def feed(self, data: bytes) -> Iterator[bytes]:
        pending = data
        while True:
            output, pending = self._step(pending)
            if output:
                yield output
            if not pending and len(output) < CHUNK:
                return

    def _step(self, data: bytes) -> tuple[bytes, bytes]:
        if self._decompressor.eof and data:
            if self._coding == "deflate":
                raise self._malformed(None)
            self._decompressor = zlib.decompressobj(_GZIP)
        decompressor = self._decompressor
        try:
            output = decompressor.decompress(data, CHUNK)
        except zlib.error as error:
            raise self._malformed(error) from None
        rest = decompressor.unused_data if decompressor.eof else decompressor.unconsumed_tail
        self._encoded += len(data) - len(rest)
        self._decoded += len(output)
        if self._decoded > (limit := max(_MIN_EXPANDED, int(self._encoded * MAX_RATIO))):
            raise DecompressionLimitError(
                layer=self._index,
                encoded_bytes=self._encoded,
                max_ratio=MAX_RATIO,
                limit=limit,
                observed=self._decoded,
                operation_id=self._operation_id,
                call_id=self._info.call_id,
                info=self._info,
            )
        return output, rest

    def end(self) -> None:
        if not self._decompressor.eof:
            raise self._malformed(None)

    def _malformed(self, cause: BaseException | None) -> ProtocolDataError:
        from ..protocols.errors import ProtocolDataError  # ruff: ignore[import-outside-top-level] - Load protocol errors only on this failure.

        return ProtocolDataError(
            condition="malformed",
            operation_id=self._operation_id,
            call_id=self._info.call_id,
            info=self._info,
            cause=cause,
        )


class ContentDecoder:
    """Decode a body through each coding its response names, last applied first.

    The codings are checked at the first body chunk, so a response without a body never fails on them. Chunks are
    never empty.
    """

    __slots__ = ("_applied", "_info", "_operation_id")

    def __init__(self, info: ResponseInfo, operation_id: str | None) -> None:
        """Read the codings of a response."""
        self._info = info
        self._operation_id = operation_id
        self._applied = codings(info)

    @property
    def identity(self) -> bool:
        """Return whether the response applied no content coding, so its body passes through untouched."""
        return not self._applied

    def decoded(self, chunks: Iterator[bytes]) -> Iterator[bytes]:
        """Return the decoded chunks of a content-coded body; an identity body passes through untouched."""
        return self._decoded(chunks) if self._applied else chunks

    def adecoded(self, chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        """Return the decoded chunks of a content-coded async body; an identity body passes through untouched."""
        return self._adecoded(chunks) if self._applied else chunks

    def _decoded(self, chunks: Iterator[bytes]) -> Iterator[bytes]:
        layers: tuple[_Layer, ...] = ()
        for chunk in chunks:
            layers = layers or self._layers()
            yield from _through(layers, 0, chunk)
        for layer in layers:
            layer.end()

    async def _adecoded(self, chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
        layers: tuple[_Layer, ...] = ()
        async for chunk in chunks:
            layers = layers or self._layers()
            for piece in _through(layers, 0, chunk):
                yield piece
        for layer in layers:
            layer.end()

    def _layers(self) -> tuple[_Layer, ...]:
        applied, info, operation_id = self._applied, self._info, self._operation_id
        if len(applied) > MAX_LAYERS:
            raise ProtocolSizeError(
                kind="content_layers",
                limit=MAX_LAYERS,
                observed=len(applied),
                unit="items",
                operation_id=operation_id,
                call_id=info.call_id,
                info=info,
            )
        if (unknown := next((coding for coding in applied if coding not in _WBITS), None)) is not None:
            raise UnsupportedContentCodingError(
                coding=unknown, operation_id=operation_id, call_id=info.call_id, info=info
            )
        return tuple(_Layer(coding, index, info, operation_id) for index, coding in enumerate(reversed(applied)))


def _through(layers: tuple[_Layer, ...], index: int, chunk: bytes) -> Iterator[bytes]:
    if index == len(layers):
        yield chunk
        return
    for piece in layers[index].feed(chunk):
        yield from _through(layers, index + 1, piece)
