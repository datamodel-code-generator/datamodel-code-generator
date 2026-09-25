"""HTTP results, the declared responses of an operation, and the dispatch that checks what a handler returns."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Generic, Literal, Protocol, TypeAlias

from starlette.responses import Response
from typing_extensions import TypeVar

from ..model_codecs.errors import CodecError
from ..model_codecs.media import encode_json, normalize_media_type
from ..model_codecs.parameters import ParameterFragment, ParameterPlan, RawParameter, decode_parameter
from ..model_codecs.unset import UNSET, Unset
from ..model_codecs.values import ModelInput, ModelValue
from ..model_codecs.wire import checked_wire
from .errors import response_failure

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..model_codecs.context import CodecContext
    from ..model_codecs.wire import WireValue

BodyT_co = TypeVar("BodyT_co", covariant=True)
ResponseKind: TypeAlias = Literal["json", "text", "binary"]

_TOKEN: Final = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_FIELD_VALUE: Final = re.compile(r"[\t\x20-\x7e\x80-\xff]*")
_OWNED_HEADERS: Final = frozenset({"content-length", "content-type", "transfer-encoding"})
_EMPTY_STATUSES: Final = frozenset({204, 205, 304})
_MIN_STATUS: Final = 100
_MAX_STATUS: Final = 599
_MIN_CONTENT_STATUS: Final = 200
_PAIR: Final = 2


@dataclass(frozen=True, slots=True, kw_only=True)
class HTTPResult(Generic[BodyT_co]):
    """Send one declared response: its status, an optional payload, a media type, and extra headers."""

    status_code: int
    body: BodyT_co | Unset = UNSET
    media_type: str | None = None
    headers: tuple[tuple[str, str], ...] = ()


class PayloadCodec(Protocol):
    """Validate and encode one response payload in its bound direction."""

    def encode(self, value: object, context: CodecContext) -> WireValue:
        """Return the validated wire value of a payload."""
        ...


class HeaderCodec(Protocol):
    """Validate one response header value in its bound direction."""

    def from_wire(self, wire: WireValue, context: CodecContext) -> object:
        """Validate a wire value to send."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class MediaPlan:
    """One declared media type of a response, and the codec of its payload when the content has a schema."""

    media_type: str
    kind: ResponseKind
    codec: tuple[Callable[[], PayloadCodec], CodecContext] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class HeaderPlan:
    """One effective declared response header, with the plan and codec that validate its value."""

    name: str
    required: bool = False
    plan: ParameterPlan | None = None
    codec: tuple[Callable[[], HeaderCodec], CodecContext] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ResponsePlan:
    """One declared response: an exact status such as 200, a range such as 4XX, or default."""

    status: str
    media: tuple[MediaPlan, ...] = ()
    headers: tuple[HeaderPlan, ...] = ()

    def select(self, media_type: str | None) -> tuple[MediaPlan, str] | None:
        """Return the declared media for a media type, or the default media, together with the type to send."""
        if media_type is None:
            chosen = next((item for item in self.media if item.media_type == "application/json"), None) or next(
                (item for item in self.media if item.media_type.partition(";")[0].endswith("+json")), self.media[0]
            )
            return None if "*" in chosen.media_type else (chosen, chosen.media_type)
        try:
            requested = normalize_media_type(media_type)
        except ValueError:
            return None
        essence = requested.partition(";")[0]
        wildcard = f"{essence.partition('/')[0]}/*"
        for item in self.media:
            if item.media_type == requested or item.media_type.partition(";")[0] in {essence, wildcard, "*/*"}:
                return item, requested
        return None


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationResponses:
    """Every declared response of one operation, and the status and media a bare return value takes."""

    responses: tuple[ResponsePlan, ...]
    primary: tuple[int, str | None] | None = None
    head: bool = False
    _exact: dict[int, ResponsePlan] = field(init=False, repr=False, compare=False)
    _ranges: dict[int, ResponsePlan] = field(init=False, repr=False, compare=False)
    _default: ResponsePlan | None = field(init=False, repr=False, compare=False)
    primary_response: tuple[int, ResponsePlan, MediaPlan | None] | None = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Index the declared responses by exact status, status class, and default, and bind the primary."""
        exact = {int(item.status): item for item in self.responses if item.status.isdigit()}
        object.__setattr__(self, "_exact", exact)
        object.__setattr__(
            self, "_ranges", {int(item.status[0]): item for item in self.responses if item.status.endswith("XX")}
        )
        object.__setattr__(self, "_default", next((item for item in self.responses if item.status == "default"), None))
        if self.primary is None:
            object.__setattr__(self, "primary_response", None)
            return
        status, media_type = self.primary
        response = exact[status]
        media = None if media_type is None else next(item for item in response.media if item.media_type == media_type)
        object.__setattr__(self, "primary_response", (status, response, media))

    def find(self, status: int) -> ResponsePlan | None:
        """Return the response that declares a status: exact, then the same status class, then default."""
        return self._exact.get(status) or self._ranges.get(status // 100) or self._default


def dispatch(value: object, plan: OperationResponses) -> object:
    """Pass a bare value to FastAPI's response_model unchanged; check and encode an explicit wrapper."""
    if isinstance(value, (Response, HTTPResult, ModelValue, ModelInput)):
        return respond(value, plan)
    return value


