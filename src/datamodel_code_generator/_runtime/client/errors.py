"""Public exceptions of a generated client; their messages name only safe call metadata, never payloads."""

from __future__ import annotations

import re
from enum import Enum
from typing import TYPE_CHECKING, Final, Generic, Literal, TypeAlias

from typing_extensions import TypeVar

if TYPE_CHECKING:
    from .responses import HeadersView, ResponseInfo

E_co = TypeVar("E_co", covariant=True, default=object)

RetryStopReason: TypeAlias = Literal[
    "status_not_retryable",
    "transport_not_retryable",
    "auth_unrefreshable",
    "retry_owned_by_transport",
    "operation_never",
    "server_forbids_retry",
    "disabled",
    "max_retries_exhausted",
    "auth_recovery_exhausted",
    "auth_exchange_budget_exhausted",
    "body_not_replayable",
    "unsafe_operation",
    "network_budget_exhausted",
    "parent_budget_exhausted",
    "server_delay_exceeds_limit",
    "deadline_insufficient",
    "client_closed",
    "cancelled",
    "decode_failure",
    "callback_failure",
]
IOPhase: TypeAlias = Literal["connect", "read", "write", "pool", "unknown"]

_CONDITION: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")
_ACRONYM: Final = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL: Final = re.compile(r"([a-z0-9])([A-Z])")


class DeliveryState(Enum):
    """How far a request got: never sent, possibly sent, or answered with a response."""

    NOT_SENT = "NOT_SENT"
    MAYBE_SENT = "MAYBE_SENT"
    RESPONSE_STARTED = "RESPONSE_STARTED"


class SDKError(Exception):
    """Base of every exception the client raises, with the identifiers and metadata of the failed call."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the call identifiers, the response metadata when one arrived, and the original cause."""
        super().__init__()
        self.operation_id = operation_id
        self.call_id = call_id
        self.parent_session_id = parent_session_id
        self.info = info
        self.cause = cause
        self.secondary_errors = tuple(secondary_errors)

    @property
    def reason_code(self) -> str:
        """Return the snake_case name of the exception class."""
        return _CAMEL.sub(r"\1_\2", _ACRONYM.sub(r"\1_\2", type(self).__name__)).lower()

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (("operation_id", self.operation_id), ("call_id", self.call_id))

    def __str__(self) -> str:
        """Name the class and safe call metadata only."""
        details = ", ".join(f"{name}={value!r}" for name, value in self._details() if value is not None)
        return f"{type(self).__name__}({details})"


def _condition(value: str) -> str:
    if not _CONDITION.fullmatch(value):
        msg = "A condition must be an SDK-defined lowercase symbol"
        raise ValueError(msg)
    return value


class ConfigurationError(SDKError):
    """A setting or argument the client rejected before sending anything."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        field_path: tuple[str, ...] = (),
        condition: str = "invalid_value",
        source_uri: str | None = None,
        source_pointer: str | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep where the setting lives and which rule it broke."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.field_path = tuple(field_path)
        self.condition = _condition(condition)
        self.source_uri = source_uri
        self.source_pointer = source_pointer

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("field_path", ".".join(self.field_path) or None), ("condition", self.condition))


class RequestEncodingError(SDKError):
    """An argument that its declared wire form cannot carry; the location never includes the value."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        location: tuple[str | int, ...] = (),
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the argument path that failed."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.location = tuple(location)

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("location", ".".join(map(str, self.location)) or None))


