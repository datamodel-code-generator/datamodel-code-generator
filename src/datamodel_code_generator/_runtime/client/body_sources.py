"""Per-call native inputs and temporary multipart composition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from .bodies import BinarySource, is_async_binary_input, is_binary_input, is_file_input
from .errors import DecodeError
from .multipart import AsyncMultipartAttempt, MultipartAttempt, MultipartSource, is_file_part, is_multipart

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

    from .bodies import AsyncContent, EncodedAttempt, SyncContent


@dataclass(frozen=True, slots=True)
class RequestCoding:
    """A declared request content coding."""

    token: str
    attempt: Callable[[EncodedAttempt, Callable[[], None]], EncodedAttempt]
    source: Callable[[BodySource], BodySource]


def _inputs(body: object) -> Iterator[object]:
    if is_multipart(body):
        for part in body.parts:
            if is_file_part(part):
                yield part.content
    else:
        yield body


class BodyBindings:
    """Keep native file offsets captured before callbacks and encoding."""

    __slots__ = ("_entries",)

    def __init__(self) -> None:
        self._entries: dict[int, tuple[object, BinarySource]] = {}

    def bind(self, body: object, *, asynchronous: bool = False) -> BinarySource:
        """Reuse a captured input or retain a newly encoded input."""
        if (entry := self._entries.get(id(body))) is not None:
            return entry[1]
        if not is_binary_input(body) and not (asynchronous and is_async_binary_input(body)):
            raise DecodeError(
                reason="unencodable",
                direction="request",
                location=("body",),
                cause=TypeError("A binary body must be bytes, a path, a binary file or an iterable of bytes"),
            )
        source = BinarySource(body)
        self._entries[id(body)] = body, source
        return source

    def close(self) -> list[OSError]:
        """Close every file this call opened from a path and return the failures."""
        entries, self._entries = self._entries, {}
        return [failure for _, source in entries.values() if (failure := source.close()) is not None]

    async def aclose(self) -> list[OSError]:
        """Close every file this call opened from a path, each in a thread, and return the failures."""
        entries, self._entries = self._entries, {}
        return [failure for _, source in entries.values() if (failure := await source.aclose()) is not None]


def capture_body(body: object) -> BodyBindings | None:
    """Capture file offsets without consuming other request values."""
    bindings: BodyBindings | None = None
    for item in _inputs(body):
        if is_file_input(item):
            if bindings is None:
                bindings = BodyBindings()
            bindings.bind(item)
    return bindings


class BodySource:
    """A call's native inputs with optional multipart framing and compression."""

    __slots__ = ("_aencode", "_bindings", "_encode", "_multipart", "_pieces")

    def __init__(self, pieces: list[bytes | BinarySource], bindings: BodyBindings, *, multipart: bool) -> None:
        self._pieces = pieces
        self._bindings = bindings
        self._multipart = multipart
        self._encode: Callable[[SyncContent], SyncContent] | None = None
        self._aencode: Callable[[AsyncContent], AsyncContent] | None = None

    @property
    def replayable(self) -> bool:
        """Require every input to remain replayable."""
        return all(isinstance(piece, bytes) or piece.replayable for piece in self._pieces)

    def encoded(
        self, encode: Callable[[SyncContent], SyncContent], aencode: Callable[[AsyncContent], AsyncContent]
    ) -> BodySource:
        """Apply a declared streaming coding to each attempt."""
        self._encode, self._aencode = encode, aencode
        return self

    def open(self) -> SyncContent:
        """Prepare one synchronous native stream."""
        pieces: list[bytes | SyncContent] = [
            piece if isinstance(piece, bytes) else piece.open() for piece in self._pieces
        ]
        attempt = MultipartAttempt(pieces) if self._multipart else cast("SyncContent", pieces[0])
        return self._encode(attempt) if self._encode is not None else attempt

    async def aopen(self) -> AsyncContent:
        """Prepare one asynchronous native stream."""
        pieces: list[bytes | AsyncContent] = [
            piece if isinstance(piece, bytes) else await piece.aopen() for piece in self._pieces
        ]
        attempt = AsyncMultipartAttempt(pieces) if self._multipart else cast("AsyncContent", pieces[0])
        return self._aencode(attempt) if self._aencode is not None else attempt

    def close(self) -> list[OSError]:
        """Close every file this call opened from a path and return the failures."""
        return self._bindings.close()

    async def aclose(self) -> list[OSError]:
        """Close every file this call opened from a path, each in a thread, and return the failures."""
        return await self._bindings.aclose()


def bind_body(content: object, *, entry: BodyBindings | None = None, asynchronous: bool = False) -> BodySource:
    """Bind the encoded layout without buffering native streams."""
    bindings = BodyBindings() if entry is None else entry
    inputs: Iterable[object]
    if isinstance(content, MultipartSource):
        inputs = content.ainputs() if asynchronous else content.inputs()
    else:
        inputs = (content,)
    pieces = [
        piece if isinstance(piece, bytes) else bindings.bind(piece, asynchronous=asynchronous) for piece in inputs
    ]
    return BodySource(pieces, bindings, multipart=isinstance(content, MultipartSource))
