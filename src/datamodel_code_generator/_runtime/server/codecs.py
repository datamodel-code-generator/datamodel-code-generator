"""Select the outbound codec bound to one declared response body by status code and media type."""

from __future__ import annotations

from typing import TYPE_CHECKING, Generic

from typing_extensions import Never, TypeVar

from ..model_codecs.errors import CodecSelectionError
from ..model_codecs.media import normalize_media_type

if TYPE_CHECKING:
    from collections.abc import Callable

CodecT_co = TypeVar("CodecT_co", covariant=True)

_JSON = "application/json"


class ResponseCodecs(Generic[CodecT_co]):
    """The outbound codecs of one operation's declared response bodies, selected like the response itself."""

    __slots__ = ("_bodies",)

    def __init__(self, bodies: tuple[tuple[str, tuple[tuple[str, Callable[[], CodecT_co]], ...]], ...]) -> None:
        """Keep each declared status key's media types and codec accessors in declaration order."""
        self._bodies = dict(bodies)

    def select(self, status_code: int, media_type: str | None) -> CodecT_co:
        """Return the codec of a status, exact before its class before default, and of its default media."""
        if type(status_code) is not int:
            msg = "status_code must be an integer"
            raise CodecSelectionError(msg)
        key = next(
            (key for key in (str(status_code), f"{str(status_code)[:1]}XX", "default") if key in self._bodies), None
        )
        media = () if key is None else self._bodies[key]
        if media_type is not None:
            try:
                wanted = normalize_media_type(media_type)
            except ValueError:
                wanted = media_type
            accessor = next((codec for declared, codec in media if declared == wanted), None)
        else:
            accessor = next(
                (codec for declared, codec in media if declared == _JSON),
                next((codec for declared, codec in media if declared.partition(";")[0].endswith("+json")), None),
            ) or next((codec for _, codec in media), None)
        if accessor is None:
            msg = f"The operation declares no codec-bearing body for status {status_code} and that media type"
            raise CodecSelectionError(msg)
        return accessor()

    def part(self, *, status_code: int, name: str, media_type: str | None = None) -> Never:  # noqa: PLR6301
        """Refuse part codecs: builtin responses have no multipart bodies with declared non-file parts."""
        msg = f"The {status_code} response declares no part {name!r} for {media_type or 'its default media'}"
        raise CodecSelectionError(msg)
