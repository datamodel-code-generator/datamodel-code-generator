"""Keep event and key types through generated Ed25519, RSA-PSS, and HMAC webhook helpers, in both modes."""

from __future__ import annotations

from datetime import datetime

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey, RSAPublicNumbers
from pets.protocols import KeySet, VerifiedWebhook
from pets.webhooks.github import push
from pets.webhooks.keyed import rsa
from pets.webhooks.keys import Ed25519Key, HmacKey, RSAPSSKey
from pets.webhooks.standard import ed25519
from pets.webhooks.wycheproof import rsa as wycheproof
from pets_models import Count, Message, Push
from typing_extensions import assert_type


def keys(raw_key: bytes, modulus: int) -> tuple[KeySet[Ed25519Key], KeySet[RSAPSSKey]]:
    """Wrap an Ed25519 public key from its raw bytes and an RSA public key from its numbers."""
    ed25519_key = Ed25519Key(id="active", public_key=Ed25519PublicKey.from_public_bytes(raw_key))
    rsa_key = RSAPSSKey(id="rsa-2048", public_key=RSAPublicNumbers(65537, modulus).public_key())
    return KeySet(keys=(ed25519_key,)), KeySet(keys=(rsa_key,))


def verify(body: bytes, headers: list[tuple[str, str]], raw_key: bytes, modulus: int, now: datetime) -> None:
    """Return each helper's event type."""
    ed25519_keys, rsa_keys = keys(raw_key, modulus)
    assert_type(ed25519_keys.keys[0].public_key, Ed25519PublicKey)
    assert_type(rsa_keys.keys[0].public_key, RSAPublicKey)
    assert_type(rsa_keys.keys[0].id, str)
    assert_type(ed25519.verify(body, headers, ed25519_keys, now=now), VerifiedWebhook[Message])
    assert_type(rsa.verify(body, headers, rsa_keys, now=now), VerifiedWebhook[Message])
    assert_type(wycheproof.verify(body, headers, rsa_keys, now=now), VerifiedWebhook[Count])
    hmac_keys = KeySet(keys=(HmacKey(id="active", secret=b"secret"),))
    assert_type(push.verify(body, headers, hmac_keys, now=now), VerifiedWebhook[Push])


async def awaited_helpers(
    body: bytes, headers: list[tuple[str, str]], raw_key: bytes, modulus: int, now: datetime
) -> None:
    """Await the asyncio helpers, keeping the same result types."""
    ed25519_keys, rsa_keys = keys(raw_key, modulus)
    assert_type(await ed25519.verify_async(body, headers, ed25519_keys, now=now), VerifiedWebhook[Message])
    assert_type(await rsa.verify_async(body, headers, rsa_keys, now=now), VerifiedWebhook[Message])
