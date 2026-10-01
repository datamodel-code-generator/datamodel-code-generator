"""Pure retry eligibility, server timing, and saturated backoff decisions."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Final, Literal

from ..model_codecs.unset import Unset
from .errors import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from .errors import DeliveryState, RetryStopReason
    from .hooks import RetryReason
    from .options import ResolvedRetryOptions
    from .responses import HeadersView

_ASCII_WHITESPACE: Final = " \t\r\n\f\v"
_SAFE_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
_AUTH_STATUSES: Final = frozenset({401, 403, 407})
_ERROR_STATUS_MIN: Final = 400
_ERROR_STATUS_MAX: Final = 599
_MONTHS: Final = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_DAY_NAME: Final = r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)"
_LONG_DAY_NAME: Final = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
_MONTH: Final = r"(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
_TIME: Final = r"(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
_HTTP_DATES: Final = tuple(
    re.compile(pattern)
    for pattern in (
        rf"{_DAY_NAME}, (?P<day>[0-9]{{2}}) {_MONTH} (?P<year>[0-9]{{4}}) {_TIME} GMT",
        rf"{_LONG_DAY_NAME}, (?P<day>[0-9]{{2}})-{_MONTH}-(?P<year>[0-9]{{2}}) {_TIME} GMT",
        rf"{_DAY_NAME} {_MONTH} (?P<day>[0-9]{{2}}| [0-9]) {_TIME} (?P<year>[0-9]{{4}})",
    )
)
_YEAR_WINDOW: Final = 50
_SHORT_YEAR_LENGTH: Final = 2
_LEAP_SECOND: Final = 60


@dataclass(frozen=True, slots=True)
class IdempotencyPlan:
    """The generated operation's complete server idempotency declaration."""

    header_name: str
    replay_safe_with_key: bool
    retention_seconds: float
    scope: str


@dataclass(frozen=True, slots=True)
class RetryHeaders:
    """Operation-bound names of optional response retry controls."""

    retry_after_ms_header: str | None
    should_retry_header: str | None


EMPTY_RETRY_HEADERS: Final = RetryHeaders(None, None)


def _header(value: str | Unset | None, declared: str | None, name: str) -> str | None:
    if isinstance(value, Unset):
        return declared
    if value is not None and (declared is None or value.lower() != declared.lower()):
        raise ConfigurationError(field_path=("retry", name), condition="invalid_value")
    return value


def bind_retry_headers(
    retry: ResolvedRetryOptions,
    *,
    retry_after_ms_header: str | None,
    should_retry_header: str | None,
) -> RetryHeaders:
    """Resolve metadata inheritance and reject undeclared or mismatched vendor controls."""
    milliseconds = _header(retry.retry_after_ms_header, retry_after_ms_header, "retry_after_ms_header")
    hint = _header(retry.should_retry_header, should_retry_header, "should_retry_header")
    if milliseconds is None and hint is None:
        return EMPTY_RETRY_HEADERS
    return RetryHeaders(milliseconds, hint)


def should_retry(headers: HeadersView | None, name: str | None) -> bool | None:
    """Read duplicate true/false hints, with false taking precedence."""
    if headers is None or name is None:
        return None
    result = None
    for value in headers.get_all(name):
        normalized = value.strip(_ASCII_WHITESPACE).lower()
        if normalized == "false":
            return False
        if normalized == "true":
            result = True
    return result


def status_retry_reason(status: int, retry: ResolvedRetryOptions, *, hint: bool | None) -> RetryReason | None:
    """Select ordinary error-status candidates without enabling authentication retries."""
    if (
        _ERROR_STATUS_MIN <= status <= _ERROR_STATUS_MAX
        and status not in _AUTH_STATUSES
        and (status in retry.statuses or hint is True)
    ):
        return "status"
    return None


