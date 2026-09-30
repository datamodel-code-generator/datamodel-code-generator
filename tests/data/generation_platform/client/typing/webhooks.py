"""Preserve webhook event and borrowed-key types through the public contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pets.errors import ProtocolConfigurationError, WebhookReplayError
from pets.options import UNSET, Unset
from pets.protocols import (
    AsyncMemoryReplayStore,
    AsyncReplayStore,
    KeySet,
    MemoryReplayStore,
    OperationRef,
    ReplayStore,
    ResolvedWebhookOptions,
    VerifiedSignature,
    VerifiedWebhook,
    Verifier,
    WebhookOptions,
)
from typing_extensions import assert_type

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True)
class ApplicationKey:
    """An application-owned key whose internal fields are not part of the verifier contract."""

    identity: str


class ApplicationVerifier:
    """A structural implementation of the borrowed synchronous verifier interface."""

    def verify(
        self,
        raw_body: bytes,
        ordered_headers: tuple[tuple[str, str], ...],
        keys: KeySet[ApplicationKey],
        now: datetime,
        limits: ResolvedWebhookOptions,
    ) -> VerifiedSignature:
        """Keep the application's verification implementation outside this static sample."""
        raise NotImplementedError


def contracts(now: datetime, limits: ResolvedWebhookOptions) -> None:
    """Keep one key type and expose the declared record and option field types."""
    keys = KeySet(keys=(ApplicationKey("active"),))
    assert_type(keys, KeySet[ApplicationKey])
    assert_type(keys.keys, tuple[ApplicationKey, ...])
    verifier: Verifier[ApplicationKey] = ApplicationVerifier()
    assert_type(verifier.verify(b"event", (), keys, now, limits), VerifiedSignature)
    event = VerifiedWebhook[str](
        data="event", delivery_id=None, timestamp=None, matched_key_id="active", duplicate=False
    )
    assert_type(event.data, str)
    accepts_event(event)
    options = WebhookOptions(max_keys=2, past_tolerance=0, future_tolerance=UNSET)
    assert_type(options.max_keys, int | Unset)
    assert_type(options.past_tolerance, float | Unset)
    assert_type(limits.past_tolerance, float)
    operation = OperationRef(pointer="/webhooks/event/post")
    assert_type(operation.document, str | None)
    failure = ProtocolConfigurationError(field_path=("keys",), condition="invalid_value", operation=operation)
    assert_type(failure.operation, OperationRef | None)
    duplicate = WebhookReplayError(delivery_id="delivery", namespace="application")
    assert_type(duplicate.namespace, str)


def accepts_event(event: VerifiedWebhook[object]) -> None:
    """Accept a more specific immutable event through the covariant result contract."""
    assert_type(event.data, object)


def claim(now: datetime, store: ReplayStore) -> None:
    """Use a memory store wherever a synchronous borrowed store is accepted."""
    assert_type(store.claim("application", "delivery", now), bool)
    memory: ReplayStore = MemoryReplayStore(max_entries=2)
    assert_type(memory.claim("application", "delivery", now), bool)


async def claim_async(now: datetime, store: AsyncReplayStore) -> None:
    """Await one asynchronous claim and retain its boolean result."""
    assert_type(await store.claim("application", "delivery", now), bool)
    memory: AsyncReplayStore = AsyncMemoryReplayStore(max_entries=2)
    assert_type(await memory.claim("application", "delivery", now), bool)
