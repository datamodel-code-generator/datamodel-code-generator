"""Keep event, key, and fact types through generated webhook helpers, in both modes."""

from __future__ import annotations

from datetime import datetime, timezone

from pets.protocols import KeySet, VerifiedWebhook, WebhookOptions
from pets.webhooks.callbacks import delivered
from pets.webhooks.github import push
from pets.webhooks.keys import HmacKey
from pets.webhooks.standard import message
from pets.webhooks.stripe import event
from pets_models import Delivery, Message, Push
from typing_extensions import assert_type


def receive(raw_body: bytes, headers: list[tuple[str, str]], secret: bytes) -> Message:
    """Verify one delivery with the active key, return its event."""
    keys = KeySet(keys=(HmacKey(id="2026-09", secret=secret),))
    verified = message.verify(raw_body, headers, keys, now=datetime.now(timezone.utc))
    return verified.data


def verify(body: bytes, headers: list[tuple[str, str]], now: datetime) -> None:
    """Return each helper's event type and the signature facts."""
    keys = KeySet(keys=(HmacKey(id="active", secret=b"secret"),))
    result = message.verify(body, headers, keys, now=now, options=WebhookOptions(max_keys=2))
    assert_type(result, VerifiedWebhook[Message])
    assert_type(result.data, Message)
    assert_type(event.verify(body, headers, keys, now=now), VerifiedWebhook[Message])
    assert_type(result.delivery_id, str | None)
    assert_type(result.timestamp, datetime | None)
    assert_type(result.matched_key_id, str)
    assert_type(push.verify(body, (("X-Hub-Signature-256", "sha256=00"),), keys, now=now), VerifiedWebhook[Push])
    assert_type(delivered.verify(body, [], keys, now=now, options=None), VerifiedWebhook[Delivery])
    wider: VerifiedWebhook[object] = result
    del wider


async def awaited_helpers(body: bytes, headers: list[tuple[str, str]], now: datetime) -> None:
    """Await the asyncio helpers keeping the same result types."""
    keys = KeySet(keys=(HmacKey(id="active", secret=b"secret"),))
    result = await message.verify_async(body, headers, keys, now=now)
    assert_type(result, VerifiedWebhook[Message])
    assert_type(await push.verify_async(body, headers, keys, now=now), VerifiedWebhook[Push])
    assert_type(await event.verify_async(body, headers, keys, now=now), VerifiedWebhook[Message])
