"""Multipart/form-data request bodies: ordered field and file parts, sent with the boundary of their call.

A field part carries a value, a file part any binary body of the client's mode. Each send encodes the fields and
begins each file part's own attempt, so a multipart body can be sent again when all of its file parts can.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Final, Generic, NoReturn, TypeAlias, TypeVar

from typing_extensions import TypeAliasType, TypeIs

from ..model_codecs.errors import CodecError, ParameterEncodingError
from ..model_codecs.media import encode_json, normalize_media_type
from ..model_codecs.unset import Unset
from ..model_codecs.wire import checked_wire
from .bodies import AsyncBodyFactory, AsyncFileBody, AsyncStreamBody, BodyFactory, FileBody, StreamBody
from .errors import RequestEncodingError, add_secondary

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator

    from ..model_codecs.wire import WireValue
    from .bodies import AsyncBinaryBody, AsyncBodyAttempt, BodyAttempt, BodyAttemptContext, SyncBinaryBody

PartT = TypeVar("PartT")
InputT = TypeVar("InputT")
PartT_co = TypeVar("PartT_co", covariant=True)
ContentT_co = TypeVar("ContentT_co", bound="SyncBinaryBody | AsyncBinaryBody", covariant=True)
_MULTIPART: Final = "A multipart body must be of the client's mode"
_TOKEN: Final = re.compile(r"[!#$%&'*+.^_`|~0-9a-zA-Z-]+")
_BREAK: Final = re.compile(r"[\r\n\x00]")
_PART_HEADERS: Final = frozenset({"content-type", "content-disposition"})
_PART: Final = "A multipart part must be a FieldPart, or a FilePart of a binary body of the client's mode"
_SYNC: Final[tuple[type[SyncBinaryBody], ...]] = (bytes, FileBody, StreamBody, BodyFactory)
_ASYNC: Final[tuple[type[AsyncBinaryBody], ...]] = (bytes, AsyncFileBody, AsyncStreamBody, AsyncBodyFactory)


class FieldPart(Generic[PartT_co]):
    """A form-data part that is not a file: a named value, sent with the media type and headers it names.

    A string is sent as it is, another scalar as JSON text, and an object or array as JSON; UNSET leaves it out.
    """

    __slots__ = ("_content_type", "_headers", "_name", "_value")

    def __init__(
        self,
        name: str,
        value: PartT_co | Unset,
        content_type: str | None = None,
        headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        """Keep the part's name, value, media type, and extra headers in their order."""
        self._name = name
        self._value = value
        self._content_type = content_type
        self._headers = headers

    @property
    def name(self) -> str:
        """Return the part's name."""
        return self._name

    @property
    def value(self) -> PartT_co | Unset:
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
    BodyInput: TypeAlias = bytes | FileBody | StreamBody | BodyFactory | MultipartBody[PartT]
    AsyncBodyInput: TypeAlias = bytes | AsyncFileBody | AsyncStreamBody | AsyncBodyFactory | AsyncMultipartBody[PartT]
else:
    BodyInput = TypeAliasType(
        "BodyInput", "bytes | FileBody | StreamBody | BodyFactory | MultipartBody[PartT]", type_params=(PartT,)
    )
    AsyncBodyInput = TypeAliasType(
        "AsyncBodyInput",
        "bytes | AsyncFileBody | AsyncStreamBody | AsyncBodyFactory | AsyncMultipartBody[PartT]",
        type_params=(PartT,),
    )


def _malformed(name: object, cause: BaseException) -> RequestEncodingError:
    return RequestEncodingError(location=("body", name if isinstance(name, str) else "?"), cause=cause)


