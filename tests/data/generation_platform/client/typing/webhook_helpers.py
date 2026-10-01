"""Keep event, key, and fact types through generated webhook helpers, in both modes and with each replay store."""

from __future__ import annotations

from datetime import datetime, timezone

from pets.protocols import AsyncMemoryReplayStore, KeySet, MemoryReplayStore, VerifiedWebhook, WebhookOptions
from pets.webhooks.callbacks import delivered
from pets.webhooks.github import push
from pets.webhooks.keys import HmacKey
from pets.webhooks.standard import message
from pets_models import Delivery, Message, Push
from typing_extensions import assert_type


def receive(raw_body: bytes, headers: list[tuple[str, str]], secret: bytes, store: MemoryReplayStore) -> Message:
    """Verify one delivery with the active key, claim its delivery id once, and return its event."""
    keys = KeySet(keys=(HmacKey(id="2026-09", secret=secret),))
    verified = message.verify(raw_body, headers, keys, now=datetime.now(timezone.utc), replay_store=store)
    return verified.data


def verify(body: bytes, headers: list[tuple[str, str]], now: datetime) -> None:
    """Return each helper's event type and the signature facts."""
    keys = KeySet(keys=(HmacKey(id="active", secret=b"secret"),))
    result = message.verify(
        body, headers, keys, now=now, replay_store=MemoryReplayStore(), options=WebhookOptions(max_keys=2)
    )
    assert_type(result, VerifiedWebhook[Message])
    assert_type(result.data, Message)
    assert_type(result.delivery_id, str | None)
    assert_type(result.timestamp, datetime | None)
    assert_type(result.matched_key_id, str)
    assert_type(result.duplicate, bool)
    assert_type(push.verify(body, (("X-Hub-Signature-256", "sha256=00"),), keys, now=now), VerifiedWebhook[Push])
    assert_type(delivered.verify(body, [], keys, now=now, options=None), VerifiedWebhook[Delivery])
    wider: VerifiedWebhook[object] = result
    del wider


async def awaited_helpers(body: bytes, headers: list[tuple[str, str]], now: datetime) -> None:
    """Await the asyncio helpers with an asyncio store, keeping the same result types."""
    keys = KeySet(keys=(HmacKey(id="active", secret=b"secret"),))
    result = await message.verify_async(body, headers, keys, now=now, replay_store=AsyncMemoryReplayStore())
    assert_type(result, VerifiedWebhook[Message])
    assert_type(await push.verify_async(body, headers, keys, now=now), VerifiedWebhook[Push])
