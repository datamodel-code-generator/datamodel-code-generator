"""Multipart bodies: form-data field and file parts sent with the boundary of their call, and received parts.

A field part carries a value, a file part any binary body of the client's mode. A call encodes the fields and the
heads of the file parts once, and each send begins each file part's own attempt around them, so a multipart body can
be sent again when all of its file parts can. A body whose schema has file parts is checked against the plans of its
members: each part's name, kind, and repeats, and the presence of every required member.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from functools import partial
from typing import TYPE_CHECKING, Final, Generic, Literal, NoReturn, TypeAlias, TypeVar, cast

from typing_extensions import TypeAliasType, TypeIs

from ..model_codecs.errors import CodecError, ParameterEncodingError
from ..model_codecs.media import issue, json_value, media_kind, normalize_media_type, typed
from ..model_codecs.media import json_bytes as _json_bytes
from ..model_codecs.parameters import ParameterPlan, part_pairs
from ..model_codecs.unset import Unset
from .bodies import AsyncBinaryBody, SyncBinaryBody, is_async_binary_input, is_binary_input
from .errors import DecodeError
from .media import charset, encode_text, most_specific, normalized, with_charset
from .responses import HeadersView

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterable, Iterator
    from typing import Any, Protocol

    from ..model_codecs.media import JSONValue, LexicalKind
    from .bodies import AsyncContent, SyncContent

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
T = TypeVar("T")
PartT_co = TypeVar("PartT_co", covariant=True)
T_co = TypeVar("T_co", covariant=True)
PartKind: TypeAlias = Literal["string", "integer", "number", "boolean", "json"]
ContentT_co = TypeVar("ContentT_co", bound="SyncBinaryBody | AsyncBinaryBody", covariant=True)

if TYPE_CHECKING:

    class ValueCodec(Protocol[T_co]):
        """The codec of a received member's values."""

        def decode(self, content: bytes) -> T_co:
            """Return the value of received JSON bytes."""
            ...

        def convert(self, value: object) -> T_co:
            """Return the value of a parsed text value."""
            ...

        @property
        def errors(self) -> tuple[type[Exception], ...]:
            """Return the failures of the codec's backend."""
            ...

        def malformed(self, error: Exception) -> bool:
            """Return whether a failure of the codec's backend is received bytes that are not JSON."""
            ...


_MULTIPART: Final = "A multipart body must be of the client's mode"
_TOKEN: Final = re.compile(r"[!#$%&'*+.^_`|~0-9a-zA-Z-]+")
_BREAK: Final = re.compile(r"[\r\n\x00]")
_PART_HEADERS: Final = frozenset({"content-type", "content-disposition"})
_PARAMETERS: Final = r';\s*([^\s;=]+)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^\s;]*))'
_PART: Final = "A multipart part must be a FieldPart, or a FilePart of a binary body of the client's mode"
_UNDECLARED: Final = "A form-data part is not declared"
_EXCLUDED: Final = "A form-data member that its direction excludes takes no part"
_KIND: Final = "A form-data file member takes FileParts, and any other member FieldParts"
_REPEATED: Final = "A form-data body repeats a single-valued member"
_MISSING: Final = "A form-data body lacks a required member"
_MEDIA: Final = "A part's media type must fall within its member's encoding"
_TEXT: Final = "A text part carries a scalar"
_CLAIMED: Final = "Two form-data members write parts of the same name"


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
    """How one form-data member is sent or read: its kind, repeats, files, requiredness, codec, and media types.

    A received part is read in its lexical kind, or as JSON. A sent value is encoded by the codec, when the member
    has one, and each item of a repeated member becomes its own part; a file member repeats as FileParts. The media
    types of the member's encoding bound the media type a part names, and the first concrete one is its default. A
    member whose encoding gives a query style writes a part for each name and value that style gives its value. A
    member the direction excludes, read-only in a request, takes no part.
    """

    __slots__ = (
        "codec",
        "content_types",
        "excluded",
        "file",
        "kind",
        "name",
        "repeated",
        "required",
        "style",
    )

    def __init__(  # noqa: PLR0913
        self,
        name: str,
        kind: PartKind = "string",
        *,
        repeated: bool = False,
        file: bool = False,
        required: bool = False,
        excluded: bool = False,
        codec: PartCodec | None = None,
        content_types: tuple[str, ...] = (),
        style: ParameterPlan | None = None,
    ) -> None:
        """Keep the member's name, kind, repeats, files, requiredness, exclusion, codec, media, and style."""
        self.name = name
        self.kind: PartKind = kind
        self.repeated = repeated
        self.file = file
        self.required = required
        self.excluded = excluded
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