@dataclass(frozen=True, slots=True, kw_only=True)
class RetryState:
    """Immutable facts at one retry decision boundary."""

    failure_kind: Literal["status", "transport", "auth"]
    reason: RetryReason | None
    method: str
    retry_safety: Literal["method_default", "idempotent", "never"]
    idempotency: IdempotencyPlan | None
    key_expires_at: float | None
    delivery_state: DeliveryState
    resource_attempt_count: int
    body_replayable: bool
    network_available: bool
    server_hint: bool | None
    proven_not_sent: bool = False
    exchange_available: bool = True


def replay_safe(
    method: str,
    retry_safety: Literal["method_default", "idempotent", "never"],
    idempotency: IdempotencyPlan | None,
    key_expires_at: float | None,
    *,
    now: float,
) -> bool:
    """Check payload-independent safety after the operation-never gate has passed."""
    if key_expires_at is not None and now >= key_expires_at:
        return False
    return (
        method in _SAFE_METHODS
        or retry_safety == "idempotent"
        or (idempotency is not None and idempotency.replay_safe_with_key and key_expires_at is not None)
    )


def body_replay_safe(
    method: str,
    retry_safety: Literal["method_default", "idempotent", "never"],
    idempotency: IdempotencyPlan | None,
    key_expires_at: float | None,
    *,
    now: float,
) -> bool:
    """Return whether a request may send its body again with its method, as a 307 or 308 redirect does.

    An operation that never retries never sends its body again; otherwise the payload-independent safety decides.
    """
    return retry_safety != "never" and replay_safe(method, retry_safety, idempotency, key_expires_at, now=now)


_NOT_RETRYABLE: Final[dict[str, RetryStopReason]] = {
    "auth": "auth_unrefreshable",
    "status": "status_not_retryable",
    "transport": "transport_not_retryable",
}


def _policy_stop(
    state: RetryState,
    retry: ResolvedRetryOptions,
    retry_owner: Literal["sdk", "transport"],
) -> RetryStopReason | None:
    if state.reason is None or (state.reason == "pool_timeout" and not retry.retry_on_pool_timeout):
        return _NOT_RETRYABLE[state.failure_kind]
    if retry_owner == "transport":
        return "retry_owned_by_transport"
    if state.retry_safety == "never":
        return "operation_never"
    if state.server_hint is False:
        return "server_forbids_retry"
    if retry.max_retries == 0:
        return "disabled"
    return None


def retry_stop(
    state: RetryState,
    retry: ResolvedRetryOptions,
    *,
    retry_owner: Literal["sdk", "transport"],
    now: float,
    auth_recovery_used: bool = False,
) -> RetryStopReason | None:
    """Return the first failed retry gate, after termination precedence has been checked."""
    if (stop := _policy_stop(state, retry, retry_owner)) is not None:
        return stop
    if state.resource_attempt_count >= 1 + retry.max_retries:
        return "max_retries_exhausted"
    if state.reason == "auth_invalid_token" and auth_recovery_used:
        return "auth_recovery_exhausted"
    if not state.exchange_available:
        return "auth_exchange_budget_exhausted"
    return _replay_stop(state, now=now)


def _replay_stop(state: RetryState, *, now: float) -> RetryStopReason | None:
    """Return why the request cannot be sent again: its body, its safety, or the network budget."""
    if not state.body_replayable:
        return "body_not_replayable"
    safe = replay_safe(
        state.method,
        state.retry_safety,
        state.idempotency,
        state.key_expires_at,
        now=now,
    )
    unsent = state.proven_not_sent and (state.key_expires_at is None or now < state.key_expires_at)
    if not (safe or unsent):
        return "unsafe_operation"
    if not state.network_available:
        return "network_budget_exhausted"
    return None


@dataclass(frozen=True, slots=True)
class ServerDelay:
    """The original server delay and its monotonic receipt time."""

    seconds: float
    received_at: float


