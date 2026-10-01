"""Verify the deliveries of generated webhook helpers with builtin signatures, then decode and claim their events.

The stages run in a fixed order: local configuration, sizes, header syntax, the timestamp window, signature
candidates, the replay prerequisite, decoding, and the replay claim. Errors keep no key, signature, header, or body.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Generic

from typing_extensions import TypeVar

from .signatures import DELIVERY_ID, RAW_BODY, TIMESTAMP, UnsupportedKeyError, satisfied, signature_bytes
from .webhook_events import (
    Facts,
    SignedPlan,
    areceived,
    configuration_error,
    expiry,
    facts,
    instant,
    local_arguments,
    received,
    reject,
    size,
    sizes,
    within,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from .signatures import FactField, SignatureAlgorithm, SignatureProfile
    from .webhooks import AsyncReplayStore, KeySet, ReplayStore, VerifiedWebhook, WebhookOptions

__all__ = ("WebhookPlan", "averify_webhook", "verify_webhook")


T = TypeVar("T")
K = TypeVar("K")

_TIMESTAMP: Final = re.compile(r"[0-9]{1,19}")
_VISIBLE: Final = re.compile(r"[\x21-\x7e]+")
_UNITS: Final = {"seconds": 1_000_000, "milliseconds": 1000}


@dataclass(frozen=True, slots=True, kw_only=True)
class WebhookPlan(SignedPlan[T], Generic[T, K]):
    """A generated webhook helper with a builtin signature: its identity, fingerprint, profile, decoder, and policy.

    The fingerprint is the replay namespace of its claims.
    """

    signature: SignatureProfile[K]


def _checked_keys(keys: KeySet[K], profile: SignatureProfile[K], helper_id: str) -> list[tuple[K, str]]:
    """Return each key with its id, refusing a key of another profile, a repeated id, and one a header cannot carry."""
    algorithm, candidates, seen = profile.algorithm, list[tuple[K, str]](), set[str]()
    for index, key in enumerate(keys.keys):
        if type(key) is not algorithm.key_type:
            raise configuration_error(("keys", str(index)), "wrong_capability", helper_id)
        if (identity := algorithm.key_id(key)) in seen or (
            profile.key_id is not None and not _VISIBLE.fullmatch(identity)
        ):
            raise configuration_error(("keys", str(index), "id"), "invalid_value", helper_id)
        seen.add(identity)
        candidates.append((key, identity))
    return candidates


def _named(name: str, header: str) -> bool:
    return name.isascii() and name.lower() == header


def _fact(
    headers: Sequence[tuple[str, str]], header: str, pattern: re.Pattern[str], field: FactField | None, helper_id: str
) -> str:
    """Return a fact header's single value, kept unstripped, after checking its syntax and declared constraint."""
    values = [value for name, value in headers if _named(name, header)]
    value = values[0] if len(values) == 1 else ""
    if not pattern.fullmatch(value) or (field is not None and not satisfied(field, value.encode("ascii"))):
        reject("malformed_signature", helper_id)
    return value


def _matches(  # noqa: PLR0913, PLR0917
    algorithm: SignatureAlgorithm[K],
    index: int,
    key: K,
    parts: tuple[bytes, ...],
    signatures: tuple[bytes, ...],
    helper_id: str,
) -> bool:
    """Return whether a key made one of the signatures, refusing a key its backend cannot use by its index.

    The refusal is raised after the handler ends, so it chains no error of the key's backend.
    """
    try:
        return algorithm.matches(key, parts, signatures)
    except UnsupportedKeyError:
        pass
    raise configuration_error(("keys", str(index)), "wrong_capability", helper_id)


def _authenticate(  # noqa: PLR0913, PLR0914
    plan: WebhookPlan[T, K],
    raw_body: bytes,
    headers: Sequence[tuple[str, str]],
    keys: KeySet[K],
    *,
    now: datetime,
    store: object,
    options: object,
    asynchronous: bool,
) -> Facts:
    """Run every stage before decoding; return the authenticated facts, with the claim to make when given a store."""
    helper_id, profile = plan.helper_id, plan.signature
    now_us, limits = local_arguments(
        helper_id, raw_body, headers, keys, now=now, store=store, options=options, asynchronous=asynchronous
    )
    candidates = _checked_keys(keys, profile, helper_id)
    sizes(limits, raw_body, headers, len(candidates), helper_id)
    separator = profile.separator
    elements = [
        element
        for name, value in headers
        if _named(name, profile.header)
        for element in (value.split(separator) if separator is not None else (value,))
    ]
    if not elements:
        reject("malformed_signature", helper_id)
    size("signatures", limits.max_signatures, len(elements), helper_id)
    signatures = tuple(signature_bytes(profile, element) for element in elements)
    timestamp, fact, header = profile.timestamp, profile.delivery_id, profile.key_id
    stamp = None if timestamp is None else _fact(headers, timestamp.header, _TIMESTAMP, timestamp, helper_id)
    delivery = None if fact is None else _fact(headers, fact.header, _VISIBLE, fact, helper_id)
    key_id = None if header is None else _fact(headers, header, _VISIBLE, None, helper_id)
    if None in signatures:
        reject("malformed_signature", helper_id)
    moment = signed = None
    if timestamp is not None and stamp is not None:
        signed = int(stamp) * _UNITS[timestamp.unit]
        if not within(now_us, signed, limits) or (moment := instant(signed)) is None:
            reject("timestamp_window", helper_id)
    values = {
        RAW_BODY: raw_body,
        TIMESTAMP: (stamp or "").encode("ascii"),
        DELIVERY_ID: (delivery or "").encode("ascii"),
    }
    parts = tuple(part if isinstance(part, bytes) else values[part] for part in profile.parts)
    eligible = [
        (index, key, identity)
        for index, (key, identity) in enumerate(candidates)
        if key_id is None or identity == key_id
    ]
    if not eligible:
        reject("missing_key", helper_id)
    verified = tuple(signature for signature in signatures if signature is not None)
    found = (
        identity
        for index, key, identity in eligible
        if _matches(profile.algorithm, index, key, parts, verified, helper_id)
    )
    if (matched := next(found, None)) is None:
        reject("invalid_signature", helper_id)
    return facts(delivery, moment, matched, store, expiry(signed, now_us, limits), helper_id)


def verify_webhook(  # noqa: PLR0913
    plan: WebhookPlan[T, K],
    raw_body: bytes,
    headers: Sequence[tuple[str, str]],
    keys: KeySet[K],
    *,
    now: datetime,
    replay_store: ReplayStore | None = None,
    options: WebhookOptions | None = None,
) -> VerifiedWebhook[T]:
    """Verify a delivery, decode its event, and claim its delivery id in a synchronous replay store when given.

    The claim is at most once, as `received` describes.
    """
    authenticated = _authenticate(
        plan, raw_body, headers, keys, now=now, store=replay_store, options=options, asynchronous=False
    )
    return received(plan, raw_body, authenticated, replay_store)


async def averify_webhook(  # noqa: PLR0913
    plan: WebhookPlan[T, K],
    raw_body: bytes,
    headers: Sequence[tuple[str, str]],
    keys: KeySet[K],
    *,
    now: datetime,
    replay_store: AsyncReplayStore | None = None,
    options: WebhookOptions | None = None,
) -> VerifiedWebhook[T]:
    """Verify and decode as verify_webhook does, then await the claim of an asynchronous replay store when given."""
    authenticated = _authenticate(
        plan, raw_body, headers, keys, now=now, store=replay_store, options=options, asynchronous=True
    )
    return await areceived(plan, raw_body, authenticated, replay_store)
