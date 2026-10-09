"""Hooks that observe the events of each call, and the events they receive.

A hook's return value never changes a request: request options, credentials, and signers do. Events
carry no query, header, body, or credential values, and a call without hooks builds none.
"""

from __future__ import annotations

from collections.abc import Mapping  # noqa: TC003 - Public annotations support get_type_hints().
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Protocol, TypeAlias

__all__ = (
    "AsyncHook",
    "AsyncLimiter",
    "AsyncPermit",
    "CallEvent",
    "CallOutcome",
    "EventName",
    "Hook",
    "IOPhase",
    "Limiter",
    "LimiterContext",
    "Permit",
    "RetryReason",
)

IOPhase: TypeAlias = Literal["connect", "read", "write", "pool", "unknown"]
EventName: TypeAlias = Literal[
    "call_start",
    "limiter_wait",
    "limiter_acquired",
    "attempt_start",
    "response_headers",
    "transport_failure",
    "attempt_end",
    "retry_scheduled",
    "call_end",
    "stream_end",
]
RetryReason: TypeAlias = Literal[
    "status",
    "connect_timeout",
    "connect_error",
    "pool_timeout",
    "read_timeout",
    "read_error",
    "write_timeout",
    "write_error",
    "remote_protocol",
]
CallOutcome: TypeAlias = Literal["success", "error", "cancel", "handed_off"]
JSONScalar: TypeAlias = bool | int | float | str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class CallEvent:
    """One event of a call: its identifiers, the attempt it concerns, and safe facts, never payloads or secrets.

    A URL appears only as the operation's path template and the origin, a request ID only when the operation declares
    its header, and the context is the caller's secret-free scalar map. `options` summarizes the effective settings on
    `call_start` only.
    """

    name: EventName
    call_id: str
    operation_id: str | None
    path: str | None
    origin: str | None
    parent_session_id: str | None = None
    attempt_index: int | None = None
    sent: bool = False
    phase: IOPhase | None = None
    status: int | None = None
    duration: float | None = None
    retry_reason: RetryReason | None = None
    request_id: str | None = None
    outcome: CallOutcome | None = None
    attempt_count: int = 0
    options: Mapping[str, JSONScalar] | None = None
    context: Mapping[str, JSONScalar] = field(default_factory=lambda: MappingProxyType({}))


class Hook(Protocol):
    """A synchronous observer of call events."""

    def on_event(self, event: CallEvent) -> None:
        """Observe one event; raising stops the call's further network actions."""
        ...


class AsyncHook(Protocol):
    """An asynchronous observer of call events, which only asyncio clients await."""

    async def on_event(self, event: CallEvent) -> None:
        """Observe one event; raising stops the call's further network actions."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class LimiterContext:
    """The safe identifiers and remaining time of a call entering its application limiter."""

    operation_id: str | None
    origin: str
    call_id: str
    parent_session_id: str | None
    remaining_timeout: float | None


class Permit(Protocol):
    """Permission to hold one synchronous request or response until it is released."""

    def release(self) -> None:
        """Release the permission; repeated calls have no further effect."""
        ...


class AsyncPermit(Protocol):
    """Permission to hold one asynchronous request or response until it is released."""

    async def release(self) -> None:
        """Release the permission; repeated calls have no further effect."""
        ...


class Limiter(Protocol):
    """A synchronous application limiter that admits a call under its remaining budget."""

    def acquire(self, context: LimiterContext) -> Permit:
        """Wait for and return permission to send and retain the response."""
        ...


class AsyncLimiter(Protocol):
    """An asyncio application limiter that admits a call under its remaining budget."""

    async def acquire(self, context: LimiterContext) -> AsyncPermit:
        """Wait for and return permission to send and retain the response."""
        ...
