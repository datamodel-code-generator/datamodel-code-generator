"""Bind one key type through an adapter helper's verifier and keys, and keep mapped and unsigned event types."""

from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pets.errors import ProtocolDataError
from pets.protocols import (
    KeySet,
    ResolvedWebhookOptions,
    VerifiedSignature,
    VerifiedWebhook,
)
from pets.webhooks.adapted import message, plain
from pets.webhooks.keys import HmacKey
from pets.webhooks.mapped import event as mapped
from pets.webhooks.stripe import event
from pets.webhooks.unsigned import event as unsigned_event
from pets.webhooks.unsigned import message as unsigned_message
from pets_models import Customer, Invoice, Message
from typing_extensions import assert_type


@dataclass(frozen=True)
class StripeKey:
    """An application key: the SDK never reads its fields."""

    name: str
    secret: bytes = field(repr=False)


class StripeVerifier:
    """Verify `Stripe-Signature: t=<seconds>,v1=<hex>` over `<t>.<raw body>` with the first matching key."""

    def verify(
        self,
        raw_body: bytes,
        ordered_headers: tuple[tuple[str, str], ...],
        keys: KeySet[StripeKey],
        now: datetime,
        limits: ResolvedWebhookOptions,
    ) -> VerifiedSignature:
        """Return the signed timestamp and the matched key, or raise ProtocolDataError."""
        del now
        values = [value for name, value in ordered_headers if name.lower() == "stripe-signature"]
        items = [item.partition("=") for item in (values[0].split(",") if len(values) == 1 else ())]
        stamps = [value for scheme, _, value in items if scheme == "t"]
        encoded = [value for scheme, _, value in items if scheme == "v1"]
        if len(stamps) != 1 or not (stamps[0].isascii() and stamps[0].isdigit()) or not encoded:
            raise ProtocolDataError(reason="malformed_signature")
        if (count := len(encoded)) > limits.max_signatures:
            raise ProtocolDataError(reason="too_large")
        try:
            signatures = [bytes.fromhex(value) for value in encoded if value.isascii()]
        except ValueError:
            signatures = []
        if len(signatures) != count:
            raise ProtocolDataError(reason="malformed_signature")
        signed = stamps[0].encode() + b"." + raw_body
        for key in keys.keys:
            expected = hmac.new(key.secret, signed, "sha256").digest()
            if any(hmac.compare_digest(expected, signature) for signature in signatures):
                moment = datetime.fromtimestamp(int(stamps[0]), timezone.utc)
                return VerifiedSignature(delivery_id=None, timestamp=moment, matched_key_id=key.name)
        raise ProtocolDataError(reason="invalid_signature")


def receive_stripe(raw_body: bytes, headers: list[tuple[str, str]], secret: bytes) -> Invoice | Customer:
    """Verify one Stripe-style delivery with the application's verifier and return its mapped event."""
    keys = KeySet(keys=(StripeKey(name="2026-09", secret=secret),))
    verified = event.verify(raw_body, headers, keys, verifier=StripeVerifier(), now=datetime.now(timezone.utc))
    return verified.data


def decode(raw_body: bytes) -> Invoice | Customer:
    """Decode an unsigned delivery, whose event nothing authenticates."""
    return unsigned_event.decode_unverified(raw_body)


def adapted(body: bytes, headers: list[tuple[str, str]], now: datetime) -> None:
    """Keep the helpers' event types with the application's key type in both modes."""
    keys = KeySet(keys=(StripeKey(name="active", secret=b"secret"),))
    verifier = StripeVerifier()
    result = event.verify(body, headers, keys, verifier=verifier, now=now)
    assert_type(result, VerifiedWebhook[Invoice | Customer])
    assert_type(result.data, Invoice | Customer)
    assert_type(message.verify(body, (), keys, verifier=verifier, now=now), VerifiedWebhook[Message])
    assert_type(plain.verify(body, headers, keys, verifier=verifier, now=now, options=None), VerifiedWebhook[Message])
    hmac_keys = KeySet(keys=(HmacKey(id="active", secret=b"secret"),))
    assert_type(mapped.verify(body, headers, hmac_keys, now=now), VerifiedWebhook[Invoice | Customer])
    assert_type(unsigned_event.decode_unverified(body), Invoice | Customer)
    assert_type(unsigned_message.decode_unverified(body, options=None), Message)


async def awaited(body: bytes, headers: list[tuple[str, str]], now: datetime) -> None:
    """Await the asyncio helper with the same synchronous verifier."""
    keys = KeySet(keys=(StripeKey(name="active", secret=b"secret"),))
    result = await event.verify_async(body, headers, keys, verifier=StripeVerifier(), now=now)
    assert_type(result, VerifiedWebhook[Invoice | Customer])
