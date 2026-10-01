"""Decode webhook events, and the stages every webhook helper shares: arguments, limits, the window, and the claim.

Builtin signatures and application verifiers authenticate a delivery in their own modules, so a package whose helpers
use only one of them never loads the other's dependencies. Errors keep no key, signature, header, or body.
"""

from __future__ import annotations

from collections.abc import Coroutine
from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING, Final, Generic, Literal, NoReturn, TypeGuard

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
from .records import BodySelector
from .values import Missing, resolve
from .webhooks import KeySet, ResolvedWebhookOptions, VerifiedWebhook, WebhookOptions

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from ..client.operations import InboundModelCodec
    from ..model_codecs.context import CodecContext
    from ..model_codecs.wire import WireValue
    from .webhooks import AsyncReplayStore, ReplayStore

__all__ = (
    "EventDecoder",
    "EventPlan",
    "Facts",
    "MappedEventDecoder",
    "SignedPlan",
    "areceived",
    "configuration_error",
    "decode_unsigned",
    "epoch_microseconds",
    "expiry",
    "facts",
    "instant",
    "local_arguments",
    "received",
    "reject",
    "size",
    "sizes",
    "within",
)

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=timezone.utc)
_LATEST: Final = datetime.max.replace(tzinfo=timezone.utc)
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


def _parsed(raw_body: bytes, helper_id: str) -> WireValue:
    """Return a body's JSON value, raising ProtocolDataError with no trace of the body when it does not parse."""
    try:
        wire: WireValue | _Failed = decode_json(raw_body)
    except _DATA_ERRORS:
        wire = _FAILED
    if isinstance(wire, _Failed):
        raise ProtocolDataError(condition="malformed", helper_id=helper_id)
    return wire


class EventDecoder(Generic[T_co]):
    """Decode a JSON body into a helper's event type, validated against its schema when asked.

    The codec context of the event's request-body use is received as a server receives a request.
    """

    __slots__ = ("_codec", "_context", "_validate")

    def __init__(self, codec: Callable[[], InboundModelCodec[T_co]], context: CodecContext, *, validate: bool) -> None:
        """Keep the codec accessor, the receiving context of its use, and whether to validate with the schema."""
        self._codec = codec
        self._context = replace(context, surface="server")
        self._validate = validate

    def decode(self, raw_body: bytes, helper_id: str) -> T_co:
        """Return the event, raising ProtocolDataError for JSON that does not parse or a value its type refuses."""
        return self.typed(_parsed(raw_body, helper_id), helper_id)

    def typed(self, wire: WireValue, helper_id: str) -> T_co:
        """Return the event of a parsed body, raising ProtocolDataError for a value its type refuses.

        Data errors are categorized as ordinary responses categorize them, and configuration errors propagate. The
        error keeps no cause, context, or other trace of the body.
        """
        codec = self._codec()
        try:
            event: T_co | _Failed = (
                codec.decode(wire, self._context).require_model()
                if self._validate
                else codec.convert(wire, self._context)
            )
        except _DATA_ERRORS:
            event = _FAILED
        if isinstance(event, _Failed):
            raise ProtocolDataError(condition="value", helper_id=helper_id)
        return event


class MappedEventDecoder(Generic[T_co]):
    """Decode a JSON body by the event type a body member names, each type with its own decoder.

    The member must be a string naming one of the types exactly; an absent member, null, another JSON type, or an
    unknown name raises ProtocolDataError with the member's selector and no trace of the body.
    """

    __slots__ = ("_events", "_location")

    def __init__(self, pointer: str, events: Mapping[str, EventDecoder[T_co]]) -> None:
        """Keep the RFC 6901 pointer of the member naming the type, and the decoder of each type name."""
        self._location = BodySelector(pointer=pointer)
        self._events = dict(events)

    def decode(self, raw_body: bytes, helper_id: str) -> T_co:
        """Return the event of the type the body names, parsing the body once."""
        wire = _parsed(raw_body, helper_id)
        name = resolve(wire, self._location.pointer)
        if isinstance(name, str) and (decoder := self._events.get(name)) is not None:
            return decoder.typed(wire, helper_id)
        condition: Literal["missing", "null", "type", "value"] = "type"
        match name:
            case str():
                condition = "value"
            case Missing():
                condition = "missing"
            case None:
                condition = "null"
            case _:
                pass
        raise ProtocolDataError(condition=condition, location=self._location, helper_id=helper_id)