def _integer(value: str) -> float | None:
    if not value.isascii() or not value.isdecimal():
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _date(value: str, received_wall_time: float) -> float | None:
    matched = next((matched for pattern in _HTTP_DATES if (matched := pattern.fullmatch(value)) is not None), None)
    if matched is None:
        return None
    year = int(matched["year"])
    month, day = _MONTHS.index(matched["month"]) + 1, int(matched["day"])
    hour, minute, second = int(matched["hour"]), int(matched["minute"]), int(matched["second"])
    if second > _LEAP_SECOND:
        return None
    if len(matched["year"]) == _SHORT_YEAR_LENGTH:
        received = datetime.fromtimestamp(received_wall_time, timezone.utc)
        year += received.year // 100 * 100
        if (year, month, day, hour, minute, second) > (
            received.year + _YEAR_WINDOW,
            received.month,
            received.day,
            received.hour,
            received.minute,
            received.second,
        ):
            year -= 100
    date = datetime(year, month, day, hour, minute, min(second, _LEAP_SECOND - 1), tzinfo=timezone.utc)
    return date.timestamp() + (second == _LEAP_SECOND)


def _seconds(value: str, received_wall_time: float) -> float | None:
    normalized = value.strip(_ASCII_WHITESPACE)
    if (integer := _integer(normalized)) is not None:
        return integer
    try:
        date = _date(normalized, received_wall_time)
    except (ValueError, OverflowError, OSError):
        return None
    return None if date is None else max(0.0, date - received_wall_time)


def _maximum(values: Iterable[str], parse: Callable[[str], float | None]) -> float | None:
    return max((parsed for value in values if (parsed := parse(value)) is not None), default=None)


def retry_after(
    headers: HeadersView,
    *,
    milliseconds_header: str | None,
    received_at: float,
    received_wall_time: float,
) -> ServerDelay | None:
    """Parse independent raw values, preferring valid vendor milliseconds including zero."""
    if milliseconds_header is not None:
        milliseconds = _maximum(
            headers.get_all(milliseconds_header), lambda value: _integer(value.strip(_ASCII_WHITESPACE))
        )
        if milliseconds is not None:
            return ServerDelay(milliseconds / 1000.0, received_at)
    seconds = header_delay(headers, "Retry-After", received_wall_time)
    return None if seconds is None else ServerDelay(seconds, received_at)


def header_delay(headers: HeadersView, name: str, received_wall_time: float) -> float | None:
    """Return the longest valid delay in seconds that a header gives as delta seconds or an HTTP date, or None.

    A date is measured from the response's receipt wall time and never gives a negative delay.
    """
    return _maximum(headers.get_all(name), lambda value: _seconds(value, received_wall_time))


@dataclass(frozen=True, slots=True)
class RetryDelay:
    """One chosen backoff cap and absolute wait target, retained across cleanup and hooks."""

    reason: RetryReason
    backoff_cap: float
    not_before: float
    delay: float


@dataclass(frozen=True, slots=True)
class RetryTiming:
    """The current retry clock boundary and an unevaluated random source."""

    previous_cap: float | None
    now: float
    deadline_at: float | None
    draw: Callable[[], float] = field(repr=False)


def retry_delay(
    retry: ResolvedRetryOptions,
    *,
    reason: RetryReason,
    server: ServerDelay | None,
    timing: RetryTiming,
) -> RetryDelay | Literal["server_delay_exceeds_limit", "deadline_insufficient"]:
    """Choose one bounded backoff without shortening a server delay or restarting its clock."""
    now, deadline_at, previous_cap = timing.now, timing.deadline_at, timing.previous_cap
    server_target = now
    if server is not None:
        if retry.max_retry_after is not None and server.seconds > retry.max_retry_after:
            return "server_delay_exceeds_limit"
        server_target = server.received_at + server.seconds
        if deadline_at is not None and server_target >= deadline_at:
            return "deadline_insufficient"
    cap = retry.initial_delay if previous_cap is None else min(retry.max_delay, previous_cap * 2.0)
    delay = min(cap * timing.draw(), math.nextafter(cap, 0.0)) if retry.jitter == "full" and cap else cap
    target = max(now + delay, server_target)
    if deadline_at is not None and target >= deadline_at:
        return "deadline_insufficient"
    return RetryDelay(reason, cap, target, max(0.0, target - now))
