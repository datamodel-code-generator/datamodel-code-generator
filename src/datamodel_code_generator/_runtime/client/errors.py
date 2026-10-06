"""Public exceptions of a generated client; their messages name only safe call metadata, never payloads."""

from __future__ import annotations

import re
from datetime import datetime  # noqa: TC003 - Public annotations support get_type_hints().
from enum import Enum
from typing import ClassVar, Final, Literal, TypeAlias, TypeGuard, get_args

from typing_extensions import TypedDict, TypeIs, Unpack

from ..protocols.references import OperationRef
from .responses import HeadersView, Response, ResponseInfo  # noqa: TC001 - Public annotations support get_type_hints().

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
    "body_not_replayable",
    "unsafe_operation",
    "server_delay_exceeds_limit",
    "deadline_insufficient",
    "client_closed",
    "cancelled",
    "decode_failure",
    "callback_failure",
]
IOPhase: TypeAlias = Literal["connect", "read", "write", "pool", "unknown"]
DeadlinePhase: TypeAlias = Literal[
    "encode", "auth", "limiter", "sleep", "send", "decode", "stream", "cleanup", "unknown"
]
AuthPhase: TypeAlias = Literal["connect", "read", "write", "pool", "validate", "unknown"]
AuthReason: TypeAlias = Literal[
    "provider_failed",
    "provider_closed",
    "token_expired",
    "invalid_expiry",
    "oauth_error",
    "timeout",
    "reauthorization_required",
    "signing_failed",
]
OAuthErrorCode: TypeAlias = Literal[
    "invalid_request",
    "invalid_client",
    "invalid_grant",
    "unauthorized_client",
    "unsupported_grant_type",
    "invalid_scope",
]
_StoreAction: TypeAlias = Literal[
    "get",
    "set",
    "delete",
]

_CONDITION: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")
_ACRONYM: Final = re.compile(r"([A-Z]+)([A-Z][a-z])")
OAUTH_ERROR_CODES: Final[tuple[str, ...]] = get_args(OAuthErrorCode)
MIN_STATUS: Final = 100
MAX_STATUS: Final = 599
_SERVER_ERROR: Final = 500
_CAMEL: Final = re.compile(r"([a-z0-9])([A-Z])")


class DeliveryState(Enum):
    """How far a request got: never sent, possibly sent, or answered with a response."""

    NOT_SENT = "NOT_SENT"
    MAYBE_SENT = "MAYBE_SENT"
    RESPONSE_STARTED = "RESPONSE_STARTED"


class _CallMetadata(TypedDict, total=False):
    delivery_state: DeliveryState | None
    operation_id: str | None
    call_id: str | None
    parent_session_id: str | None
    cause: BaseException | None
    secondary_errors: tuple[BaseException, ...]
    attempt_count: int
    elapsed: float
    completed_result: Response[object] | None


class ErrorMetadata(_CallMetadata, total=False):
    """The call metadata every SDK error accepts beside its own fields."""

    info: ResponseInfo | None


def _delivery_default(metadata: _CallMetadata, state: DeliveryState) -> None:
    """Give an error without a delivery state, whether absent or None, its constructor's default."""
    if metadata.get("delivery_state") is None:
        metadata["delivery_state"] = state


