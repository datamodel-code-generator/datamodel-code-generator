"""Media selectors: the concrete request or response media type an operation's request codecs chose for a declared one.

The type parameters are the body and result types of the declared media type, which the generated select methods fix;
no public constructor takes them, so a selector only comes from its operation.
"""

from __future__ import annotations

from typing import Final, Generic, TypeVar

from .errors import CodecSelectionError

SyncOutT = TypeVar("SyncOutT")
AsyncOutT = TypeVar("AsyncOutT")
ResultT = TypeVar("ResultT")
SelectorT = TypeVar("SelectorT", bound="MediaSelector")
_TOKEN: Final = object()


class MediaSelector:
    """A media type an operation's request codecs selected: a declared type, and the concrete type it stands for.

    Only the request codecs of an operation make one, and only calls of that operation take it.
    """

    __slots__ = ("_concrete", "_declared", "_owner")

    def __init__(self, declared_media: str, concrete_media: str, owner: object, token: object) -> None:
        """Keep the declared and concrete media types and the request codecs that selected them."""
        if token is not _TOKEN:
            msg = f"A {type(self).__name__} is made by the select methods of an operation's request codecs"
            raise TypeError(msg)
        self._declared = declared_media
        self._concrete = concrete_media
        self._owner = owner

    @property
    def declared_media(self) -> str:
        """Return the declared media type, which may be a range such as image/*."""
        return self._declared

    @property
    def concrete_media(self) -> str:
        """Return the concrete media type the call sends or accepts."""
        return self._concrete

    def __repr__(self) -> str:
        """Name the declared and concrete media types."""
        return f"{type(self).__name__}(declared_media={self._declared!r}, concrete_media={self._concrete!r})"

    @classmethod
    def chosen(cls, media: MediaSelector, owner: object) -> tuple[str, str]:
        """Return the declared and concrete media types of a selector of this kind that an operation's codecs made."""
        if type(media) is not cls or media._owner is not owner:
            msg = f"The {type(media).__name__} was selected for another operation or direction"
            raise CodecSelectionError(msg)
        return media._declared, media._concrete


class RequestMedia(MediaSelector, Generic[SyncOutT, AsyncOutT]):
    """A request media type selected for one operation, with the body types of the sync and asyncio clients."""

    __slots__ = ()


class ResponseMedia(MediaSelector, Generic[ResultT]):
    """A response media type selected for one operation, with the values its successes return."""

    __slots__ = ()


def select_media(kind: type[SelectorT], declared_media: str, concrete_media: str, owner: object) -> SelectorT:
    """Return a selector of a declared media type for a concrete one, bound to the request codecs that chose it."""
    return kind(declared_media, concrete_media, owner, _TOKEN)
