"""Report the public webhook errors of a generated package and their retained, private helper context."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, get_type_hints

from tests.data.python.client_runtime import record

if TYPE_CHECKING:
    from types import ModuleType


def webhook_errors(package: ModuleType, lines: list[str]) -> None:
    """Inspect generated public classes, their constructor contracts, and their safe representations."""
    errors, protocols, responses = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("errors", "protocols", "responses")
    )
    secret = "private-webhook-marker"
    operation = protocols.OperationRef(pointer=f"/paths/{secret}/post", document=f"https://example.com/{secret}")
    info = responses.ResponseInfo(
        status_code=400,
        headers=responses.HeadersView((("x-private", secret),)),
        call_id="call-1",
        elapsed=0.25,
        content_type="application/json",
    )
    cause = RuntimeError(secret)
    cleanup = RuntimeError(f"{secret}-cleanup")
    cases = (
        ("ProtocolError", {}),
        (
            "ConfigurationError",
            {
                "field_path": (secret,),
                "reason": "binding_mismatch",
                "source_uri": f"https://example.com/{secret}",
                "source_pointer": f"/{secret}",
            },
        ),
        ("ProtocolDataError", {"condition": "malformed"}),
        ("ProtocolSizeError", {"kind": "keys", "limit": 8, "observed": 9, "unit": "items"}),
        ("WebhookVerificationError", {"condition": "invalid_signature"}),
        ("UnsupportedContentCodingError", {"coding": secret}),
        (
            "DecompressionLimitError",
            {"layer": 1, "encoded_bytes": 2, "max_ratio": 100.0, "limit": 1_048_576, "observed": 1_048_577},
        ),
    )
    for name, fields in cases:
        secondary = [cleanup]
        error_type = getattr(errors, name)
        error = error_type(
            **fields,
            helper_id=secret,
            operation=operation,
            operation_id="webhook",
            call_id="call-1",
            parent_session_id="session-1",
            info=info,
            cause=cause,
            secondary_errors=secondary,
        )
        secondary.clear()
        hints = get_type_hints(error_type.__init__)
        lines.extend((
            (
                f"  {name}: base={error_type.__bases__[0].__name__} sdk={isinstance(error, errors.SDKError)} "
                f"reason={error.reason_code}"
            ),
            f"    fields={','.join(sorted(vars(error)))}",
            (
                f"    context={error.helper_id == secret}/{error.operation is operation}/{error.info is info}/"
                f"{error.cause is cause}/{error.secondary_errors == (cleanup,)} "
                f"call={error.operation_id}/{error.call_id}/{error.parent_session_id}"
            ),
            (
                f"    hints={hints['operation'] == protocols.OperationRef | None}/"
                f"{hints['helper_id'] == str | None}/{hints.get('info') == responses.ResponseInfo | None} "
                f"retained={all(getattr(error, key) == value for key, value in fields.items())}"
            ),
            f"    str={error} repr={error!r} secret={secret in str(error) or secret in repr(error)}",
        ))
    for label, create in (
        ("protocol defaults", errors.ProtocolError),
        (
            "configuration defaults",
            lambda: errors.ConfigurationError(field_path=(), reason="invalid_value"),
        ),
        ("verification defaults", lambda: errors.WebhookVerificationError(condition="missing_key")),
        ("store defaults", lambda: errors.ProtocolStoreError(action="get")),
    ):
        error = create()
        defaults = (
            error.helper_id,
            error.operation,
            error.operation_id,
            error.call_id,
            error.parent_session_id,
            error.info,
            error.cause,
            error.secondary_errors,
        )
        lines.append(f"  {label}: {defaults!r} {error}")
    for condition in (
        "unknown_field",
        "invalid_value",
        "missing_metadata",
        "missing_adapter",
        "wrong_capability",
        "security_partition",
        "binding_mismatch",
    ):
        error = errors.ConfigurationError(field_path=(), reason=condition)
        lines.append(f"  configuration reason: {error.reason}")
    for condition in ("malformed_signature", "invalid_signature", "missing_key", "timestamp_window"):
        error = errors.WebhookVerificationError(condition=condition)
        lines.append(f"  verification condition: {error.condition}")
    for action in (
        "get",
        "set",
        "delete",
    ):
        error = errors.ProtocolStoreError(action=action)
        lines.append(f"  store action: {error.action} entry={error.entry_id}")
    field_path = [secret]
    error = errors.ConfigurationError(field_path=field_path, reason="invalid_value")
    field_path.clear()
    lines.append(f"  copied configuration path: {error.field_path == (secret,)}")
    for label, create in (
        ("configuration missing path", lambda: errors.ConfigurationError(reason="invalid_value")),
        ("configuration missing reason", lambda: errors.ConfigurationError(field_path=())),
        (
            "configuration reason unknown",
            lambda: errors.ConfigurationError(field_path=(), reason="private-webhook-marker"),
        ),
        ("configuration reason type", lambda: errors.ConfigurationError(field_path=(), reason=3)),
        (
            "configuration helper type",
            lambda: errors.ConfigurationError(field_path=(), reason="invalid_value", helper_id=3),
        ),
        ("helper type", lambda: errors.ProtocolError(helper_id=3)),
        ("operation type", lambda: errors.ProtocolError(operation="private-webhook-marker")),
        ("verification missing condition", errors.WebhookVerificationError),
        ("verification condition unknown", lambda: errors.WebhookVerificationError(condition="unknown")),
        ("verification condition type", lambda: errors.WebhookVerificationError(condition=None)),
        ("store missing action", errors.ProtocolStoreError),
        ("store action unknown", lambda: errors.ProtocolStoreError(action="unknown")),
        ("store action type", lambda: errors.ProtocolStoreError(action=None)),
        ("store entry type", lambda: errors.ProtocolStoreError(action="get", entry_id=3)),
        ("positional message", lambda: errors.WebhookVerificationError(secret, condition="invalid_signature")),
        ("extra signature", lambda: errors.WebhookVerificationError(condition="invalid_signature", signature=secret)),
    ):
        record(lines, label, create)
