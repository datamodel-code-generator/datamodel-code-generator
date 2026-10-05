"""Typed response header accessors of generated client operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, NoReturn

from typing_extensions import TypeVar

from ..model_codecs.parameters import ParameterFragment, RawParameter, decode_parameter
from ..model_codecs.unset import UNSET, Unset
from .errors import response_failure
from .operations import DATA_ERRORS, status_key

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..model_codecs.context import CodecContext
    from ..model_codecs.parameters import ParameterPlan
    from ..model_codecs.wire import WireValue
    from .errors import DecodeError
    from .operations import InboundModelCodec
    from .responses import ResponseInfo

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)
M_co = TypeVar("M_co", covariant=True)


def _header_failure(info: ResponseInfo, operation_id: str | None, cause: BaseException | None = None) -> DecodeError:
    """Return the failure of a declared response header that is missing, repeated, or invalid; no body is involved."""
    error = response_failure(info, "invalid_header", cause=cause)
    error.operation_id = operation_id
    return error


def required_header(info: ResponseInfo, operation_id: str | None) -> NoReturn:
    """Refuse a response that lacks a header its status declares required."""
    raise _header_failure(info, operation_id)


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


def native_value(codec: Callable[[], InboundModelCodec[T]], context: CodecContext) -> NativeValue[T]:
    """Return a decoder of a header or part value that constructs the native value."""
    return NativeValue(codec, context)


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
        """Return a header's value or what its absence yields, or raise the response's DecodeError."""
        key = status_key(info.status_code, self._keys)
        if (branch := self._headers.get(name.lower(), {}).get(key or "")) is None:
            raise _header_failure(info, self._operation_id)
        fragments = tuple(
            ParameterFragment(header.encode("latin-1"), value.encode()) for header, value in info.headers.items()
        )
        try:
            wire = decode_parameter(branch.plan, RawParameter(location="header", fragments=fragments))
            if isinstance(wire, Unset):
                return branch.missing(info, self._operation_id)
            return branch.decode(wire)
        except DATA_ERRORS as error:
            raise _header_failure(info, self._operation_id, error) from None
