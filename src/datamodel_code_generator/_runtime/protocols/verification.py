"""Verify the deliveries of generated webhook helpers, then decode their events and claim them for replay detection.

The stages run in a fixed order: local configuration, sizes, header syntax, the timestamp window, signature
candidates, the replay prerequisite, decoding, and the replay claim. Errors keep no key, signature, header, or body.
"""

from __future__ import annotations

import re
from collections.abc import Coroutine
from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING, Final, Generic, Literal, NoReturn

from typing_extensions import TypeIs, TypeVar

from ..client.errors import (
    ProtocolConfigurationError,
    ProtocolSizeError,
    WebhookReplayError,
    WebhookStoreError,
    WebhookVerificationError,
    is_sequence,
)
from ..model_codecs.errors import (
    CodecBindingError,
    CodecResourceLimitError,
    ModelProjectionError,
    NativeValidationError,
    ParameterEncodingError,
    WireValidationError,
)
from ..model_codecs.media import decode_json
from ..model_codecs.unset import Unset
from .errors import ProtocolDataError
from .signatures import DELIVERY_ID, RAW_BODY, TIMESTAMP, satisfied, signature_bytes
from .webhooks import KeySet, ResolvedWebhookOptions, VerifiedWebhook, WebhookOptions

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ..client.operations import InboundModelCodec
    from ..model_codecs.context import CodecContext
    from ..model_codecs.wire import WireValue
    from .signatures import FactField, SignatureProfile
    from .webhooks import AsyncReplayStore, ReplayStore

__all__ = ("EventDecoder", "WebhookPlan", "averify_webhook", "verify_webhook")


T = TypeVar("T")
K = TypeVar("K")

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=timezone.utc)
_LATEST: Final = datetime.max.replace(tzinfo=timezone.utc)
_TIMESTAMP: Final = re.compile(r"[0-9]{1,19}")
_VISIBLE: Final = re.compile(r"[\x21-\x7e]+")
_UNITS: Final = {"seconds": 1_000_000, "milliseconds": 1000}
_DEFAULTS: Final = ResolvedWebhookOptions(
    max_body_bytes=8388608,
    max_header_bytes=16384,
    max_keys=8,
    max_signatures=8,
    past_tolerance=300.0,
    future_tolerance=30.0,
    replay_ttl=300.0,
)
_OPTIONS: Final = tuple(item.name for item in fields(WebhookOptions))
_DATA_ERRORS: Final = (
    CodecBindingError,
    CodecResourceLimitError,
    ModelProjectionError,
    NativeValidationError,
    ParameterEncodingError,
    WireValidationError,
)


class _Failed(Enum):
    FAILED = "failed"


_FAILED: Final = _Failed.FAILED


class EventDecoder(Generic[T]):
    """Decode a verified JSON body into a helper's event type, validated against its schema when asked.

    The codec context of the event's request-body use is received as a server receives a request.
    """

    __slots__ = ("_codec", "_context", "_validate")

    def __init__(self, codec: Callable[[], InboundModelCodec[T]], context: CodecContext, *, validate: bool) -> None:
        """Keep the codec accessor, the receiving context of its use, and whether to validate with the schema."""
        self._codec = codec
        self._context = replace(context, surface="server")
        self._validate = validate

    def decode(self, raw_body: bytes, helper_id: str) -> T:
        """Return the event, raising ProtocolDataError for JSON that does not parse or a value its type refuses.

        Data errors are categorized as ordinary responses categorize them, and configuration errors propagate. The
        error keeps no cause, context, or other trace of the body.
        """
        try:
            wire: WireValue | _Failed = decode_json(raw_body)
        except _DATA_ERRORS:
            wire = _FAILED
        if isinstance(wire, _Failed):
            raise ProtocolDataError(condition="malformed", helper_id=helper_id)
        codec = self._codec()
        try:
            event: T | _Failed = (
                codec.decode(wire, self._context).require_model()
                if self._validate
                else codec.convert(wire, self._context)
            )
        except _DATA_ERRORS:
            event = _FAILED
        if isinstance(event, _Failed):
            raise ProtocolDataError(condition="value", helper_id=helper_id)
        return event


