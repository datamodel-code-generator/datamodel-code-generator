"""Public exceptions of a generated client; their messages name only safe call metadata, never payloads."""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import ClassVar, Final, Generic, Literal, TypeAlias, get_args

from typing_extensions import TypeIs, TypeVar

from ..model_codecs.unset import UNSET, Unset
from ..protocols.references import OperationRef
from .responses import HeadersView, Response, ResponseInfo  # noqa: TC001 - Public annotations support get_type_hints().
from .scopes import scope_tuple

E_co = TypeVar("E_co", covariant=True, default=object)
T_co = TypeVar("T_co", covariant=True, default=object)

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
DeadlinePhase: TypeAlias = Literal[
    "encode", "auth", "limiter", "sleep", "send", "decode", "stream", "cleanup", "unknown"
]
AuthPhase: TypeAlias = Literal[
    "admission", "load", "connect", "read", "write", "pool", "validate", "store", "wait", "unknown"
]
OAuthErrorCode: TypeAlias = Literal[
    "invalid_request",
    "invalid_client",
    "invalid_grant",
    "unauthorized_client",
    "unsupported_grant_type",
    "invalid_scope",
]
AuthTimeoutKind: TypeAlias = Literal["phase", "provider"]
AuthLimitKind: TypeAlias = Literal["pending_refreshes", "waiters"]
AuthBudgetKind: TypeAlias = Literal["auth_exchange", "network", "parent_network"]
AuthFailureKind: TypeAlias = Literal[
    "transport", "deadline", "http_status", "malformed_response", "invalid_token_response", "stopped"
]
ReauthorizationCondition: TypeAlias = Literal[
    "invalid_grant", "missing_token", "no_refresh_token", "unusable_loaded_token"
]
TokenLoadPurpose: TypeAlias = Literal["initial_load", "before_refresh", "invalid_grant_reload", "reload"]
TokenStoreAction: TypeAlias = Literal["store", "retry_store", "replace", "load"]
TokenPersistencePurpose: TypeAlias = Literal[TokenLoadPurpose, "store", "retry_store", "replace"]
PERSISTENCE_PURPOSES: Final = get_args(TokenPersistencePurpose)
AuthAction: TypeAlias = Literal[
    "get",
    "refresh",
    "retry_store",
    "replace_token_set",
    "reload_token_set",
    "exchange_code",
    "begin",
    "poll",
    "close",
    "aclose",
]
_ProtocolCondition: TypeAlias = Literal[
    "unknown_field",
    "invalid_value",
    "missing_metadata",
    "missing_adapter",
    "wrong_capability",
    "security_partition",
    "binding_mismatch",
]
_StoreAction: TypeAlias = Literal[
    "lookup",
    "fingerprint_vary",
    "compare_exchange",
    "delete",
    "invalidate",
    "claim",
    "put",
    "get",
    "open",
    "read",
    "close",
    "purge_terminal",
    "admit",
    "record",
    "reset",
    "snapshot",
]

_CONDITION: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")
_ACRONYM: Final = re.compile(r"([A-Z]+)([A-Z][a-z])")
OAUTH_ERROR_CODES: Final[tuple[str, ...]] = get_args(OAuthErrorCode)
MIN_STATUS: Final = 100
MAX_STATUS: Final = 599
_CAMEL: Final = re.compile(r"([a-z0-9])([A-Z])")


class DeliveryState(Enum):
    """How far a request got: never sent, possibly sent, or answered with a response."""

    NOT_SENT = "NOT_SENT"
    MAYBE_SENT = "MAYBE_SENT"
    RESPONSE_STARTED = "RESPONSE_STARTED"