class _Names:
    """The member plans of a body with file parts, and the names its parts have used so far.

    Once a member's style writes part names of its own, each part name is owned by the member that wrote it.
    """

    __slots__ = ("additional", "declared", "owners", "seen")

    def __init__(self, plans: tuple[PartPlan, ...], additional: PartPlan | None) -> None:
        self.declared = {plan.name: plan for plan in plans}
        self.additional = additional
        self.seen: set[str] = set()
        self.owners: dict[str, str] | None = {} if any(plan.style is not None for plan in plans) else None

    def plan(self, name: object, *, file: bool, omitted: bool = False) -> PartPlan:
        """Return the plan of a part, refusing an undeclared name, the other kind of part, or a repeat.

        A member its direction excludes takes only an omitted part.
        """
        if not isinstance(name, str) or (plan := self.declared.get(name, self.additional)) is None:
            raise _malformed(name, ValueError(_UNDECLARED))
        if plan.excluded and not omitted:
            raise _malformed(name, ValueError(_EXCLUDED))
        if plan.file is not file:
            raise _malformed(name, ValueError(_KIND))
        if name in self.seen and not (file and plan.repeated):
            raise _malformed(name, ValueError(_REPEATED))
        self.seen.add(name)
        return plan

    def claim(self, member: str, written: Iterable[str]) -> None:
        """Refuse part names another member's parts have written."""
        if self.owners is not None and any(self.owners.setdefault(name, member) != member for name in written):
            raise _malformed(member, ValueError(_CLAIMED))

    def check(self) -> None:
        """Refuse a body that lacks a part of a required member."""
        for plan in self.declared.values():
            if plan.required and plan.name not in self.seen:
                raise _malformed(plan.name, ValueError(_MISSING))


def _malformed(name: object, cause: BaseException, *, whole: bool = False) -> DecodeError:
    location = ("body",) if whole else ("body", name if isinstance(name, str) else "?")
    return DecodeError(reason="unencodable", direction="request", location=location, cause=cause)


def multipart_head(
    boundary: str, name: str, filename: str | None, content_type: str | None, headers: tuple[tuple[str, str], ...]
) -> bytes:
    """Return the delimiter and headers that open one form-data part, as HTML forms quote its name and filename.

    Content-Type and Content-Disposition come from the part itself, so its own headers may not repeat them.
    """
    lines = [f"--{boundary}", f"Content-Disposition: {_disposition(name, filename)}"]
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


def _disposition(name: str, filename: str | None) -> str:
    disposition = f'form-data; name="{_quoted(name)}"'
    return disposition if filename is None else f'{disposition}; filename="{_quoted(filename)}"'


def _quoted(text: str) -> str:
    return text.replace('"', "%22").replace("\r", "%0D").replace("\n", "%0A")


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


def encode_multipart(
    value: JSONValue,
    boundary: str,
    content_types: Mapping[str, str] | None = None,
    styled: Mapping[str, ParameterPlan] | None = None,
) -> bytes:
    """Serialize an object as ordered form-data parts, repeating a part for each array member; an empty one writes none.

    A member with an encoding's media type is written in it. A member in `styled` writes a part for each name and
    value its query style gives, without percent-encoding, and no two members may then write parts of the same name.
    """
    if not isinstance(value, Mapping):
        msg = "A multipart form value must be an object"
        raise ParameterEncodingError(msg)
    parts: list[bytes] = []
    owners: dict[str, str] = {}
    for name, item in value.items():
        if styled and (style := styled.get(name)) is not None:
            pairs = part_pairs(style, item)
            if any(owners.setdefault(key, name) != name for key, _ in pairs):
                raise ParameterEncodingError(_CLAIMED)
            parts.extend(
                piece
                for key, text in pairs
                for piece in (multipart_head(boundary, key, None, None, ()), text.encode(), b"\r\n")
            )
            continue
        if styled and owners.setdefault(name, name) != name:
            raise ParameterEncodingError(_CLAIMED)
        declared = None if content_types is None else content_types.get(name)
        for member in item if isinstance(item, list) else (item,):
            content, media_type = multipart_member(member, declared)
            parts.extend((multipart_head(boundary, name, None, media_type, ()), content, b"\r\n"))
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts)


