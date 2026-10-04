"""Authenticate webhook deliveries with fixed signature presets before decoding their events.

Local arguments, sizes, syntax, timestamp tolerance, and signatures are checked before any model decoding.
Errors keep no key, signature, header, or body, and verification retains no delivery state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Generic

from typing_extensions import TypeVar

from .signatures import UnsupportedKeyError, signature_bytes
from .webhook_events import (
    EventPlan,
    Facts,
    configuration_error,
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
    from typing import Literal

    from .signatures import Encoding, SignatureAlgorithm
    from .webhooks import KeySet, VerifiedWebhook, WebhookOptions

__all__ = ("WebhookPlan", "averify_webhook", "verify_webhook")

T = TypeVar("T")
K = TypeVar("K")
_TIMESTAMP: Final = re.compile(r"[0-9]{1,19}")
_VISIBLE: Final = re.compile(r"[\x21-\x7e]+")


@dataclass(frozen=True, slots=True, kw_only=True)
class WebhookPlan(EventPlan[T], Generic[T, K]):
    """A generated helper's fixed signature kind, algorithm, and raw-body header settings."""

    kind: Literal["standard_webhooks", "stripe_style", "body_hmac", "ed25519", "rsa-pss-sha256"]
    algorithm: SignatureAlgorithm[K]
    header: str
    encoding: Encoding
    prefix: str


def _checked_keys(keys: KeySet[K], algorithm: SignatureAlgorithm[K], helper_id: str) -> list[tuple[K, str]]:
    """Return active keys with their ids, refusing a wrong key type or repeated id."""
    candidates, seen = list[tuple[K, str]](), set[str]()
    for index, key in enumerate(keys.keys):
        if type(key) is not algorithm.key_type:
            raise configuration_error(("keys", str(index)), "wrong_capability", helper_id)
        if (identity := algorithm.key_id(key)) in seen:
            raise configuration_error(("keys", str(index), "id"), "invalid_value", helper_id)
        seen.add(identity)
        candidates.append((key, identity))
    return candidates


def _named(name: str, header: str) -> bool:
    return name.isascii() and name.lower() == header


def _header(headers: Sequence[tuple[str, str]], header: str, helper_id: str) -> str:
    """Return exactly one nonempty header value without changing its authenticated bytes."""
    values = [value for name, value in headers if _named(name, header)]
    if len(values) != 1 or not values[0]:
        reject("malformed_signature", helper_id)
    return values[0]


def _matches(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
    algorithm: SignatureAlgorithm[K],
    index: int,
    key: K,
    parts: tuple[bytes, ...],
    signatures: tuple[bytes, ...],
    helper_id: str,
) -> bool:
    """Verify with a key, reporting unsupported backend capability without chaining its error."""
    try:
        return algorithm.matches(key, parts, signatures)
    except UnsupportedKeyError:
        pass
    raise configuration_error(("keys", str(index)), "wrong_capability", helper_id)


def _signed(
    plan: WebhookPlan[T, K], raw_body: bytes, headers: Sequence[tuple[str, str]], values: list[str]
) -> tuple[str | None, str | None, tuple[bytes, ...], list[str]]:
    """Return a preset's signed timestamp, delivery id, authenticated parts, and signature candidates."""
    helper_id = plan.helper_id
    stamp: str | None = None
    delivery: str | None = None
    parts: tuple[bytes, ...] = (raw_body,)
    elements = values
    match plan.kind:
        case "standard_webhooks":
            stamp = _header(headers, "webhook-timestamp", helper_id)
            delivery = _header(headers, "webhook-id", helper_id)
            if not _VISIBLE.fullmatch(delivery):
                reject("malformed_signature", helper_id)
            elements = [element for value in values for element in value.split(" ")]
            parts = (delivery.encode("ascii"), b".", _ascii(stamp), b".", raw_body)
        case "stripe_style":
            fields = [
                field.strip(" \t").partition("=") for field in _header(headers, plan.header, helper_id).split(",")
            ]
            if any(not separator or not name or not value for name, separator, value in fields):
                reject("malformed_signature", helper_id)
            if len(timestamps := [value for name, _, value in fields if name == "t"]) != 1:
                reject("malformed_signature", helper_id)
            stamp = timestamps[0]
            elements = [value for name, _, value in fields if name == "v1"]
            parts = (_ascii(stamp), b".", raw_body)
    return stamp, delivery, parts, elements


def _ascii(text: str) -> bytes:
    return text.encode("ascii") if text.isascii() else b""


def _authenticate(  # ruff: ignore[too-many-arguments]
    plan: WebhookPlan[T, K],
    raw_body: bytes,
    headers: Sequence[tuple[str, str]],
    keys: KeySet[K],
    *,
    now: datetime,
    options: object,
) -> Facts:
    """Authenticate the exact bytes of one preset and return its verified facts."""
    helper_id, algorithm = plan.helper_id, plan.algorithm
    now_us, limits = local_arguments(helper_id, raw_body, headers, keys, now=now, options=options)
    candidates = _checked_keys(keys, algorithm, helper_id)
    sizes(limits, raw_body, headers, len(candidates), helper_id)
    if not (values := [value for name, value in headers if _named(name, plan.header)]):
        reject("malformed_signature", helper_id)
    stamp, delivery, parts, elements = _signed(plan, raw_body, headers, values)
    if not elements:
        reject("malformed_signature", helper_id)
    size("signatures", limits.max_signatures, len(elements), helper_id)
    signatures = tuple(signature_bytes(plan.encoding, plan.prefix, algorithm.size, element) for element in elements)
    if None in signatures:
        reject("malformed_signature", helper_id)
    moment = None
    if stamp is not None:
        if not _TIMESTAMP.fullmatch(stamp):
            reject("malformed_signature", helper_id)
        signed = int(stamp) * 1_000_000
        if not within(now_us, signed, limits) or (moment := instant(signed)) is None:
            reject("timestamp_window", helper_id)
    if not candidates:
        reject("missing_key", helper_id)
    verified = tuple(signature for signature in signatures if signature is not None)
    found = (
        identity
        for index, (key, identity) in enumerate(candidates)
        if _matches(algorithm, index, key, parts, verified, helper_id)
    )
    if (matched := next(found, None)) is None:
        reject("invalid_signature", helper_id)
    return Facts(delivery, moment, matched)


def verify_webhook(  # ruff: ignore[too-many-arguments]
    plan: WebhookPlan[T, K],
    raw_body: bytes,
    headers: Sequence[tuple[str, str]],
    keys: KeySet[K],
    *,
    now: datetime,
    options: WebhookOptions | None = None,
) -> VerifiedWebhook[T]:
    """Authenticate a delivery, then decode its event without retaining delivery state."""
    return received(plan, raw_body, _authenticate(plan, raw_body, headers, keys, now=now, options=options))


async def averify_webhook(  # ruff: ignore[too-many-arguments, unused-async]
    plan: WebhookPlan[T, K],
    raw_body: bytes,
    headers: Sequence[tuple[str, str]],
    keys: KeySet[K],
    *,
    now: datetime,
    options: WebhookOptions | None = None,
) -> VerifiedWebhook[T]:
    """Authenticate and decode with the same arguments and behavior as verify_webhook."""
    return verify_webhook(plan, raw_body, headers, keys, now=now, options=options)
