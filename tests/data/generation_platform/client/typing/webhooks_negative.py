"""Reject changed key types, mutable records, invalid options."""

from __future__ import annotations

from pets.errors import ProtocolConfigurationError, WebhookVerificationError
from pets.protocols import (
    KeySet,
    OperationRef,
    VerifiedSignature,
    VerifiedWebhook,
    Verifier,
    WebhookOptions,
)


def wrong_keys(keys: KeySet[str], verifier: Verifier[str]) -> None:
    """Require the same invariant key type and an immutable tuple of borrowed keys."""
    wider_keys: KeySet[object] = keys  # error
    wider_verifier: Verifier[object] = verifier  # error
    KeySet[str](keys=["active"])  # error
    keys.keys = ("changed",)  # error
    del wider_keys, wider_verifier


def wrong_records(signature: VerifiedSignature, event: VerifiedWebhook[str]) -> None:
    """Reject record mutation, wrong event payloads, and undeclared option or error values."""
    signature.matched_key_id = "changed"  # error
    event.data = "changed"  # error
    wrong_event: VerifiedWebhook[int] = event  # error
    WebhookOptions(max_keys=None)  # error
    WebhookOptions(max_keys=1.5)  # error
    OperationRef("/webhooks/event/post", pointer="/webhooks/event/post")  # error
    ProtocolConfigurationError(field_path=("keys",), condition="missing")  # error
    ProtocolConfigurationError(field_path=("keys",), condition="invalid_value", operation="event")  # error
    WebhookVerificationError(condition="bad_key")  # error
    del wrong_event
