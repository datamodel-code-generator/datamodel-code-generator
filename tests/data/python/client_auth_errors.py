"""Exercise authentication exceptions through generated public constructors."""

from __future__ import annotations

import importlib
from inspect import Parameter, signature
from typing import TYPE_CHECKING, get_args, get_type_hints

from tests.data.python.client_runtime import outcome, record

if TYPE_CHECKING:
    from types import ModuleType


_AUTH_FIELDS = ("reason", "delivery_state", "phase", "status_code", "oauth_error", "effective_timeout")
_CONFIGURATION_FIELDS = ("reason", "field_path", "source_uri", "source_pointer", "helper_id", "operation")


def _constructors(errors: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    headers = responses.HeadersView((("Authorization", "private-secret"),))
    info = responses.ResponseInfo(
        status_code=401, headers=headers, call_id="auth-call", elapsed=0.25, content_type=None, attempt_count=2
    )
    cause = ValueError("private-secret")
    secondary = OSError("private-secret")
    for name, required, fields in (
        ("AuthError", {"reason": "provider_failed"}, _AUTH_FIELDS),
        ("ConfigurationError", {}, _CONFIGURATION_FIELDS),
    ):
        constructor = getattr(errors, name)
        bare = constructor(**required)
        parameters = signature(constructor).parameters
        lines.append(
            f"  family {name} bases={tuple(base.__name__ for base in constructor.__bases__)} code={bare.reason_code}"
        )
        lines.append(
            f"  signature {name} keyword-only={all(value.kind in {Parameter.KEYWORD_ONLY, Parameter.VAR_KEYWORD} for value in parameters.values())} required={tuple(key for key, value in parameters.items() if value.default is Parameter.empty and value.kind is not Parameter.VAR_KEYWORD)}"
        )
        lines.append(f"  hints {name} {tuple(get_type_hints(constructor.__init__))}")
        lines.append(
            f"  defaults {name} fields={tuple((field, getattr(bare, field)) for field in fields)} common={(bare.operation_id, bare.call_id, bare.parent_session_id, bare.info, bare.cause, bare.secondary_errors, bare.attempt_count, bare.elapsed, bare.request_id)}"
        )
        values = dict(required)
        if name == "ConfigurationError":
            values.update(
                field_path=["private-secret"],
                reason="invalid_scope",
                source_uri="https://private-secret.example/schema",
                source_pointer="/private-secret",
            )
        else:
            values.update(phase="validate", delivery_state=errors.DeliveryState.NOT_SENT, status_code=400)
        secondaries = [secondary]
        error = constructor(
            **values,
            operation_id="auth_operation",
            call_id="auth-call",
            parent_session_id="auth-parent",
            info=info,
            cause=cause,
            secondary_errors=secondaries,
        )
        secondaries.append(RuntimeError("later"))
        lines.append(
            f"  metadata {name} call={(error.operation_id, error.call_id, error.parent_session_id)} info={error.info is info} headers={error.info.headers is headers} cause={error.cause is cause} secondary={error.secondary_errors == (secondary,)} measurements={(error.attempt_count, error.elapsed, error.request_id)}"
        )
        lines.append(f"  supplied {name} {tuple((field, getattr(error, field)) for field in fields)}")
        lines.append(f"  safe {name} {'private-secret' not in str(error) + repr(error)} text={str(error)!r}")
        for keyword in ("message", "condition", "retry_stop_reason"):
            lines.append(
                f"  undeclared {name}.{keyword} {outcome(lambda constructor=constructor, required=required, keyword=keyword: constructor(**required, **{keyword: 'private-secret'}))}"
            )
        lines.append(f"  positional {name} {outcome(lambda constructor=constructor: constructor('private-secret'))}")
    lines.append(f"  missing required AuthError {outcome(errors.AuthError)}")
    for values in (
        {"reason": "oauth_error", "status_code": 400, "oauth_error": "invalid_client"},
        {"reason": "oauth_error", "status_code": 600},
        {"reason": "oauth_error", "status_code": True},
        {"reason": "oauth_error", "oauth_error": "slow_down"},
        {"reason": "timeout", "effective_timeout": -1.0},
        {"reason": "timeout", "effective_timeout": 1.0},
        {"reason": "reauthorization_required", "oauth_error": "invalid_grant"},
    ):
        lines.append(f"  oauth values {values} {outcome(lambda values=values: str(errors.AuthError(**values)))}")


def _validation(errors: ModuleType, lines: list[str]) -> None:
    for reason in (*get_args(errors.AuthReason), "expired", "private-secret"):
        record(lines, f"auth reason {reason}", lambda reason=reason: errors.AuthError(reason=reason).reason_code)
    for phase in ("connect", "read", "write", "pool", "validate", "unknown", "store"):
        record(
            lines, f"auth phase {phase}", lambda phase=phase: errors.AuthError(reason="timeout", phase=phase).phase
        )
    for state in errors.DeliveryState:
        record(
            lines,
            f"auth delivery {state.value}",
            lambda state=state: errors.AuthError(reason="provider_failed", delivery_state=state).delivery_state.value,
        )
    for field in ("phase", "delivery_state"):
        for label, value in (("unknown", "private-secret"), ("none", None), ("bool", True), ("list", [])):
            record(
                lines,
                f"invalid auth {field} {label}",
                lambda field=field, value=value: errors.AuthError(reason="provider_failed", **{field: value}),
            )
    for label, value in (("symbol", "private-secret"), ("empty", ""), ("none", None), ("list", [])):
        record(
            lines,
            f"invalid ConfigurationError reason {label}",
            lambda value=value: errors.ConfigurationError(reason=value),
        )


def auth_errors(package: ModuleType, lines: list[str]) -> None:
    """Report the exact auth error contracts, safe diagnostics, and constructor validation."""
    errors, responses = (importlib.import_module(f"{package.__name__}.{name}") for name in ("errors", "responses"))
    _constructors(errors, responses, lines)
    _validation(errors, lines)
