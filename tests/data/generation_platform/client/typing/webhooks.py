"""Preserve webhook event and borrowed-key types through the public contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pets.errors import ConfigurationError
from pets.options import UNSET
from pets.protocols import (
    KeySet,
    OperationRef,
    ResolvedWebhookOptions,
    VerifiedSignature,
    VerifiedWebhook,
    Verifier,
    WebhookOptions,
)
from typing_extensions import assert_type

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True)
class ApplicationKey:
    """An application-owned key whose internal fields are not part of the verifier contract."""

    identity: str


class ApplicationVerifier:
    """A structural implementation of the borrowed synchronous verifier interface."""

    def verify(
        self,
        raw_body: bytes,
        ordered_headers: tuple[tuple[str, str], ...],
        keys: KeySet[ApplicationKey],
        now: datetime,
        limits: ResolvedWebhookOptions,
    ) -> VerifiedSignature:
        """Keep the application's verification implementation outside this static sample."""
        raise NotImplementedError


def contracts(now: datetime, limits: ResolvedWebhookOptions) -> None:
    """Keep one key type and expose the declared record and option field types."""
    keys = KeySet(keys=(ApplicationKey("active"),))
    assert_type(keys, KeySet[ApplicationKey])
    assert_type(keys.keys, tuple[ApplicationKey, ...])
    verifier: Verifier[ApplicationKey] = ApplicationVerifier()
    assert_type(verifier.verify(b"event", (), keys, now, limits), VerifiedSignature)
    event = VerifiedWebhook[str](data="event", delivery_id=None, timestamp=None, matched_key_id="active")
    assert_type(event.data, str)
    accepts_event(event)
    options = WebhookOptions(max_keys=2, past_tolerance=0, future_tolerance=UNSET)
    assert_type(options.max_keys, int | UNSET)
    assert_type(options.past_tolerance, float | UNSET)
    assert_type(limits.past_tolerance, float)
    operation = OperationRef(pointer="/webhooks/event/post")
    assert_type(operation.document, str | None)
    failure = ConfigurationError(field_path=("keys",), reason="invalid_value", helper_id="event")
    assert_type(failure.helper_id, str | None)


def accepts_event(event: VerifiedWebhook[object]) -> None:
    """Accept a more specific immutable event through the covariant result contract."""
    assert_type(event.data, object)