class SDKError(Exception):
    """Base of every exception the client raises, with the identifiers and metadata of the failed call."""

    __slots__ = ("_counters",)

    _counters: tuple[int, int, int, int, int, int, tuple[str, ...], int, int | None]
    _reason_code: ClassVar[str | None] = None

    def __init__(  # noqa: PLR0913
        self,
        *,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the call identifiers, the response metadata when one arrived, and the original cause."""
        super().__init__()
        self.operation_id = operation_id
        self.call_id = call_id
        self.parent_session_id = parent_session_id
        self.info = info
        self.cause = cause
        self.secondary_errors = tuple(secondary_errors)

        set_error_counters(
            self,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )

    @property
    def resource_attempt_count(self) -> int:
        """Return the resource attempts started."""
        return self._counters[0]

    @property
    def redirect_count(self) -> int:
        """Return the redirect hops followed."""
        return self._counters[1]

    @property
    def auth_exchange_count(self) -> int:
        """Return the authentication exchanges sent."""
        return self._counters[2]

    @property
    def network_send_count(self) -> int:
        """Return the transport sends started."""
        return self._counters[3]

    @property
    def network_send_budget_used(self) -> int:
        """Return the network send slots reserved."""
        return self._counters[4]

    @property
    def auth_exchange_budget_used(self) -> int:
        """Return the authentication exchange slots reserved."""
        return self._counters[5]

    @property
    def auth_refresh_ids(self) -> tuple[str, ...]:
        """Return the related authentication refresh identifiers."""
        return self._counters[6]

    @property
    def auth_refresh_pending(self) -> int:
        """Return the related authentication refreshes still pending."""
        return self._counters[7]

    @property
    def wire_send_count(self) -> int | None:
        """Return the wire sends observed, or None without sufficient adapter evidence."""
        return self._counters[8]

    @property
    def reason_code(self) -> str:
        """Return the snake_case name of the exception class."""
        return self._reason_code or _CAMEL.sub(r"\1_\2", _ACRONYM.sub(r"\1_\2", type(self).__name__)).lower()

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (("operation_id", self.operation_id), ("call_id", self.call_id))

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


def set_error_counters(  # noqa: PLR0913
    error: SDKError,
    *,
    resource_attempt_count: int = 0,
    redirect_count: int = 0,
    auth_exchange_count: int = 0,
    network_send_count: int = 0,
    network_send_budget_used: int = 0,
    auth_exchange_budget_used: int = 0,
    auth_refresh_ids: tuple[str, ...] = (),
    auth_refresh_pending: int = 0,
    wire_send_count: int | None = None,
) -> None:
    """Finalize an error's readonly counter snapshot before publishing it."""
    counters = (
        error_count(resource_attempt_count, "resource_attempt_count"),
        error_count(redirect_count, "redirect_count"),
        error_count(auth_exchange_count, "auth_exchange_count"),
        error_count(network_send_count, "network_send_count"),
        error_count(network_send_budget_used, "network_send_budget_used"),
        error_count(auth_exchange_budget_used, "auth_exchange_budget_used"),
        _error_ids(auth_refresh_ids),
        error_count(auth_refresh_pending, "auth_refresh_pending"),
        _error_optional_count(wire_send_count, "wire_send_count"),
    )
    object.__setattr__(error, "_counters", counters)  # noqa: PLC2801 - Finalize the readonly snapshot.


def _condition(value: object) -> str:
    if not isinstance(value, str) or not _CONDITION.fullmatch(value):
        msg = "A condition must be an SDK-defined lowercase symbol"
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


def _error_optional_count(value: object, field: str) -> int | None:
    return None if value is None else error_count(value, field)


def _error_delivery(value: object) -> DeliveryState:
    if not isinstance(value, DeliveryState):
        msg = "delivery_state must be a DeliveryState"
        raise ValueError(msg)  # noqa: TRY004 - Exception constructors reject invalid fields with ValueError.
    return value


def is_sequence(value: object) -> TypeIs[tuple[object, ...] | list[object]]:
    """Return whether a value is a tuple or a list."""
    return isinstance(value, (tuple, list))


def _error_ids(value: object) -> tuple[str, ...]:
    if is_sequence(value) and len(identifiers := tuple(item for item in value if isinstance(item, str))) == len(value):
        return identifiers
    msg = "auth_refresh_ids must contain only strings"
    raise ValueError(msg)


def _error_identifier(value: object, field: str) -> str | None:
    if value is None or isinstance(value, str):
        return value
    msg = f"{field} must be a string or None"
    raise ValueError(msg)


def _error_expiry(value: object) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    msg = "expires_at must be a datetime or None"
    raise ValueError(msg)


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
    """A setting or argument the client rejected before sending anything."""

    _shows_field_path: ClassVar[bool] = True

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
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep where the setting lives and which rule it broke."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self.field_path = tuple(field_path)
        self.condition = _condition(condition)
        self.source_uri = source_uri
        self.source_pointer = source_pointer

    def _details(self) -> tuple[tuple[str, object], ...]:
        path = (("field_path", ".".join(self.field_path) or None),) if self._shows_field_path else ()
        return (*super()._details(), *path, ("condition", self.condition))


class AuthConfigurationError(ConfigurationError):
    """Authentication settings are invalid before credential acquisition or sending."""

    _shows_field_path = False


class SigningConfigurationError(ConfigurationError):
    """Signing metadata or body capabilities cannot satisfy the configured signer."""

    _shows_field_path = False


class ProtocolConfigurationError(ConfigurationError):
    """Missing or inconsistent helper configuration, rejected before the helper performs I/O."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        field_path: tuple[str, ...],
        condition: _ProtocolCondition,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        source_uri: str | None = None,
        source_pointer: str | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the configuration location, safe rejection category, and helper context."""
        error_choice(
            condition,
            (
                "unknown_field",
                "invalid_value",
                "missing_metadata",
                "missing_adapter",
                "wrong_capability",
                "security_partition",
                "binding_mismatch",
            ),
            "condition",
        )
        _protocol_context(helper_id, operation)
        super().__init__(
            field_path=field_path,
            condition=condition,
            source_uri=source_uri,
            source_pointer=source_pointer,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.helper_id = helper_id
        self.operation = operation

    def _details(self) -> tuple[tuple[str, object], ...]:
        return tuple((name, value) for name, value in super()._details() if name != "field_path")


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
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the argument path that failed."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self.location = tuple(location)

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("location", ".".join(map(str, self.location)) or None))


BodySourceKind: TypeAlias = Literal["bytes", "file", "stream", "factory", "multipart"]


class BodyNotReplayableError(SDKError):
    """A body that cannot be sent again: a one-shot source already read, or one another call is reading."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        source_kind: BodySourceKind,
        condition: Literal["one_shot", "consumed", "same_attempt", "concurrent", "digest_unavailable", "not_seekable"],
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the kind of body source and why it cannot be sent."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self.source_kind: BodySourceKind = source_kind
        self.condition = condition

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("source_kind", self.source_kind), ("condition", self.condition))


class BodyChangedError(SDKError):
    """A body that differs from what its source declared or first showed; its bytes are never sent again."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        source_kind: Literal["file", "factory", "multipart"],
        check: Literal["stat", "length", "fingerprint", "digest"],
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the kind of body source and the check that found the difference."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self.source_kind: Literal["file", "factory", "multipart"] = source_kind
        self.check = check

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("source_kind", self.source_kind), ("check", self.check))


