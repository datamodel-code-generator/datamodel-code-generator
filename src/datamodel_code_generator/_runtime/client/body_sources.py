"""Per-call native inputs of the binary bodies a package declares, bound once and opened for each attempt."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast

from .bodies import BinarySource, is_async_binary_input, is_binary_input, is_file_input
from .errors import DecodeError
from .logical import in_thread

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from .content import AsyncContent, BodyEntry, SyncContent


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


class BoundBody:
    """A call's native input, or the inputs of a body built of several, and any coding."""

    __slots__ = ("_aencode", "_bindings", "_encode", "_pieces")

    def __init__(self, pieces: list[bytes | BinarySource], bindings: BodyBindings) -> None:
        self._pieces = pieces
        self._bindings = bindings
        self._encode: Callable[[SyncContent], SyncContent] | None = None
        self._aencode: Callable[[AsyncContent], AsyncContent] | None = None

    @property
    def replayable(self) -> bool:
        """Require every input to remain replayable."""
        return all(isinstance(piece, bytes) or piece.replayable for piece in self._pieces)

    def encoded(
        self, encode: Callable[[SyncContent], SyncContent], aencode: Callable[[AsyncContent], AsyncContent]
    ) -> BoundBody:
        """Apply a declared streaming coding to each attempt."""
        self._encode, self._aencode = encode, aencode
        return self

    def attempt(self) -> SyncContent:
        """Open the input for one synchronous attempt."""
        return cast("BinarySource", self._pieces[0]).open()

    async def aattempt(self) -> AsyncContent:
        """Open the input for one asynchronous attempt."""
        return await cast("BinarySource", self._pieces[0]).aopen()

    def open(self) -> SyncContent:
        """Prepare one synchronous native stream."""
        attempt = self.attempt()
        return self._encode(attempt) if self._encode is not None else attempt

    async def aopen(self) -> AsyncContent:
        """Prepare one asynchronous native stream."""
        attempt = await self.aattempt()
        return self._aencode(attempt) if self._aencode is not None else attempt

    def close(self) -> list[OSError]:
        """Close every file this call opened from a path and return the failures."""
        return self._bindings.close()

    async def aclose(self) -> list[OSError]:
        """Close every file this call opened from a path in one thread call, and return the failures."""
        return await self._bindings.aclose()


class BinaryBodies:
    """Bind the binary bodies of a package: bytes, a binary file, a path, or an iterable of bytes."""

    __slots__ = ()

    @staticmethod
    def inputs(body: object) -> Iterable[object]:
        """Return the native inputs of a body whose file offsets the call captures at entry."""
        return (body,)

    def capture(self, body: object) -> BodyBindings | None:
        """Capture file offsets without consuming other request values."""
        bindings: BodyBindings | None = None
        for item in self.inputs(body):
            if is_file_input(item):
                if bindings is None:
                    bindings = BodyBindings()
                bindings.bind(item)
        return bindings

    @staticmethod
    def bind(content: object, entry: BodyEntry | None, *, asynchronous: bool) -> BoundBody:
        """Bind a native input without buffering native streams."""
        bindings = BodyBindings() if entry is None else cast("BodyBindings", entry)
        return BoundBody([bindings.bind(content, asynchronous=asynchronous)], bindings)

    @staticmethod
    def raw(body: object) -> tuple[object, str | None]:
        """Return a raw call's binary body as it is sent, without a media type of its own."""
        return body, None


BINARY_BODIES: Final = BinaryBodies()
