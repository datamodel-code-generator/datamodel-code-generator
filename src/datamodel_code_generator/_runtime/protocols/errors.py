"""Exceptions of protocol helpers whose fields name protocol records, loaded only when a helper or caller needs them.

The generated `errors` module exports them beside every other exception; their messages name only safe metadata.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Generic, Literal, TypeAlias, get_args

from typing_extensions import TypeIs, TypeVar

from ..client.errors import (
    ProtocolConfigurationError,
    ProtocolError,
    ProtocolStoreError,
    ResultUnavailableError,
    error_choice,
    error_count,
    error_string,
    error_time,
)
from ..client.responses import ResponseInfo  # noqa: TC001 - Public annotations support get_type_hints().
from ..model_codecs.unset import UNSET, Unset
from .caches import string_tuple
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
    "CacheInvalidationError",
    "CacheProtocolError",
    "CacheStoreError",
    "CacheValidatorConflictError",
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
T_co = TypeVar("T_co", covariant=True, default=object)

_DataCondition: TypeAlias = Literal["missing", "null", "type", "value", "malformed", "inconsistent"]
_SessionLimitKind: TypeAlias = Literal["network_sends", "pages", "items", "polls", "reconnects", "parts"]

_DATA_CONDITIONS: Final = get_args(_DataCondition)
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


def _raw_prefix(value: object) -> None:
    if not isinstance(value, bytes) or len(value) > MAX_RAW_PREFIX:
        msg = "raw_prefix must be at most 65536 bytes"
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


class CacheValidatorConflictError(ProtocolConfigurationError):
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
            condition="binding_mismatch",
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


class CacheInvalidationError(CacheStoreError, Generic[T_co]):
    """A tag invalidation the store failed; a mutation's completed result stays available and is never sent again."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        tags: tuple[str, ...],
        completed_result: T_co | Unset = UNSET,
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
        """Keep the tags and the mutation's result, if any; the store action is fixed."""
        if not string_tuple(tags):
            msg = "tags must be a tuple of strings"
            raise ValueError(msg)
        super().__init__(
            action="invalidate",
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
        self.tags = tags
        self._completed_result = completed_result

    @property
    def completed_result(self) -> T_co | Unset:
        """Return the mutation's result, or UNSET for a manual invalidation."""
        return self._completed_result

    @property
    def has_completed_result(self) -> bool:
        """Return whether a mutation completed before the invalidation failed."""
        return not isinstance(self._completed_result, Unset)

    def require_result(self) -> T_co:
        """Return the mutation's completed result, or raise ResultUnavailableError."""
        if isinstance(result := self._completed_result, Unset):
            raise ResultUnavailableError(operation_id=self.operation_id, call_id=self.call_id)
        return result
