"""Reject changed key types, mutable records, invalid options, and sync/async store mismatches."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pets.errors import ProtocolConfigurationError, WebhookVerificationError
from pets.protocols import (
    AsyncMemoryReplayStore,
    AsyncReplayStore,
    KeySet,
    MemoryReplayStore,
    OperationRef,
    ReplayStore,
    VerifiedSignature,
    VerifiedWebhook,
    Verifier,
    WebhookOptions,
)

if TYPE_CHECKING:
    from datetime import datetime


def wrong_keys(keys: KeySet[str], verifier: Verifier[str]) -> None:
    """Require the same invariant key type and an immutable tuple of borrowed keys."""
    wider_keys: KeySet[object] = keys  # error
    wider_verifier: Verifier[object] = verifier  # error
    KeySet[str](keys=["active"])  # error
    keys.keys = ("changed",)  # error
    del wider_keys, wider_verifier


def wrong_records(signature: VerifiedSignature, event: VerifiedWebhook[str]) -> None:
    """Reject record mutation, wrong event payloads, and undeclared option or error values."""
    signature.matched_key_id = "changed"  # error
    event.data = "changed"  # error
    wrong_event: VerifiedWebhook[int] = event  # error
    WebhookOptions(max_keys=None)  # error
    WebhookOptions(max_keys=1.5)  # error
    WebhookOptions(replay_ttl="long")  # error
    OperationRef("/webhooks/event/post", pointer="/webhooks/event/post")  # error
    ProtocolConfigurationError(field_path=("keys",), condition="missing")  # error
    ProtocolConfigurationError(field_path=("keys",), condition="invalid_value", operation="event")  # error
    WebhookVerificationError(condition="bad_key")  # error
    del wrong_event


async def wrong_stores(now: datetime, sync: ReplayStore, asynchronous: AsyncReplayStore) -> None:
    """Keep synchronous and asynchronous store operations distinct."""
    await sync.claim("application", "delivery", now)  # error
    result: bool = asynchronous.claim("application", "delivery", now)  # error
    sync = AsyncMemoryReplayStore()  # error
    asynchronous = MemoryReplayStore()  # error
    MemoryReplayStore(max_entries="many")  # error
    del result
