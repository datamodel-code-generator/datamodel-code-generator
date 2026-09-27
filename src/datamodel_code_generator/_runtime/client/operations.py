"""Operation plans of a generated client: how each argument is sent and each response is decoded."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Final, Generic, Literal, Protocol, TypeAlias

from typing_extensions import TypeIs, TypeVar

from ..model_codecs.errors import (
    CodecBindingError,
    CodecError,
    CodecResourceLimitError,
    ModelProjectionError,
    NativeValidationError,
    ParameterEncodingError,
    WireValidationError,
)
from ..model_codecs.media import (
    decode_form,
    decode_json,
    encode_form,
    encode_json,
    form_encode,
    issue,
    percent_decode,
    split_form,
)
from ..model_codecs.parameters import query_pairs
from ..model_codecs.selectors import MediaSelector, RequestMedia
from ..model_codecs.unset import Unset
from ..model_codecs.wire import checked_wire
from .errors import (
    BodyProtocolError,
    ConfigurationError,
    DecodeError,
    HTTPStatusError,
    RequestEncodingError,
    ResponseDecodeError,
    ResponseValidationError,
    UnexpectedMediaTypeError,
    UnexpectedStatusError,
)
from .media import charset, essence, most_specific, normalized, with_charset
from .multipart import (
    DecodedPart,
    MultipartData,
    MultipartSource,
    PartPlan,
    PartSyntaxError,
    decode_parts,
    encode_multipart,
    new_boundary,
    parse_multipart,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Container, Sequence

    from ..model_codecs.context import CodecContext
    from ..model_codecs.media import FieldPlan
    from ..model_codecs.parameters import ParameterPlan
    from ..model_codecs.values import DecodedValue
    from ..model_codecs.wire import WireValue
    from .multipart import PartDecoder
    from .responses import ResponseInfo

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)
E_co = TypeVar("E_co", covariant=True)

FormData: TypeAlias = tuple[tuple[str, str], ...]
BodyKind: TypeAlias = Literal["json", "text", "form", "multipart", "binary"]
ReadKind: TypeAlias = Literal["json", "text", "form", "multipart"]

BODYLESS_STATUSES: Final = frozenset({204, 205, 304})
_MIN_SUCCESS: Final = 200
_MAX_SUCCESS: Final = 299
_MIN_ERROR: Final = 400
_MAX_ERROR: Final = 599
_PAIR: Final = 2
DATA_ERRORS: Final = (
    CodecBindingError,
    CodecResourceLimitError,
    ModelProjectionError,
    NativeValidationError,
    ParameterEncodingError,
    WireValidationError,
)


class OutboundModelCodec(Protocol):
    """The sending half of a model codec, as the generated bindings provide it."""

    def encode(self, value: object, context: CodecContext) -> WireValue:
        """Validate a native value or a snapshot of it, and return its wire value."""
        ...


class Projected(Protocol[T_co]):
    """A decoded value that yields its native value, or raises for a known projection gap."""

    def require_model(self) -> T_co:
        """Return the native value."""
        ...


class InboundModelCodec(Protocol[T_co]):
    """The receiving half of a model codec whose native value a call returns."""

    def decode(self, wire: WireValue, context: CodecContext) -> Projected[T_co]:
        """Validate a received wire value and construct its native value or envelope."""
        ...


class InboundEnvelopeCodec(Protocol[T]):
    """The receiving half of a model codec whose model value or envelope a call returns."""

    def decode(self, wire: WireValue, context: CodecContext) -> DecodedValue[T]:
        """Validate a received wire value and construct its native value or envelope."""
        ...


@dataclass(frozen=True, slots=True)
class Encoder:
    """A model codec accessor bound to its sending context."""

    codec: Callable[[], OutboundModelCodec]
    context: CodecContext

    def encode(self, value: object) -> WireValue:
        """Return the validated wire value of a native value or snapshot."""
        return self.codec().encode(value, self.context)


@dataclass(frozen=True, slots=True, kw_only=True)
class ServerVariable:
    """One server variable with its default and, when declared, its allowed values."""

    name: str
    default: str
    enum: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class ServerPlan:
    """One effective server: an absolute URL template and its variables."""

    url: str
    variables: tuple[ServerVariable, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class ParameterSpec:
    """One effective parameter: its wire plan and, when it has a schema, the codec that validates its argument."""

    plan: ParameterPlan
    encoder: Encoder | None = None

    def encode(self, value: object) -> WireValue:
        """Return the wire value of a present argument."""
        return checked_wire(value) if self.encoder is None else self.encoder.encode(value)


@dataclass(frozen=True, slots=True, kw_only=True)
class EncodedBody:
    """A request body encoded for its selected media type; a binary body stays the input the call was given."""

    media_type: str
    content: object


@dataclass(frozen=True, slots=True, kw_only=True)
class BodyMedia:
    """One declared request media type and how an argument becomes its bytes.

    A form-data schema with file parts takes parts, which the plans of its members check.
    """

    media_type: str
    kind: BodyKind
    encoder: Encoder | None = None
    fields: tuple[FieldPlan, ...] = ()
    additional: FieldPlan | None = None
    parts: tuple[PartPlan, ...] | None = None
    additional_part: PartPlan | None = None
    encoded: tuple[ParameterPlan, ...] = ()
    content_types: tuple[tuple[str, str], ...] = ()

    def encode(self, value: object, sent: str) -> object:
        """Encode one body argument for the media type sent, or raise the codec or media failure.

        Text takes the charset the sent media type names; a binary body is sent as it is given.
        """
        match self.kind:
            case "json":
                return encode_json(self.wire(value))
            case "text":
                if not isinstance(text := self.wire(value), str):
                    msg = "A text body must be a string"
                    raise ParameterEncodingError(msg)
                return text.encode(charset(sent))
            case "form" if self.encoder is not None:
                styled = {plan.name: partial(query_pairs, plan) for plan in self.encoded} if self.encoded else None
                return encode_form(self.encoder.encode(value), self.fields, self.additional, styled)
            case "form":
                return _form_data(value)
            case _:
                pass
        return value

    def wire(self, value: object) -> WireValue:
        """Return the wire value of a JSON or text argument."""
        return checked_wire(value) if self.encoder is None else self.encoder.encode(value)

    def multipart(self, value: object, boundary: str) -> object:
        """Return a form-data body: an object's members as parts, or the parts a call gives, checked by any plans."""
        if self.encoder is None:
            return MultipartSource(value, boundary, self.parts, self.additional_part)
        return encode_multipart(
            self.encoder.encode(value), boundary, dict(self.content_types) if self.content_types else None
        )