@dataclass(frozen=True, slots=True, kw_only=True)
class EventPlan(Generic[T]):
    """A generated webhook helper's identity and event decoder; an unsigned helper needs nothing else."""

    helper_id: str
    event: EventDecoder[T] | MappedEventDecoder[T]


@dataclass(frozen=True, slots=True, kw_only=True)
class SignedPlan(EventPlan[T]):
    """A helper that authenticates its deliveries: its fingerprint, the replay namespace of its claims, and policy."""

    fingerprint: str
    duplicates: Literal["report", "reject"] = "report"


@dataclass(frozen=True, slots=True)
class Facts:
    """The authenticated facts of a delivery, with the claim to make when given a store."""

    delivery_id: str | None
    timestamp: datetime | None
    matched_key_id: str
    claim: tuple[str, datetime] | None


def configuration_error(
    field_path: tuple[str, ...], condition: Literal["invalid_value", "wrong_capability"], helper_id: str
) -> ProtocolConfigurationError:
    """Return the error of a wrong argument, by its field path."""
    return ProtocolConfigurationError(field_path=field_path, condition=condition, helper_id=helper_id)


def reject(
    condition: Literal[
        "malformed_signature", "invalid_signature", "missing_key", "timestamp_window", "missing_delivery_id"
    ],
    helper_id: str,
) -> NoReturn:
    """Refuse a delivery for the condition."""
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


def epoch_microseconds(moment: datetime) -> int:
    """Return the exact microseconds of an aware datetime since the epoch."""
    offset = moment - _EPOCH
    return (offset.days * 86400 + offset.seconds) * 1_000_000 + offset.microseconds


def _aware(moment: object) -> TypeGuard[datetime]:
    return isinstance(moment, datetime) and moment.utcoffset() is not None


def _limits(options: object, helper_id: str) -> ResolvedWebhookOptions:
    if options is None:
        return _DEFAULTS
    if not isinstance(options, WebhookOptions):
        raise configuration_error(("options",), "invalid_value", helper_id)
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
        raise configuration_error(("replay_store",), "wrong_capability", helper_id)


def local_arguments(  # noqa: PLR0913
    helper_id: str,
    raw_body: object,
    headers: object,
    keys: object,
    *,
    now: object,
    store: object,
    options: object,
    asynchronous: bool,
) -> tuple[int, ResolvedWebhookOptions]:
    """Refuse the first wrong argument by its field path; return now in microseconds and the resolved limits."""
    for name, valid in (("raw_body", _instance(raw_body, bytes)), ("headers", _is_pairs(headers))):
        if not valid:
            raise configuration_error((name,), "invalid_value", helper_id)
    if not _instance(keys, KeySet):
        raise configuration_error(("keys",), "invalid_value", helper_id)
    if not _aware(now):
        raise configuration_error(("now",), "invalid_value", helper_id)
    limits = _limits(options, helper_id)
    _claimer(store, helper_id, asynchronous=asynchronous)
    return epoch_microseconds(now), limits


def size(kind: Literal["body", "headers", "keys", "signatures"], limit: int, observed: int, helper_id: str) -> None:
    """Refuse an observed size or count over its limit."""
    if observed > limit:
        unit: Literal["bytes", "items"] = "items" if kind in {"keys", "signatures"} else "bytes"
        raise ProtocolSizeError(kind=kind, limit=limit, observed=observed, unit=unit, helper_id=helper_id)