def multipart_head(
    boundary: str, name: str, filename: str | None, content_type: str | None, headers: tuple[tuple[str, str], ...]
) -> bytes:
    """Return the delimiter and headers that open one form-data part, as HTML forms quote its name and filename.

    Content-Type and Content-Disposition come from the part itself, so its own headers may not repeat them.
    """
    disposition = f'form-data; name="{_quoted(name)}"'
    if filename is not None:
        disposition += f'; filename="{_quoted(filename)}"'
    lines = [f"--{boundary}", f"Content-Disposition: {disposition}"]
    if content_type is not None:
        try:
            normalize_media_type(content_type)
        except ValueError:
            msg = "A part media type must be a media type"
            raise ParameterEncodingError(msg) from None
        lines.append(f"Content-Type: {content_type}")
    for key, value in headers:
        if not _TOKEN.fullmatch(key) or key.lower() in _PART_HEADERS or _BREAK.search(value):
            msg = "A part header must be a token other than Content-Type and Content-Disposition, on one line"
            raise ParameterEncodingError(msg)
        lines.append(f"{key}: {value}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


def _quoted(text: str) -> str:
    return text.replace('"', "%22").replace("\r", "%0D").replace("\n", "%0A")


def multipart_member(value: WireValue) -> tuple[bytes, str | None]:
    """Return a part's bytes and media type: a string as it is, another scalar as JSON text, the rest as JSON."""
    match value:
        case str():
            return value.encode(), None
        case Mapping() | tuple():
            return encode_json(value), "application/json"
        case _:
            pass
    return encode_json(value), None


def encode_multipart(value: WireValue, boundary: str) -> bytes:
    """Serialize an object as ordered form-data parts, repeating a part for each array member."""
    if not isinstance(value, Mapping):
        msg = "A multipart form value must be an object"
        raise ParameterEncodingError(msg)
    parts: list[bytes] = []
    for name, item in value.items():
        if item == ():
            msg = "An empty array cannot be represented by repeated parts"
            raise ParameterEncodingError(msg)
        for member in item if isinstance(item, tuple) else (item,):
            content, media_type = multipart_member(member)
            parts.extend((multipart_head(boundary, name, None, media_type, ()), content, b"\r\n"))
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts)


def new_boundary() -> str:
    """Return a new random boundary, one for each call."""
    return f"dcg{os.urandom(16).hex()}"


def _refused(part: object) -> NoReturn:
    raise _malformed(getattr(part, "name", None), TypeError(_PART))


def _field(part: FieldPart[object], boundary: str) -> bytes | None:
    """Return a field part's bytes with its head and tail, or None when its value is UNSET."""
    if isinstance(value := part.value, Unset):
        return None
    try:
        content, media_type = multipart_member(checked_wire(value))
        head = multipart_head(boundary, part.name, None, part.content_type or media_type, part.headers)
    except (CodecError, TypeError, AttributeError) as error:
        raise _malformed(part.name, error) from None
    return head + content + b"\r\n"


def _file_head(part: FilePart[SyncBinaryBody | AsyncBinaryBody], boundary: str) -> bytes:
    try:
        return multipart_head(
            boundary, part.name, part.filename, part.content_type or "application/octet-stream", part.headers
        )
    except (CodecError, TypeError, AttributeError) as error:
        raise _malformed(part.name, error) from None


class _MultipartAttempt:
    """One attempt of a multipart body: the encoded heads and fields, and each file part's own attempt."""

    __slots__ = ("_length", "_pieces")

    def __init__(self, pieces: list[bytes | BodyAttempt]) -> None:
        self._pieces = pieces
        lengths = [len(piece) if isinstance(piece, bytes) else piece.content_length for piece in pieces]
        self._length = None if None in lengths else sum(length for length in lengths if length is not None)

    @property
    def content_length(self) -> int | None:
        """Return the body's length when every file part knows its own."""
        return self._length

    @property
    def content_type(self) -> None:
        """Name no media type; the request's Content-Type carries the boundary."""

    def iter_bytes(self) -> Iterator[bytes]:
        """Yield the parts in order, each file part as its attempt reads it."""
        for piece in self._pieces:
            if isinstance(piece, bytes):
                yield piece
            else:
                yield from piece.iter_bytes()

    def close(self) -> None:
        """Close every file part's attempt, raising the first failure with the others beside it."""
        _closed_all([piece.close for piece in self._pieces if not isinstance(piece, bytes)])


class _AsyncMultipartAttempt:
    """One async attempt of a multipart body: the encoded heads and fields, and each file part's own attempt."""

    __slots__ = ("_length", "_pieces")

    def __init__(self, pieces: list[bytes | AsyncBodyAttempt]) -> None:
        self._pieces = pieces
        lengths = [len(piece) if isinstance(piece, bytes) else piece.content_length for piece in pieces]
        self._length = None if None in lengths else sum(length for length in lengths if length is not None)

    @property
    def content_length(self) -> int | None:
        """Return the body's length when every file part knows its own."""
        return self._length

    @property
    def content_type(self) -> None:
        """Name no media type; the request's Content-Type carries the boundary."""

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        """Yield the parts in order, each file part as its attempt reads it."""
        for piece in self._pieces:
            if isinstance(piece, bytes):
                yield piece
            else:
                async for chunk in piece.aiter_bytes():
                    yield chunk

    async def aclose(self) -> None:
        """Close every file part's attempt, raising the first failure with the others beside it."""
        failures = [
            failure
            for piece in self._pieces
            if not isinstance(piece, bytes) and (failure := await _aquiet(piece)) is not None
        ]
        _raised(failures)