@dataclass(frozen=True, slots=True, kw_only=True)
class WebhookPlan(Generic[T, K]):
    """A generated webhook helper: its identity, fingerprint, signature profile, event decoder, and duplicate policy.

    The fingerprint is the replay namespace of its claims.
    """

    helper_id: str
    fingerprint: str
    signature: SignatureProfile[K]
    event: EventDecoder[T]
    duplicates: Literal["report", "reject"] = "report"


@dataclass(frozen=True, slots=True)
class _Facts:
    delivery_id: str | None
    timestamp: datetime | None
    matched_key_id: str
    claim: tuple[str, datetime] | None


def _configuration(
    field_path: tuple[str, ...], condition: Literal["invalid_value", "wrong_capability"], helper_id: str
) -> ProtocolConfigurationError:
    return ProtocolConfigurationError(field_path=field_path, condition=condition, helper_id=helper_id)


def _reject(
    condition: Literal[
        "malformed_signature", "invalid_signature", "missing_key", "timestamp_window", "missing_delivery_id"
    ],
    helper_id: str,
) -> NoReturn:
    raise WebhookVerificationError(condition=condition, helper_id=helper_id)


def _is_pairs(value: object) -> TypeIs[list[tuple[str, str]] | tuple[tuple[str, str], ...]]:
    return is_sequence(value) and all(_is_pair(item) for item in value)


def _is_pair(value: object) -> bool:
    return (
        is_sequence(value)
        and type(value) is tuple
        and len(value) == 2  # noqa: PLR2004
        and all(isinstance(item, str) for item in value)
    )


def _instance(value: object, kind: type) -> bool:
    return type(value) is kind if kind is bytes else isinstance(value, kind)


def _microseconds(moment: object, helper_id: str) -> int:
    if not isinstance(moment, datetime) or moment.utcoffset() is None:
        raise _configuration(("now",), "invalid_value", helper_id)
    offset = moment - _EPOCH
    return (offset.days * 86400 + offset.seconds) * 1_000_000 + offset.microseconds


def _limits(options: object, helper_id: str) -> ResolvedWebhookOptions:
    if options is None:
        return _DEFAULTS
    if not isinstance(options, WebhookOptions):
        raise _configuration(("options",), "invalid_value", helper_id)
    given = {name: value for name in _OPTIONS if not isinstance(value := getattr(options, name), Unset)}
    return replace(_DEFAULTS, **given)


def _claimer(store: object, helper_id: str, *, asynchronous: bool) -> None:
    """Refuse a store without a callable claim, or whose claim, or its __call__, is not of the call's mode."""
    if store is None:
        return
    from inspect import iscoroutinefunction  # noqa: PLC0415 - Only a call given a store inspects its mode.

    claim = getattr(store, "claim", None)
    if (
        not callable(claim)
        or (
            iscoroutinefunction(claim) or iscoroutinefunction(getattr(claim, "__call__", None))  # noqa: B004
        )
        is not asynchronous
    ):
        raise _configuration(("replay_store",), "wrong_capability", helper_id)


def _checked_keys(keys: KeySet[K], profile: SignatureProfile[K], helper_id: str) -> list[tuple[K, str]]:
    """Return each key with its id, refusing a key of another profile, a repeated id, and one a header cannot carry."""
    algorithm, candidates, seen = profile.algorithm, list[tuple[K, str]](), set[str]()
    for index, key in enumerate(keys.keys):
        if not _instance(key, algorithm.key_type):
            raise _configuration(("keys", str(index)), "wrong_capability", helper_id)
        if (identity := algorithm.key_id(key)) in seen or (
            profile.key_id is not None and not _VISIBLE.fullmatch(identity)
        ):
            raise _configuration(("keys", str(index), "id"), "invalid_value", helper_id)
        seen.add(identity)
        candidates.append((key, identity))
    return candidates


