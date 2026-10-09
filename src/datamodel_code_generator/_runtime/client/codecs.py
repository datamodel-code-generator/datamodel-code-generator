"""Typed response header accessors of generated client operations, and the model codecs their bindings call."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, NoReturn, cast

from typing_extensions import TypeVar

from ..model_codecs.media import media_kind
from ..model_codecs.parameter_reads import ParameterFragment, RawParameter, decode_parameter
from ..model_codecs.unset import UNSET
from .errors import response_failure
from .operations import PARSE_ERRORS, status_key

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..model_codecs.parameters import ParameterPlan
    from .errors import DecodeError
    from .operations import InboundModelCodec
    from .responses import ResponseInfo

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)
M_co = TypeVar("M_co", covariant=True)


def _header_failure(
    info: ResponseInfo, operation_id: str | None, name: str, cause: BaseException | None = None
) -> DecodeError:
    """Return the failure of a declared response header that is missing, repeated, or invalid, located at its name."""
    error = response_failure(info, "invalid_header", cause=cause)
    error.operation_id = operation_id
    error.location = ("header", name)
    return error


def required_header(info: ResponseInfo, operation_id: str | None, name: str) -> NoReturn:
    """Refuse a response that lacks a header its status declares required."""
    raise _header_failure(info, operation_id, name)


def optional_header(_info: ResponseInfo, _operation_id: str | None, _name: str) -> UNSET:
    """Return UNSET for an optional header the response lacks."""
    return UNSET


@dataclass(frozen=True, slots=True, kw_only=True)
class HeaderBranch(Generic[T_co, M_co]):
    """One status's declaration of a header: its wire plan, the codec of its value, and what its absence yields."""

    plan: ParameterPlan
    codec: InboundModelCodec[T_co]
    missing: Callable[[ResponseInfo, str | None, str], M_co]


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
        """Return a header's value or what its absence yields, or raise the response's DecodeError.

        A single-valued header that the response repeats is invalid; arrays and objects combine every occurrence.
        """
        key = status_key(info.status_code, self._keys)
        if (branch := self._headers.get(name.lower(), {}).get(key or "")) is None:
            raise _header_failure(info, self._operation_id, name)
        single = branch.plan.content_media_type is not None or branch.plan.shape == "scalar"
        if single and len(info.headers.get_all(name)) > 1:
            raise _header_failure(info, self._operation_id, name)
        fragments = tuple(
            ParameterFragment(header.encode("latin-1"), value.encode()) for header, value in info.headers.items()
        )
        codec = branch.codec
        errors: tuple[type[Exception], ...] = (*codec.errors, *PARSE_ERRORS)
        try:
            wire = decode_parameter(branch.plan, RawParameter(location="header", fragments=fragments))
            if wire is UNSET:
                return branch.missing(info, self._operation_id, name)
            if media_kind(branch.plan.content_media_type or "") == "json":
                return codec.decode(cast("str", wire).encode())
            return codec.text(wire)
        except errors as error:
            raise _header_failure(info, self._operation_id, name, error) from None
