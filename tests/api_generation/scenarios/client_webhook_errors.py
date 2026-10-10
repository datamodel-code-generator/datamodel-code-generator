"""Report the public webhook errors of a generated package and their retained, private helper context."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType


def webhook_errors(package: ModuleType, lines: list[str]) -> None:
    """Inspect the errors a webhook helper raises, their context, and their safe representations."""
    errors, protocols, responses = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("errors", "protocols", "responses")
    )
    secret = "private-webhook-marker"
    operation = protocols.OperationRef(pointer=f"/paths/{secret}/post", document=f"https://example.com/{secret}")
    info = responses.ResponseInfo(
        status_code=400,
        headers=responses.HeadersView((("x-private", secret),)),
        elapsed=0.25,
        content_type="application/json",
    )
    cause = RuntimeError(secret)
    configuration = {
        "field_path": (secret,),
        "reason": "binding_mismatch",
        "source_uri": f"https://example.com/{secret}",
        "source_pointer": f"/{secret}",
        "helper_id": secret,
    }
    for error, fields in (
        (errors.ConfigurationError(**configuration, operation_id="webhook", info=info, cause=cause), configuration),
        *(
            (
                errors.ProtocolDataError(
                    reason=reason, helper_id=secret, operation=operation, operation_id="webhook", info=info, cause=cause
                ),
                {"reason": reason, "helper_id": secret, "operation": operation},
            )
            for reason in (
                "malformed",
                "too_large",
                "malformed_signature",
                "invalid_signature",
                "missing_key",
                "timestamp_window",
            )
        ),
    ):
        lines.extend((
            f"  {type(error).__name__}: base={type(error).__bases__[0].__name__} reason={error.reason}",
            f"    fields={','.join(sorted(vars(error)))}",
            (
                f"    context={error.info is info}/{error.cause is cause} "
                f"retained={all(getattr(error, key) == value for key, value in fields.items())}"
            ),
            f"    str={error} secret={secret in str(error) or secret in repr(error)}",
        ))
    field_path = [secret]
    error = errors.ConfigurationError(field_path=field_path, reason="invalid_value")
    field_path.clear()
    lines.append(f"  copied configuration path: {error.field_path == (secret,)}")