def _size(kind: Literal["body", "headers", "keys", "signatures"], limit: int, observed: int, helper_id: str) -> None:
    if observed > limit:
        unit: Literal["bytes", "items"] = "items" if kind in {"keys", "signatures"} else "bytes"
        raise ProtocolSizeError(kind=kind, limit=limit, observed=observed, unit=unit, helper_id=helper_id)


def _named(name: str, header: str) -> bool:
    return name.isascii() and name.lower() == header


def _fact(
    headers: Sequence[tuple[str, str]], header: str, pattern: re.Pattern[str], field: FactField | None, helper_id: str
) -> str:
    """Return a fact header's single value, kept unstripped, after checking its syntax and declared constraint."""
    values = [value for name, value in headers if _named(name, header)]
    value = values[0] if len(values) == 1 else ""
    if not pattern.fullmatch(value) or (field is not None and not satisfied(field, value.encode("ascii"))):
        _reject("malformed_signature", helper_id)
    return value


def _instant(microseconds: int) -> datetime | None:
    try:
        return _EPOCH + timedelta(microseconds=microseconds)
    except OverflowError:
        return None


def _ceiling(seconds: float) -> int:
    numerator, denominator = seconds.as_integer_ratio()
    return -(-numerator * 1_000_000 // denominator)


def _within(now: int, timestamp: int, limits: ResolvedWebhookOptions) -> bool:
    """Return whether a timestamp lies in the inclusive window around now, comparing exact microseconds."""
    past, past_scale = limits.past_tolerance.as_integer_ratio()
    future, future_scale = limits.future_tolerance.as_integer_ratio()
    return (now - timestamp) * past_scale <= past * 1_000_000 and (timestamp - now) * future_scale <= future * 1_000_000


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
) -> _Facts:
    """Run every stage before decoding; return the authenticated facts, with the claim to make when given a store."""
    helper_id, profile = plan.helper_id, plan.signature
    for name, valid in (("raw_body", _instance(raw_body, bytes)), ("headers", _is_pairs(headers))):
        if not valid:
            raise _configuration((name,), "invalid_value", helper_id)
    if not _instance(keys, KeySet):
        raise _configuration(("keys",), "invalid_value", helper_id)
    now_us = _microseconds(now, helper_id)
    limits = _limits(options, helper_id)
    _claimer(store, helper_id, asynchronous=asynchronous)
    candidates = _checked_keys(keys, profile, helper_id)
    _size("body", limits.max_body_bytes, len(raw_body), helper_id)
    _size("headers", limits.max_header_bytes, sum(len(name) + len(value) for name, value in headers), helper_id)
    _size("keys", limits.max_keys, len(candidates), helper_id)
    separator = profile.separator
    elements = [
        element
        for name, value in headers
        if _named(name, profile.header)
        for element in (value.split(separator) if separator is not None else (value,))
    ]
    if not elements:
        _reject("malformed_signature", helper_id)
    _size("signatures", limits.max_signatures, len(elements), helper_id)
    signatures = tuple(signature_bytes(profile, element) for element in elements)
    timestamp, fact, header = profile.timestamp, profile.delivery_id, profile.key_id
    stamp = None if timestamp is None else _fact(headers, timestamp.header, _TIMESTAMP, timestamp, helper_id)
    delivery = None if fact is None else _fact(headers, fact.header, _VISIBLE, fact, helper_id)
    key_id = None if header is None else _fact(headers, header, _VISIBLE, None, helper_id)
    if None in signatures:
        _reject("malformed_signature", helper_id)
    moment = None
    if timestamp is not None and stamp is not None:
        instant = int(stamp) * _UNITS[timestamp.unit]
        if not _within(now_us, instant, limits) or (moment := _instant(instant)) is None:
            _reject("timestamp_window", helper_id)
        expires_us = instant + _ceiling(limits.past_tolerance) + _ceiling(limits.future_tolerance)
    else:
        expires_us = now_us + _ceiling(limits.replay_ttl)
    values = {
        RAW_BODY: raw_body,
        TIMESTAMP: (stamp or "").encode("ascii"),
        DELIVERY_ID: (delivery or "").encode("ascii"),
    }
    parts = tuple(part if isinstance(part, bytes) else values[part] for part in profile.parts)
    eligible = [(key, identity) for key, identity in candidates if key_id is None or identity == key_id]
    if not eligible:
        _reject("missing_key", helper_id)
    verified = tuple(signature for signature in signatures if signature is not None)
    matches = profile.algorithm.matches
    if (matched := next((identity for key, identity in eligible if matches(key, parts, verified)), None)) is None:
        _reject("invalid_signature", helper_id)
    if store is not None and delivery is None:
        _reject("missing_delivery_id", helper_id)
    claim = None if store is None or delivery is None else (delivery, _instant(expires_us) or _LATEST)
    return _Facts(delivery, moment, matched, claim)


