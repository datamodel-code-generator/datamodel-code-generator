"""Reject a verifier of another key type, missing or positional verifiers, async verifiers, and unsigned misuse."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pets.protocols import KeySet, ResolvedWebhookOptions, VerifiedSignature, Verifier
from pets.webhooks.keys import HmacKey
from pets.webhooks.stripe import event
from pets.webhooks.unsigned import message as unsigned_message

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True)
class StripeKey:
    """An application key."""

    name: str


@dataclass(frozen=True)
class OtherKey:
    """Another application's key."""

    name: str


class AsyncVerifier:
    """A verifier whose verify must be awaited, which the synchronous contract refuses."""

    async def verify(
        self,
        raw_body: bytes,
        ordered_headers: tuple[tuple[str, str], ...],
        keys: KeySet[StripeKey],
        now: datetime,
        limits: ResolvedWebhookOptions,
    ) -> VerifiedSignature:
        """Never called."""
        raise NotImplementedError


def wrong_calls(  # noqa: PLR0913, PLR0917
    body: bytes,
    now: datetime,
    stripe_keys: KeySet[StripeKey],
    other_keys: KeySet[OtherKey],
    hmac_keys: KeySet[HmacKey],
    verifier: Verifier[StripeKey],
    any_verifier: Verifier[object],
) -> None:
    """Reject each misuse of an adapter or unsigned helper."""
    event.verify(body, [], other_keys, verifier=verifier, now=now)  # error
    event.verify(body, [], stripe_keys, verifier=any_verifier, now=now)  # error
    event.verify(body, [], hmac_keys, verifier=verifier, now=now)  # error
    event.verify(body, [], stripe_keys, now=now)  # error
    event.verify(body, [], stripe_keys, verifier, now=now)  # error
    event.verify(body, [], stripe_keys, verifier=AsyncVerifier(), now=now)  # error
    event.verify(body, [], stripe_keys, verifier=object(), now=now)  # error
    unsigned_message.verify(body, [], stripe_keys, verifier=verifier, now=now)  # error
    unsigned_message.decode_unverified(body, [])  # error
    unsigned_message.decode_unverified("{}")  # error
    unsigned_message.decode_unverified(body, replay_store=None)  # error