def new_boundary() -> str:
    """Return a new random boundary, one for each call."""
    return f"dcg{os.urandom(16).hex()}"


def _refused(part: object) -> NoReturn:
    raise _malformed(getattr(part, "name", None), TypeError(_PART))


def _field(part: FieldPart[object], boundary: str, plan: PartPlan, names: _Names | None = None) -> bytes | None:
    """Return a field part's bytes with their heads and tails, one part for each item of a repeated member.

    UNSET leaves an optional member out and is refused for a required one. A styled member writes a part for each
    name and value its style gives, without percent-encoding.
    """
    if isinstance(value := part.value, Unset):
        if plan.required:
            raise _malformed(part.name, ValueError(_MISSING))
        return None
    try:
        wire = cast("JSONValue", value if (codec := plan.codec) is None else codec.dump(value))
        if plan.style is not None and names is not None:
            return _styled(part, boundary, part_pairs(plan.style, wire), names)
        if names is not None and names.owners is not None:
            names.claim(part.name, (part.name,))
        if not plan.repeated or not isinstance(wire, list):
            return _member(part, boundary, wire, plan)
        return b"".join([_member(part, boundary, item, plan) for item in wire])
    except _errors(plan) as error:
        raise _malformed(part.name, error) from None


def _errors(plan: PartPlan) -> tuple[type[Exception], ...]:
    return (CodecError, TypeError, AttributeError, *(() if plan.codec is None else plan.codec.errors))


def _styled(part: FieldPart[object], boundary: str, pairs: tuple[tuple[str, str], ...], names: _Names) -> bytes:
    """Return the parts a styled member writes, each name claimed for it.

    Each value is text in the charset of the media type its part names, UTF-8 without one.
    """
    names.claim(part.name, [key for key, _ in pairs])
    media_type = part.content_type
    return b"".join([
        multipart_head(boundary, key, None, media_type, part.headers)
        + (text.encode() if media_type is None else encode_text(text, media_type))
        + b"\r\n"
        for key, text in pairs
    ])


def _member(part: FieldPart[object], boundary: str, item: JSONValue, plan: PartPlan) -> bytes:
    if plan.content_types:
        media_type = plan.media(part.content_type)
        content, shaped = multipart_member(item, media_type)
    else:
        media_type = part.content_type
        content, shaped = multipart_member(item)
    return multipart_head(boundary, part.name, None, media_type or shaped, part.headers) + content + b"\r\n"


def _file_head(part: FilePart[SyncBinaryBody | AsyncBinaryBody], boundary: str, plan: PartPlan) -> bytes:
    try:
        media_type = plan.media(part.content_type) if plan.content_types else part.content_type
        if media_type is None and plan.content_types:
            raise ParameterEncodingError(_MEDIA)
        return multipart_head(
            boundary, part.name, part.filename, media_type or "application/octet-stream", part.headers
        )
    except (CodecError, TypeError, AttributeError) as error:
        raise _malformed(part.name, error) from None


class MultipartAttempt:
    """One attempt of a multipart body: the encoded heads and fields, and each file part's own attempt."""

    __slots__ = ("_length", "_pieces")

    def __init__(self, pieces: list[bytes | SyncContent]) -> None:
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


class AsyncMultipartAttempt:
    """One async attempt of a multipart body: the encoded heads and fields, and each file part's own attempt."""

    __slots__ = ("_length", "_pieces")

    def __init__(self, pieces: list[bytes | AsyncContent]) -> None:
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


def _is_field(value: object) -> TypeIs[FieldPart[object]]:
    return isinstance(value, FieldPart)


def is_file_part(value: object) -> TypeIs[FilePart[SyncBinaryBody | AsyncBinaryBody]]:
    return isinstance(value, FilePart)


def is_multipart(value: object) -> TypeIs[MultipartBody[object] | AsyncMultipartBody[object]]:
    """Return whether a body is a multipart body of either mode."""
    return isinstance(value, (MultipartBody, AsyncMultipartBody))


def _is_sync(value: object) -> TypeIs[MultipartBody[object]]:
    return isinstance(value, MultipartBody)


