"""Verify the deliveries of generated webhook helpers through application verifiers, then decode and claim their events.

The stages run in a fixed order: local configuration, sizes, the verifier, the contract fence over the facts it
returns, the timestamp window, the replay prerequisite, decoding, and the replay claim. The verifier is synchronous in
both modes, is called once, and its errors propagate as they are. Errors keep no key, signature, header, or body.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import CoroutineType
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
    return type(value) is str and bool(value)


def _utc(value: object) -> datetime | None:
    """Return an exact datetime in UTC, reading its offset once, or None for another type, a naive or failing offset.

    The offset is applied by hand, so a time zone whose offset changes between reads cannot give another instant.
    """
    if type(value) is not datetime:
        return None
    try:
        offset = value.utcoffset()
        moment = None if offset is None else (value.replace(tzinfo=None) - offset).replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        moment = None
    return moment


def _kept(plan: AdapterPlan[T], signature: object) -> tuple[str | None, datetime | None, str] | None:
    """Return a result's delivery id, UTC timestamp, and matched key id, each read once, or None for a broken contract.

    The result must be exactly a VerifiedSignature whose matched_key_id is a nonempty str, whose declared facts are a
    nonempty str and an aware datetime, and whose undeclared facts are None.
    """
    if type(signature) is not VerifiedSignature:
        return None
    delivery_id, timestamp, matched_key_id = signature.delivery_id, signature.timestamp, signature.matched_key_id
    if not _text(matched_key_id) or not (_text(delivery_id) if plan.delivery_id else delivery_id is None):
        return None
    if not plan.timestamp:
        return None if timestamp is not None else (delivery_id, None, matched_key_id)
    moment = _utc(timestamp)
    return None if moment is None else (delivery_id, moment, matched_key_id)


def _fenced(plan: AdapterPlan[T], signature: object) -> tuple[str | None, datetime | None, str]:
    """Return a verifier's facts when it keeps the contract, or raise AdapterContractError with nothing sent.

    A coroutine result is closed unawaited. The error is raised outside any handler, so it keeps no context.
    """
    if (kept := _kept(plan, signature)) is not None:
        return kept
    if type(signature) is CoroutineType:
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
    delivery_id, timestamp, matched_key_id = _fenced(plan, verify(raw_body, tuple(headers), keys, now, limits))
    signed = None
    if timestamp is not None:
        signed = epoch_microseconds(timestamp)
        if not within(now_us, signed, limits):
            reject("timestamp_window", helper_id)
    return facts(delivery_id, timestamp, matched_key_id, store, expiry(signed, now_us, limits), helper_id)


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
