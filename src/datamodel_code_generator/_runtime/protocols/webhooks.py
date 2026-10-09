"""Webhook verification limits, borrowed keys, and authenticated signature facts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime  # ruff: ignore[typing-only-standard-library-import] - Public annotations support get_type_hints().
from typing import Final, Generic, Protocol

from typing_extensions import TypeVar

from ..client.timing import check_limits
from ..model_codecs.unset import UNSET, Unset

K = TypeVar("K")
T_co = TypeVar("T_co", covariant=True)

_LIMITS: Final = (
    ("max_body_bytes", False, False, False),
    ("max_header_bytes", False, False, False),
    ("max_keys", False, False, False),
    ("max_signatures", False, False, False),
    ("past_tolerance", True, False, True),
    ("future_tolerance", True, False, True),
)


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
    """A decoded authenticated event and its facts; applications may deduplicate by delivery id."""

    data: T_co = field(repr=False)
    delivery_id: str | None
    timestamp: datetime | None
    matched_key_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class WebhookOptions:
    """Standalone verification limits; every UNSET field takes its webhook default."""

    max_body_bytes: int | Unset = UNSET
    max_header_bytes: int | Unset = UNSET
    max_keys: int | Unset = UNSET
    max_signatures: int | Unset = UNSET
    past_tolerance: float | Unset = UNSET
    future_tolerance: float | Unset = UNSET

    def __post_init__(self) -> None:
        """Reject invalid counts and durations without accepting bool or disabled limits."""
        check_limits(self, _LIMITS)


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedWebhookOptions:
    """The fully resolved verification limits passed to an application verifier."""

    max_body_bytes: int
    max_header_bytes: int
    max_keys: int
    max_signatures: int
    past_tolerance: float
    future_tolerance: float


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