def _is_async(value: object) -> TypeIs[AsyncMultipartBody[object]]:
    return isinstance(value, AsyncMultipartBody)


def _layout(
    parts: tuple[object, ...], boundary: str, names: _Names | None
) -> list[bytes | FilePart[SyncBinaryBody | AsyncBinaryBody]]:
    """Return a body's pieces in order: the encoded fields and heads between the file parts, whose inputs are sent.

    With member plans, each part must be declared for its kind, and every required member must have a part.
    """
    pieces: list[bytes | FilePart[SyncBinaryBody | AsyncBinaryBody]] = []
    encoded: list[bytes] = []
    for part in parts:
        if _is_field(part):
            plan = _ANY if names is None else names.plan(part.name, file=False, omitted=isinstance(part.value, Unset))
            if (field := _field(part, boundary, plan, names)) is not None:
                encoded.append(field)
        elif is_file_part(part):
            file = _ANY if names is None else names.plan(part.name, file=True)
            if names is not None and names.owners is not None:
                names.claim(part.name, (part.name,))
            encoded.append(_file_head(part, boundary, file))
            pieces.extend((b"".join(encoded), part))
            encoded = [b"\r\n"]
        else:
            _refused(part)
    if names is not None:
        names.check()
    encoded.append(f"--{boundary}--\r\n".encode())
    pieces.append(b"".join(encoded))
    return pieces


def _inputs(
    pieces: list[bytes | FilePart[SyncBinaryBody | AsyncBinaryBody]], *, asynchronous: bool = False
) -> Iterator[AsyncBinaryBody]:
    """Yield a body's encoded pieces and each file part's binary input, refusing an input of the other mode."""
    for piece in pieces:
        if isinstance(piece, bytes):
            yield piece
        else:
            content = piece.content
            if not (is_async_binary_input(content) if asynchronous else is_binary_input(content)):
                _refused(piece)
            yield content


class MultipartSource:
    """A multipart body with the boundary of its call, which builds the attempt of each send in the client's mode.

    It encodes the fields and the heads of the file parts once, checked by the member plans of a schema with file
    parts; a body without a schema has none.
    """

    __slots__ = ("_pieces", "body", "boundary")

    def __init__(
        self,
        body: object,
        boundary: str,
        plans: tuple[PartPlan, ...] | None = None,
        additional: PartPlan | None = None,
    ) -> None:
        """Encode the body's fields and file heads for every attempt, refusing a body that is no multipart body."""
        if not is_multipart(body):
            raise _malformed(None, TypeError(_MULTIPART), whole=True)
        self.body = body
        self.boundary = boundary
        self._pieces = _layout(body.parts, boundary, None if plans is None else _Names(plans, additional))

    def inputs(self) -> Iterator[SyncBinaryBody]:
        """Yield the once-encoded layout and synchronous file inputs for call-level binding."""
        if not _is_sync(self.body):
            raise _malformed(None, TypeError(_MULTIPART), whole=True)
        return cast("Iterator[SyncBinaryBody]", _inputs(self._pieces))

    def ainputs(self) -> Iterator[AsyncBinaryBody]:
        """Yield the once-encoded layout and asynchronous file inputs for call-level binding."""
        if not _is_async(self.body):
            raise _malformed(None, TypeError(_MULTIPART), whole=True)
        return _inputs(self._pieces, asynchronous=True)


class DecodedPart(Generic[T_co]):
    """One part of a multipart response: its name, value, filename, media type, and headers in their order.

    Its representation names only the part and its media type, never its value, filename, or header values.
    """

    __slots__ = ("_content_type", "_filename", "_headers", "_name", "_value")

    def __init__(
        self, name: str | None, value: T_co, filename: str | None, content_type: str | None, headers: HeadersView
    ) -> None:
        """Keep the part as it arrived, with its value decoded."""
        self._name = name
        self._value = value
        self._filename = filename
        self._content_type = content_type
        self._headers = headers

    @property
    def name(self) -> str | None:
        """Return the part's form-data name, or None when its Content-Disposition names none."""
        return self._name

    @property
    def value(self) -> T_co:
        """Return the part's value."""
        return self._value

    @property
    def filename(self) -> str | None:
        """Return the filename the part names, which is never used as a path."""
        return self._filename

    @property
    def content_type(self) -> str | None:
        """Return the part's media type, or None when it names none."""
        return self._content_type

    @property
    def headers(self) -> HeadersView:
        """Return the part's headers in their order, duplicates included."""
        return self._headers

    def __eq__(self, other: object) -> bool:
        """Compare the parts field by field."""
        return _is_part(other) and self._fields() == other._fields()

    def __hash__(self) -> int:
        """Hash the part's fields."""
        return hash(self._fields())

    def __repr__(self) -> str:
        """Name the part and its media type only."""
        return f"DecodedPart(name={self._name!r}, content_type={self._content_type!r})"

    def _fields(self) -> tuple[str | None, T_co, str | None, str | None, HeadersView]:
        return self._name, self._value, self._filename, self._content_type, self._headers