def _quiet(close: Callable[[], object]) -> Exception | None:
    try:
        close()
    except Exception as error:  # noqa: BLE001
        return error
    return None


async def _aquiet(attempt: AsyncBodyAttempt) -> Exception | None:
    try:
        await attempt.aclose()
    except Exception as error:  # noqa: BLE001
        return error
    return None


def _raised(failures: list[Exception]) -> None:
    if failures:
        first = failures[0]
        for failure in failures[1:]:
            add_secondary(first, failure)
        raise first


def _closed_all(closes: list[Callable[[], object]]) -> None:
    _raised([failure for close in closes if (failure := _quiet(close)) is not None])


def _is_field(value: object) -> TypeIs[FieldPart[object]]:
    return isinstance(value, FieldPart)


def _is_file(value: object) -> TypeIs[FilePart[SyncBinaryBody | AsyncBinaryBody]]:
    return isinstance(value, FilePart)


def is_multipart(value: object) -> TypeIs[MultipartBody[object] | AsyncMultipartBody[object]]:
    """Return whether a body is a multipart body of either mode."""
    return isinstance(value, (MultipartBody, AsyncMultipartBody))


def _is_sync(value: object) -> TypeIs[MultipartBody[object]]:
    return isinstance(value, MultipartBody)


def _is_async(value: object) -> TypeIs[AsyncMultipartBody[object]]:
    return isinstance(value, AsyncMultipartBody)


def _layout(parts: tuple[object, ...], boundary: str, inputs: tuple[type[InputT], ...]) -> list[bytes | InputT]:
    """Return a body's pieces in order: the encoded heads and fields, and each file part's binary input."""
    pieces: list[bytes | InputT] = []
    for part in parts:
        if _is_field(part):
            if (field := _field(part, boundary)) is not None:
                pieces.append(field)
        elif _is_file(part) and isinstance(content := part.content, inputs):
            pieces.extend((_file_head(part, boundary), content, b"\r\n"))
        else:
            _refused(part)
    pieces.append(f"--{boundary}--\r\n".encode())
    return pieces


class MultipartSource:
    """A multipart body with the boundary of its call, which builds the attempt of each send in the client's mode."""

    __slots__ = ("body", "boundary")

    def __init__(self, body: object, boundary: str) -> None:
        """Keep the body as the call gave it and the boundary its Content-Type names."""
        self.body = body
        self.boundary = boundary

    def attempt(self, context: BodyAttemptContext) -> BodyAttempt:
        """Encode the fields and begin each file part's attempt, refusing parts of the other mode."""
        if not _is_sync(body := self.body):
            raise RequestEncodingError(location=("body",), cause=TypeError(_MULTIPART))
        pieces: list[bytes | BodyAttempt] = []
        try:
            pieces.extend(
                piece if isinstance(piece, bytes) else piece(context)
                for piece in _layout(body.parts, self.boundary, _SYNC)
            )
        except BaseException:
            _closed_all([piece.close for piece in pieces if not isinstance(piece, bytes)])
            raise
        return _MultipartAttempt(pieces)

    async def aattempt(self, context: BodyAttemptContext) -> AsyncBodyAttempt:
        """Encode the fields and begin each file part's async attempt, refusing parts of the other mode."""
        if not _is_async(body := self.body):
            raise RequestEncodingError(location=("body",), cause=TypeError(_MULTIPART))
        pieces: list[bytes | AsyncBodyAttempt] = []
        try:
            for piece in _layout(body.parts, self.boundary, _ASYNC):
                pieces.append(piece if isinstance(piece, bytes) else await piece(context))  # noqa: PERF401 - Begun attempts must stay to be closed.
        except BaseException:
            _raised([
                failure
                for piece in pieces
                if not isinstance(piece, bytes) and (failure := await _aquiet(piece)) is not None
            ])
            raise
        return _AsyncMultipartAttempt(pieces)
