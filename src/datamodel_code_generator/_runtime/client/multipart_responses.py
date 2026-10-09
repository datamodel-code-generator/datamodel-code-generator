"""Multipart responses: the parts of a declared multipart response, split by the standard library's MIME parser.

A form-data response maps its parts to the members of its schema; any other multipart response keeps each part's
bytes, name, filename, media type, and headers.
"""

from __future__ import annotations

import re
from email.parser import BytesParser
from email.policy import compat32
from functools import partial
from typing import TYPE_CHECKING, Final, Generic, cast

from typing_extensions import TypeIs, TypeVar

from ..model_codecs.errors import CodecError
from ..model_codecs.media import issue, json_value, typed
from .media import charset
from .operations import BodyFramingError, BodyValueError, Branch, InvalidBodyError, read_branch
from .responses import HeadersView

if TYPE_CHECKING:
    from collections.abc import Callable
    from email.message import Message
    from typing import Protocol

    from ..model_codecs.media import JSONValue, LexicalKind
    from .multipart import PartKind, PartPlan
    from .operations import InboundModelCodec
    from .responses import ResponseInfo

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)

if TYPE_CHECKING:

    class ValueCodec(Protocol[T_co]):
        """The codec of a received member's values."""

        def decode(self, content: bytes) -> T_co:
            """Return the value of received JSON bytes."""

        def text(self, value: object) -> T_co:
            """Return the value of a parsed text value."""

        @property
        def errors(self) -> tuple[type[Exception], ...]:
            """Return the failures of the codec's backend."""

        def malformed(self, error: Exception) -> bool:
            """Return whether a failure of the codec's backend is received bytes that are not JSON."""


_PARAMETERS: Final = r';\s*([^\s;=]+)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^\s;]*))'
_BROKEN: Final = "A multipart body must hold its boundary and parts with headers, each part ending at the next boundary"


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


def _text_of(raw: str) -> str:
    """Return a header value as UTF-8, or as Latin-1 when its bytes are not UTF-8."""
    data = raw.encode("ascii", "surrogateescape")
    try:
        return data.decode()
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _part(message: Message) -> DecodedPart[bytes]:
    if message.defects or (payload := message.get_payload(decode=True)) is None:
        raise ValueError(_BROKEN)
    headers = HeadersView([(key, _text_of(value)) for key, value in message.raw_items()])
    disposition = headers.get("content-disposition") or ""
    return DecodedPart(
        _parameter(disposition, "name"),
        cast("bytes", payload),
        _parameter(disposition, "filename"),
        headers.get("content-type"),
        headers,
    )


def parse_multipart(body: bytes, content_type: str | None) -> tuple[DecodedPart[bytes], ...]:
    """Split a multipart body into its parts by the boundary its Content-Type names; raise ValueError if broken.

    The preamble and epilogue are skipped, and a part's bytes are kept as they arrived, unless it names a transfer
    encoding; a part that is a multipart body or a message itself is refused.
    """
    head = b"Content-Type: " + (content_type or "").encode("latin-1") + b"\r\n\r\n"
    message = BytesParser(policy=compat32).parsebytes(head + body)
    if message.defects or not message.is_multipart():
        raise ValueError(_BROKEN)
    return tuple(_part(part) for part in cast("list[Message]", message.get_payload()))


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
        return codec.text(_part_text(part, kind))
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


def _parts(body: bytes, info: ResponseInfo) -> tuple[DecodedPart[bytes], ...]:
    try:
        return parse_multipart(body, info.content_type)
    except (ValueError, RecursionError) as error:
        raise BodyFramingError(error) from None


def _multipart_data(body: bytes, info: ResponseInfo) -> MultipartData[bytes]:
    return MultipartData(_parts(body, info))


def _object(plans: tuple[PartPlan, ...], additional: PartPlan | None, body: bytes, info: ResponseInfo) -> object:
    parts = _parts(body, info)
    try:
        return decode_parts(parts, plans, additional)
    except (CodecError, ValueError, RecursionError) as error:
        raise InvalidBodyError(error) from None


class PartsReader(Generic[T]):
    """Read a form-data response with file parts into its parts, each value read by the decoder of its member.

    A part must be declared, or allowed by the schema's other properties, only a repeated member may repeat, and
    every required member must have a part; text that is not its member's kind is a decode failure, a value its codec
    refuses a validation failure.
    """

    __slots__ = ("_additional", "_declared")

    def __init__(self, parts: tuple[PartDecoder[T], ...], additional: PartDecoder[T] | None = None) -> None:
        """Keep the decoder of each declared member and of any other part."""
        self._declared = {part.name: part for part in parts}
        self._additional = additional

    def __call__(self, body: bytes, info: ResponseInfo) -> MultipartData[T]:
        """Split the body into its parts and read each of them."""
        parts: list[DecodedPart[T]] = []
        seen: set[str] = set()
        for part in _parts(body, info):
            if (name := part.name) is None or (plan := self._declared.get(name, self._additional)) is None:
                raise InvalidBodyError(issue(code="multipart.undeclared", message="A form-data part is not declared"))
            if plan.excluded:
                raise BodyValueError(
                    issue(code="multipart.excluded", message="A form-data part carries a member its direction excludes")
                )
            if name in seen and not plan.repeated:
                raise InvalidBodyError(
                    issue(code="multipart.duplicate", message="A form-data body repeats a single-valued member")
                )
            seen.add(name)
            try:
                value = plan.read(part)
            except PartSyntaxError as error:
                raise InvalidBodyError(error.cause) from None
            except PartValueError as error:
                raise BodyValueError(error.cause) from None
            parts.append(DecodedPart(name, value, part.filename, part.content_type, part.headers))
        for plan in self._declared.values():
            if plan.required and plan.name not in seen:
                raise BodyValueError(issue(code="multipart.missing", message="A required form-data member has no part"))
        return MultipartData(tuple(parts))


def multipart_branch(status: str, media_type: str) -> Branch[MultipartData[bytes]]:
    """Return a multipart branch without a schema, keeping each part's bytes, name, filename, and headers."""
    return Branch(status, media_type, _multipart_data)


def parts_branch(status: str, media_type: str, reader: PartsReader[T]) -> Branch[MultipartData[T]]:
    """Return a form-data branch whose schema has file parts, read part by part into their members' values."""
    return Branch(status, media_type, reader)


def object_branch(
    status: str,
    media_type: str,
    codec: InboundModelCodec[T],
    parts: tuple[PartPlan, ...],
    additional: PartPlan | None = None,
) -> Branch[T]:
    """Return a form-data branch whose schema has no file parts, read into an object its model codec decodes."""
    return read_branch(status, media_type, codec, partial(_object, parts, additional))