class SDKError(Exception):
    """Base of every exception the client raises, with the identifiers and measurements of the failed call.

    `reason` is a stable snake_case symbol; a success that a later hook failure interrupted stays in
    `completed_result`.
    """

    _reason_code: ClassVar[str | None] = None

    def __init__(  # noqa: PLR0913
        self,
        *,
        reason: str | None = None,
        delivery_state: DeliveryState | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        attempt_count: int = 0,
        elapsed: float = 0.0,
        completed_result: Response[object] | None = None,
    ) -> None:
        """Keep the call identifiers, the response metadata when one arrived, and the original cause.

        Without a delivery state, an error with a response is RESPONSE_STARTED and one without is NOT_SENT until the
        call that publishes it gives its own.
        """
        super().__init__()
        self.reason = reason
        self.delivery_state = (
            _error_delivery(delivery_state)
            if delivery_state is not None
            else DeliveryState.NOT_SENT
            if info is None
            else DeliveryState.RESPONSE_STARTED
        )
        self.operation_id = operation_id
        self.call_id = call_id
        self.parent_session_id = parent_session_id
        self.info = info
        self.cause = cause
        self.secondary_errors = tuple(secondary_errors)
        self.attempt_count = error_count(attempt_count, "attempt_count") if info is None else info.attempt_count
        self.elapsed = error_time(elapsed, "elapsed") if info is None else info.elapsed
        self.completed_result = completed_result

    @property
    def request_id(self) -> str | None:
        """Return the request identifier the response carried, or None without one."""
        return None if self.info is None else self.info.request_id

    @property
    def reason_code(self) -> str:
        """Return the class's fixed code, the stable reason, or the snake_case name of the exception class."""
        return (
            self._reason_code
            or self.reason
            or _CAMEL.sub(r"\1_\2", _ACRONYM.sub(r"\1_\2", type(self).__name__)).lower()
        )

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (("operation_id", self.operation_id), ("call_id", self.call_id), ("reason", self.reason))

    def __str__(self) -> str:
        """Name the class and safe call metadata only."""
        details = ", ".join(f"{name}={value!r}" for name, value in self._details() if value is not None)
        return f"{type(self).__name__}({details})"


def add_secondary(error: BaseException, *failures: BaseException) -> None:
    """Keep cleanup failures beside the error that is already propagating, never in its place.

    An SDK error lists them; any other error names each one in a note.
    """
    for failure in failures:
        if error is failure:
            continue
        if isinstance(error, SDKError):
            error.secondary_errors = (*error.secondary_errors, failure)
            continue
        note = f"Secondary cleanup failure: {type(failure).__name__}"
        if _is_notes(notes := error.__dict__.get("__notes__")):
            notes.append(note)
        else:
            error.__dict__["__notes__"] = [note]


def _is_notes(value: object) -> TypeIs[list[object]]:
    return isinstance(value, list)


def _condition(value: object) -> str:
    if not isinstance(value, str) or not _CONDITION.fullmatch(value):
        msg = "A reason must be an SDK-defined lowercase symbol"
        raise ValueError(msg)
    return value


def error_choice(value: object, choices: tuple[str, ...], field: str) -> None:
    """Refuse an error field value outside its declared choices."""
    if not isinstance(value, str) or value not in choices:
        msg = f"{field} must be one of its declared values"
        raise ValueError(msg)


def error_time(value: object, field: str, *, nonnegative: bool = True) -> float:
    """Return an error field duration as a finite float, refusing booleans and values outside its range."""
    match value:
        case bool():
            pass
        case int() | float():
            try:
                number = float(value)
            except OverflowError:
                pass
            else:
                if -float("inf") < number < float("inf") and (not nonnegative or number >= 0):
                    return number
        case _:
            pass
    msg = f"{field} must be a finite number in its permitted range"
    raise ValueError(msg)


def _error_status(value: object) -> int:
    if type(value) is not int or not MIN_STATUS <= value <= MAX_STATUS:
        msg = "status_code must be an HTTP status code"
        raise ValueError(msg)
    return value


def error_count(value: object, field: str) -> int:
    """Return an error field count, refusing booleans and negative or non-integer values."""
    if type(value) is not int or value < 0:
        msg = f"{field} must be a nonnegative integer"
        raise ValueError(msg)
    return value


def _error_delivery(value: object) -> DeliveryState:
    if not isinstance(value, DeliveryState):
        msg = "delivery_state must be a DeliveryState"
        raise ValueError(msg)  # noqa: TRY004 - Exception constructors reject invalid fields with ValueError.
    return value


def is_sequence(value: object) -> TypeIs[tuple[object, ...] | list[object]]:
    """Return whether a value is a tuple or a list."""
    return isinstance(value, (tuple, list))


def error_string(value: object, field: str, *, optional: bool = False) -> None:
    """Refuse an error field value that is not a string, or None where optional."""
    if isinstance(value, str) or (optional and value is None):
        return
    msg = f"{field} must be a string{' or None' if optional else ''}"
    raise ValueError(msg)


def _protocol_context(helper_id: object, operation: object) -> None:
    error_string(helper_id, "helper_id", optional=True)
    if operation is not None and not isinstance(operation, OperationRef):
        msg = "operation must be an OperationRef or None"
        raise ValueError(msg)


