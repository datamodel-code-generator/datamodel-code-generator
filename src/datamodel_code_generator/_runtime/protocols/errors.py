"""Exceptions of protocol helpers whose fields name protocol records, loaded only when a helper or caller needs them.

The generated `errors` module exports them beside every other exception; their messages name only safe metadata.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Final, Generic, Literal, TypeAlias, get_args

from typing_extensions import TypeIs, TypeVar

from ..client.errors import (
    MAX_STATUS,
    MIN_STATUS,
    APIConnectionError,
    ConfigurationError,
    DeliveryState,
    ProtocolError,
    ProtocolStoreError,
    RetryStopReason,
    error_choice,
    error_count,
    error_string,
    error_time,
)
from ..client.responses import HeadersView, ResponseInfo
from .records import (
    PROGRESS_KEYS,
    PollSnapshot,
    ProgressKey,
    ProtocolProgress,
    RequestTarget,
    Selector,
)
from .references import OperationRef
from .resume import ResumeState, ResumeStateError
from .sources import UploadProgress

__all__ = (
    "CacheProtocolError",
    "CacheStoreError",
    "CacheValidatorConflictError",
    "ConcurrentReceiveError",
    "DeliveryUnknownError",
    "HandshakeResponse",
    "IncompleteFrameError",
    "NonResumableSourceError",
    "OperationCancelledError",
    "OperationFailedError",
    "PaginationCycleError",
    "PollWaitLimitError",
    "PollingStateError",
    "ProtocolDataError",
    "ProtocolStateError",
    "ResumeStateError",
    "SessionLimitError",
    "StreamDecodeError",
    "StreamInterruptedError",
    "StreamRemoteError",
    "StreamResumeExhaustedError",
    "UploadDeliveryUnknownError",
    "UploadExpiredError",
    "UploadOffsetError",
    "UploadSourceChangedError",
    "WebSocketClosedError",
    "WebSocketHandshakeError",
    "WebSocketProxyError",
)

E_co = TypeVar("E_co", covariant=True, default=object)
P_co = TypeVar("P_co", covariant=True, default=object)

_DataCondition: TypeAlias = Literal["missing", "null", "type", "value", "malformed", "inconsistent"]
_SessionLimitKind: TypeAlias = Literal["pages", "items", "polls", "reconnects", "parts"]

HandshakeCondition: TypeAlias = Literal[
    "invalid_message", "invalid_header", "upgrade", "negotiation", "security", "size"
]

_DATA_CONDITIONS: Final = get_args(_DataCondition)
_HANDSHAKE_CONDITIONS: Final = get_args(HandshakeCondition)
_UNKNOWN_DELIVERIES: Final = (DeliveryState.MAYBE_SENT, DeliveryState.RESPONSE_STARTED)
MAX_CLOSE_REASON: Final = 123
_SESSION_LIMIT_KINDS: Final = get_args(_SessionLimitKind)
_LOCATIONS: Final = (*get_args(Selector), *get_args(RequestTarget))
_VALIDATOR_HEADERS: Final = ("If-None-Match", "If-Modified-Since")
MAX_RAW_PREFIX: Final = 65536


def _location(value: object) -> None:
    if value is not None and not isinstance(value, _LOCATIONS):
        msg = "location must be a selector, a request target, or None"
        raise ValueError(msg)


def _resume_state(value: object) -> None:
    if value is not None and not isinstance(value, ResumeState):
        msg = "resume_state must be a ResumeState or None"
        raise ValueError(msg)


def _is_progress_key(value: object) -> TypeIs[ProgressKey]:
    return value in PROGRESS_KEYS


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _progress(value: object) -> ProtocolProgress:
    if _is_mapping(value):
        progress: dict[ProgressKey, int] = {
            key: error_count(count, "progress") for key, count in value.items() if _is_progress_key(key)
        }
        if len(progress) == len(value):
            return MappingProxyType(progress)
    msg = "progress must map progress keys to nonnegative integers"
    raise ValueError(msg)


def _snapshot(value: object) -> None:
    if not isinstance(value, PollSnapshot):
        msg = "snapshot must be a PollSnapshot"
        raise ValueError(msg)  # noqa: TRY004 - Exception constructors reject invalid fields with ValueError.


def _raw_prefix(value: object, field: str = "raw_prefix") -> None:
    if not isinstance(value, bytes) or len(value) > MAX_RAW_PREFIX:
        msg = f"{field} must be at most 65536 bytes"
        raise ValueError(msg)


def _headers(value: object) -> None:
    if not isinstance(value, HeadersView):
        msg = "headers must be a HeadersView"
        raise ValueError(msg)  # noqa: TRY004 - Exception constructors reject invalid fields with ValueError.


def _status(value: object, field: str) -> None:
    if not MIN_STATUS <= error_count(value, field) <= MAX_STATUS:
        msg = f"{field} must be an HTTP status"
        raise ValueError(msg)


def _context(helper_id: object, operation: object) -> None:
    error_string(helper_id, "helper_id", optional=True)
    if operation is not None and not isinstance(operation, OperationRef):
        msg = "operation must be an OperationRef or None"
        raise ValueError(msg)


def _upload_progress(value: object) -> None:
    if not isinstance(value, UploadProgress):
        msg = "progress must be an UploadProgress"
        raise ValueError(msg)  # noqa: TRY004 - Exception constructors reject invalid fields with ValueError.


def _aware(value: object) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        msg = "expires_at must be a timezone-aware datetime"
        raise ValueError(msg)


def _flag(value: object, field: str) -> None:
    if type(value) is not bool:
        msg = f"{field} must be a bool"
        raise ValueError(msg)


class ProtocolDataError(ProtocolError):
    """Received data that is missing, null, of another type or value, malformed, or inconsistent."""

    condition: _DataCondition

    def __init__(  # noqa: PLR0913
        self,
        *,
        condition: _DataCondition = "value",
        location: Selector | RequestTarget | None = None,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep which rule the received data broke and the selector or target it concerns."""
        error_choice(condition, _DATA_CONDITIONS, "condition")
        _location(location)
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
        self.location = location

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("condition", self.condition))