class BodyFactoryError(SDKError):
    """A body factory, file, or stream that failed while it was opened or read; the cause is its exception."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        attempt_index: int | None = None,
        hop_index: int | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the attempt and redirect hop whose body failed."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self.attempt_index = attempt_index
        self.hop_index = hop_index

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("attempt_index", self.attempt_index), ("hop_index", self.hop_index))


class SigningExecutionError(SDKError):
    """A signing callback failed before the request was sent."""

    __slots__ = ("_signer_index",)

    def __init__(  # noqa: PLR0913
        self,
        *,
        signer_index: int | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the signer position and original callback failure without retaining signing inputs."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self._signer_index = _error_optional_count(signer_index, "signer_index")

    @property
    def signer_index(self) -> int | None:
        """Return the failed signer's zero-based position, when known."""
        return self._signer_index

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("signer_index", self.signer_index))


class AuthRefreshError(SDKError):
    """Credential acquisition failed with safe provider state and observed delivery evidence."""

    __slots__ = ("_delivery_state", "_phase", "_provider_id", "_refresh_id", "_state")

    _states: ClassVar[tuple[str, ...]] = (
        "UNKNOWN",
        "UNINITIALIZED",
        "LOAD_FAILED",
        "READY",
        "PERSIST_PENDING",
        "EXCHANGE_REJECTED",
        "UNCERTAIN",
        "REAUTH_REQUIRED",
        "CLOSING",
        "CLOSED",
        "CREATED",
        "EXCHANGING",
        "SUCCEEDED",
        "FAILED_NOT_SENT",
    )

    def __init__(  # noqa: PLR0913
        self,
        *,
        provider_id: str | None = None,
        refresh_id: str | None = None,
        state: str = "UNKNOWN",
        delivery_state: DeliveryState = DeliveryState.MAYBE_SENT,
        phase: AuthPhase = "unknown",
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep provider identifiers, the SDK state, and the phase without exposing credential material."""
        error_choice(state, self._states, "state")
        error_choice(
            phase,
            get_args(AuthPhase),
            "phase",
        )
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self._provider_id = _error_identifier(provider_id, "provider_id")
        self._refresh_id = _error_identifier(refresh_id, "refresh_id")
        self._state = state
        self._delivery_state = _error_delivery(delivery_state)
        self._phase: AuthPhase = phase

    @property
    def provider_id(self) -> str | None:
        """Return the provider identifier, when known."""
        return self._provider_id

    @property
    def refresh_id(self) -> str | None:
        """Return the refresh identifier, when the failure belongs to one."""
        return self._refresh_id

    @property
    def state(self) -> str:
        """Return the SDK state observed when credentials became unavailable."""
        return self._state

    @property
    def delivery_state(self) -> DeliveryState:
        """Return the observed delivery evidence for credential acquisition."""
        return self._delivery_state

    @property
    def phase(
        self,
    ) -> AuthPhase:
        """Return the credential acquisition phase that failed."""
        return self._phase

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (
            *super()._details(),
            ("state", self.state),
            ("delivery_state", self.delivery_state.value),
            ("phase", self.phase),
        )


class AuthProviderExecutionError(AuthRefreshError):
    """An application credential provider raised an ordinary callback failure."""

    __slots__ = ("_callback",)

    def __init__(  # noqa: PLR0913
        self,
        *,
        callback: Literal["get", "invalidate", "refresh"],
        provider_id: str | None = None,
        refresh_id: str | None = None,
        state: str = "UNKNOWN",
        delivery_state: DeliveryState = DeliveryState.MAYBE_SENT,
        phase: AuthPhase = "unknown",
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Identify the failed callback while preserving its exact exception as the cause."""
        error_choice(callback, ("get", "invalidate", "refresh"), "callback")
        super().__init__(
            provider_id=provider_id,
            refresh_id=refresh_id,
            state=state,
            delivery_state=delivery_state,
            phase=phase,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self._callback: Literal["get", "invalidate", "refresh"] = callback

    @property
    def callback(self) -> Literal["get", "invalidate", "refresh"]:
        """Return the provider callback category that failed."""
        return self._callback


class InsufficientScopeError(AuthRefreshError):
    """Known credential grants do not contain the caller's required scopes."""

    __slots__ = ("_granted_scopes", "_missing_scopes", "_required_scopes")

    def __init__(  # noqa: PLR0913
        self,
        *,
        required_scopes: tuple[str, ...],
        granted_scopes: tuple[str, ...],
        provider_id: str | None = None,
        refresh_id: str | None = None,
        state: str = "UNKNOWN",
        delivery_state: DeliveryState = DeliveryState.MAYBE_SENT,
        phase: AuthPhase = "unknown",
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Copy canonical known scope tuples and retain their ordered difference for explicit inspection."""
        super().__init__(
            provider_id=provider_id,
            refresh_id=refresh_id,
            state=state,
            delivery_state=delivery_state,
            phase=phase,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self._required_scopes = scope_tuple(required_scopes)
        self._granted_scopes = scope_tuple(granted_scopes)
        granted = frozenset(self._granted_scopes)
        self._missing_scopes = tuple(scope for scope in self._required_scopes if scope not in granted)

    @property
    def required_scopes(self) -> tuple[str, ...]:
        """Return the canonical scopes requested by this caller."""
        return self._required_scopes

    @property
    def granted_scopes(self) -> tuple[str, ...]:
        """Return the credential's known canonical grants."""
        return self._granted_scopes

    @property
    def missing_scopes(self) -> tuple[str, ...]:
        """Return required scopes absent from the grants, in canonical required order."""
        return self._missing_scopes


class TokenExpiredError(AuthRefreshError):
    """Credential material has expired or carries an invalid expiry declaration."""

    __slots__ = ("_condition", "_expires_at")

    def __init__(  # noqa: PLR0913
        self,
        *,
        condition: Literal["expired", "nonpositive_expiry", "invalid_expiry"],
        expires_at: datetime | None = None,
        provider_id: str | None = None,
        refresh_id: str | None = None,
        state: str = "UNKNOWN",
        delivery_state: DeliveryState = DeliveryState.MAYBE_SENT,
        phase: AuthPhase = "unknown",
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the expiry category and an actual datetime, never an invalid raw expiry value."""
        error_choice(condition, ("expired", "nonpositive_expiry", "invalid_expiry"), "condition")
        super().__init__(
            provider_id=provider_id,
            refresh_id=refresh_id,
            state=state,
            delivery_state=delivery_state,
            phase=phase,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self._condition: Literal["expired", "nonpositive_expiry", "invalid_expiry"] = condition
        self._expires_at = _error_expiry(expires_at)

    @property
    def condition(self) -> Literal["expired", "nonpositive_expiry", "invalid_expiry"]:
        """Return the safe symbol identifying the expiry defect."""
        return self._condition

    @property
    def expires_at(self) -> datetime | None:
        """Return the declared expiry when it was a valid datetime."""
        return self._expires_at

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("condition", self.condition))


class OAuthExchangeError(AuthRefreshError):
    """A token endpoint rejected an exchange, or an exchange failed where the SDK may try again later."""

    __slots__ = ("_oauth_error", "_status_code")

    _reason_code = "oauth_exchange_error"

    def __init__(  # noqa: PLR0913
        self,
        *,
        status_code: int | None = None,
        oauth_error: OAuthErrorCode | None = None,
        provider_id: str | None = None,
        refresh_id: str | None = None,
        state: str = "UNKNOWN",
        delivery_state: DeliveryState = DeliveryState.MAYBE_SENT,
        phase: AuthPhase = "unknown",
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the obtained status and the standard OAuth error code, never the endpoint's descriptions."""
        if status_code is not None:
            _error_status(status_code)
        if oauth_error is not None:
            error_choice(oauth_error, OAUTH_ERROR_CODES, "oauth_error")
        super().__init__(
            provider_id=provider_id,
            refresh_id=refresh_id,
            state=state,
            delivery_state=delivery_state,
            phase=phase,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self._status_code = status_code
        self._oauth_error: OAuthErrorCode | None = oauth_error

    @property
    def status_code(self) -> int | None:
        """Return the token endpoint's status, or None without an HTTP response."""
        return self._status_code

    @property
    def oauth_error(
        self,
    ) -> OAuthErrorCode | None:
        """Return the standard OAuth error code, or None without one."""
        return self._oauth_error

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("status_code", self.status_code), ("oauth_error", self.oauth_error))


class AuthTimeoutError(AuthRefreshError):
    """A token endpoint exchange ran out of time before anything was sent, or its provider session expired."""

    __slots__ = ("_effective_timeout", "_timeout_kind")

    def __init__(  # noqa: PLR0913
        self,
        *,
        effective_timeout: float,
        timeout_kind: AuthTimeoutKind,
        provider_id: str | None = None,
        refresh_id: str | None = None,
        state: str = "UNKNOWN",
        delivery_state: DeliveryState = DeliveryState.MAYBE_SENT,
        phase: AuthPhase = "unknown",
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the timeout that fired and whether it bounded one phase or the whole provider session."""
        seconds = error_time(effective_timeout, "effective_timeout")
        error_choice(timeout_kind, get_args(AuthTimeoutKind), "timeout_kind")
        super().__init__(
            provider_id=provider_id,
            refresh_id=refresh_id,
            state=state,
            delivery_state=delivery_state,
            phase=phase,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self._effective_timeout = seconds
        self._timeout_kind: AuthTimeoutKind = timeout_kind

    @property
    def effective_timeout(self) -> float:
        """Return the timeout in seconds that fired."""
        return self._effective_timeout

    @property
    def timeout_kind(self) -> AuthTimeoutKind:
        """Return whether one phase or the whole provider session timed out."""
        return self._timeout_kind

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("timeout_kind", self.timeout_kind))


class AuthStateUncertainError(AuthRefreshError):
    """A consumable credential may have been used by an exchange whose outcome is unknown, so it is never reused."""

    __slots__ = ("_failure_kind", "_status_code")

    def __init__(  # noqa: PLR0913
        self,
        *,
        failure_kind: AuthFailureKind,
        status_code: int | None = None,
        provider_id: str | None = None,
        refresh_id: str | None = None,
        state: str = "UNKNOWN",
        delivery_state: DeliveryState = DeliveryState.MAYBE_SENT,
        phase: AuthPhase = "unknown",
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the category that left the exchange's outcome unknown and any status obtained, never the body."""
        error_choice(
            failure_kind,
            get_args(AuthFailureKind),
            "failure_kind",
        )
        if status_code is not None:
            _error_status(status_code)
        super().__init__(
            provider_id=provider_id,
            refresh_id=refresh_id,
            state=state,
            delivery_state=delivery_state,
            phase=phase,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self._failure_kind: AuthFailureKind = failure_kind
        self._status_code = status_code

    @property
    def failure_kind(
        self,
    ) -> AuthFailureKind:
        """Return the category that left the outcome unknown."""
        return self._failure_kind

    @property
    def status_code(self) -> int | None:
        """Return the token endpoint's status, or None without a complete response."""
        return self._status_code

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("failure_kind", self.failure_kind), ("status_code", self.status_code))


class AuthReauthorizationRequiredError(AuthRefreshError):
    """The credential cannot be renewed without a new authorization by its owner."""

    __slots__ = ("_condition",)

    def __init__(  # noqa: PLR0913
        self,
        *,
        condition: ReauthorizationCondition,
        provider_id: str | None = None,
        refresh_id: str | None = None,
        state: str = "UNKNOWN",
        delivery_state: DeliveryState = DeliveryState.MAYBE_SENT,
        phase: AuthPhase = "unknown",
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep why a new authorization is required."""
        error_choice(condition, get_args(ReauthorizationCondition), "condition")
        super().__init__(
            provider_id=provider_id,
            refresh_id=refresh_id,
            state=state,
            delivery_state=delivery_state,
            phase=phase,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self._condition: ReauthorizationCondition = condition

    @property
    def condition(self) -> ReauthorizationCondition:
        """Return why a new authorization is required."""
        return self._condition

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("condition", self.condition))


class AuthStateConflictError(AuthRefreshError):
    """An explicit OAuth operation is not permitted in the provider's or flow's current state."""

    __slots__ = ("_action",)

    def __init__(  # noqa: PLR0913
        self,
        *,
        action: AuthAction,
        provider_id: str | None = None,
        refresh_id: str | None = None,
        state: str = "UNKNOWN",
        delivery_state: DeliveryState = DeliveryState.MAYBE_SENT,
        phase: AuthPhase = "unknown",
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the refused operation; the state names where the provider or flow stood."""
        error_choice(action, get_args(AuthAction), "action")
        super().__init__(
            provider_id=provider_id,
            refresh_id=refresh_id,
            state=state,
            delivery_state=delivery_state,
            phase=phase,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self._action: AuthAction = action

    @property
    def action(
        self,
    ) -> AuthAction:
        """Return the refused operation."""
        return self._action

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("action", self.action))


class AuthTokenLoadError(AuthRefreshError):
    """Loading a token family's persisted token set failed, or what the load returned is unusable."""

    __slots__ = ("_purpose",)

    def __init__(  # noqa: PLR0913
        self,
        *,
        purpose: TokenLoadPurpose,
        provider_id: str | None = None,
        refresh_id: str | None = None,
        state: str = "UNKNOWN",
        delivery_state: DeliveryState = DeliveryState.MAYBE_SENT,
        phase: AuthPhase = "unknown",
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep why the provider loaded the token set; the loaded value itself is never kept."""
        error_choice(purpose, get_args(TokenLoadPurpose), "purpose")
        super().__init__(
            provider_id=provider_id,
            refresh_id=refresh_id,
            state=state,
            delivery_state=delivery_state,
            phase=phase,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self._purpose: TokenLoadPurpose = purpose

    @property
    def purpose(self) -> TokenLoadPurpose:
        """Return why the provider loaded the token set."""
        return self._purpose

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("purpose", self.purpose))


class AuthTokenStoreError(AuthRefreshError):
    """Storing a token set failed or has an unknown outcome; the token set waiting to be stored is never exposed."""

    __slots__ = ("_action", "_expected_revision", "_pending_revision")

    def __init__(  # noqa: PLR0913
        self,
        *,
        action: TokenStoreAction = "store",
        expected_revision: int | None = None,
        pending_revision: int | None = None,
        provider_id: str | None = None,
        refresh_id: str | None = None,
        state: str = "UNKNOWN",
        delivery_state: DeliveryState = DeliveryState.MAYBE_SENT,
        phase: AuthPhase = "unknown",
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the persistence action and the revisions it compared and tried to store."""
        error_choice(action, get_args(TokenStoreAction), "action")
        expected = _error_optional_count(expected_revision, "expected_revision")
        pending = _error_optional_count(pending_revision, "pending_revision")
        super().__init__(
            provider_id=provider_id,
            refresh_id=refresh_id,
            state=state,
            delivery_state=delivery_state,
            phase=phase,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self._action: TokenStoreAction = action
        self._expected_revision = expected
        self._pending_revision = pending

    @property
    def action(self) -> TokenStoreAction:
        """Return the persistence action that failed."""
        return self._action

    @property
    def expected_revision(self) -> int | None:
        """Return the stored revision the action expected, or None when it expected nothing stored."""
        return self._expected_revision

    @property
    def pending_revision(self) -> int | None:
        """Return the revision the action tried to store, or None."""
        return self._pending_revision

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (
            *super()._details(),
            ("action", self.action),
            ("expected_revision", self.expected_revision),
            ("pending_revision", self.pending_revision),
        )


class AuthTokenStoreConflictError(AuthTokenStoreError):
    """A token store refused a token set its stored revision conflicts with, or a load conflicted with the held one."""

    __slots__ = ("_observed_revision",)

    def __init__(  # noqa: PLR0913
        self,
        *,
        observed_revision: int | None = None,
        action: TokenStoreAction = "store",
        expected_revision: int | None = None,
        pending_revision: int | None = None,
        provider_id: str | None = None,
        refresh_id: str | None = None,
        state: str = "UNKNOWN",
        delivery_state: DeliveryState = DeliveryState.MAYBE_SENT,
        phase: AuthPhase = "unknown",
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the revision found instead of the expected one, when the store or the load reported it."""
        observed = _error_optional_count(observed_revision, "observed_revision")
        super().__init__(
            action=action,
            expected_revision=expected_revision,
            pending_revision=pending_revision,
            provider_id=provider_id,
            refresh_id=refresh_id,
            state=state,
            delivery_state=delivery_state,
            phase=phase,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self._observed_revision = observed

    @property
    def observed_revision(self) -> int | None:
        """Return the revision found instead of the expected one, or None when unknown."""
        return self._observed_revision

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("observed_revision", self.observed_revision))


class AuthConcurrencyLimitError(AuthRefreshError):
    """A provider refused a new shared refresh job or waiter because its configured limit was reached."""

    __slots__ = ("_limit", "_limit_kind")

    def __init__(  # noqa: PLR0913
        self,
        *,
        limit_kind: AuthLimitKind,
        limit: int,
        provider_id: str | None = None,
        refresh_id: str | None = None,
        state: str = "UNKNOWN",
        delivery_state: DeliveryState = DeliveryState.MAYBE_SENT,
        phase: AuthPhase = "unknown",
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep which limit refused the caller and its configured value."""
        error_choice(limit_kind, get_args(AuthLimitKind), "limit_kind")
        super().__init__(
            provider_id=provider_id,
            refresh_id=refresh_id,
            state=state,
            delivery_state=delivery_state,
            phase=phase,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self._limit_kind: AuthLimitKind = limit_kind
        self._limit = error_count(limit, "limit")

    @property
    def limit_kind(self) -> AuthLimitKind:
        """Return whether pending jobs or waiters reached their limit."""
        return self._limit_kind

    @property
    def limit(self) -> int:
        """Return the configured limit."""
        return self._limit

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("limit_kind", self.limit_kind), ("limit", self.limit))


class AuthBudgetExceededError(AuthRefreshError):
    """A call could not reserve the token exchange or network send a new token acquisition needs."""

    __slots__ = ("_budget_kind", "_limit", "_used")

    def __init__(  # noqa: PLR0913
        self,
        *,
        budget_kind: AuthBudgetKind,
        limit: int,
        used: int,
        provider_id: str | None = None,
        refresh_id: str | None = None,
        state: str = "UNKNOWN",
        delivery_state: DeliveryState = DeliveryState.MAYBE_SENT,
        phase: AuthPhase = "unknown",
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the exhausted budget and how many slots were already consumed."""
        error_choice(budget_kind, get_args(AuthBudgetKind), "budget_kind")
        super().__init__(
            provider_id=provider_id,
            refresh_id=refresh_id,
            state=state,
            delivery_state=delivery_state,
            phase=phase,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self._budget_kind: AuthBudgetKind = budget_kind
        self._limit = error_count(limit, "limit")
        self._used = error_count(used, "used")

    @property
    def budget_kind(self) -> AuthBudgetKind:
        """Return the budget that refused the acquisition."""
        return self._budget_kind

    @property
    def limit(self) -> int:
        """Return the configured number of available slots."""
        return self._limit

    @property
    def used(self) -> int:
        """Return the slots already reserved before this acquisition."""
        return self._used

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("budget_kind", self.budget_kind), ("limit", self.limit), ("used", self.used))


class AuthProviderClosedError(AuthRefreshError):
    """The provider is closing or closed and cannot supply credentials."""

    __slots__ = ()

    _states = ("CLOSED", "CLOSING")

    def __init__(  # noqa: PLR0913
        self,
        *,
        provider_id: str | None = None,
        refresh_id: str | None = None,
        state: Literal["CLOSED", "CLOSING"] = "CLOSED",
        delivery_state: DeliveryState = DeliveryState.MAYBE_SENT,
        phase: AuthPhase = "unknown",
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Default to the closed state, the only states a closing or closed provider reports."""
        super().__init__(
            provider_id=provider_id,
            refresh_id=refresh_id,
            state=state,
            delivery_state=delivery_state,
            phase=phase,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )


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
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the delivery evidence and the I/O phase that failed."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self.delivery_state = delivery_state
        self.phase: IOPhase = phase
        self.retry_stop_reason = retry_stop_reason

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("delivery_state", self.delivery_state.value), ("phase", self.phase))


class PhaseTimeoutError(TransportError):
    """An I/O phase exceeded its own timeout before the logical deadline."""

    __slots__ = ("_effective_timeout",)

    def __init__(  # noqa: PLR0913
        self,
        *,
        effective_timeout: float,
        phase: Literal["connect", "read", "write", "pool"],
        delivery_state: DeliveryState,
        retry_stop_reason: RetryStopReason | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the expired phase cap, delivery evidence, and native timeout cause."""
        error_choice(phase, ("connect", "read", "write", "pool"), "phase")
        super().__init__(
            delivery_state=_error_delivery(delivery_state),
            phase=phase,
            retry_stop_reason=retry_stop_reason,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self._effective_timeout = error_time(effective_timeout, "effective_timeout")

    @property
    def effective_timeout(self) -> float:
        """Return the configured phase cap that expired, in seconds."""
        return self._effective_timeout

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("effective_timeout", self.effective_timeout))


class DeadlineExceededError(SDKError):
    """The call or stream exhausted its monotonic total budget."""

    __slots__ = ("_deadline_at", "_delivery_state", "_elapsed", "_phase")

    def __init__(  # noqa: PLR0913
        self,
        *,
        deadline_at: float,
        elapsed: float,
        delivery_state: DeliveryState,
        phase: DeadlinePhase = "unknown",
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the absolute expiry, elapsed time, interrupted activity, and delivery evidence."""
        error_choice(
            phase, ("encode", "auth", "limiter", "sleep", "send", "decode", "stream", "cleanup", "unknown"), "phase"
        )
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self._deadline_at = error_time(deadline_at, "deadline_at", nonnegative=False)
        self._elapsed = error_time(elapsed, "elapsed")
        self._delivery_state = _error_delivery(delivery_state)
        self._phase: DeadlinePhase = phase

    @property
    def deadline_at(self) -> float:
        """Return the absolute monotonic deadline in seconds."""
        return self._deadline_at

    @property
    def elapsed(self) -> float:
        """Return the elapsed duration before the failure in seconds."""
        return self._elapsed

    @property
    def delivery_state(self) -> DeliveryState:
        """Return the request's delivery evidence at termination."""
        return self._delivery_state

    @property
    def phase(self) -> DeadlinePhase:
        """Return the activity interrupted by the deadline."""
        return self._phase

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (
            *super()._details(),
            ("delivery_state", self.delivery_state.value),
            ("phase", self.phase),
        )


class RequestCancelledError(SDKError):
    """An explicit cancellation token stopped the call; native cancellation keeps its original type."""

    __slots__ = ("_delivery_state", "_source")

    def __init__(  # noqa: PLR0913
        self,
        *,
        source: Literal["cancel_token", "parent_cancel_token"],
        delivery_state: DeliveryState,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the observed token source and request delivery evidence."""
        error_choice(source, ("cancel_token", "parent_cancel_token"), "source")
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self._source: Literal["cancel_token", "parent_cancel_token"] = source
        self._delivery_state = _error_delivery(delivery_state)

    @property
    def source(self) -> Literal["cancel_token", "parent_cancel_token"]:
        """Return the explicit token source that requested cancellation."""
        return self._source

    @property
    def delivery_state(self) -> DeliveryState:
        """Return the request's delivery evidence at cancellation."""
        return self._delivery_state

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("source", self.source), ("delivery_state", self.delivery_state.value))


class BudgetExceededError(SDKError):
    """The call could not reserve a network send within its configured budget."""

    __slots__ = ("_budget_kind", "_limit", "_used")

    def __init__(  # noqa: PLR0913
        self,
        *,
        budget_kind: Literal["network", "parent_network"],
        limit: int,
        used: int,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the exhausted budget and how many slots were already consumed."""
        error_choice(budget_kind, ("network", "parent_network"), "budget_kind")
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self._budget_kind: Literal["network", "parent_network"] = budget_kind
        self._limit = error_count(limit, "limit")
        self._used = error_count(used, "used")

    @property
    def budget_kind(self) -> Literal["network", "parent_network"]:
        """Return the network budget that refused admission."""
        return self._budget_kind

    @property
    def limit(self) -> int:
        """Return the configured number of available sends."""
        return self._limit

    @property
    def used(self) -> int:
        """Return the send slots already reserved before this admission."""
        return self._used

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("budget_kind", self.budget_kind), ("limit", self.limit), ("used", self.used))


class LimiterExecutionError(SDKError):
    """An application limiter failed while acquiring or releasing a permit."""

    __slots__ = ("_action",)

    def __init__(  # noqa: PLR0913
        self,
        *,
        action: Literal["acquire", "release"],
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the failing callback category without exposing the callback's values."""
        error_choice(action, ("acquire", "release"), "action")
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self._action: Literal["acquire", "release"] = action

    @property
    def action(self) -> Literal["acquire", "release"]:
        """Return whether acquisition or release failed."""
        return self._action

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("action", self.action))


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
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the response metadata, the bounded body, and the error payload or why it did not decode."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
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
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the response metadata and the bounded body."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
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
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the response metadata, bounded body, and decode cause without its traceback."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=None if cause is None else cause.with_traceback(None),
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
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
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
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
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
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
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
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
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
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
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the response metadata and the cause, with an empty body."""
        super().__init__(
            info=info,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
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
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the limit, how many bytes arrived, and the bounded prefix."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self.representation = representation
        self.limit = limit
        self.observed_bytes = observed_bytes
        self.body_prefix = body_prefix


class ProtocolError(SDKError):
    """Base of failures in received protocol data, such as a content coding that does not decode."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the helper context beside the shared call metadata."""
        _protocol_context(helper_id, operation)
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
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
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep which record overflowed, its limit, and how much arrived."""
        super().__init__(
            helper_id=helper_id,
            operation=operation,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self.kind = kind
        self.limit = limit
        self.observed = observed
        self.unit = unit

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("kind", self.kind), ("limit", self.limit), ("observed", self.observed))


class WebhookVerificationError(ProtocolError):
    """A webhook signature or authenticated fact that verification rejected before decoding its event."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        condition: Literal[
            "malformed_signature", "invalid_signature", "missing_key", "timestamp_window", "missing_delivery_id"
        ],
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep only the safe rejection category and shared context, never signature or key material."""
        error_choice(
            condition,
            ("malformed_signature", "invalid_signature", "missing_key", "timestamp_window", "missing_delivery_id"),
            "condition",
        )
        super().__init__(
            helper_id=helper_id,
            operation=operation,
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


class WebhookReplayError(ProtocolError):
    """A previously claimed webhook delivery that the helper's duplicate policy rejects."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        delivery_id: str,
        namespace: str,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the delivery and namespace for explicit inspection, excluding both from its message."""
        for field, value in (("delivery_id", delivery_id), ("namespace", namespace)):
            error_string(value, field)
        super().__init__(
            helper_id=helper_id,
            operation=operation,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.delivery_id = delivery_id
        self.namespace = namespace


class ProtocolStoreError(ProtocolError):
    """A helper store operation that failed; the original cause and entry identity remain available."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        action: _StoreAction,
        entry_id: str | None = None,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the store action and private entry identity alongside the helper context."""
        error_choice(
            action,
            (
                "lookup",
                "fingerprint_vary",
                "compare_exchange",
                "delete",
                "invalidate",
                "claim",
                "put",
                "get",
                "open",
                "read",
                "close",
                "purge_terminal",
                "admit",
                "record",
                "reset",
                "snapshot",
            ),
            "action",
        )
        error_string(entry_id, "entry_id", optional=True)
        super().__init__(
            helper_id=helper_id,
            operation=operation,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.action = action
        self.entry_id = entry_id

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("action", self.action))


class WebhookStoreError(ProtocolStoreError):
    """A replay store operation that failed, so the webhook cannot be accepted."""


class CircuitStoreError(ProtocolStoreError):
    """A circuit store operation that failed or returned the wrong type; no request is resent because of it."""


class ReplayStoreFullError(WebhookStoreError):
    """A replay store with no space for a new claim while every retained entry is still live."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        max_entries: int,
        action: _StoreAction,
        entry_id: str | None = None,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the store capacity and action without discarding an existing claim."""
        if type(max_entries) is not int or max_entries < 0:
            msg = "max_entries must be a nonnegative integer"
            raise ValueError(msg)
        super().__init__(
            action=action,
            entry_id=entry_id,
            helper_id=helper_id,
            operation=operation,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.max_entries = max_entries

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("max_entries", self.max_entries))


class UnsupportedContentCodingError(ProtocolError):
    """A response content coding that no decoder handles; its name stays out of the message."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        coding: str,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the coding the response named."""
        super().__init__(
            helper_id=helper_id,
            operation=operation,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
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
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the decoding layer, its encoded bytes so far, and the ratio its limit came from."""
        super().__init__(
            kind="expanded_content",
            limit=limit,
            observed=observed,
            unit="bytes",
            helper_id=helper_id,
            operation=operation,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self.layer = layer
        self.encoded_bytes = encoded_bytes
        self.max_ratio = max_ratio


class RedirectPolicyError(SDKError):
    """A redirect that cannot be followed safely; no redirect body is retained."""

    __slots__ = ("_delivery_state",)

    def __init__(  # noqa: PLR0913
        self,
        *,
        delivery_state: DeliveryState,
        body_available: Literal[False] = False,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep delivery evidence, obtained response metadata, and the original failure."""
        if body_available is not False:
            msg = "body_available must be False"
            raise ValueError(msg)
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self._delivery_state = _error_delivery(delivery_state)

    @property
    def delivery_state(self) -> DeliveryState:
        """Return how far the request progressed before redirect handling stopped."""
        return self._delivery_state

    @property
    def body_available(self) -> Literal[False]:
        """Return False: redirect policy failures never retain a response body."""
        return False

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("delivery_state", self.delivery_state.value))


class AdapterContractError(SDKError):
    """A transport that broke its contract, such as handing over a body something already read; nothing is retried."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        delivery_state: DeliveryState = DeliveryState.MAYBE_SENT,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep how far the request got."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self.delivery_state = delivery_state

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("delivery_state", self.delivery_state.value))


class AdapterExecutionError(SDKError):
    """A transport adapter that failed outside its classified I/O errors, such as a programming error; no retry."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        delivery_state: DeliveryState = DeliveryState.MAYBE_SENT,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep how far the request got."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self.delivery_state = delivery_state

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("delivery_state", self.delivery_state.value))


class ClientClosedError(SDKError):
    """A call to a client or view that is closing or closed, including one it stopped while running."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        state: Literal["CLOSING", "CLOSED"],
        owner: Literal["client", "view"] = "client",
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the state of the client or view that refused the call."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self.state = state
        self.owner = owner

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("state", self.state), ("owner", self.owner))


class CleanupError(SDKError):
    """A close that ran out of its cleanup time or failed to release something; closing again waits again."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        pending_calls: int = 0,
        pending_leases: int = 0,
        pending_providers: int = 0,
        timeout: float | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep what is still unfinished and the cleanup time that ran out."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
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

    def __init__(  # noqa: PLR0913
        self,
        *,
        detected_backend: str | None = None,
        expected_backend: Literal["asyncio"] = "asyncio",
        loop_mismatch: bool = False,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the backend found, the one required, and whether only the event loop differs."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self.detected_backend = detected_backend
        self.expected_backend = expected_backend
        self.loop_mismatch = loop_mismatch

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("loop_mismatch", self.loop_mismatch))


class ResponseConsumedError(SDKError):
    """A read of a streaming response that its earlier read, iteration, close, or failure already ruled out."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        state: Literal["streaming", "consumed", "closed", "failed"],
        action: Literal["read", "text", "json", "iter_bytes", "iter_raw_bytes", "stream_to"],
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the state of the response and the action it refused."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self.state = state
        self.action = action

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("state", self.state), ("action", self.action))


class HookExecutionError(SDKError, Generic[T_co]):
    """A hook failed on an event, so the call sent nothing more; a success it had decoded stays available.

    `sent` tells whether the call reached its transport at least once.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        event_name: str,
        sent: bool,
        delivery_state: DeliveryState,
        completed_result: Response[T_co] | Unset = UNSET,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
        resource_attempt_count: int = 0,
        redirect_count: int = 0,
        auth_exchange_count: int = 0,
        network_send_count: int = 0,
        network_send_budget_used: int = 0,
        auth_exchange_budget_used: int = 0,
        auth_refresh_ids: tuple[str, ...] = (),
        auth_refresh_pending: int = 0,
        wire_send_count: int | None = None,
    ) -> None:
        """Keep the event whose hook failed, how far the call got, and the success it completed, if any."""
        super().__init__(
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
            resource_attempt_count=resource_attempt_count,
            redirect_count=redirect_count,
            auth_exchange_count=auth_exchange_count,
            network_send_count=network_send_count,
            network_send_budget_used=network_send_budget_used,
            auth_exchange_budget_used=auth_exchange_budget_used,
            auth_refresh_ids=auth_refresh_ids,
            auth_refresh_pending=auth_refresh_pending,
            wire_send_count=wire_send_count,
        )
        self.event_name = event_name
        self.sent = sent
        self.delivery_state = delivery_state
        self.completed_result = completed_result

    @property
    def has_completed_result(self) -> bool:
        """Return whether the call decoded its success before the hook failed."""
        return not isinstance(self.completed_result, Unset)

    def require_result(self) -> Response[T_co]:
        """Return the success the call decoded before the hook failed, or raise ResultUnavailableError."""
        if isinstance(result := self.completed_result, Unset):
            raise ResultUnavailableError(operation_id=self.operation_id, call_id=self.call_id)
        return result

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("event_name", self.event_name), ("sent", self.sent))


class ResultUnavailableError(SDKError):
    """No success was decoded before the hook failure, so there is none to require."""
