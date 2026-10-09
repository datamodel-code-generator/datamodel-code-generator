"""Multipart bodies: form-data field and file parts that HTTPX2 encodes as `files=`.

A field part carries a value, a file part a binary body that is read synchronously: bytes, a synchronous file
object, a path, or an iterable of bytes. HTTPX2 encodes a multipart body and reads its files synchronously, so an async
file or async iterable is refused before anything is sent, rather than buffered, in either mode. A body whose parts
are all bytes is encoded once; one with streamed file parts is encoded at each attempt under the boundary its media
type names, each file part read from the position it had when the call began, so it can be sent again when all of its
file parts can.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from io import UnsupportedOperation
from os import SEEK_END, SEEK_SET, urandom
from typing import TYPE_CHECKING, Final, Generic, Literal, NoReturn, TypeAlias, TypeVar, cast

from typing_extensions import TypeAliasType, TypeIs

from ..model_codecs.errors import CodecError, ParameterEncodingError
from ..model_codecs.media import json_bytes as _json_bytes
from ..model_codecs.media import media_kind
from ..model_codecs.parameters import ParameterPlan, part_pairs
from ..model_codecs.unset import UNSET
from .bodies import AsyncBinaryBody, SyncBinaryBody, is_binary_input, next_chunk
from .errors import DecodeError
from .logical import in_thread
from .media import encode_text, most_specific, normalized, with_charset

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Iterator
    from typing import Any, Protocol

    from ..model_codecs.media import JSONValue
    from .bodies import BinaryContent

    class PartCodec(Protocol):
        """The codec of a sent member's values, as the generated operation registry binds it."""

        def dump(self, value: Any) -> object:
            """Return the JSON value of a value."""
            ...

        @property
        def errors(self) -> tuple[type[Exception], ...]:
            """Return the failures of the codec's backend."""
            ...


PartT = TypeVar("PartT")
PartT_co = TypeVar("PartT_co", covariant=True)
PartKind: TypeAlias = Literal["string", "integer", "number", "boolean", "json"]
ContentT_co = TypeVar("ContentT_co", bound="SyncBinaryBody | AsyncBinaryBody", covariant=True)
Entry: TypeAlias = tuple[str, str | None, object, str | None, tuple[tuple[str, str], ...]]

_MULTIPART: Final = "A multipart body must be a MultipartBody or an AsyncMultipartBody"
_PART: Final = "A multipart part must be a FieldPart or a FilePart named by a string"
_SYNC: Final = (
    "A multipart file part must be read synchronously: bytes, a binary file object, a path, or an iterable of bytes"
)
_MEDIA: Final = "A part's media type must fall within its member's encoding"
_TEXT: Final = "A text part carries a scalar"
_DISPOSITION: Final = "A part's Content-Disposition comes from its name and filename, so its headers may not name it"
_CLAIMED: Final = "Two form-data members write parts of the same name"
_OCTETS: Final = "application/octet-stream"
_FORM_DATA: Final = "multipart/form-data"