class ProtocolStateError(ProtocolError):
    """An operation the helper's current state forbids, such as concurrent consumption or a finished handle."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        state: str,
        action: str,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the SDK-defined state and method names."""
        error_string(state, "state")
        error_string(action, "action")
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
        self.state = state
        self.action = action

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("state", self.state), ("action", self.action))


class SessionLimitError(ProtocolError):
    """A finite session cap reached while continuation remains; partial progress is never a success."""

    kind: _SessionLimitKind

    def __init__(  # noqa: PLR0913
        self,
        *,
        kind: _SessionLimitKind,
        limit: int,
        progress: ProtocolProgress,
        resume_state: ResumeState | None = None,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the exhausted cap, a read-only copy of the progress, and any exportable resume state."""
        error_choice(kind, _SESSION_LIMIT_KINDS, "kind")
        error_count(limit, "limit")
        copied = _progress(progress)
        _resume_state(resume_state)
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
        self.kind = kind
        self.limit = limit
        self.progress = copied
        self.resume_state = resume_state

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("kind", self.kind), ("limit", self.limit))


class StreamResumeExhaustedError(SessionLimitError):
    """Automatic stream reconnection that exhausted its reconnect budget; it never ends as a normal EOF."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        kind: Literal["reconnects"],
        limit: int,
        progress: ProtocolProgress,
        resume_state: ResumeState | None = None,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the exhausted reconnection budget with the stream's progress and resume state."""
        error_choice(kind, ("reconnects",), "kind")
        super().__init__(
            kind=kind,
            limit=limit,
            progress=progress,
            resume_state=resume_state,
            helper_id=helper_id,
            operation=operation,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )


class PaginationCycleError(ProtocolDataError):
    """A continuation already seen in this session; its condition is always inconsistent and the value is not kept."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        page_index: int,
        first_seen_page_index: int,
        resume_state: ResumeState | None = None,
        location: Selector | RequestTarget | None = None,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the repeating page and the page that first returned the continuation."""
        error_count(page_index, "page_index")
        error_count(first_seen_page_index, "first_seen_page_index")
        _resume_state(resume_state)
        super().__init__(
            condition="inconsistent",
            location=location,
            helper_id=helper_id,
            operation=operation,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.page_index = page_index
        self.first_seen_page_index = first_seen_page_index
        self.resume_state = resume_state

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (
            *super()._details(),
            ("page_index", self.page_index),
            ("first_seen_page_index", self.first_seen_page_index),
        )


class PollingStateError(ProtocolDataError):
    """A polled state of another type or of no declared value; success is never inferred from it."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        condition: Literal["type", "value"] = "value",
        location: Selector | RequestTarget | None = None,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep whether the state had another type or an undeclared value."""
        error_choice(condition, ("type", "value"), "condition")
        super().__init__(
            condition=condition,
            location=location,
            helper_id=helper_id,
            operation=operation,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )


class PollWaitLimitError(ProtocolError):
    """A server-required wait longer than the allowed wait or the remaining deadline; nothing is sent early."""

    kind: Literal["wait", "deadline"]

    def __init__(  # noqa: PLR0913
        self,
        *,
        kind: Literal["wait", "deadline"],
        required_wait: float,
        limit: float,
        resume_state: ResumeState | None = None,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the required wait, the limit it exceeds, and any exportable resume state."""
        error_choice(kind, ("wait", "deadline"), "kind")
        required = error_time(required_wait, "required_wait")
        allowed = error_time(limit, "limit")
        _resume_state(resume_state)
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
        self.kind = kind
        self.required_wait = required
        self.limit = allowed
        self.resume_state = resume_state

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("kind", self.kind), ("required_wait", self.required_wait), ("limit", self.limit))


class OperationFailedError(ProtocolError, Generic[P_co]):
    """A remote operation that reached its declared failed state; its final poll stays available."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        snapshot: PollSnapshot[P_co],
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the terminal poll snapshot for explicit inspection."""
        _snapshot(snapshot)
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
        self._snapshot = snapshot

    @property
    def snapshot(self) -> PollSnapshot[P_co]:
        """Return the poll that reported the terminal state."""
        return self._snapshot


class OperationCancelledError(ProtocolError, Generic[P_co]):
    """A remote operation that reached its declared cancelled state; local cancellation is never this."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        snapshot: PollSnapshot[P_co],
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the terminal poll snapshot for explicit inspection."""
        _snapshot(snapshot)
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
        self._snapshot = snapshot

    @property
    def snapshot(self) -> PollSnapshot[P_co]:
        """Return the poll that reported the terminal state."""
        return self._snapshot


class StreamDecodeError(ProtocolDataError):
    """A stream record that failed to decode or validate; it never triggers an automatic reconnect."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        sequence: int,
        raw_prefix: bytes,
        truncated: bool,
        condition: _DataCondition = "malformed",
        location: Selector | RequestTarget | None = None,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the sequence, at most 64 KiB of raw bytes, and decode cause without its traceback."""
        error_count(sequence, "sequence")
        _raw_prefix(raw_prefix)
        _flag(truncated, "truncated")
        super().__init__(
            condition=condition,
            location=location,
            helper_id=helper_id,
            operation=operation,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=None if cause is None else cause.with_traceback(None),
            secondary_errors=secondary_errors,
        )
        self.sequence = sequence
        self.raw_prefix = raw_prefix
        self.truncated = truncated

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("sequence", self.sequence), ("truncated", self.truncated))


class StreamInterruptedError(ProtocolError):
    """A stream that ended or failed before its declared termination."""

    condition: Literal["eof", "transport"]

    def __init__(  # noqa: PLR0913
        self,
        *,
        condition: Literal["eof", "transport"],
        sequence: int,
        resume_state: ResumeState | None = None,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep how the stream stopped, the last sequence delivered, and any exportable resume state."""
        error_choice(condition, ("eof", "transport"), "condition")
        error_count(sequence, "sequence")
        _resume_state(resume_state)
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
        self.sequence = sequence
        self.resume_state = resume_state

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("condition", self.condition), ("sequence", self.sequence))


class IncompleteFrameError(StreamInterruptedError):
    """An incomplete frame or missing final newline at EOF; its condition is always eof."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        buffered_bytes: int,
        sequence: int,
        resume_state: ResumeState | None = None,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep how many bytes of the incomplete frame were buffered."""
        error_count(buffered_bytes, "buffered_bytes")
        super().__init__(
            condition="eof",
            sequence=sequence,
            resume_state=resume_state,
            helper_id=helper_id,
            operation=operation,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.buffered_bytes = buffered_bytes

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("buffered_bytes", self.buffered_bytes))


class StreamRemoteError(ProtocolError, Generic[E_co]):
    """A declared error event of a stream, raised instead of yielding it; its typed data stays available."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        event_type: str | None,
        data: E_co,
        sequence: int,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the event type, the typed error value, and its sequence."""
        error_string(event_type, "event_type", optional=True)
        error_count(sequence, "sequence")
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
        self.event_type = event_type
        self._data = data
        self.sequence = sequence

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("sequence", self.sequence))

    @property
    def data(self) -> E_co:
        """Return the decoded error event value."""
        return self._data


class CacheStoreError(ProtocolStoreError):
    """A cache store operation that failed or broke the store contract; no request is sent again because of it."""


class CacheProtocolError(ProtocolDataError):
    """A response the cache cannot apply, such as a 304 without a usable entry; its condition is always inconsistent."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        location: Selector | RequestTarget | None = None,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the response's context; the condition is fixed."""
        super().__init__(
            condition="inconsistent",
            location=location,
            helper_id=helper_id,
            operation=operation,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )


class CacheValidatorConflictError(ConfigurationError):
    """A validator header the caller gave that differs from the stored entry's; the value itself is never kept."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        header_name: Literal["If-None-Match", "If-Modified-Since"],
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
        """Keep the header's name as the field path; the condition is fixed."""
        error_choice(header_name, _VALIDATOR_HEADERS, "header_name")
        super().__init__(
            field_path=(header_name,),
            reason="binding_mismatch",
            helper_id=helper_id,
            operation=operation,
            source_uri=source_uri,
            source_pointer=source_pointer,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.header_name = header_name


class ConcurrentReceiveError(ProtocolStateError):
    """A receive while another receive of the same session waits; its state is receiving and its action receive."""

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
    ) -> None:
        """Fix the state and the action."""
        super().__init__(
            state="receiving",
            action="receive",
            helper_id=helper_id,
            operation=operation,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )


class WebSocketClosedError(ProtocolError):
    """A WebSocket connection that closed: the close code and reason received, and whether the closure was normal.

    A normal closure ends a session's iteration; receive raises this class either way. The reason never appears in
    messages.
    """

    _reason_code = "web_socket_closed_error"

    def __init__(  # noqa: PLR0913
        self,
        *,
        code: int | None,
        reason: str,
        clean: bool,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the close code, at most 123 UTF-8 bytes of reason, and whether the closure was normal."""
        if code is not None:
            error_count(code, "code")
        error_string(reason, "reason")
        if len(reason.encode("utf-8", "replace")) > MAX_CLOSE_REASON:
            msg = "reason must be at most 123 UTF-8 bytes"
            raise ValueError(msg)
        _flag(clean, "clean")
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
        self.code = code
        self.reason: str = reason
        self.clean = clean

    def _details(self) -> tuple[tuple[str, object], ...]:
        kept = tuple(item for item in super()._details() if item[0] != "reason")
        return (*kept, ("code", self.code), ("clean", self.clean))


class WebSocketHandshakeError(APIConnectionError):
    """A WebSocket handshake whose response broke the protocol, its negotiation, or a security limit; never retried."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        condition: HandshakeCondition,
        delivery_state: DeliveryState,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        retry_stop_reason: RetryStopReason | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep what the handshake broke; its phase is always connect."""
        error_choice(condition, _HANDSHAKE_CONDITIONS, "condition")
        _context(helper_id, operation)
        super().__init__(
            delivery_state=delivery_state,
            phase="connect",
            retry_stop_reason=retry_stop_reason,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.condition: HandshakeCondition = condition
        self.helper_id = helper_id
        self.operation = operation

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("condition", self.condition))


class WebSocketProxyError(APIConnectionError):
    """A proxy that refused or broke the tunnel to a WebSocket server; nothing reached the server and it is not retried.

    Neither the proxy's credentials nor its response body are kept.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        proxy_status_code: int | None = None,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        retry_stop_reason: RetryStopReason | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the proxy's status; the phase is always connect and nothing was sent."""
        if proxy_status_code is not None:
            _status(proxy_status_code, "proxy_status_code")
        _context(helper_id, operation)
        super().__init__(
            delivery_state=DeliveryState.NOT_SENT,
            phase="connect",
            retry_stop_reason=retry_stop_reason,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.proxy_status_code = proxy_status_code
        self.helper_id = helper_id
        self.operation = operation

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("proxy_status_code", self.proxy_status_code))


class HandshakeResponse(ProtocolError):  # noqa: N818 - The protocol contract names this signal.
    """A connector's signal that a handshake got an HTTP response other than 101, with at most 64 KiB of its body.

    Only connectors raise it; the client turns it into the call's HTTP failure, redirect, or retry, so it never reaches
    a caller. The headers and the body never appear in messages.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        status_code: int,
        headers: HeadersView,
        body_prefix: bytes,
        truncated: bool,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the status, the headers, and the body prefix."""
        _status(status_code, "status_code")
        _headers(headers)
        _raw_prefix(body_prefix, "body_prefix")
        _flag(truncated, "truncated")
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
        self.status_code = status_code
        self.headers = headers
        self.body_prefix = body_prefix
        self.truncated = truncated

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("status_code", self.status_code))


class DeliveryUnknownError(ProtocolError):
    """A message whose delivery is unknown: it may have reached the peer; it is never sent again automatically."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        delivery_state: DeliveryState,
        resume_state: ResumeState | None = None,
        message_id: str | None = None,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep how far the message may have got, any exportable resume state, and its declared message ID."""
        if delivery_state not in _UNKNOWN_DELIVERIES:
            msg = "delivery_state must be MAYBE_SENT or RESPONSE_STARTED"
            raise ValueError(msg)
        _resume_state(resume_state)
        error_string(message_id, "message_id", optional=True)
        super().__init__(
            helper_id=helper_id,
            operation=operation,
            delivery_state=delivery_state,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.resume_state = resume_state
        self.message_id = message_id

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("delivery_state", self.delivery_state.value))


class UploadDeliveryUnknownError(DeliveryUnknownError):
    """An upload append, part, or completion whose outcome stays unknown; the state is kept for an explicit resume.

    The `part` phase is reserved for the parts profile.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        phase: Literal["append", "part", "complete"],
        progress: UploadProgress,
        delivery_state: DeliveryState,
        resume_state: ResumeState | None = None,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the phase whose outcome is unknown and the progress the server confirmed before it."""
        error_choice(phase, ("append", "part", "complete"), "phase")
        _upload_progress(progress)
        super().__init__(
            delivery_state=delivery_state,
            resume_state=resume_state,
            helper_id=helper_id,
            operation=operation,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.phase = phase
        self.progress = progress

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("phase", self.phase))


class UploadSourceChangedError(ProtocolDataError):
    """Upload content whose size is not the upload's; nothing more is sent.

    `expected_size` is the upload's size and `actual_size` the size the source has now.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        expected_size: int,
        actual_size: int,
        location: Selector | RequestTarget | None = None,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the upload's size and the size the source has now."""
        error_count(expected_size, "expected_size")
        error_count(actual_size, "actual_size")
        super().__init__(
            condition="inconsistent",
            location=location,
            helper_id=helper_id,
            operation=operation,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.expected_size = expected_size
        self.actual_size = actual_size

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("expected_size", self.expected_size), ("actual_size", self.actual_size))


class UploadOffsetError(ProtocolDataError):
    """A remote upload offset that regressed, passed the content, or committed part of a chunk where none may be."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        confirmed_offset: int,
        expected_offset: int,
        remote_offset: int,
        size: int,
        resume_state: ResumeState | None = None,
        location: Selector | RequestTarget | None = None,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the confirmed, expected, and remote offsets and the size of the content."""
        for name, value in (
            ("confirmed_offset", confirmed_offset),
            ("expected_offset", expected_offset),
            ("remote_offset", remote_offset),
            ("size", size),
        ):
            error_count(value, name)
        _resume_state(resume_state)
        super().__init__(
            condition="inconsistent",
            location=location,
            helper_id=helper_id,
            operation=operation,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.confirmed_offset = confirmed_offset
        self.expected_offset = expected_offset
        self.remote_offset = remote_offset
        self.size = size
        self.resume_state = resume_state

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (
            *super()._details(),
            ("confirmed_offset", self.confirmed_offset),
            ("expected_offset", self.expected_offset),
            ("remote_offset", self.remote_offset),
            ("size", self.size),
        )


class UploadExpiredError(ResumeStateError):
    """An upload checkpoint past the expiry its server declared; its condition is always expired."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        expires_at: datetime,
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the server's UTC expiry."""
        _aware(expires_at)
        super().__init__(
            condition="expired",
            helper_id=helper_id,
            operation=operation,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.expires_at = expires_at


class NonResumableSourceError(ConfigurationError):
    """An upload source that cannot be read again, such as a one-shot stream or a reader that cannot seek."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        source_kind: Literal["iterable", "iterator", "stream", "reader"],
        helper_id: str | None = None,
        operation: OperationRef | None = None,
        operation_id: str | None = None,
        call_id: str | None = None,
        parent_session_id: str | None = None,
        info: ResponseInfo | None = None,
        cause: BaseException | None = None,
        secondary_errors: tuple[BaseException, ...] = (),
    ) -> None:
        """Keep the kind of source given; the condition is wrong_capability at the source argument."""
        error_choice(source_kind, ("iterable", "iterator", "stream", "reader"), "source_kind")
        super().__init__(
            field_path=("source",),
            reason="wrong_capability",
            helper_id=helper_id,
            operation=operation,
            operation_id=operation_id,
            call_id=call_id,
            parent_session_id=parent_session_id,
            info=info,
            cause=cause,
            secondary_errors=secondary_errors,
        )
        self.source_kind = source_kind

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("source_kind", self.source_kind))
