"""Per-call native inputs, and the parts of multipart bodies with streamed file parts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from .bodies import BinarySource, is_async_binary_input, is_binary_input, is_file_input
from .errors import DecodeError
from .logical import in_thread
from .multipart import FormParts, MultipartAttempt, PartFile, is_file_part, is_multipart

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

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
        """Close every file this call opened from a path in one thread call, and return the failures."""
        if any(source.owned for _, source in self._entries.values()):
            return await in_thread(self.close)
        return self.close()


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
    """A call's native input, or the parts of a multipart body with streamed file parts, and any coding.

    Each attempt of a multipart body is encoded under the boundary its media type names.
    """

    __slots__ = ("_aencode", "_bindings", "_encode", "_parts", "_pieces")

    def __init__(self, pieces: list[bytes | BinarySource], bindings: BodyBindings, parts: FormParts | None) -> None:
        self._pieces = pieces
        self._bindings = bindings
        self._parts = parts
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
        if (parts := self._parts) is None:
            attempt: SyncContent = cast("BinarySource", self._pieces[0]).open()
        else:
            attempt = MultipartAttempt(
                parts.entries,
                [piece if isinstance(piece, bytes) else PartFile(piece.open()) for piece in self._pieces],
                parts.media_type,
            )
        return self._encode(attempt) if self._encode is not None else attempt

    async def aopen(self) -> AsyncContent:
        """Prepare one asynchronous native stream."""
        if (parts := self._parts) is None:
            attempt: AsyncContent = await cast("BinarySource", self._pieces[0]).aopen()
        else:
            attempt = MultipartAttempt(
                parts.entries,
                [piece if isinstance(piece, bytes) else PartFile(await piece.aopen()) for piece in self._pieces],
                parts.media_type,
                threaded=any(not isinstance(piece, bytes) and piece.owned for piece in self._pieces),
            )
        return self._aencode(attempt) if self._aencode is not None else attempt

    def close(self) -> list[OSError]:
        """Close every file this call opened from a path and return the failures."""
        return self._bindings.close()

    async def aclose(self) -> list[OSError]:
        """Close every file this call opened from a path in one thread call, and return the failures."""
        return await self._bindings.aclose()


def bind_body(content: object, *, entry: BodyBindings | None = None, asynchronous: bool = False) -> BodySource:
    """Bind a native input, or each file input of a multipart body's parts, without buffering native streams."""
    bindings = BodyBindings() if entry is None else entry
    if not isinstance(content, FormParts):
        return BodySource([bindings.bind(content, asynchronous=asynchronous)], bindings, None)
    pieces: list[bytes | BinarySource] = [
        piece if type(piece) is bytes else bindings.bind(piece, asynchronous=asynchronous)
        for _, _, piece, _, _ in content.entries
    ]
    return BodySource(pieces, bindings, content)