def respond(value: object, plan: OperationResponses) -> Response:
    """Check a handler result against the declared responses and encode it as a Response."""
    try:
        if isinstance(value, Response):
            return _checked(value, plan)
        if isinstance(value, HTTPResult):
            return _result(value, plan)
        return _primary(value, plan)
    except CodecError as error:
        msg = "The handler result does not match its declared response"
        raise response_failure(msg) from error


def _checked(response: Response, plan: OperationResponses) -> Response:
    if (declared := plan.find(response.status_code)) is None:
        msg = f"The operation declares no {response.status_code} response"
        raise response_failure(msg)
    for name, value in response.raw_headers:
        if b"\r" in value or b"\n" in value or not _TOKEN.fullmatch(name.decode("latin-1")):
            msg = "The response has an invalid header"
            raise response_failure(msg)
    if (media_type := response.headers.get("content-type")) is not None and (
        not declared.media or declared.select(media_type) is None
    ):
        msg = f"The {response.status_code} response does not declare {media_type}"
        raise response_failure(msg)
    return response


def _result(result: HTTPResult[object], plan: OperationResponses) -> Response:
    status = result.status_code
    if type(status) is not int or not _MIN_STATUS <= status <= _MAX_STATUS:
        msg = "The result status must be an integer from 100 to 599"
        raise response_failure(msg)
    if (declared := plan.find(status)) is None:
        msg = f"The operation declares no {status} response"
        raise response_failure(msg)
    headers = _headers(result.headers, declared)
    if plan.head or status < _MIN_CONTENT_STATUS or status in _EMPTY_STATUSES or not declared.media:
        if not isinstance(result.body, Unset) or result.media_type is not None:
            msg = f"The {status} response carries no body"
            raise response_failure(msg)
        return _response(status, None, None, headers)
    if (selected := declared.select(result.media_type)) is None:
        msg = f"The {status} response does not declare the result media type"
        raise response_failure(msg)
    if isinstance(result.body, Unset):
        msg = f"The {status} response needs a body"
        raise response_failure(msg)
    media, media_type = selected
    return _response(status, media_type, _encode(media, result.body), headers)


def _primary(value: object, plan: OperationResponses) -> Response:
    if (primary := plan.primary_response) is None:
        msg = "The operation has no primary response for a bare return value"
        raise response_failure(msg)
    status, response, media = primary
    headers = _headers((), response)
    if media is None:
        if value is not None:
            msg = f"The {status} response carries no body"
            raise response_failure(msg)
        return _response(status, None, None, headers)
    return _response(status, media.media_type, _encode(media, value), headers)


def _json(value: object) -> WireValue:
    try:
        return checked_wire(value)
    except (TypeError, ValueError):
        msg = "The payload is not a JSON value"
        raise response_failure(msg) from None


def _encode(media: MediaPlan, value: object) -> bytes:
    codec = media.codec
    if media.kind == "json":
        return encode_json(_json(value) if codec is None else codec[0]().encode(value, codec[1]))
    payload = value if codec is None else codec[0]().encode(value, codec[1])
    if media.kind == "text" and isinstance(payload, str):
        return payload.encode()
    if media.kind == "binary" and isinstance(payload, bytes):
        return payload
    msg = f"A {media.kind} payload must be {'a string' if media.kind == 'text' else 'bytes'}"
    raise response_failure(msg)


def _pairs(headers: object) -> tuple[tuple[str, str], ...]:
    msg = "Result headers must be a tuple of name and value pairs"
    if not isinstance(headers, tuple):
        raise response_failure(msg)
    pairs: list[tuple[str, str]] = []
    for item in headers:
        if not (isinstance(item, tuple) and len(item) == _PAIR and all(isinstance(part, str) for part in item)):
            raise response_failure(msg)
        name, value = item
        if not _TOKEN.fullmatch(name) or not _FIELD_VALUE.fullmatch(value):
            msg = "A result header has an invalid name or value"
            raise response_failure(msg)
        if name.lower() in _OWNED_HEADERS:
            msg = f"The runtime sets the {name} header"
            raise response_failure(msg)
        pairs.append((name, value))
    return tuple(pairs)


def _headers(headers: object, declared: ResponsePlan) -> tuple[tuple[str, str], ...]:
    pairs = _pairs(headers)
    for header in declared.headers:
        key = header.name.lower()
        if not (values := [value for name, value in pairs if name.lower() == key]):
            if header.required:
                msg = f"The response requires the {header.name} header"
                raise response_failure(msg)
            continue
        if header.plan is None or header.codec is None:
            continue
        fragments = tuple(ParameterFragment(key.encode(), value.encode("latin-1")) for value in values)
        get, context = header.codec
        get().from_wire(
            checked_wire(decode_parameter(header.plan, RawParameter(location="header", fragments=fragments))), context
        )
    return pairs


def _response(
    status: int, media_type: str | None, content: bytes | None, headers: tuple[tuple[str, str], ...]
) -> Response:
    response = Response(content=content, status_code=status, media_type=media_type)
    for name, value in headers:
        response.headers.append(name, value)
    return response