def _is_tuple(value: object) -> TypeIs[tuple[object, ...]]:
    return isinstance(value, tuple)


def _is_form_data(value: object) -> TypeIs[FormData]:
    return _is_tuple(value) and all(_is_pair(pair) for pair in value)


def _is_pair(value: object) -> bool:
    return _is_tuple(value) and len(value) == _PAIR and all(isinstance(item, str) for item in value)


def _form_data(value: object) -> bytes:
    if not _is_form_data(value):
        msg = "Form data must be a tuple of name and value string pairs"
        raise ParameterEncodingError(msg)
    return "&".join(f"{form_encode(name)}={form_encode(text)}" for name, text in value).encode("ascii")


@dataclass(frozen=True, slots=True, kw_only=True)
class RequestBody:
    """An operation's declared request media, the one a call without media_type sends, and whether it is required."""

    media: tuple[BodyMedia, ...]
    default: str | None = None
    required: bool = False

    def encode(
        self, operation_id: str | None, value: object, media_type: str | MediaSelector | None, owner: object = None
    ) -> EncodedBody | None:
        """Select the media and encode the argument; an omitted optional body sends nothing.

        A request media selector of the operation sends its concrete media type with its declared type's encoding;
        a media range such as image/* is only sent through one.
        """
        if isinstance(value, Unset):
            if media_type is not None:
                raise ConfigurationError(
                    field_path=("media_type",), condition="without_body", operation_id=operation_id
                )
            if self.required:
                raise RequestEncodingError(location=("body",), operation_id=operation_id)
            return None
        concrete: str | None = None
        match media_type:
            case None:
                pass
            case MediaSelector():
                media_type, concrete = RequestMedia.chosen(media_type, owner)
            case str() if "*" in essence(media_type):
                raise ConfigurationError(field_path=("media_type",), condition="undeclared", operation_id=operation_id)
            case _:
                pass
        selected = self.select(operation_id, media_type)
        sent = selected.media_type if concrete is None else _sent(concrete, selected.media_type)
        try:
            if selected.kind == "multipart":
                boundary = new_boundary()
                return EncodedBody(
                    media_type=f"{sent}; boundary={boundary}", content=selected.multipart(value, boundary)
                )
            content = selected.encode(value, sent)
        except RequestEncodingError as error:
            raise RequestEncodingError(location=error.location, operation_id=operation_id, cause=error.cause) from None
        except (*DATA_ERRORS, ValueError, TypeError) as error:
            raise RequestEncodingError(location=("body",), operation_id=operation_id, cause=error) from None
        return EncodedBody(media_type=sent, content=content)

    def select(self, operation_id: str | None, media_type: str | None) -> BodyMedia:
        """Return the declared media a call names, or the default media when it names none."""
        if media_type is None and self.default is None:
            raise ConfigurationError(field_path=("media_type",), condition="missing", operation_id=operation_id)
        wanted = self.default if media_type is None else normalized(media_type)
        if (found := next((media for media in self.media if media.media_type == wanted), None)) is None:
            raise ConfigurationError(field_path=("media_type",), condition="undeclared", operation_id=operation_id)
        return found


