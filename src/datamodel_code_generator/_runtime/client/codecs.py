"""Request codec facades and typed response header accessors of generated client operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, NoReturn, cast

from typing_extensions import Never, TypeVar

from ..model_codecs.errors import CodecSelectionError
from ..model_codecs.parameters import ParameterFragment, RawParameter, decode_parameter
from ..model_codecs.selectors import MediaSelector, RequestMedia, ResponseMedia, select_media
from ..model_codecs.unset import UNSET, Unset
from .errors import ResponseHeaderDecodeError
from .media import most_specific, normalized
from .operations import DATA_ERRORS, status_key

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..model_codecs.context import CodecContext
    from ..model_codecs.parameters import ParameterPlan
    from ..model_codecs.values import DecodedValue
    from ..model_codecs.wire import WireValue
    from .operations import InboundEnvelopeCodec, InboundModelCodec
    from .responses import ResponseInfo

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)
M_co = TypeVar("M_co", covariant=True)
BodyT_co = TypeVar("BodyT_co", covariant=True)
ParameterT_co = TypeVar("ParameterT_co", covariant=True)
PartT_co = TypeVar("PartT_co", covariant=True, default=Never)
RequestMediaT_co = TypeVar("RequestMediaT_co", covariant=True, default=Never)
ResponseMediaT_co = TypeVar("ResponseMediaT_co", covariant=True, default=Never)


class RequestCodecs(Generic[BodyT_co, ParameterT_co, PartT_co, RequestMediaT_co, ResponseMediaT_co]):
    """The outbound codecs and media selectors of one operation's request, selected by declaration.

    It codes the body media, parameters, and parts, and selects the concrete media type of a declared request or
    success response media type; only this operation's calls take its selectors.
    """

    __slots__ = ("_bodies", "_default", "_extras", "_parameters", "_parts", "_requests", "_responses")

    def __init__(  # noqa: PLR0913
        self,
        *,
        bodies: tuple[tuple[str, Callable[[], BodyT_co]], ...] = (),
        default: str | None = None,
        parameters: tuple[tuple[str, str, Callable[[], ParameterT_co]], ...] = (),
        parts: tuple[tuple[str, str, Callable[[], PartT_co] | None], ...] = (),
        extras: tuple[tuple[str, Callable[[], PartT_co]], ...] = (),
        requests: tuple[str, ...] = (),
        responses: tuple[str, ...] = (),
    ) -> None:
        """Keep the accessors of each declared media type, parameter, part, and media's extra parts, and the default.

        A part without an accessor is a file member, which has no codec and takes no extra part's codec either.
        `requests` and `responses` are the declared request and success response media types selectors choose among.
        """
        self._bodies = dict(bodies)
        self._default = default
        self._parameters = {(location, name): accessor for location, name, accessor in parameters}
        self._parts = {(media_type, name): accessor for media_type, name, accessor in parts}
        self._extras = dict(extras)
        self._requests = requests
        self._responses = responses

    def _body(self, media_type: str | MediaSelector | None) -> BodyT_co:
        wanted: str | None
        match media_type:
            case MediaSelector():
                wanted, _ = RequestMedia.chosen(media_type, self)
            case _:
                wanted = self._default if media_type is None else normalized(media_type)
        if (accessor := self._bodies.get(wanted or "")) is None:
            msg = "The operation declares no codec-bearing request body for that media type"
            raise CodecSelectionError(msg)
        return accessor()

    def _parameter(self, location: str, name: str) -> ParameterT_co:
        if (accessor := self._parameters.get((location, name))) is None:
            msg = f"The operation declares no codec-bearing {location} parameter {name!r}"
            raise CodecSelectionError(msg)
        return accessor()

    def _part(self, name: str, media_type: str | None) -> PartT_co:
        wanted = (self._default if media_type is None else normalized(media_type)) or ""
        if (accessor := self._parts.get((wanted, name), self._extras.get(wanted))) is None:
            msg = f"The operation declares no codec-bearing part {name!r} for that media type"
            raise CodecSelectionError(msg)
        return accessor()

    def request_media(self, declared_media: str, concrete_media: str) -> RequestMediaT_co:
        """Return the selector of a declared request media type for a concrete one, as the select method checks it.

        It raises CodecSelectionError when the concrete type does not dispatch to the declared one.
        """
        return self._request_media(declared_media, concrete_media)

    def _request_media(self, declared_media: str, concrete_media: str) -> RequestMediaT_co:
        declared, concrete = _chosen(self._requests, declared_media, concrete_media, winner=True)
        return cast("RequestMediaT_co", select_media(RequestMedia, declared, concrete, self))

    def _response_media(self, declared_media: str, concrete_media: str) -> ResponseMediaT_co:
        declared, concrete = _chosen(self._responses, declared_media, concrete_media, winner=False)
        return cast("ResponseMediaT_co", select_media(ResponseMedia, declared, concrete, self))


def request_media(codecs: object, declared_media: str, concrete_media: str) -> MediaSelector:
    """Return the selector an operation's request codecs make for a declared and a concrete media type."""
    owner = cast("RequestCodecs[object, object, object, MediaSelector, object]", codecs)
    return owner.request_media(declared_media, concrete_media)


