"""Webhook verification limits, borrowed keys, signature facts, and replay-store contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime  # noqa: TC003 - Public annotations support get_type_hints().
from typing import Generic, Protocol

from typing_extensions import TypeVar

from ..model_codecs.unset import UNSET, Unset
from .options import WEBHOOK_LIMITS, check_limits

K = TypeVar("K")
T_co = TypeVar("T_co", covariant=True)


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
        check_limits(self, WEBHOOK_LIMITS)


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
