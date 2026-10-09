"""Exercise authentication exceptions through generated public constructors."""

from __future__ import annotations

import importlib
from datetime import datetime, timezone
from inspect import Parameter, signature
from typing import TYPE_CHECKING, get_args, get_type_hints

from tests.data.python.client_runtime import outcome

if TYPE_CHECKING:
    from types import ModuleType


_AUTH_FIELDS = ("reason", "status_code", "oauth_error", "expires_at")
_CONFIGURATION_FIELDS = ("reason", "field_path", "source_uri", "source_pointer", "helper_id")


def _constructors(errors: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    headers = responses.HeadersView((("Authorization", "private-secret"),))
    info = responses.ResponseInfo(
        status_code=401, headers=headers, call_id="auth-call", elapsed=0.25, content_type=None, attempt_count=2
    )
    cause = ValueError("private-secret")
    for name, required, fields in (
        ("AuthError", {"reason": "provider_failed"}, _AUTH_FIELDS),
        ("ConfigurationError", {}, _CONFIGURATION_FIELDS),
    ):
        constructor = getattr(errors, name)
        bare = constructor(**required)
        parameters = signature(constructor).parameters
        lines.append(f"  family {name} bases={tuple(base.__name__ for base in constructor.__bases__)}")
        lines.append(
            f"  signature {name} keyword-only={all(value.kind is Parameter.KEYWORD_ONLY for value in parameters.values())} required={tuple(key for key, value in parameters.items() if value.default is Parameter.empty)}"
        )
        lines.append(f"  hints {name} {tuple(get_type_hints(constructor.__init__))}")
        lines.append(
            f"  defaults {name} fields={tuple((field, getattr(bare, field)) for field in fields)} common={(bare.operation_id, bare.info, bare.cause, bare.attempt_count, bare.elapsed, bare.request_id)}"
        )
        values = dict(required)
        if name == "ConfigurationError":
            values.update(
                field_path=["private-secret"],
                reason="invalid_scope",
                source_uri="https://private-secret.example/schema",
                source_pointer="/private-secret",
                info=info,
            )
        else:
            values.update(status_code=400)
        error = constructor(**values, operation_id="auth_operation", cause=cause)
        lines.append(
            f"  metadata {name} operation={error.operation_id} info={error.info is info or error.info} cause={error.cause is cause} measurements={(error.attempt_count, error.elapsed, error.request_id)}"
        )
        lines.append(f"  supplied {name} {tuple((field, getattr(error, field)) for field in fields)}")
        lines.append(f"  safe {name} {'private-secret' not in str(error) + repr(error)} text={str(error)!r}")
        lines.append(f"  positional {name} {outcome(lambda constructor=constructor: constructor('private-secret'))}")
    lines.append(f"  missing required AuthError {outcome(errors.AuthError)}")
    for values in (
        {"reason": "oauth_error", "status_code": 400, "oauth_error": "invalid_client"},
        {"reason": "reauthorization_required", "oauth_error": "invalid_grant"},
    ):
        lines.append(f"  oauth values {values} {outcome(lambda values=values: str(errors.AuthError(**values)))}")
    lines.append(f"  reasons {get_args(errors.AuthReason)}")
    for expiry in (None, datetime(2020, 1, 2, tzinfo=timezone.utc)):
        error = errors.AuthError(reason="token_expired", expires_at=expiry)
        lines.append(f"  expiry {error.reason} {error.expires_at!r} same={error.expires_at is expiry}")


def auth_errors(package: ModuleType, lines: list[str]) -> None:
    """Report the auth and configuration error contracts and their safe diagnostics."""
    errors, responses = (importlib.import_module(f"{package.__name__}.{name}") for name in ("errors", "responses"))
    _constructors(errors, responses, lines)