def _chosen(
    declarations: tuple[str, ...], declared_media: str, concrete_media: str, *, winner: bool
) -> tuple[str, str]:
    """Return a declared media type and a concrete type it stands for, or raise CodecSelectionError.

    A request's concrete type must dispatch to the declared type among all of them; a response's must be in its range.
    """
    declared, concrete = normalized(declared_media), normalized(concrete_media)
    if (
        declared is None
        or concrete is None
        or declared not in declarations
        or "*" in concrete.partition(";")[0]
        or most_specific(concrete, declarations if winner else (declared,)) != declared
    ):
        msg = f"The declared media type {declared_media!r} does not stand for {concrete_media!r}"
        raise CodecSelectionError(msg)
    return declared, concrete


def required_header(info: ResponseInfo, operation_id: str | None) -> NoReturn:
    """Refuse a response that lacks a header its status declares required."""
    raise ResponseHeaderDecodeError(info=info, operation_id=operation_id, call_id=info.call_id)


def optional_header(_info: ResponseInfo, _operation_id: str | None) -> Unset:
    """Return UNSET for an optional header the response lacks."""
    return UNSET


@dataclass(frozen=True, slots=True, kw_only=True)
class HeaderBranch(Generic[T_co, M_co]):
    """One status's declaration of a header: its wire plan, its value decoder, and what its absence yields."""

    plan: ParameterPlan
    decode: Callable[[WireValue], T_co]
    missing: Callable[[ResponseInfo, str | None], M_co]


class NativeValue(Generic[T_co]):
    """Decode a received header or part value into its native value: by its schema, or through its converter alone."""

    __slots__ = ("_codec", "_context")

    def __init__(self, codec: Callable[[], InboundModelCodec[T_co]], context: CodecContext) -> None:
        """Bind the value's codec accessor and context."""
        self._codec = codec
        self._context = context

    def __call__(self, wire: WireValue) -> T_co:
        """Validate a wire value against its schema and construct its native value."""
        return self._codec().decode(wire, self._context).require_model()

    def convert(self, wire: WireValue) -> T_co:
        """Construct the native value of a wire value through its converter alone."""
        return self._codec().convert(wire, self._context)


class EnvelopeValue(Generic[T]):
    """Decode a received header or part value into its model value or envelope, always by its schema."""

    __slots__ = ("_codec", "_context")

    def __init__(self, codec: Callable[[], InboundEnvelopeCodec[T]], context: CodecContext) -> None:
        """Bind the value's codec accessor and context."""
        self._codec = codec
        self._context = context

    def __call__(self, wire: WireValue) -> DecodedValue[T]:
        """Validate a wire value against its schema and construct its model value or envelope."""
        return self._codec().decode(wire, self._context)

    convert = __call__


def native_value(codec: Callable[[], InboundModelCodec[T]], context: CodecContext) -> NativeValue[T]:
    """Return a decoder of a header or part value that constructs the native value."""
    return NativeValue(codec, context)


def envelope_value(codec: Callable[[], InboundEnvelopeCodec[T]], context: CodecContext) -> EnvelopeValue[T]:
    """Return a decoder of a header or part value that constructs a model value or envelope.

    An envelope keeps its strict contract, so it is validated against its schema even where values are converted.
    """
    return EnvelopeValue(codec, context)


class ResponseHeaders(Generic[T_co, M_co]):
    """Decode an operation's declared response headers by the status's declaration, without I/O or the response."""

    __slots__ = ("_headers", "_keys", "_operation_id")

    def __init__(
        self,
        operation_id: str | None,
        keys: frozenset[str],
        headers: tuple[tuple[str, tuple[tuple[str, HeaderBranch[T_co, M_co]], ...]], ...],
    ) -> None:
        """Keep every declared response key and, for each header name, the statuses that declare it."""
        self._operation_id = operation_id
        self._keys = keys
        self._headers = {name.lower(): dict(branches) for name, branches in headers}

    def decode(self, info: ResponseInfo, name: str) -> T_co | M_co:
        """Return a header's value or what its absence yields, or raise ResponseHeaderDecodeError."""
        key = status_key(info.status_code, self._keys)
        if (branch := self._headers.get(name.lower(), {}).get(key or "")) is None:
            raise ResponseHeaderDecodeError(info=info, operation_id=self._operation_id, call_id=info.call_id)
        fragments = tuple(
            ParameterFragment(header.encode("latin-1"), value.encode()) for header, value in info.headers.items()
        )
        try:
            wire = decode_parameter(branch.plan, RawParameter(location="header", fragments=fragments))
            if isinstance(wire, Unset):
                return branch.missing(info, self._operation_id)
            return branch.decode(wire)
        except DATA_ERRORS as error:
            raise ResponseHeaderDecodeError(
                info=info, operation_id=self._operation_id, call_id=info.call_id, cause=error
            ) from None
