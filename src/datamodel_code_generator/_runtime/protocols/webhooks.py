"""Webhook verification limits, borrowed keys, signature facts, and replay-store contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime  # noqa: TC003 - Public annotations support get_type_hints().
from sys import float_info
from typing import Generic, Protocol

from typing_extensions import TypeVar

from ..client.errors import ProtocolConfigurationError
from ..model_codecs.unset import UNSET, Unset

K = TypeVar("K")
T_co = TypeVar("T_co", covariant=True)


def positive_count(value: object, name: str) -> None:
    """Validate a positive integer at a public configuration boundary."""
    if type(value) is not int or value <= 0:
        raise ProtocolConfigurationError(field_path=(name,), condition="invalid_value")


def _seconds(value: object, name: str, *, allow_zero: bool) -> None:
    match value:
        case bool():
            pass
        case int() | float() if 0 <= value <= float_info.max and (allow_zero or value > 0):
            return
        case _:
            pass
    raise ProtocolConfigurationError(field_path=(name,), condition="invalid_value")


def _key_tuple(value: object) -> None:
    if not isinstance(value, tuple):
        msg = "keys must be a tuple"
        raise TypeError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class KeySet(Generic[K]):
    """Borrow an ordered tuple of active keys without inspecting or copying their material."""

    keys: tuple[K, ...] = field(repr=False)

    def __post_init__(self) -> None:
        """Require the declared tuple rather than consuming an arbitrary iterable."""
        _key_tuple(self.keys)


@dataclass(frozen=True, slots=True, kw_only=True)
class VerifiedSignature:
    """Authenticated facts returned by a verifier and checked before decoding the event."""

    delivery_id: str | None
    timestamp: datetime | None
    matched_key_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class VerifiedWebhook(Generic[T_co]):
    """A decoded authenticated event and the outcome of its optional replay claim."""

    data: T_co = field(repr=False)
    delivery_id: str | None
    timestamp: datetime | None
    matched_key_id: str
    duplicate: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class WebhookOptions:
    """Standalone verification limits; every UNSET field takes its webhook default."""

    max_body_bytes: int | Unset = UNSET
    max_header_bytes: int | Unset = UNSET
    max_keys: int | Unset = UNSET
    max_signatures: int | Unset = UNSET
    past_tolerance: float | Unset = UNSET
    future_tolerance: float | Unset = UNSET
    replay_ttl: float | Unset = UNSET

    def __post_init__(self) -> None:
        """Reject invalid counts and durations without accepting bool or disabled limits."""
        for name, count in (
            ("max_body_bytes", self.max_body_bytes),
            ("max_header_bytes", self.max_header_bytes),
            ("max_keys", self.max_keys),
            ("max_signatures", self.max_signatures),
        ):
            if not isinstance(count, Unset):
                positive_count(count, name)
        for name, seconds in (
            ("past_tolerance", self.past_tolerance),
            ("future_tolerance", self.future_tolerance),
            ("replay_ttl", self.replay_ttl),
        ):
            if not isinstance(seconds, Unset):
                _seconds(seconds, name, allow_zero=name != "replay_ttl")


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedWebhookOptions:
    """The fully resolved verification limits passed to an application verifier."""

    max_body_bytes: int
    max_header_bytes: int
    max_keys: int
    max_signatures: int
    past_tolerance: float
    future_tolerance: float
    replay_ttl: float


class Verifier(Protocol[K]):
    """A synchronous, non-networking verifier that authenticates the body and every returned fact."""

    def verify(
        self,
        raw_body: bytes,
        ordered_headers: tuple[tuple[str, str], ...],
        keys: KeySet[K],
        now: datetime,
        limits: ResolvedWebhookOptions,
    ) -> VerifiedSignature:
        """Return signature facts only after verifying the complete received bytes."""
        ...


class ReplayStore(Protocol):
    """A borrowed store that atomically retains delivery claims until their expiry."""

    def claim(self, namespace: str, delivery_id: str, expires_at: datetime) -> bool:
        """Return False for an unexpired duplicate, or claim it; full capacity raises an error."""
        ...


class AsyncReplayStore(Protocol):
    """A borrowed asynchronous store with the same atomic replay-claim contract."""

    async def claim(self, namespace: str, delivery_id: str, expires_at: datetime) -> bool:
        """Return False for an unexpired duplicate, or claim it; full capacity raises an error."""
        ...