class ConfigurationError(SDKError):
    """A setting, argument, or call the client refused: invalid options, a closed client, or a forbidden redirect."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        field_path: tuple[str | int, ...] = (),
        reason: str = "invalid_value",
        source_uri: str | None = None,
        source_pointer: str | None = None,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        **metadata: Unpack[ErrorMetadata],
    ) -> None:
        """Keep where the setting lives, which rule it broke, and the helper that refused it."""
        _protocol_context(helper_id, operation)
        super().__init__(reason=_condition(reason), **metadata)
        self.reason: str = reason
        self.field_path = tuple(field_path)
        self.source_uri = source_uri
        self.source_pointer = source_pointer
        self.helper_id = helper_id
        self.operation = operation

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("field_path", ".".join(map(str, self.field_path)) or None))


class APIConnectionError(SDKError):
    """A classified I/O failure of the HTTP transport, with how far the request got."""

    def __init__(
        self,
        *,
        phase: IOPhase | DeadlinePhase = "unknown",
        reason: str | None = None,
        retry_stop_reason: RetryStopReason | None = None,
        **metadata: Unpack[ErrorMetadata],
    ) -> None:
        """Keep the delivery evidence and the phase that failed; without evidence the request may have been sent."""
        _delivery_default(metadata, DeliveryState.MAYBE_SENT)
        super().__init__(reason=reason, **metadata)
        self.phase: IOPhase | DeadlinePhase = phase
        self.retry_stop_reason: RetryStopReason | None = retry_stop_reason

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("delivery_state", self.delivery_state.value), ("phase", self.phase))


class APITimeoutError(APIConnectionError):
    """An I/O phase exceeded its own timeout, or the call or stream exhausted its monotonic deadline.

    A phase timeout keeps the cap that expired in `effective_timeout`; a deadline keeps its absolute monotonic
    `deadline_at` and the reason `deadline_exceeded`.
    """

    def __init__(
        self,
        *,
        phase: IOPhase | DeadlinePhase = "unknown",
        effective_timeout: float | None = None,
        deadline_at: float | None = None,
        reason: str | None = None,
        retry_stop_reason: RetryStopReason | None = None,
        **metadata: Unpack[ErrorMetadata],
    ) -> None:
        """Keep the expired phase cap or deadline beside the delivery evidence."""
        if reason == "deadline_exceeded" and deadline_at is None:
            msg = "A deadline_exceeded timeout needs its deadline_at"
            raise ValueError(msg)
        super().__init__(phase=phase, reason=reason, retry_stop_reason=retry_stop_reason, **metadata)
        self.effective_timeout = None if effective_timeout is None else error_time(effective_timeout, "timeout")
        self.deadline_at = None if deadline_at is None else error_time(deadline_at, "deadline_at", nonnegative=False)

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("effective_timeout", self.effective_timeout))


def is_deadline(error: object) -> TypeGuard[APITimeoutError]:
    """Return whether an error is an exhausted call or stream deadline rather than an I/O failure."""
    return isinstance(error, APITimeoutError) and error.deadline_at is not None


def is_phase_timeout(error: object) -> TypeGuard[APITimeoutError]:
    """Return whether an error is an I/O phase that exceeded its own timeout."""
    return isinstance(error, APITimeoutError) and error.deadline_at is None


def is_transport(error: object) -> TypeGuard[APIConnectionError]:
    """Return whether an error is a classified I/O failure of the transport, a phase timeout included."""
    return isinstance(error, APIConnectionError) and not is_deadline(error)


def is_http_error(error: object) -> TypeGuard[APIStatusError]:
    """Return whether an error is a final 4xx or 5xx response rather than an unexpected status."""
    return isinstance(error, APIStatusError) and error.reason != "unexpected_status"


def is_client_closed(error: object) -> TypeGuard[ConfigurationError]:
    """Return whether an error refused a call because its client or view is closing or closed."""
    return isinstance(error, ConfigurationError) and error.reason == "client_closed"


def is_redirect_refused(error: object) -> TypeGuard[ConfigurationError]:
    """Return whether an error is a redirect that cannot be followed safely."""
    return isinstance(error, ConfigurationError) and error.reason == "redirect_refused"


_CALL_STATES: Final = frozenset({"client_closed", "redirect_refused", "response_consumed"})


def is_auth_classified(error: object) -> bool:
    """Return whether a credential or signing callback's failure is already classified.

    It is when the callback raised an auth failure, or a refused setting rather than the state of a call it made.
    """
    return isinstance(error, AuthError) or (isinstance(error, ConfigurationError) and error.reason not in _CALL_STATES)


def is_hook_failure(error: object) -> TypeGuard[SDKError]:
    """Return whether an error is a hook's failure, which stops the call."""
    return type(error) is SDKError and error.reason == "hook_failed"


