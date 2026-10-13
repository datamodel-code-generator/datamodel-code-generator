"""Part plans: how one form-data member is sent or read, which the generator writes and both multipart codings take.

The module imports no HTTP or async library, so that the generator plans parts without the client's dependencies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from ..model_codecs.errors import ParameterEncodingError
from .media import most_specific, normalized, with_charset

if TYPE_CHECKING:
    from typing import Any, Protocol

    from ..model_codecs.parameters import ParameterPlan

    class PartCodec(Protocol):
        """The codec of a sent member's values, as the generated operation registry binds it."""

        def dump(self, value: Any) -> object:
            """Return the JSON value of a value."""
            ...

        @property
        def errors(self) -> tuple[type[Exception], ...]:
            """Return the failures of the codec's backend."""
            ...


__all__ = ("MEDIA_OUTSIDE_ENCODING", "PartKind", "PartPlan")

PartKind: TypeAlias = Literal["string", "integer", "number", "boolean", "json"]
MEDIA_OUTSIDE_ENCODING: Final = "A part's media type must fall within its member's encoding"


class PartPlan:
    """How one form-data member is sent or read: its kind, repeats, codec, media types, and style.

    A received part is read in its lexical kind, or as JSON. A sent value is encoded by the codec, when the member
    has one, and each item of a repeated member becomes its own part: JSON for a member of the JSON kind, whose type
    has no one scalar kind, and for any other by the value's shape. The media types of the member's encoding bound
    the media type a part names, and the first concrete one is its default. A member whose encoding gives a query
    style writes a part for each name and value that style gives its value.
    """

    __slots__ = ("codec", "content_types", "kind", "name", "repeated", "style")

    def __init__(  # noqa: PLR0913
        self,
        name: str,
        kind: PartKind = "string",
        *,
        repeated: bool = False,
        codec: PartCodec | None = None,
        content_types: tuple[str, ...] = (),
        style: ParameterPlan | None = None,
    ) -> None:
        """Keep the member's name, kind, repeats, codec, media, and style."""
        self.name = name
        self.kind: PartKind = kind
        self.repeated = repeated
        self.codec = codec
        self.content_types = content_types
        self.style = style

    def media(self, named: str | None) -> str | None:
        """Return the media type of a part: the one it names within the encoding's, or the encoding's default.

        A named media type is sent normalized, with the charset of the declared type it falls within when it names none.
        """
        if named is None:
            return next((media for media in self.content_types if "*" not in media.partition(";")[0]), None)
        if (wanted := normalized(named)) is None or (declared := most_specific(wanted, self.content_types)) is None:
            raise ParameterEncodingError(MEDIA_OUTSIDE_ENCODING)
        return with_charset(wanted, declared)