def sizes(
    limits: ResolvedWebhookOptions, raw_body: bytes, headers: Sequence[tuple[str, str]], keys: int, helper_id: str
) -> None:
    """Refuse a body, headers, or key count over its limit, in that order."""
    size("body", limits.max_body_bytes, len(raw_body), helper_id)
    size("headers", limits.max_header_bytes, sum(len(name) + len(value) for name, value in headers), helper_id)
    size("keys", limits.max_keys, keys, helper_id)


def instant(microseconds: int) -> datetime | None:
    """Return the aware UTC datetime of microseconds since the epoch, or None beyond datetime's range."""
    try:
        return _EPOCH + timedelta(microseconds=microseconds)
    except OverflowError:
        return None


def _ceiling(seconds: float) -> int:
    numerator, denominator = seconds.as_integer_ratio()
    return -(-numerator * 1_000_000 // denominator)


def within(now: int, timestamp: int, limits: ResolvedWebhookOptions) -> bool:
    """Return whether a timestamp lies in the inclusive window around now, comparing exact microseconds."""
    past, past_scale = limits.past_tolerance.as_integer_ratio()
    future, future_scale = limits.future_tolerance.as_integer_ratio()
    return (now - timestamp) * past_scale <= past * 1_000_000 and (timestamp - now) * future_scale <= future * 1_000_000


def expiry(timestamp: int | None, now: int, limits: ResolvedWebhookOptions) -> int:
    """Return when a claim expires: after both tolerances past the timestamp, or replay_ttl after now without one."""
    if timestamp is None:
        return now + _ceiling(limits.replay_ttl)
    return timestamp + _ceiling(limits.past_tolerance) + _ceiling(limits.future_tolerance)


def facts(  # noqa: PLR0913, PLR0917
    delivery_id: str | None,
    timestamp: datetime | None,
    matched_key_id: str,
    store: object,
    expires: int,
    helper_id: str,
) -> Facts:
    """Return a delivery's facts and claim, refusing a store given to a delivery without a delivery id."""
    if store is not None and delivery_id is None:
        reject("missing_delivery_id", helper_id)
    claim = None if store is None or delivery_id is None else (delivery_id, instant(expires) or _LATEST)
    return Facts(delivery_id, timestamp, matched_key_id, claim)


def _duplicate(plan: SignedPlan[T], delivery_id: str, claimed: object) -> bool:
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


def _store_failure(plan: SignedPlan[T], error: Exception) -> WebhookStoreError:
    return WebhookStoreError(action="claim", helper_id=plan.helper_id, cause=error)


def _verified(data: T, facts: Facts, *, duplicate: bool) -> VerifiedWebhook[T]:
    return VerifiedWebhook(
        data=data,
        delivery_id=facts.delivery_id,
        timestamp=facts.timestamp,
        matched_key_id=facts.matched_key_id,
        duplicate=duplicate,
    )


def received(
    plan: SignedPlan[T], raw_body: bytes, facts: Facts, replay_store: ReplayStore | None
) -> VerifiedWebhook[T]:
    """Decode an authenticated delivery's event, then claim its delivery id in a synchronous store when given.

    The claim is made after decoding and before the application processes the event, so it is at most once: if
    processing fails afterwards, a retried delivery is a duplicate, or rejected under `reject`, until the claim
    expires, and duplicates are detected only until then.
    """
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


async def areceived(
    plan: SignedPlan[T], raw_body: bytes, facts: Facts, replay_store: AsyncReplayStore | None
) -> VerifiedWebhook[T]:
    """Decode as received does, then await the claim of an asynchronous store when given; it is at most once too."""
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


def decode_unsigned(plan: EventPlan[T], raw_body: bytes, *, options: WebhookOptions | None = None) -> T:
    """Decode a delivery's event without authenticating it, refusing only a body over max_body_bytes first.

    Nothing about the delivery is verified, so the event is whatever its sender chose.
    """
    helper_id = plan.helper_id
    if not _instance(raw_body, bytes):
        raise configuration_error(("raw_body",), "invalid_value", helper_id)
    size("body", _limits(options, helper_id).max_body_bytes, len(raw_body), helper_id)
    return plan.event.decode(raw_body, helper_id)
