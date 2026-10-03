"""Reject keys of another profile, private keys, encoded keys, and positional key arguments."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pets.protocols import KeySet
from pets.webhooks.github import push
from pets.webhooks.wycheproof import rsa
from pets.webhooks.keys import Ed25519Key, HmacKey, RSAPSSKey
from pets.webhooks.rfc8032 import ed25519

if TYPE_CHECKING:
    from datetime import datetime

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey


def wrong_calls(  # noqa: PLR0913, PLR0917
    body: bytes,
    now: datetime,
    hmac_keys: KeySet[HmacKey],
    ed25519_keys: KeySet[Ed25519Key],
    rsa_keys: KeySet[RSAPSSKey],
    ed25519_public: Ed25519PublicKey,
    rsa_public: RSAPublicKey,
    ed25519_private: Ed25519PrivateKey,
    rsa_private: RSAPrivateKey,
) -> None:
    """Reject each misuse of a key or a helper."""
    ed25519.verify(body, [], hmac_keys, now=now)  # error
    ed25519.verify(body, [], rsa_keys, now=now)  # error
    rsa.verify(body, [], ed25519_keys, now=now)  # error
    push.verify(body, [], ed25519_keys, now=now)  # error
    Ed25519Key(id="key", public_key=ed25519_private)  # error
    RSAPSSKey(id="key", public_key=rsa_private)  # error
    Ed25519Key(id="key", public_key=rsa_public)  # error
    RSAPSSKey(id="key", public_key=ed25519_public)  # error
    Ed25519Key(id="key", public_key=b"raw public key bytes")  # error
    RSAPSSKey(id="key", public_key="-----BEGIN PUBLIC KEY-----")  # error
    Ed25519Key("key", ed25519_public)  # error
    RSAPSSKey(id=1, public_key=rsa_public)  # error
