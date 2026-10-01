"""Exceptions of protocol helpers whose fields name protocol records, loaded only when a helper or caller needs them.

The generated `errors` module exports them beside every other exception; their messages name only safe metadata.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Generic, Literal, TypeAlias, get_args

from typing_extensions import TypeIs, TypeVar

from ..client.errors import (
    DeliveryState,
    ProtocolError,
    ProtocolSizeError,
    error_choice,
    error_count,
    error_string,
    error_time,
)
from ..client.responses import ResponseInfo  # noqa: TC001 - Public annotations support get_type_hints().
from .records import (
    PROGRESS_KEYS,
    PollSnapshot,
    ProgressKey,
    ProtocolProgress,
    RequestTarget,
    Selector,
)
from .references import OperationRef  # noqa: TC001 - Public annotations support get_type_hints().
from .resume import ResumeState, ResumeStateError, ResumeStateTooLargeError

__all__ = (
    "BatchDeliveryUnknownError",
    "BatchItemTooLargeError",
    "BatchProtocolError",
    "DeliveryUnknownError",
    "IncompleteFrameError",
    "OperationCancelledError",
    "OperationFailedError",
    "PaginationCycleError",
    "PollWaitLimitError",
    "PollingStateError",
    "ProtocolDataError",
    "ProtocolStateError",
    "ResumeStateError",
    "ResumeStateTooLargeError",
    "SessionLimitError",
    "StreamDecodeError",
    "StreamInterruptedError",
    "StreamRemoteError",
    "StreamResumeExhaustedError",
)

E_co = TypeVar("E_co", covariant=True, default=object)
P_co = TypeVar("P_co", covariant=True, default=object)
R_co = TypeVar("R_co", covariant=True, default=object)

_DataCondition: TypeAlias = Literal["missing", "null", "type", "value", "malformed", "inconsistent"]
_SessionLimitKind: TypeAlias = Literal["network_sends", "pages", "items", "polls", "reconnects", "parts"]

_DATA_CONDITIONS: Final = get_args(_DataCondition)
_SESSION_LIMIT_KINDS: Final = get_args(_SessionLimitKind)
_LOCATIONS: Final = (*get_args(Selector), *get_args(RequestTarget))
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


def _raw_prefix(value: object) -> None:
    if not isinstance(value, bytes) or len(value) > MAX_RAW_PREFIX:
        msg = "raw_prefix must be at most 65536 bytes"
        raise ValueError(msg)


def _unknown_delivery(value: object) -> None:
    if value not in {DeliveryState.MAYBE_SENT, DeliveryState.RESPONSE_STARTED}:
        msg = "delivery_state must be MAYBE_SENT or RESPONSE_STARTED"
        raise ValueError(msg)


def _is_tuple(value: object) -> TypeIs[tuple[object, ...]]:
    return isinstance(value, tuple)


def _tuple(value: object, field: str) -> tuple[object, ...]:
    if not _is_tuple(value):
        msg = f"{field} must be a tuple"
        raise ValueError(msg)
    return value


def _indices(value: object, field: str) -> tuple[int, ...]:
    return tuple(error_count(item, field) for item in _tuple(value, field))


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
        self.progress = copied
        self.resume_state = resume_state

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("kind", self.kind), ("limit", self.limit))


class StreamResumeExhaustedError(SessionLimitError):
    """Automatic stream reconnection that exhausted its reconnect or send budget; it never ends as a normal EOF."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        kind: Literal["reconnects", "network_sends"],
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
        """Keep the exhausted reconnection budget with the stream's progress and resume state."""
        error_choice(kind, ("reconnects", "network_sends"), "kind")
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
        """Keep the record's sequence and at most 64 KiB of its raw bytes, which never appear in messages."""
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
        self.event_type = event_type
        self._data = data
        self.sequence = sequence

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("sequence", self.sequence))

    @property
    def data(self) -> E_co:
        """Return the decoded error event value."""
        return self._data


class DeliveryUnknownError(ProtocolError):
    """A send that may have reached the server without a confirmed outcome; it is never resent automatically."""

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
        """Keep how far the send got, any resume state, and the message it concerns."""
        _unknown_delivery(delivery_state)
        _resume_state(resume_state)
        error_string(message_id, "message_id", optional=True)
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
        self.delivery_state = delivery_state
        self.resume_state = resume_state
        self.message_id = message_id

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("delivery_state", self.delivery_state.value))


class BatchProtocolError(ProtocolDataError):
    """Batch results that cannot be matched with their items: missing, duplicate, or unknown IDs, or another count.

    `indices` are the input indices of the items concerned; the condition is always inconsistent.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        indices: tuple[int, ...],
        location: Selector | RequestTarget | None = None,
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
        """Keep the input indices of the items whose results do not match."""
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
        self.indices = _indices(indices, "indices")

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("indices", self.indices))


class BatchItemTooLargeError(ProtocolSizeError):
    """An input item whose encoded bytes exceed what one request may carry; it is never sent."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        index: int,
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
        """Keep the item's input index; the kind is always body and the unit bytes."""
        error_count(index, "index")
        super().__init__(
            kind="body",
            limit=error_count(limit, "limit"),
            observed=error_count(observed, "observed"),
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
        self.index = index

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("index", self.index))


class BatchDeliveryUnknownError(DeliveryUnknownError, Generic[R_co]):
    """A batch request that may have reached the server without a response; its items are never resent.

    `partial_results` are that batch's records not yet returned, and `batch_indices` the input indices of its items.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        partial_results: tuple[R_co, ...],
        batch_indices: tuple[int, ...],
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
        """Keep the batch's unreturned records and its items' input indices; there is no message ID."""
        _tuple(partial_results, "partial_results")
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
        self._partial_results = partial_results
        self.batch_indices = _indices(batch_indices, "batch_indices")

    @property
    def partial_results(self) -> tuple[R_co, ...]:
        """Return the batch's records that were not returned before the error."""
        return self._partial_results

    def _details(self) -> tuple[tuple[str, object], ...]:
        return (*super()._details(), ("batch_indices", self.batch_indices))