def _sent(concrete: str, declared: str) -> str:
    """Return the media type a selector sends: its concrete type, with the declared charset when it names none.

    A boundary the concrete type names is left out, since a multipart body names the boundary of its call.
    """
    bare, *parameters = concrete.split("; ")
    kept = "; ".join((bare, *(parameter for parameter in parameters if not parameter.startswith("boundary="))))
    return with_charset(kept, declared)


class Branch(Generic[T_co]):
    """Decode one declared response: a status key and one of its media types, or its absence of a body."""

    __slots__ = ("_decode", "media_type", "status")

    def __init__(self, status: str, media_type: str | None, decode: Callable[[bytes, ResponseInfo], T_co]) -> None:
        """Bind the status key, the declared media type (None for no body), and the decoder."""
        self.status = status
        self.media_type = media_type
        self._decode = decode

    def decode(self, body: bytes, info: ResponseInfo) -> T_co:
        """Decode a complete body of this branch."""
        return self._decode(body, info)


class _InvalidBodyError(Exception):
    """A body that failed to decode, published as the error of its kind of failure."""

    def __init__(self, cause: BaseException) -> None:
        super().__init__()
        self.cause = cause

    def failure(self, info: ResponseInfo, body: bytes) -> ResponseDecodeError:
        """Return the syntax failure of a body that does not parse."""
        return DecodeError(info=info, body_bytes=body, call_id=info.call_id, cause=self.cause)


class _BodyValueError(_InvalidBodyError):
    def failure(self, info: ResponseInfo, body: bytes) -> ResponseDecodeError:
        return ResponseValidationError(info=info, body_bytes=body, call_id=info.call_id, cause=self.cause)


class _BodyFramingError(_InvalidBodyError):
    def failure(self, info: ResponseInfo, body: bytes) -> ResponseDecodeError:
        return BodyProtocolError(
            info=info, condition="invalid_framing", body_bytes=body, call_id=info.call_id, cause=self.cause
        )


def _json(body: bytes, _: ResponseInfo) -> WireValue:
    try:
        return decode_json(body)
    except CodecError as error:
        raise _InvalidBodyError(error) from None


def _text(body: bytes, info: ResponseInfo) -> str:
    try:
        return body.decode(charset(info.content_type or ""))
    except UnicodeDecodeError as error:
        raise _InvalidBodyError(error) from None


def _pairs(body: bytes, _: ResponseInfo) -> FormData:
    try:
        return tuple(
            (percent_decode(name, plus=True), percent_decode(value, plus=True)) for name, value in split_form(body)
        )
    except CodecError as error:
        raise _InvalidBodyError(error) from None


def _bytes(body: bytes, _: ResponseInfo) -> bytes:
    return body


def _none(_body: bytes, _info: ResponseInfo) -> None:
    return None


def _multipart(body: bytes, info: ResponseInfo) -> tuple[DecodedPart[bytes], ...]:
    try:
        return parse_multipart(body, info.content_type)
    except ValueError as error:
        raise _BodyFramingError(error) from None