def redirect_refused(**metadata: Unpack[ErrorMetadata]) -> ConfigurationError:
    """Return the failure of a redirect that cannot be followed safely; no redirect body is retained."""
    _delivery_default(metadata, DeliveryState.RESPONSE_STARTED)
    return ConfigurationError(field_path=("redirects",), reason="redirect_refused", **metadata)


class APIStatusError(SDKError):
    """A final status the operation does not declare as a success, with its bounded body.

    `body` is the payload decoded with the operation's declared error schema for the status, or the bounded raw bytes
    when none is declared or it did not decode; the decode failure is then the cause.
    """

    info: ResponseInfo

    def __init__(  # noqa: PLR0913
        self,
        *,
        info: ResponseInfo,
        body: object = None,
        body_bytes: bytes = b"",
        truncated: bool = False,
        reason: str | None = None,
        retry_stop_reason: RetryStopReason | None = None,
        **metadata: Unpack[_CallMetadata],
    ) -> None:
        """Keep the response metadata, the decoded or raw body, and the bounded body bytes."""
        _delivery_default(metadata, DeliveryState.RESPONSE_STARTED)
        super().__init__(info=info, reason=reason, **metadata)
        self.body = body
        self.body_bytes = body_bytes
        self.truncated = truncated
        self.retry_stop_reason: RetryStopReason | None = retry_stop_reason

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


class BadRequestError(APIStatusError):
    """A final 400 response."""


class AuthenticationError(APIStatusError):
    """A final 401 response."""


class PermissionDeniedError(APIStatusError):
    """A final 403 response."""


class NotFoundError(APIStatusError):
    """A final 404 response."""


class ConflictError(APIStatusError):
    """A final 409 response."""


class UnprocessableEntityError(APIStatusError):
    """A final 422 response."""


class RateLimitError(APIStatusError):
    """A final 429 response."""


class InternalServerError(APIStatusError):
    """A final 5xx response."""


_STATUS_ERRORS: Final[dict[int, type[APIStatusError]]] = {
    400: BadRequestError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    422: UnprocessableEntityError,
    429: RateLimitError,
}


def status_error(status: int) -> type[APIStatusError]:
    """Return the exception class of a final status that the operation does not declare as a success."""
    return InternalServerError if status >= _SERVER_ERROR else _STATUS_ERRORS.get(status, APIStatusError)


class AuthError(SDKError):
    """Credential acquisition, a token exchange, or request signing failed; no credential material is kept.

    An OAuth rejection keeps the token endpoint's status and its standard error code, an expired token its expiry,
    and a timeout the cap that fired.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        reason: AuthReason,
        phase: AuthPhase = "unknown",
        status_code: int | None = None,
        oauth_error: OAuthErrorCode | None = None,
        expires_at: datetime | None = None,
        effective_timeout: float | None = None,
        **metadata: Unpack[ErrorMetadata],
    ) -> None:
        """Keep why credentials failed and the phase, never the provider's descriptions or secrets."""
        error_choice(reason, get_args(AuthReason), "reason")
        error_choice(phase, get_args(AuthPhase), "phase")
        if status_code is not None:
            _error_status(status_code)
        if oauth_error is not None:
            error_choice(oauth_error, OAUTH_ERROR_CODES, "oauth_error")
        _delivery_default(metadata, DeliveryState.MAYBE_SENT)
        super().__init__(reason=reason, **metadata)
        self.reason: AuthReason = reason
        self.phase: AuthPhase = phase
        self.status_code = status_code
        self.oauth_error: OAuthErrorCode | None = oauth_error
        self.expires_at = expires_at
        self.effective_timeout = None if effective_timeout is None else error_time(effective_timeout, "timeout")

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (
            *super()._details(),
            ("delivery_state", self.delivery_state.value),
            ("phase", self.phase),
            ("status_code", self.status_code),
            ("oauth_error", self.oauth_error),
        )