class FieldPart(Generic[PartT_co]):
    """A form-data part that is not a file: a named value, sent with the media type and headers it names.

    A string is sent as it is, another scalar as JSON text, and an object or array as JSON; UNSET leaves it out.
    """

    __slots__ = ("_content_type", "_headers", "_name", "_value")

    def __init__(
        self,
        name: str,
        value: PartT_co | UNSET,
        content_type: str | None = None,
        headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        """Keep the part's name, value, media type, and extra headers in their order."""
        self._name = name
        self._value: PartT_co | UNSET = value
        self._content_type = content_type
        self._headers = headers

    @property
    def name(self) -> str:
        """Return the part's name."""
        return self._name

    @property
    def value(self) -> PartT_co | UNSET:
        """Return the part's value, or UNSET for a part left out."""
        return self._value

    @property
    def content_type(self) -> str | None:
        """Return the part's media type, or None for the one its value implies."""
        return self._content_type

    @property
    def headers(self) -> tuple[tuple[str, str], ...]:
        """Return the part's extra headers in their order."""
        return self._headers


class FilePart(Generic[ContentT_co]):
    """A form-data file part: a named binary body, sent with the filename, media type, and headers it names."""

    __slots__ = ("_content", "_content_type", "_filename", "_headers", "_name")

    def __init__(
        self,
        name: str,
        content: ContentT_co,
        filename: str | None = None,
        content_type: str | None = None,
        headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        """Keep the part's name, body, filename, media type, and extra headers in their order."""
        self._name = name
        self._content = content
        self._filename = filename
        self._content_type = content_type
        self._headers = headers

    @property
    def name(self) -> str:
        """Return the part's name."""
        return self._name

    @property
    def content(self) -> ContentT_co:
        """Return the part's binary body."""
        return self._content

    @property
    def filename(self) -> str | None:
        """Return the part's filename, or None to send none."""
        return self._filename

    @property
    def content_type(self) -> str | None:
        """Return the part's media type, or None for application/octet-stream."""
        return self._content_type

    @property
    def headers(self) -> tuple[tuple[str, str], ...]:
        """Return the part's extra headers in their order."""
        return self._headers


class MultipartBody(Generic[PartT]):
    """The ordered parts of a multipart/form-data body a synchronous client sends."""

    __slots__ = ("_parts",)

    def __init__(self, parts: tuple[FilePart[SyncBinaryBody] | FieldPart[PartT], ...]) -> None:
        """Keep the parts in their order."""
        self._parts = parts

    @property
    def parts(self) -> tuple[FilePart[SyncBinaryBody] | FieldPart[PartT], ...]:
        """Return the parts in their order."""
        return self._parts


class AsyncMultipartBody(Generic[PartT]):
    """The ordered parts of a multipart/form-data body an asyncio client sends."""

    __slots__ = ("_parts",)

    def __init__(self, parts: tuple[FilePart[AsyncBinaryBody] | FieldPart[PartT], ...]) -> None:
        """Keep the parts in their order."""
        self._parts = parts

    @property
    def parts(self) -> tuple[FilePart[AsyncBinaryBody] | FieldPart[PartT], ...]:
        """Return the parts in their order."""
        return self._parts


if TYPE_CHECKING:
    BodyInput: TypeAlias = SyncBinaryBody | MultipartBody[PartT]
    AsyncBodyInput: TypeAlias = AsyncBinaryBody | AsyncMultipartBody[PartT]
else:
    BodyInput = TypeAliasType("BodyInput", "SyncBinaryBody | MultipartBody[PartT]", type_params=(PartT,))
    AsyncBodyInput = TypeAliasType(
        "AsyncBodyInput",
        "AsyncBinaryBody | AsyncMultipartBody[PartT]",
        type_params=(PartT,),
    )


class PartPlan:
    """How one form-data member is sent or read: its kind, repeats, codec, media types, and style.

    A received part is read in its lexical kind, or as JSON. A sent value is encoded by the codec, when the member
    has one, and each item of a repeated member becomes its own part. The media types of the member's encoding bound
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
            raise ParameterEncodingError(_MEDIA)
        return with_charset(wanted, declared)


_ANY: Final = PartPlan("")


@dataclass(frozen=True, slots=True)
class FormParts:
    """The parts of a form-data body with streamed file parts, as `files=` takes them, each file input as given.

    The call binds each file input and opens it for each attempt, which HTTPX2 encodes under the boundary the media
    type names.
    """

    entries: tuple[Entry, ...]
    media_type: str


def _malformed(name: object, cause: BaseException) -> DecodeError:
    location = ("body",) if name is None else ("body", name if isinstance(name, str) else "?")
    return DecodeError(reason="unencodable", direction="request", location=location, cause=cause)


def multipart_member(value: JSONValue, media_type: str | None = None) -> tuple[bytes, str | None]:
    """Return a part's bytes and media type: a string as it is, another scalar as JSON text, the rest as JSON.

    A JSON media type writes any value as JSON, and a text one, the only other an encoding gives, a scalar as text in
    its charset.
    """
    if media_type is None:
        match value:
            case str():
                return value.encode(), None
            case Mapping() | list():
                return _json_bytes(value), "application/json"
            case _:
                return _json_bytes(value), None
    match media_kind(media_type), value:
        case "json", _:
            return _json_bytes(value), media_type
        case _, Mapping() | list():
            raise ParameterEncodingError(_TEXT)
        case _, str():
            return encode_text(value, media_type), media_type
        case _:
            return encode_text(_json_bytes(value).decode(), media_type), media_type


def _entry(
    name: str,
    content: object,
    media_type: str | None,
    headers: tuple[tuple[str, str], ...] = (),
    filename: str | None = None,
) -> Entry:
    return name, filename, content, media_type, headers


def _part_headers(media_type: str | None, headers: tuple[tuple[str, str], ...]) -> dict[str, str]:
    """Return a part's headers as HTTPX2 writes them: its media type first, then each name once, repeats comma-joined.

    A Content-Type header the part names replaces its media type.
    """
    written: dict[str, str] = {} if media_type is None else {"Content-Type": media_type}
    names: dict[str, str] = {}
    for key, value in headers:
        if (name := names.get(folded := key.lower())) is not None:
            written[name] = f"{written[name]}, {value}"
            continue
        if folded == "content-type":
            written.pop("Content-Type", None)
        names[folded] = key
        written[key] = value
    return written


def native_files(entries: Iterable[Entry], contents: Iterable[object] | None = None) -> Any:
    """Return the parts as HTTPX2's `files=` takes them, with the given contents in place of the parts' own."""
    if contents is None:
        contents = (content for _, _, content, _, _ in entries)
    return [
        (name, (filename, content, media_type, _part_headers(media_type, headers)))
        for (name, filename, _, media_type, headers), content in zip(entries, contents, strict=True)
    ]


def _encoded(entries: list[Entry], media_type: str | None) -> tuple[bytes | FormParts, str | None]:
    """Return a body's bytes, or else its streamed parts, and its media type with a new boundary.

    The media type is the one the call sends, multipart/form-data without one, its other parameters kept. A body
    without parts has no media type, as HTTPX2 sends none. A part header naming Content-Disposition is refused, as
    HTTPX2 writes it itself, and HTTPX2 checks every part's name and headers before any file input is opened.
    """
    import httpx2  # noqa: PLC0415 - The generator imports this module's plans without HTTPX2.

    if any(key.lower() == "content-disposition" for _, _, _, _, headers in entries for key, _ in headers):
        raise _malformed(None, ValueError(_DISPOSITION))
    try:
        if not entries:
            return b"", None
        sent = f"{media_type or _FORM_DATA}; boundary={urandom(16).hex()}"
        if all(type(content) is bytes for _, _, content, _, _ in entries):
            return httpx2.Request("POST", "/", headers={"Content-Type": sent}, files=native_files(entries)).read(), sent
        httpx2.Request("POST", "/", files=native_files(entries, (b"" for _ in entries)))
    except (TypeError, ValueError) as error:
        raise _malformed(None, error) from None
    return FormParts(tuple(entries), sent), sent


def encode_multipart(
    value: JSONValue,
    content_types: Mapping[str, str] | None = None,
    styled: Mapping[str, ParameterPlan] | None = None,
    sent: str | None = None,
) -> tuple[bytes | FormParts, str | None]:
    """Encode an object as ordered form-data parts, repeating a part for each array member; an empty one writes none.

    The body is sent in the media type `sent` names. A member with an encoding's media type is written in it. A member
    in `styled` writes a part for each name and value its query style gives, without percent-encoding, and no two
    members may then write parts of the same name.
    """
    if not isinstance(value, Mapping):
        msg = "A multipart form value must be an object"
        raise ParameterEncodingError(msg)
    entries: list[Entry] = []
    owners: dict[str, str] = {}
    for name, item in value.items():
        if styled and (style := styled.get(name)) is not None:
            pairs = part_pairs(style, item)
            if any(owners.setdefault(key, name) != name for key, _ in pairs):
                raise ParameterEncodingError(_CLAIMED)
            entries.extend(_entry(key, text.encode(), None) for key, text in pairs)
            continue
        if styled and owners.setdefault(name, name) != name:
            raise ParameterEncodingError(_CLAIMED)
        declared = None if content_types is None else content_types.get(name)
        for member in item if isinstance(item, list) else (item,):
            content, media_type = multipart_member(member, declared)
            entries.append(_entry(name, content, media_type))
    return _encoded(entries, sent)


def _refused(part: object) -> NoReturn:
    raise _malformed(getattr(part, "name", "?"), TypeError(_PART))


def _errors(plan: PartPlan) -> tuple[type[Exception], ...]:
    return (CodecError, TypeError, AttributeError, *(() if plan.codec is None else plan.codec.errors))


def _field(part: FieldPart[object], plan: PartPlan) -> list[Entry]:
    """Return a field part's entries, one for each item of a repeated member and none for UNSET.

    A styled member writes a part for each name and value its style gives, without percent-encoding, in the charset of
    the media type its part names, UTF-8 without one.
    """
    if (value := part.value) is UNSET:
        return []
    try:
        wire = cast("JSONValue", value if (codec := plan.codec) is None else codec.dump(value))
        if plan.style is not None:
            media_type = part.content_type
            return [
                _entry(
                    key,
                    text.encode() if media_type is None else encode_text(text, media_type),
                    media_type,
                    part.headers,
                )
                for key, text in part_pairs(plan.style, wire)
            ]
        items = wire if plan.repeated and isinstance(wire, list) else (wire,)
        return [_member(part, item, plan) for item in items]
    except _errors(plan) as error:
        raise _malformed(part.name, error) from None


def _member(part: FieldPart[object], item: JSONValue, plan: PartPlan) -> Entry:
    if plan.content_types:
        content, media_type = multipart_member(item, plan.media(part.content_type))
    else:
        content, shaped = multipart_member(item)
        media_type = part.content_type or shaped
    return _entry(part.name, content, media_type, part.headers)


def _file(part: FilePart[SyncBinaryBody | AsyncBinaryBody], plan: PartPlan) -> Entry:
    if not is_binary_input(part.content):
        raise _malformed(part.name, TypeError(_SYNC))
    try:
        media_type = plan.media(part.content_type) if plan.content_types else part.content_type
        if media_type is None and plan.content_types:
            raise ParameterEncodingError(_MEDIA)
    except (CodecError, TypeError, AttributeError) as error:
        raise _malformed(part.name, error) from None
    return _entry(part.name, part.content, media_type or _OCTETS, part.headers, part.filename)


def _named(part: object) -> bool:
    return isinstance(getattr(part, "name", None), str) and isinstance(part, FieldPart | FilePart)


def is_file_part(value: object) -> TypeIs[FilePart[SyncBinaryBody | AsyncBinaryBody]]:
    return isinstance(value, FilePart)


def is_multipart(value: object) -> TypeIs[MultipartBody[object] | AsyncMultipartBody[object]]:
    """Return whether a body is a multipart body of either mode."""
    return isinstance(value, (MultipartBody, AsyncMultipartBody))


def encode_parts(
    body: object,
    plans: tuple[PartPlan, ...] | None = None,
    additional: PartPlan | None = None,
    sent: str | None = None,
) -> tuple[bytes | FormParts, str | None]:
    """Encode the parts a call gives in their order, each by the plan of its member when the schema has them.

    A file part without a media type is sent as application/octet-stream, and the body in the media type `sent` names.
    """
    if not is_multipart(body):
        raise _malformed(None, TypeError(_MULTIPART))
    declared = {} if plans is None else {plan.name: plan for plan in plans}
    entries: list[Entry] = []
    for part in body.parts:
        if not _named(part):
            _refused(part)
        plan = declared.get(part.name, additional) or _ANY
        if isinstance(part, FieldPart):
            entries.extend(_field(part, plan))
        else:
            entries.append(_file(part, plan))
    return _encoded(entries, sent)


class PartFile:
    """A file part's opened input as HTTPX2 reads a file: from the position the call captured, measured when it can be.

    HTTPX2 rewinds a file part before reading it; an input of unknown length, an iterable or a stream, cannot seek.
    """

    __slots__ = ("_chunks", "_content")

    def __init__(self, content: BinaryContent) -> None:
        self._content = content
        self._chunks: Iterator[bytes] | None = None

    def read(self, _size: int = -1) -> bytes:
        """Return the input's next chunk that has bytes, or no bytes at its end, where HTTPX2 stops reading."""
        if self._chunks is None:
            self._chunks = self._content.iter_bytes()
        return next((chunk for chunk in self._chunks if chunk), b"")

    def seek(self, _offset: int, whence: int = SEEK_SET) -> int:
        """Rewind to the captured position, or return the measured length from the end, as HTTPX2 measures a file."""
        if (length := self._content.content_length) is None:
            raise UnsupportedOperation
        self._chunks = None
        return length if whence == SEEK_END else 0

    def tell(self) -> int:
        """Return the captured position, which is the start of the part."""
        return self.seek(0)


class MultipartAttempt:
    """One attempt of a body with streamed file parts, encoded by HTTPX2 under the boundary its media type names."""

    __slots__ = ("_stream", "_threaded", "content_length", "content_type")

    def __init__(
        self, entries: tuple[Entry, ...], contents: list[object], content_type: str, *, threaded: bool = False
    ) -> None:
        """Encode the parts with the attempt's contents, under the boundary of the body's media type.

        A threaded attempt, one with a file the call opened from a path, is read in a thread in async mode.
        """
        import httpx2  # noqa: PLC0415 - The generator imports this module's plans without HTTPX2.

        self._threaded = threaded
        native = httpx2.Request(
            "POST",
            "/",
            headers={"Content-Type": content_type},
            files=native_files(entries, contents),
        )
        self._stream = native.stream
        self.content_type: str | None = content_type
        length = native.headers.get("Content-Length")
        self.content_length = None if length is None else int(length)

    def iter_bytes(self) -> Iterator[bytes]:
        """Yield the encoded parts, each file part as HTTPX2 reads it."""
        return iter(cast("Iterable[bytes]", self._stream))

    def aiter_bytes(self) -> AsyncIterator[bytes]:
        """Yield the encoded parts in async mode, one chunk at a time in a thread when the call opened a file."""
        if self._threaded:
            return _in_thread(self.iter_bytes())
        return aiter(cast("AsyncIterator[bytes]", self._stream))


async def _in_thread(chunks: Iterator[bytes]) -> AsyncIterator[bytes]:
    while (chunk := await in_thread(next_chunk, chunks)) is not None:
        yield chunk