def _multipart_data(body: bytes, info: ResponseInfo) -> MultipartData[bytes]:
    return MultipartData(_multipart(body, info))


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
        for part in _multipart(body, info):
            if (name := part.name) is None or (plan := self._declared.get(name, self._additional)) is None:
                raise _InvalidBodyError(issue(code="multipart.undeclared", message="A form-data part is not declared"))
            if name in seen and not plan.repeated:
                raise _InvalidBodyError(
                    issue(code="multipart.duplicate", message="A form-data body repeats a single-valued member")
                )
            seen.add(name)
            try:
                value = plan.read(part)
            except PartSyntaxError as error:
                raise _InvalidBodyError(error.cause) from None
            except DATA_ERRORS as error:
                raise _BodyValueError(error) from None
            parts.append(DecodedPart(name, value, part.filename, part.content_type, part.headers))
        for plan in self._declared.values():
            if plan.required and plan.name not in seen:
                raise _BodyValueError(
                    issue(code="multipart.missing", message="A required form-data member has no part")
                )
        return MultipartData(tuple(parts))


class _Reader:
    __slots__ = ("additional", "additional_part", "fields", "kind", "parts")

    def __init__(
        self,
        kind: ReadKind,
        fields: tuple[FieldPlan, ...],
        additional: FieldPlan | None,
        parts: tuple[PartPlan, ...],
        additional_part: PartPlan | None,
    ) -> None:
        self.kind = kind
        self.fields = fields
        self.additional = additional
        self.parts = parts
        self.additional_part = additional_part

    def read(self, body: bytes, info: ResponseInfo) -> WireValue:
        match self.kind:
            case "json":
                return _json(body, info)
            case "text":
                return _text(body, info)
            case "multipart":
                parts = _multipart(body, info)
                try:
                    return decode_parts(parts, self.parts, self.additional_part)
                except (CodecError, ValueError) as error:
                    raise _InvalidBodyError(error) from None
            case _:
                pass
        try:
            return decode_form(body, self.fields, self.additional)
        except CodecError as error:
            raise _InvalidBodyError(error) from None


class _Native(Generic[T_co]):
    __slots__ = ("_codec", "_context", "_reader")

    def __init__(self, reader: _Reader, codec: Callable[[], InboundModelCodec[T_co]], context: CodecContext) -> None:
        self._reader = reader
        self._codec = codec
        self._context = context

    def __call__(self, body: bytes, info: ResponseInfo) -> T_co:
        wire = self._reader.read(body, info)
        try:
            return self._codec().decode(wire, self._context).require_model()
        except DATA_ERRORS as error:
            raise _BodyValueError(error) from None


class _Envelope(Generic[T]):
    __slots__ = ("_codec", "_context", "_reader")

    def __init__(self, reader: _Reader, codec: Callable[[], InboundEnvelopeCodec[T]], context: CodecContext) -> None:
        self._reader = reader
        self._codec = codec
        self._context = context

    def __call__(self, body: bytes, info: ResponseInfo) -> DecodedValue[T]:
        wire = self._reader.read(body, info)
        try:
            return self._codec().decode(wire, self._context)
        except DATA_ERRORS as error:
            raise _BodyValueError(error) from None


def model_branch(  # noqa: PLR0913
    status: str,
    media_type: str,
    kind: ReadKind,
    codec: Callable[[], InboundModelCodec[T]],
    context: CodecContext,
    *,
    fields: tuple[FieldPlan, ...] = (),
    additional: FieldPlan | None = None,
    parts: tuple[PartPlan, ...] = (),
    additional_part: PartPlan | None = None,
) -> Branch[T]:
    """Return a branch that decodes its body through a model codec into the native value."""
    reader = _Reader(kind, fields, additional, parts, additional_part)
    return Branch(status, media_type, _Native(reader, codec, context))


def envelope_branch(  # noqa: PLR0913
    status: str,
    media_type: str,
    kind: ReadKind,
    codec: Callable[[], InboundEnvelopeCodec[T]],
    context: CodecContext,
    *,
    fields: tuple[FieldPlan, ...] = (),
    additional: FieldPlan | None = None,
    parts: tuple[PartPlan, ...] = (),
    additional_part: PartPlan | None = None,
) -> Branch[DecodedValue[T]]:
    """Return a branch that decodes its body through a model codec into a model value or envelope."""
    reader = _Reader(kind, fields, additional, parts, additional_part)
    return Branch(status, media_type, _Envelope(reader, codec, context))


def wire_branch(status: str, media_type: str) -> Branch[WireValue]:
    """Return a JSON branch without a schema."""
    return Branch(status, media_type, _json)


def text_branch(status: str, media_type: str) -> Branch[str]:
    """Return a text branch without a schema."""
    return Branch(status, media_type, _text)


def form_branch(status: str, media_type: str) -> Branch[FormData]:
    """Return a URL-encoded branch without a schema, keeping pair order and repeated names."""
    return Branch(status, media_type, _pairs)