class DecodeError(SDKError):
    """A request argument its declared wire form cannot carry, or a response that cannot become its declared value.

    `location` names the failing argument path or header, never its value; a response keeps its bounded body.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        reason: str,
        direction: Literal["request", "response"] = "response",
        location: tuple[str | int, ...] = (),
        body_bytes: bytes = b"",
        truncated: bool = False,
        media_type: str | None = None,
        limit: int | None = None,
        observed: int | None = None,
        **metadata: Unpack[ErrorMetadata],
    ) -> None:
        """Keep which rule the body broke and where, with the bounded body of a response."""
        super().__init__(reason=_condition(reason), **metadata)
        self.reason: str = reason
        self.direction: Literal["request", "response"] = direction
        self.location = tuple(location)
        self.body_bytes = body_bytes
        self.truncated = truncated
        self.media_type = media_type
        self.limit = limit
        self.observed = observed

    def _details(self) -> tuple[tuple[str, object], ...]:
        if (info := self.info) is None:
            return (*super()._details(), ("location", ".".join(map(str, self.location)) or None))
        return (
            ("status_code", info.status_code),
            *super()._details(),
            ("location", ".".join(map(str, self.location)) or None),
            ("request_id", info.request_id),
        )


def body_failure(reason: str, cause: BaseException | None = None) -> DecodeError:
    """Return the failure of a request body that cannot be sent: changed, already read, or without its digest."""
    return DecodeError(reason=reason, direction="request", location=("body",), cause=cause)


def too_large(info: ResponseInfo, limit: int, observed: int, operation_id: str | None = None) -> DecodeError:
    """Return the failure of a body larger than its buffer limit; nothing is retried."""
    return DecodeError(
        reason="response_too_large",
        info=info,
        limit=limit,
        observed=observed,
        operation_id=operation_id,
        call_id=info.call_id,
        delivery_state=DeliveryState.RESPONSE_STARTED,
    )


def response_failure(
    info: ResponseInfo,
    reason: str,
    body: bytes = b"",
    cause: BaseException | None = None,
    *,
    media_type: str | None = None,
) -> DecodeError:
    """Return the decode failure of a received response, keeping its metadata and bounded body."""
    return DecodeError(
        reason=reason,
        info=info,
        body_bytes=body,
        call_id=info.call_id,
        cause=cause,
        delivery_state=DeliveryState.RESPONSE_STARTED,
        media_type=media_type,
    )


class ProtocolError(SDKError):
    """Base of failures in received protocol data of a helper."""

    def __init__(
        self,
        *,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        reason: str | None = None,
        **metadata: Unpack[ErrorMetadata],
    ) -> None:
        """Keep the helper context beside the shared call metadata."""
        _protocol_context(helper_id, operation)
        super().__init__(reason=reason, **metadata)
        self.helper_id = helper_id
        self.operation = operation


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
            "body",
            "headers",
            "keys",
            "signatures",
            "ack_buffer",
            "content_layers",
            "expanded_content",
        ],
        limit: int,
        observed: int,
        unit: Literal["bytes", "items"],
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        **metadata: Unpack[ErrorMetadata],
    ) -> None:
        """Keep which record overflowed, its limit, and how much arrived."""
        super().__init__(helper_id=helper_id, operation=operation, **metadata)
        self.kind = kind
        self.limit = limit
        self.observed = observed
        self.unit = unit

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("kind", self.kind), ("limit", self.limit), ("observed", self.observed))


class WebhookVerificationError(ProtocolError):
    """A webhook signature or authenticated fact that verification rejected before decoding its event."""

    def __init__(
        self,
        *,
        condition: Literal["malformed_signature", "invalid_signature", "missing_key", "timestamp_window"],
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        **metadata: Unpack[ErrorMetadata],
    ) -> None:
        """Keep only the safe rejection category and shared context, never signature or key material."""
        error_choice(
            condition,
            ("malformed_signature", "invalid_signature", "missing_key", "timestamp_window"),
            "condition",
        )
        super().__init__(helper_id=helper_id, operation=operation, **metadata)
        self.condition = condition

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("condition", self.condition))


class ProtocolStoreError(ProtocolError):
    """A helper store operation that failed; the original cause and entry identity remain available."""

    def __init__(
        self,
        *,
        action: _StoreAction,
        entry_id: str | None = None,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        **metadata: Unpack[ErrorMetadata],
    ) -> None:
        """Keep the store action and private entry identity alongside the helper context."""
        error_choice(action, get_args(_StoreAction), "action")
        error_string(entry_id, "entry_id", optional=True)
        super().__init__(helper_id=helper_id, operation=operation, **metadata)
        self.action = action
        self.entry_id = entry_id

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("action", self.action))


class UnsupportedContentCodingError(ProtocolError):
    """A response content coding that no decoder handles; its name stays out of the message."""

    def __init__(
        self,
        *,
        coding: str,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        **metadata: Unpack[ErrorMetadata],
    ) -> None:
        """Keep the coding the response named."""
        super().__init__(helper_id=helper_id, operation=operation, **metadata)
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
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        **metadata: Unpack[ErrorMetadata],
    ) -> None:
        """Keep the decoding layer, its encoded bytes so far, and the ratio its limit came from."""
        super().__init__(
            kind="expanded_content",
            limit=limit,
            observed=observed,
            unit="bytes",
            helper_id=helper_id,
            operation=operation,
            **metadata,
        )
        self.layer = layer
        self.encoded_bytes = encoded_bytes
        self.max_ratio = max_ratio


class RequestCancelledError(SDKError):
    """An explicit cancellation token stopped the call; native cancellation keeps its original type."""

    def __init__(
        self,
        *,
        source: Literal["cancel_token", "parent_cancel_token"],
        **metadata: Unpack[ErrorMetadata],
    ) -> None:
        """Keep the observed token source and request delivery evidence."""
        error_choice(source, ("cancel_token", "parent_cancel_token"), "source")
        super().__init__(**metadata)
        self.source: Literal["cancel_token", "parent_cancel_token"] = source

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("delivery_state", self.delivery_state.value))


class _AdapterError(SDKError):
    def __init__(self, **metadata: Unpack[ErrorMetadata]) -> None:
        """Keep how far the request got."""
        _delivery_default(metadata, DeliveryState.MAYBE_SENT)
        super().__init__(**metadata)

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("delivery_state", self.delivery_state.value))


class AdapterContractError(_AdapterError):
    """A transport that broke its contract, such as handing over a body something already read; nothing is retried."""


class AdapterExecutionError(_AdapterError):
    """A transport adapter that failed outside its classified I/O errors, such as a programming error; no retry."""


class CleanupError(SDKError):
    """A close that ran out of its cleanup time or failed to release something; closing again waits again."""

    def __init__(
        self,
        *,
        pending_calls: int = 0,
        pending_leases: int = 0,
        pending_providers: int = 0,
        timeout: float | None = None,
        **metadata: Unpack[ErrorMetadata],
    ) -> None:
        """Keep what is still unfinished and the cleanup time that ran out."""
        super().__init__(**metadata)
        self.pending_calls = pending_calls
        self.pending_leases = pending_leases
        self.pending_providers = pending_providers
        self.timeout = timeout

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (
            *super()._details(),
            ("pending_calls", self.pending_calls),
            ("pending_leases", self.pending_leases),
            ("timeout", self.timeout),
        )


class UnsupportedAsyncBackendError(SDKError):
    """An async client awaited outside asyncio, or on another event loop than its first call's; nothing is sent."""

    def __init__(
        self,
        *,
        detected_backend: str | None = None,
        expected_backend: Literal["asyncio"] = "asyncio",
        loop_mismatch: bool = False,
        **metadata: Unpack[ErrorMetadata],
    ) -> None:
        """Keep the backend found, the one required, and whether only the event loop differs."""
        super().__init__(**metadata)
        self.detected_backend = detected_backend
        self.expected_backend = expected_backend
        self.loop_mismatch = loop_mismatch

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("loop_mismatch", self.loop_mismatch))
