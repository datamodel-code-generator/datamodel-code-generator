"""Hooks that observe the events of each call, and the events they receive.

A hook's return value never changes a request: request options, credentials, signers, and body factories do. Events
carry no query, header, body, or credential values, and a call without hooks builds none.
"""

from __future__ import annotations

from collections.abc import Mapping  # noqa: TC003 - Public annotations support get_type_hints().
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Protocol, TypeAlias

from ..model_codecs.wire import JSONScalar  # noqa: TC001 - Public annotations support get_type_hints().
from .errors import IOPhase  # noqa: TC001 - Public annotations support get_type_hints().

__all__ = ("AsyncHook", "CallEvent", "CallOutcome", "EventName", "Hook", "RetryReason")

EventName: TypeAlias = Literal[
    "call_start",
    "auth_start",
    "auth_end",
    "auth_wait",
    "limiter_wait",
    "limiter_acquired",
    "attempt_start",
    "response_headers",
    "transport_failure",
    "attempt_end",
    "retry_scheduled",
    "redirect",
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
    "auth_invalid_token",
]
CallOutcome: TypeAlias = Literal["success", "error", "cancel", "handed_off"]


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
    attempts: int = 0
    sends: int = 0
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