def multipart_branch(status: str, media_type: str) -> Branch[MultipartData[bytes]]:
    """Return a multipart branch without a schema, keeping each part's bytes, name, filename, and headers."""
    return Branch(status, media_type, _multipart_data)


def parts_branch(status: str, media_type: str, reader: PartsReader[T]) -> Branch[MultipartData[T]]:
    """Return a form-data branch whose schema has file parts, read part by part into their members' values."""
    return Branch(status, media_type, reader)


def binary_branch(status: str, media_type: str) -> Branch[bytes]:
    """Return a branch whose value is the body itself."""
    return Branch(status, media_type, _bytes)


def empty_branch(status: str) -> Branch[None]:
    """Return a branch without a body."""
    return Branch(status, None, _none)


def status_key(status: int, keys: Container[str]) -> str | None:
    """Return the declared response key a status selects: exact, then its hundreds range, then default."""
    exact, family = str(status), f"{status // 100}XX"
    return exact if exact in keys else family if family in keys else "default" if "default" in keys else None


def _grouped(branches: tuple[Branch[T], ...]) -> dict[str, tuple[Branch[T], ...]]:
    """Group branches by status key in declaration order."""
    return {
        key: tuple(branch for branch in branches if branch.status == key)
        for key in dict.fromkeys(branch.status for branch in branches)
    }


def _matching(received: str, branches: Sequence[Branch[T]]) -> Branch[T] | None:
    """Return the branch of a received media type: exact type, then parameters ignored, then type/*, then */*."""
    if found := next((branch for branch in branches if branch.media_type == received), None):
        return found
    if (winner := most_specific(received, (item.media_type for item in branches if item.media_type))) is None:
        return None
    return next(branch for branch in branches if branch.media_type == winner)