def _duplicate(plan: WebhookPlan[T, K], delivery_id: str, claimed: object) -> bool:
    """Return whether a claim found a live duplicate, refusing a non-boolean result and duplicates under reject."""
    if type(claimed) is not bool:
        if isinstance(claimed, Coroutine):
            claimed.close()
        raise WebhookStoreError(action="claim", helper_id=plan.helper_id)
    if claimed:
        return False
    if plan.duplicates == "reject":
        raise WebhookReplayError(delivery_id=delivery_id, namespace=plan.fingerprint, helper_id=plan.helper_id)
    return True


def _store_failure(plan: WebhookPlan[T, K], error: Exception) -> WebhookStoreError:
    return WebhookStoreError(action="claim", helper_id=plan.helper_id, cause=error)


def _verified(data: T, facts: _Facts, *, duplicate: bool) -> VerifiedWebhook[T]:
    return VerifiedWebhook(
        data=data,
        delivery_id=facts.delivery_id,
        timestamp=facts.timestamp,
        matched_key_id=facts.matched_key_id,
        duplicate=duplicate,
    )


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

    The claim is made after decoding and before the application processes the event, so it is at most once: if
    processing fails afterwards, a retried delivery is a duplicate, or rejected under `reject`, until the claim
    expires, and duplicates are detected only until then.
    """
    facts = _authenticate(
        plan, raw_body, headers, keys, now=now, store=replay_store, options=options, asynchronous=False
    )
    data = plan.event.decode(raw_body, plan.helper_id)
    if replay_store is None or (claim := facts.claim) is None:
        return _verified(data, facts, duplicate=False)
    try:
        claimed: object = replay_store.claim(plan.fingerprint, *claim)
    except WebhookStoreError:
        raise
    except Exception as error:  # noqa: BLE001
        raise _store_failure(plan, error) from None
    return _verified(data, facts, duplicate=_duplicate(plan, claim[0], claimed))


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
    """Verify and decode as verify_webhook does, then await the claim of an asynchronous replay store when given.

    The claim is at most once, as verify_webhook's is.
    """
    facts = _authenticate(
        plan, raw_body, headers, keys, now=now, store=replay_store, options=options, asynchronous=True
    )
    data = plan.event.decode(raw_body, plan.helper_id)
    if replay_store is None or (claim := facts.claim) is None:
        return _verified(data, facts, duplicate=False)
    try:
        claimed: object = await replay_store.claim(plan.fingerprint, *claim)
    except WebhookStoreError:
        raise
    except Exception as error:  # noqa: BLE001
        raise _store_failure(plan, error) from None
    return _verified(data, facts, duplicate=_duplicate(plan, claim[0], claimed))
