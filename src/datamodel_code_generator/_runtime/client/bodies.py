"""Request bodies of a generated client: the attempt a transport reads for each send."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


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


class EncodedAttempt:
    """A body encoded once for its call, sent as it is by every attempt of either client."""

    __slots__ = ("content", "content_type")

    def __init__(self, content: bytes, content_type: str) -> None:
        """Keep the encoded bytes and their media type."""
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