class TransportError(SDKError):
    """A classified I/O failure of the HTTP transport, with how far the request got."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        delivery_state: DeliveryState,
        phase: IOPhase = "unknown",
        retry_stop_reason: RetryStopReason | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the delivery evidence and the I/O phase that failed."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.delivery_state = delivery_state
        self.phase = phase
        self.retry_stop_reason = retry_stop_reason

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("delivery_state", self.delivery_state.value), ("phase", self.phase))


class HTTPStatusError(SDKError, Generic[E_co]):
    """A final 4xx or 5xx response, with its bounded body and the decoded error payload when one is declared."""

    info: ResponseInfo

    def __init__(  # noqa: PLR0913
        self,
        *,
        info: ResponseInfo,
        error_data: E_co | None = None,
        error_decoded: bool = False,
        body_bytes: bytes = b"",
        truncated: bool = False,
        error_decode_error: BaseException | None = None,
        retry_stop_reason: RetryStopReason | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the response metadata, the bounded body, and the error payload or why it did not decode."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self._error_data = error_data
        self.error_decoded = error_decoded
        self.body_bytes = body_bytes
        self.truncated = truncated
        self.error_decode_error = error_decode_error
        self.retry_stop_reason = retry_stop_reason

    @property
    def error_data(self) -> E_co | None:
        """Return the decoded error payload, or None when none was declared or decoded."""
        return self._error_data

    @property
    def status_code(self) -> int:
        """Return the final status code."""
        return self.info.status_code

    @property
    def headers(self) -> HeadersView:
        """Return the final response headers."""
        return self.info.headers

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (
            ("status_code", self.info.status_code),
            *super()._details(),
            ("request_id", self.info.request_id),
            ("retry_stop_reason", self.retry_stop_reason),
        )


class UnexpectedStatusError(SDKError):
    """A final status the operation does not declare as a success or an error, such as an undeclared 3xx."""

    info: ResponseInfo

    def __init__(  # noqa: PLR0913
        self,
        *,
        info: ResponseInfo,
        body_bytes: bytes = b"",
        truncated: bool = False,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the response metadata and the bounded body."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.body_bytes = body_bytes
        self.truncated = truncated

    @property
    def status_code(self) -> int:
        """Return the final status code."""
        return self.info.status_code

    @property
    def headers(self) -> HeadersView:
        """Return the final response headers."""
        return self.info.headers

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (("status_code", self.info.status_code), *super()._details(), ("request_id", self.info.request_id))


class ResponseDecodeError(SDKError):
    """A successful response whose body could not become the declared value."""

    info: ResponseInfo

    def __init__(  # noqa: PLR0913
        self,
        *,
        info: ResponseInfo,
        body_bytes: bytes = b"",
        truncated: bool = False,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the response metadata, the bounded body, and why it did not decode."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.body_bytes = body_bytes
        self.truncated = truncated

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (("status_code", self.info.status_code), *super()._details(), ("request_id", self.info.request_id))


class UnexpectedMediaTypeError(ResponseDecodeError):
    """A successful response without a Content-Type, or with one the operation does not declare."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        info: ResponseInfo,
        actual_media_type: str | None,
        expected_media_types: tuple[str, ...],
        body_bytes: bytes = b"",
        truncated: bool = False,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the received media type and the declared ones."""
        super().__init__(
            info=info,
            body_bytes=body_bytes,
            truncated=truncated,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.actual_media_type = actual_media_type
        self.expected_media_types = tuple(expected_media_types)


class BodyProtocolError(ResponseDecodeError):
    """A body where none is allowed, a missing body, or broken framing."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        info: ResponseInfo,
        condition: Literal["forbidden_body", "missing_body", "invalid_framing", "invalid_part"],
        body_bytes: bytes = b"",
        truncated: bool = False,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep which structural rule the response broke."""
        super().__init__(
            info=info,
            body_bytes=body_bytes,
            truncated=truncated,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.condition = condition


class DecodeError(ResponseDecodeError):
    """A body that is not valid JSON, text, or the syntax of its media type."""


class ResponseValidationError(ResponseDecodeError):
    """A syntactically valid body that the schema or the native model rejects; nothing falls back to raw data."""


class ResponseHeaderDecodeError(ResponseDecodeError):
    """A declared response header that is missing, repeated, or invalid; no body is involved."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        info: ResponseInfo,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the response metadata and the cause, with an empty body."""
        super().__init__(
            info=info,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            cause=cause,
            secondary_errors=secondary_errors,
        )


class ResponseTooLargeError(SDKError):
    """A body larger than its buffer limit; nothing is retried."""

    info: ResponseInfo

    def __init__(  # noqa: PLR0913
        self,
        *,
        info: ResponseInfo,
        representation: Literal["decoded", "content_coded"],
        limit: int,
        observed_bytes: int,
        body_prefix: bytes = b"",
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the limit, how many bytes arrived, and the bounded prefix."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.representation = representation
        self.limit = limit
        self.observed_bytes = observed_bytes
        self.body_prefix = body_prefix


class ProtocolError(SDKError):
    """Base of failures in received protocol data, such as a content coding that does not decode."""


class ProtocolDataError(ProtocolError):
    """Received data that is missing, null, of another type or value, malformed, or inconsistent."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        condition: Literal["missing", "null", "type", "value", "malformed", "inconsistent"] = "value",
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep which rule the received data broke."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.condition = condition

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("condition", self.condition))


class ProtocolSizeError(ProtocolError):
    """A received record over the limit of its buffer; the oversized value never reaches the caller."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        kind: Literal[
            "page",
            "cursor",
            "line",
            "event",
            "message",
            "checkpoint",
            "body",
            "headers",
            "keys",
            "signatures",
            "ack_buffer",
            "part_manifest",
            "content_layers",
            "expanded_content",
        ],
        limit: int,
        observed: int,
        unit: Literal["bytes", "items"],
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep which record overflowed, its limit, and how much arrived."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.kind = kind
        self.limit = limit
        self.observed = observed
        self.unit = unit

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("kind", self.kind), ("limit", self.limit), ("observed", self.observed))


class UnsupportedContentCodingError(ProtocolError):
    """A response content coding that no decoder handles; its name stays out of the message."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        coding: str,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the coding the response named."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.coding = coding


class DecompressionLimitError(ProtocolSizeError):
    """A content coding that expands beyond max(1 MiB, its encoded bytes times the ratio); the response is closed."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        layer: int,
        encoded_bytes: int,
        max_ratio: float,
        limit: int,
        observed: int,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the decoding layer, its encoded bytes so far, and the ratio its limit came from."""
        super().__init__(
            kind="expanded_content",
            limit=limit,
            observed=observed,
            unit="bytes",
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.layer = layer
        self.encoded_bytes = encoded_bytes
        self.max_ratio = max_ratio