class ResponseDecoder(Generic[T_co, E_co]):
    """Dispatch a final response by status then media, decoding successes to T and failures to the error payload E."""

    __slots__ = (
        "_error_class",
        "_error_groups",
        "_errors",
        "_groups",
        "_head",
        "_keys",
        "_narrowed",
        "_permitted",
        "_success",
        "_successes",
        "accept",
    )

    def __init__(  # noqa: PLR0913
        self,
        success: tuple[Branch[T_co], ...],
        errors: tuple[Branch[E_co], ...],
        error_class: type[HTTPStatusError[E_co]],
        *,
        success_statuses: frozenset[int] = frozenset(),
        head: bool = False,
        keys: frozenset[str] | None = None,
        permitted: str | None = None,
    ) -> None:
        """Bind the success and failure branches, the operation's error class, and its extra success statuses.

        A narrowed decoder keeps the status keys of every success branch, so a status still selects its response, and
        permits a success body only of its concrete media type.
        """
        self._success = success
        self._errors = errors
        self._error_class = error_class
        self._successes = success_statuses
        self._head = head
        self._keys = frozenset(branch.status for branch in success) if keys is None else keys
        self._groups = _grouped(success)
        self._error_groups = _grouped(errors)
        self._narrowed: dict[str, ResponseDecoder[T_co, E_co]] = {}
        self._permitted = permitted
        media = dict.fromkeys(branch.media_type for branch in success if branch.media_type is not None)
        self.accept = None if head else permitted or ", ".join(media) or None

    def narrowed(self, operation_id: str | None, media_type: str) -> ResponseDecoder[T_co, E_co]:
        """Return this decoder with successes restricted to one concrete media type, which Accept then names.

        Each status keeps the branch the media type dispatches to, the most specific one, and its bodyless branch.
        """
        if (wanted := normalized(media_type)) is not None and (decoder := self._narrowed.get(wanted)) is not None:
            return decoder
        success = tuple(
            branch
            for group in self._groups.values()
            for winner in (_matching(wanted or "", group),)
            for branch in group
            if branch.media_type is None or branch is winner
        )
        if wanted is None or "*" in essence(wanted) or all(branch.media_type is None for branch in success):
            raise ConfigurationError(
                field_path=("response_media_type",), condition="undeclared", operation_id=operation_id
            )
        decoder = self._narrowed[wanted] = ResponseDecoder(
            success,
            self._errors,
            self._error_class,
            success_statuses=self._successes,
            head=self._head,
            keys=self._keys,
            permitted=wanted,
        )
        return decoder

    def success(self, status: int) -> bool:
        """Return whether a status is a typed success of this operation."""
        return _MIN_SUCCESS <= status <= _MAX_SUCCESS or status in self._successes

    def decode(
        self, info: ResponseInfo, body: bytes, *, truncated: bool = False, problem: BaseException | None = None
    ) -> T_co:
        """Return the success value of a complete response, or raise the typed failure of any other response.

        A problem that stopped reading an error body, such as a broken content coding, is kept as its decode error.
        """
        if self.success(info.status_code):
            return self._decoded(info, body, self._branch(info, body))
        raise self.failure(info, body, truncated=truncated, problem=problem)

    def failure(
        self, info: ResponseInfo, body: bytes, *, truncated: bool = False, problem: BaseException | None = None
    ) -> HTTPStatusError[E_co] | UnexpectedStatusError:
        """Return the typed failure of a response that is not a success: its HTTP error, or an unexpected status."""
        if _MIN_ERROR <= info.status_code <= _MAX_ERROR:
            return self._failure(info, body, truncated=truncated, problem=problem)
        return UnexpectedStatusError(
            info=info, body_bytes=body, truncated=truncated, call_id=info.call_id, cause=problem
        )

    def _bodyless(self, status: int) -> bool:
        return self._head or status in BODYLESS_STATUSES

    def _branch(self, info: ResponseInfo, body: bytes) -> Branch[T_co]:
        if (key := status_key(info.status_code, self._keys)) is None:
            raise UnexpectedStatusError(info=info, body_bytes=body, call_id=info.call_id)
        branch = _select(info, body, self._groups.get(key, ()), bodyless=self._bodyless(info.status_code))
        if (
            (permitted := self._permitted) is not None
            and branch.media_type is not None
            and (most_specific(info.content_type or "", (permitted,)) is None)
        ):
            raise UnexpectedMediaTypeError(
                info=info,
                actual_media_type=info.content_type,
                expected_media_types=(permitted,),
                body_bytes=body,
                call_id=info.call_id,
            )
        return branch

    @staticmethod
    def _decoded(info: ResponseInfo, body: bytes, branch: Branch[T]) -> T:
        try:
            return branch.decode(body, info)
        except _InvalidBodyError as error:
            raise error.failure(info, body) from None

    def _failure(
        self, info: ResponseInfo, body: bytes, *, truncated: bool, problem: BaseException | None
    ) -> HTTPStatusError[E_co]:
        data: E_co | None = None
        decoded = False
        key = status_key(info.status_code, self._error_groups.keys())
        declared = () if key is None else self._error_groups[key]
        if declared and not truncated:
            try:
                data = _select(info, body, declared, bodyless=self._bodyless(info.status_code)).decode(body, info)
                decoded = True
            except _InvalidBodyError as error:
                problem = error.cause
            except (BodyProtocolError, UnexpectedMediaTypeError) as error:
                problem = error
        return self._error_class(
            info=info,
            error_data=data,
            error_decoded=decoded,
            body_bytes=body,
            truncated=truncated,
            error_decode_error=problem,
            call_id=info.call_id,
        )


def _select(info: ResponseInfo, body: bytes, declared: Sequence[Branch[T]], *, bodyless: bool) -> Branch[T]:
    """Return the branch of a response among its status's declarations: its empty branch, or its media type's."""
    if bodyless or (declared and all(branch.media_type is None for branch in declared)):
        if body:
            raise BodyProtocolError(info=info, condition="forbidden_body", body_bytes=body, call_id=info.call_id)
        if (empty := next((branch for branch in declared if branch.media_type is None), None)) is None:
            raise BodyProtocolError(info=info, condition="missing_body", call_id=info.call_id)
        return empty
    if info.content_type is None or (found := _matching(info.content_type, declared)) is None:
        raise UnexpectedMediaTypeError(
            info=info,
            actual_media_type=info.content_type,
            expected_media_types=tuple(branch.media_type for branch in declared if branch.media_type is not None),
            body_bytes=body,
            call_id=info.call_id,
        )
    return found


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationPlan(Generic[T_co, E_co]):
    """Everything fixed about one operation: method, path, servers, parameters, body, and response decoding."""

    operation_id: str | None
    method: str
    path: str
    servers: tuple[ServerPlan, ...]
    responses: ResponseDecoder[T_co, E_co]
    parameters: tuple[ParameterSpec, ...] = ()
    body: RequestBody | None = None
    request_id_header: str | None = None
    response_media_type: str | None = None
    codecs: object = None