class MultipartData(Generic[T_co]):
    """The parts of a multipart response in their order; its representation counts them and shows nothing else."""

    __slots__ = ("_parts",)

    def __init__(self, parts: tuple[DecodedPart[T_co], ...]) -> None:
        """Keep the parts in their order."""
        self._parts = parts

    @property
    def parts(self) -> tuple[DecodedPart[T_co], ...]:
        """Return the parts in their order."""
        return self._parts

    def __eq__(self, other: object) -> bool:
        """Compare the parts in their order."""
        return _is_data(other) and self._parts == other._parts

    def __hash__(self) -> int:
        """Hash the parts in their order."""
        return hash(self._parts)

    def __repr__(self) -> str:
        """Count the parts."""
        return f"MultipartData(<{len(self._parts)} parts>)"


def _is_part(value: object) -> TypeIs[DecodedPart[object]]:
    return isinstance(value, DecodedPart)


def _is_data(value: object) -> TypeIs[MultipartData[object]]:
    return isinstance(value, MultipartData)


def _parameter(value: str, name: str) -> str | None:
    """Return a parameter of a header value: a token, or a quoted string with its escapes removed."""
    for found in re.finditer(_PARAMETERS, value):
        if found[1].lower() == name:
            return found[3] if found[2] is None else re.sub(r"\\(.)", r"\1", found[2])
    return None


def _headers(head: bytes) -> HeadersView:
    """Return a part's headers, read as UTF-8, or as Latin-1 when they are not UTF-8."""
    try:
        text = head.decode()
    except UnicodeDecodeError:
        text = head.decode("latin-1")
    items: list[tuple[str, str]] = []
    for line in text.split("\r\n"):
        key, colon, value = line.partition(":")
        if not colon or not _TOKEN.fullmatch(key):
            msg = "A part header must be a token, a colon, and a value"
            raise ValueError(msg)
        items.append((key, value.strip()))
    return HeadersView(items)


def parse_multipart(body: bytes, content_type: str | None) -> tuple[DecodedPart[bytes], ...]:
    """Split a multipart body into its parts by the boundary its Content-Type names; raise ValueError if broken.

    Delimiters start their own lines and may carry transport padding; the preamble and epilogue are skipped.
    """
    if content_type is None or not (boundary := _parameter(content_type, "boundary")):
        msg = "A multipart response must name its boundary"
        raise ValueError(msg)
    delimiter = b"\r\n--" + boundary.encode()
    if body.startswith(delimiter[2:]):
        position = len(delimiter) - 2
    elif (found := body.find(delimiter)) >= 0:
        position = found + len(delimiter)
    else:
        msg = "A multipart body must hold its boundary"
        raise ValueError(msg)
    parts: list[DecodedPart[bytes]] = []
    while not body.startswith(b"--", position):
        line = body.find(b"\r\n", position)
        if line < 0 or body[position:line].strip(b" \t") or (end := body.find(delimiter, line + 2)) < 0:
            msg = "A multipart part must start on its own line and end at the next boundary"
            raise ValueError(msg)
        split = body.find(b"\r\n\r\n", line, end + 2)
        head, content = (body[line + 2 : end], b"") if split < 0 else (body[line + 2 : split], body[split + 4 : end])
        headers = _headers(head) if head else HeadersView()
        disposition = headers.get("content-disposition") or ""
        parts.append(
            DecodedPart(
                _parameter(disposition, "name"),
                content,
                _parameter(disposition, "filename"),
                headers.get("content-type"),
                headers,
            )
        )
        position = end + len(delimiter)
    return tuple(parts)


