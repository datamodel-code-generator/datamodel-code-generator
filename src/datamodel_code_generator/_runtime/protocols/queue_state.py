"""Pure normalization and expired-lease recovery shared by the builtin queue stores."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from ..client.errors import ProtocolConfigurationError
from .queues import QueueEntry, QueueOutcome

if TYPE_CHECKING:
    from .queues import QueueState


def instant(value: datetime, name: str) -> datetime:
    """Normalize an aware instant to UTC, rejecting invalid adapter arguments."""
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ProtocolConfigurationError(field_path=(name,), condition="invalid_value")
    return value.astimezone(timezone.utc)


def entry_value(value: object) -> QueueEntry:
    """Require a public entry and normalize all its aware instants consistently."""
    if not isinstance(value, QueueEntry):
        raise ProtocolConfigurationError(field_path=("entry",), condition="invalid_value")
    result = value.result
    if result is not None and result.retry_at is not None:
        result = replace(result, retry_at=instant(result.retry_at, "retry_at"))
    return replace(
        value,
        created_at=instant(value.created_at, "created_at"),
        expires_at=instant(value.expires_at, "expires_at"),
        not_before=instant(value.not_before, "not_before"),
        lease_until=None if value.lease_until is None else instant(value.lease_until, "lease_until"),
        result=result,
    )


def recover(entry: QueueEntry, now: datetime) -> QueueEntry:
    """Recover only expired leases, retaining terminal outcome priority and conditional-A crash intent."""
    if entry.state != "leased" or entry.lease_until is None or entry.lease_until > now:
        return entry
    state: QueueState = "pending"
    result = entry.result
    if result is not None and result.category != "retryable":
        recorded: dict[str, QueueState] = {
            "success": "succeeded",
            "permanent": "dead",
            "unknown": "delivery_unknown",
            "cancelled": "delivery_unknown" if entry.send_intent else "cancelled",
        }
        state = recorded[result.category]
    elif entry.cancel_requested:
        state = "delivery_unknown" if entry.send_intent else "cancelled"
        result = QueueOutcome(category="cancelled")
    return replace(
        entry,
        state=state,
        result=result,
        send_intent=entry.send_intent if state == "pending" else False,
        lease_id=None,
        lease_until=None,
        version=uuid4().hex,
    )
