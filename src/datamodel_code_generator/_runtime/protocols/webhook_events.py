"""Decode webhook events and share argument, size, and timestamp checks.

Builtin signatures and application verifiers authenticate a delivery in their own modules, so a package whose helpers
use only one of them never loads the other's dependencies. Errors keep no key, signature, header, or body.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING, Final, Generic, Literal, NoReturn, TypeGuard

from typing_extensions import TypeIs, TypeVar

from ..client.errors import (
    ConfigurationError,
    is_sequence,
)
from ..model_codecs.errors import (
    CodecResourceLimitError,
    ParameterEncodingError,
    WireValidationError,
)
from ..model_codecs.unset import Unset
from .errors import ProtocolDataError
from .records import BodySelector
from .values import Missing, resolve
from .webhooks import KeySet, ResolvedWebhookOptions, VerifiedWebhook, WebhookOptions

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ..client.operations import InboundModelCodec
    from ..model_codecs.media import JSONValue

__all__ = (
    "EventDecoder",
    "EventPlan",
    "Facts",
    "MappedEventDecoder",
    "configuration_error",
    "decode_unsigned",
    "epoch_microseconds",
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
_DEFAULTS: Final = ResolvedWebhookOptions(
    max_body_bytes=8388608,
    max_header_bytes=16384,
    max_keys=8,
    max_signatures=8,
    past_tolerance=300.0,
    future_tolerance=30.0,
)
_OPTIONS: Final = tuple(item.name for item in fields(WebhookOptions))
_DATA_ERRORS: Final = (
    CodecResourceLimitError,
    ParameterEncodingError,
    WireValidationError,
)


class _Failed(Enum):
    MALFORMED = "malformed"
    VALUE = "value"


def _parsed(raw_body: bytes, helper_id: str) -> JSONValue:
    """Return a body's JSON value, raising ProtocolDataError with no trace of the body when it does not parse."""
    try:
        wire: JSONValue | _Failed = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError, RecursionError):
        wire = _Failed.MALFORMED
    if isinstance(wire, _Failed):
        raise ProtocolDataError(reason="malformed", helper_id=helper_id)
    return wire


class EventDecoder(Generic[T_co]):
    """Decode a JSON body into a helper's event type through the codec of the event's request-body use."""

    __slots__ = ("_codec",)

    def __init__(self, codec: InboundModelCodec[T_co]) -> None:
        """Keep the codec of the event's use."""
        self._codec = codec

    def decode(self, raw_body: bytes, helper_id: str) -> T_co:
        """Return the event, raising ProtocolDataError for JSON that does not parse or a value its type refuses.

        Data errors are categorized as ordinary responses categorize them, and configuration errors propagate. The
        error keeps no cause, context, or other trace of the body.
        """
        codec = self._codec
        try:
            event: T_co | _Failed = codec.decode(raw_body)
        except codec.errors as error:
            event = _Failed.MALFORMED if codec.malformed(error) else _Failed.VALUE
        if isinstance(event, _Failed):
            condition: Literal["malformed", "value"] = "malformed" if event is _Failed.MALFORMED else "value"
            raise ProtocolDataError(reason=condition, helper_id=helper_id)
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
        """Return the event of the type the body names, which its type's decoder decodes from the body."""
        name = resolve(_parsed(raw_body, helper_id), self._location.pointer)
        if isinstance(name, str) and (decoder := self._events.get(name)) is not None:
            return decoder.decode(raw_body, helper_id)
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
        raise ProtocolDataError(reason=condition, location=self._location, helper_id=helper_id)


@dataclass(frozen=True, slots=True, kw_only=True)
class EventPlan(Generic[T]):
    """A generated webhook helper's identity and event decoder; an unsigned helper needs nothing else."""

    helper_id: str
    event: EventDecoder[T] | MappedEventDecoder[T]


@dataclass(frozen=True, slots=True)
class Facts:
    """The authenticated facts of a delivery."""

    delivery_id: str | None
    timestamp: datetime | None
    matched_key_id: str


def configuration_error(
    field_path: tuple[str, ...], condition: Literal["invalid_value", "wrong_capability"], helper_id: str
) -> ConfigurationError:
    """Return the error of a wrong argument, by its field path."""
    return ConfigurationError(field_path=field_path, reason=condition, helper_id=helper_id)


def reject(
    condition: Literal["malformed_signature", "invalid_signature", "missing_key", "timestamp_window"],
    helper_id: str,
) -> NoReturn:
    """Refuse a delivery for the condition."""
    raise ProtocolDataError(reason=condition, helper_id=helper_id)


def _is_pairs(value: object) -> TypeIs[list[tuple[str, str]] | tuple[tuple[str, str], ...]]:
    return is_sequence(value) and all(_is_pair(item) for item in value)


def _is_pair(value: object) -> bool:
    return (
        is_sequence(value)
        and type(value) is tuple
        and len(value) == 2  # ruff: ignore[magic-value-comparison]
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


def local_arguments(  # ruff: ignore[too-many-arguments]
    helper_id: str,
    raw_body: object,
    headers: object,
    keys: object,
    *,
    now: object,
    options: object,
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
    return epoch_microseconds(now), limits


def size(limit: int, observed: int, helper_id: str) -> None:
    """Refuse an observed size or count over its limit."""
    if observed > limit:
        raise ProtocolDataError(reason="too_large", helper_id=helper_id)


def sizes(
    limits: ResolvedWebhookOptions, raw_body: bytes, headers: Sequence[tuple[str, str]], keys: int, helper_id: str
) -> None:
    """Refuse a body, headers, or key count over its limit, in that order."""
    size(limits.max_body_bytes, len(raw_body), helper_id)
    size(limits.max_header_bytes, sum(len(name) + len(value) for name, value in headers), helper_id)
    size(limits.max_keys, keys, helper_id)


def instant(microseconds: int) -> datetime | None:
    """Return the aware UTC datetime of microseconds since the epoch, or None beyond datetime's range."""
    try:
        return _EPOCH + timedelta(microseconds=microseconds)
    except OverflowError:
        return None


def within(now: int, timestamp: int, limits: ResolvedWebhookOptions) -> bool:
    """Return whether a timestamp lies in the inclusive window around now, comparing exact microseconds."""
    past, past_scale = limits.past_tolerance.as_integer_ratio()
    future, future_scale = limits.future_tolerance.as_integer_ratio()
    return (now - timestamp) * past_scale <= past * 1_000_000 and (timestamp - now) * future_scale <= future * 1_000_000


def received(plan: EventPlan[T], raw_body: bytes, facts: Facts) -> VerifiedWebhook[T]:
    """Decode an authenticated event and return its facts without retaining delivery state."""
    return VerifiedWebhook(
        data=plan.event.decode(raw_body, plan.helper_id),
        delivery_id=facts.delivery_id,
        timestamp=facts.timestamp,
        matched_key_id=facts.matched_key_id,
    )


def decode_unsigned(plan: EventPlan[T], raw_body: bytes, *, options: WebhookOptions | None = None) -> T:
    """Decode a delivery's event without authenticating it, refusing only a body over max_body_bytes first.

    Nothing about the delivery is verified, so the event is whatever its sender chose.
    """
    helper_id = plan.helper_id
    if not _instance(raw_body, bytes):
        raise configuration_error(("raw_body",), "invalid_value", helper_id)
    size(_limits(options, helper_id).max_body_bytes, len(raw_body), helper_id)
    return plan.event.decode(raw_body, helper_id)