def _json_part(part: DecodedPart[bytes]) -> bool:
    media = (part.content_type or "").partition(";")[0].strip().lower()
    return media == "application/json" or media.endswith("+json")


def _part_value(part: DecodedPart[bytes], kind: PartKind) -> JSONValue:
    if kind == "json" or _json_part(part):
        return json_value(part.value)
    return typed(part.value.decode(charset(part.content_type or "")), kind)


def decode_parts(
    parts: tuple[DecodedPart[bytes], ...], plans: tuple[PartPlan, ...], additional: PartPlan | None
) -> dict[str, JSONValue]:
    """Read form-data parts into an object by their members' plans, collecting repeated members into arrays."""
    declared = {plan.name: plan for plan in plans}
    result: dict[str, JSONValue] = {}
    for part in parts:
        if part.name is None:
            raise issue(code="multipart.undeclared", message="A form-data part has no name")
        plan = declared.get(part.name, additional)
        value = _part_value(part, "string" if plan is None else plan.kind)
        if part.name not in result:
            result[part.name] = [value] if plan is not None and plan.repeated else value
        elif plan is not None and plan.repeated:
            cast("list[JSONValue]", result[part.name]).append(value)
        elif part.name not in declared:
            result[part.name] = value
        else:
            raise issue(code="multipart.duplicate", message="A form-data body repeats a single-valued member")
    return result


class PartSyntaxError(Exception):
    """A received part that is not text of its member's kind, or not JSON."""

    def __init__(self, cause: BaseException) -> None:
        """Keep the failure of reading the part."""
        super().__init__()
        self.cause = cause


class PartValueError(Exception):
    """A received part whose value its member's codec refuses."""

    def __init__(self, cause: BaseException) -> None:
        """Keep the codec's failure."""
        super().__init__()
        self.cause = cause


class PartDecoder(Generic[T_co]):
    """How the parts of one member of a form-data response with file parts are read, and whether they repeat.

    A file part keeps its bytes; any other part is read in its member's kind, or as JSON, then decoded by its codec.
    """

    __slots__ = ("_read", "excluded", "name", "repeated", "required")

    def __init__(
        self,
        name: str,
        read: Callable[[DecodedPart[bytes]], T_co],
        *,
        repeated: bool = False,
        required: bool = False,
        excluded: bool = False,
    ) -> None:
        """Keep the member's name, how a part becomes its value, and whether it repeats or is required.

        A member the direction excludes, write-only in a response, has no part to read.
        """
        self.name = name
        self._read = read
        self.repeated = repeated
        self.required = required
        self.excluded = excluded

    def read(self, part: DecodedPart[bytes]) -> T_co:
        """Return the value of one part, raising PartSyntaxError when it is not text of its kind."""
        return self._read(part)


def _content(part: DecodedPart[bytes]) -> bytes:
    return part.value


def file_part(
    name: str, *, repeated: bool = False, required: bool = False, excluded: bool = False
) -> PartDecoder[bytes]:
    """Return how a file member's parts are read, as their bytes; an untyped extra part is read the same way."""
    return PartDecoder(name, _content, repeated=repeated, required=required, excluded=excluded)


def _part_text(part: DecodedPart[bytes], kind: LexicalKind) -> JSONValue:
    try:
        return typed(part.value.decode(charset(part.content_type or "")), kind)
    except ValueError as error:
        raise PartSyntaxError(error) from None


def _decoded(codec: ValueCodec[T], kind: PartKind, part: DecodedPart[bytes]) -> T:
    try:
        if kind == "json" or _json_part(part):
            return codec.decode(part.value)
        return codec.convert(_part_text(part, kind))
    except codec.errors as error:
        if codec.malformed(error):
            raise PartSyntaxError(error) from None
        raise PartValueError(error) from None


def value_part(  # noqa: PLR0913
    name: str,
    kind: PartKind,
    codec: ValueCodec[T],
    *,
    repeated: bool = False,
    required: bool = False,
    excluded: bool = False,
) -> PartDecoder[T]:
    """Return how a member's parts are read: as JSON, or in its kind, then decoded by its codec."""
    return PartDecoder(
        name,
        partial(_decoded, codec, kind),
        repeated=repeated,
        required=required,
        excluded=excluded,
    )
