"""Verify the deliveries of generated webhook helpers through application verifiers, then decode and claim their events.

The stages run in a fixed order: local configuration, sizes, the verifier, the contract fence over the facts it
returns, the timestamp window, the replay prerequisite, decoding, and the replay claim. The verifier is synchronous in
both modes, is called once, and its errors propagate as they are. Errors keep no key, signature, header, or body.
"""

from __future__ import annotations

from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from typing_extensions import TypeVar

from ..client.errors import AdapterContractError, DeliveryState
from .webhook_events import (
    Facts,
    SignedPlan,
    areceived,
    configuration_error,
    epoch_microseconds,
    expiry,
    facts,
    local_arguments,
    received,
    reject,
    sizes,
    within,
)
from .webhooks import VerifiedSignature

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .webhooks import AsyncReplayStore, KeySet, ReplayStore, VerifiedWebhook, Verifier, WebhookOptions

__all__ = ("AdapterPlan", "averify_adapted", "verify_adapted")

T = TypeVar("T")
K = TypeVar("K")


@dataclass(frozen=True, slots=True, kw_only=True)
class AdapterPlan(SignedPlan[T]):
    """A generated helper whose caller supplies the verifier: its identity, fingerprint, facts, decoder, and policy.

    `timestamp` and `delivery_id` declare whether the verifier must return each fact; an undeclared one must be None.
    """

    timestamp: bool
    delivery_id: bool


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _aware(value: object) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None


def _fenced(plan: AdapterPlan[T], signature: object) -> VerifiedSignature:
    """Return a verifier's result when it keeps the contract, or raise AdapterContractError with nothing sent.

    The result must be a VerifiedSignature whose matched_key_id is a nonempty string, whose declared facts are a
    nonempty string and an aware datetime, and whose undeclared facts are None. An awaitable result is closed unawaited.
    """
    if (
        isinstance(signature, VerifiedSignature)
        and _text(signature.matched_key_id)
        and (_text(signature.delivery_id) if plan.delivery_id else signature.delivery_id is None)
        and (_aware(signature.timestamp) if plan.timestamp else signature.timestamp is None)
    ):
        return signature
    if isinstance(signature, Coroutine):
        signature.close()
    raise AdapterContractError(delivery_state=DeliveryState.NOT_SENT)


def _authenticate(  # noqa: PLR0913
    plan: AdapterPlan[T],
    raw_body: bytes,
    headers: Sequence[tuple[str, str]],
    keys: KeySet[K],
    *,
    verifier: Verifier[K],
    now: datetime,
    store: object,
    options: object,
    asynchronous: bool,
) -> Facts:
    """Run every stage before decoding; return the verified facts, with the claim to make when given a store."""
    helper_id = plan.helper_id
    now_us, limits = local_arguments(
        helper_id, raw_body, headers, keys, now=now, store=store, options=options, asynchronous=asynchronous
    )
    if not callable(verify := getattr(verifier, "verify", None)):
        raise configuration_error(("verifier",), "wrong_capability", helper_id)
    sizes(limits, raw_body, headers, len(keys.keys), helper_id)
    signature = _fenced(plan, verify(raw_body, tuple(headers), keys, now, limits))
    signed = None
    if (timestamp := signature.timestamp) is not None:
        signed = epoch_microseconds(timestamp)
        if not within(now_us, signed, limits):
            reject("timestamp_window", helper_id)
    return facts(
        signature.delivery_id,
        timestamp,
        signature.matched_key_id,
        store,
        expiry(signed, now_us, limits),
        helper_id,
    )


def verify_adapted(  # noqa: PLR0913
    plan: AdapterPlan[T],
    raw_body: bytes,
    headers: Sequence[tuple[str, str]],
    keys: KeySet[K],
    *,
    verifier: Verifier[K],
    now: datetime,
    replay_store: ReplayStore | None = None,
    options: WebhookOptions | None = None,
) -> VerifiedWebhook[T]:
    """Verify a delivery with the caller's verifier, decode its event, and claim it in a synchronous store when given.

    The claim is at most once, as `received` describes.
    """
    authenticated = _authenticate(
        plan,
        raw_body,
        headers,
        keys,
        verifier=verifier,
        now=now,
        store=replay_store,
        options=options,
        asynchronous=False,
    )
    return received(plan, raw_body, authenticated, replay_store)


async def averify_adapted(  # noqa: PLR0913
    plan: AdapterPlan[T],
    raw_body: bytes,
    headers: Sequence[tuple[str, str]],
    keys: KeySet[K],
    *,
    verifier: Verifier[K],
    now: datetime,
    replay_store: AsyncReplayStore | None = None,
    options: WebhookOptions | None = None,
) -> VerifiedWebhook[T]:
    """Verify with the same synchronous verifier as verify_adapted, then await the claim of an asynchronous store."""
    authenticated = _authenticate(
        plan,
        raw_body,
        headers,
        keys,
        verifier=verifier,
        now=now,
        store=replay_store,
        options=options,
        asynchronous=True,
    )
    return await areceived(plan, raw_body, authenticated, replay_store)
