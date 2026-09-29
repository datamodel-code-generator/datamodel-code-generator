"""Exercise authentication exceptions through generated public constructors and readonly properties."""

from __future__ import annotations

import importlib
from datetime import datetime, timezone
from inspect import Parameter, signature
from typing import TYPE_CHECKING, get_type_hints

from tests.data.python.client_runtime import outcome, record

if TYPE_CHECKING:
    from types import ModuleType


_COUNTERS = (
    "resource_attempt_count",
    "redirect_count",
    "auth_exchange_count",
    "network_send_count",
    "network_send_budget_used",
    "auth_exchange_budget_used",
    "auth_refresh_ids",
    "auth_refresh_pending",
    "wire_send_count",
)
_AUTH_FIELDS = ("provider_id", "refresh_id", "state", "delivery_state", "phase")
_FAMILIES = (
    ("AuthConfigurationError", {}, ("field_path", "condition", "source_uri", "source_pointer")),
    ("SigningConfigurationError", {}, ("field_path", "condition", "source_uri", "source_pointer")),
    ("SigningExecutionError", {}, ("signer_index",)),
    ("AuthRefreshError", {}, ()),
    ("AuthProviderExecutionError", {"callback": "get"}, ("callback",)),
    (
        "InsufficientScopeError",
        {"required_scopes": ("read",), "granted_scopes": ()},
        ("required_scopes", "granted_scopes", "missing_scopes"),
    ),
    ("TokenExpiredError", {"condition": "expired"}, ("condition", "expires_at")),
    ("AuthProviderClosedError", {"state": "CLOSED"}, ()),
    ("OAuthExchangeError", {}, ("status_code", "oauth_error")),
    ("AuthTimeoutError", {"effective_timeout": 5.0, "timeout_kind": "phase"}, ("effective_timeout", "timeout_kind")),
    ("AuthStateUncertainError", {"failure_kind": "transport"}, ("failure_kind", "status_code")),
    ("AuthReauthorizationRequiredError", {"condition": "invalid_grant"}, ("condition",)),
    ("AuthStateConflictError", {"action": "exchange_code"}, ("action",)),
)


def _constructors(errors: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    counters = dict(zip(_COUNTERS, (1, 2, 3, 4, 5, 6, ("refresh-one",), 7, 8)))
    headers = responses.HeadersView((("Authorization", "private-secret"),))
    info = responses.ResponseInfo(
        status_code=401,
        headers=headers,
        call_id="auth-call",
        elapsed=0.25,
        content_type=None,
        **counters,
    )
    cause = ValueError("private-secret")
    secondary = OSError("private-secret")
    for name, required, own_fields in _FAMILIES:
        constructor = getattr(errors, name)
        bare = constructor(**required)
        auth_fields = _AUTH_FIELDS if isinstance(bare, errors.AuthRefreshError) else ()
        fields = (*auth_fields, *own_fields)
        parameters = signature(constructor).parameters
        lines.append(
            f"  family {name} bases={tuple(base.__name__ for base in constructor.__bases__)} code={bare.reason_code}"
        )
        lines.append(
            f"  signature {name} keyword-only={all(value.kind is Parameter.KEYWORD_ONLY for value in parameters.values())} required={tuple(key for key, value in parameters.items() if value.default is Parameter.empty)}"
        )
        lines.append(f"  hints {name} {tuple(get_type_hints(constructor.__init__))}")
        lines.append(
            f"  defaults {name} fields={tuple((field, getattr(bare, field)) for field in fields)} counters={tuple(getattr(bare, field) for field in _COUNTERS)} common={(bare.operation_id, bare.call_id, bare.parent_session_id, bare.info, bare.cause, bare.secondary_errors)}"
        )
        values = dict(required)
        if isinstance(bare, errors.ConfigurationError):
            values.update(
                field_path=["private-secret"],
                condition="invalid_scope",
                source_uri="https://private-secret.example/schema",
                source_pointer="/private-secret",
            )
        if auth_fields:
            values.update(
                provider_id="private-secret-provider",
                refresh_id="private-secret-refresh",
                state="CLOSING" if name == "AuthProviderClosedError" else "READY",
                phase="validate",
                delivery_state=errors.DeliveryState.NOT_SENT,
            )
        if name == "SigningExecutionError":
            values["signer_index"] = 2
        secondaries = [secondary]
        refresh_ids = ["refresh-one"]
        error = constructor(
            **values,
            operation_id="auth_operation",
            call_id="auth-call",
            parent_session_id="auth-parent",
            info=info,
            cause=cause,
            secondary_errors=secondaries,
            **{**counters, "auth_refresh_ids": refresh_ids},
        )
        secondaries.append(RuntimeError("later"))
        refresh_ids.append("later")
        lines.append(
            f"  metadata {name} call={(error.operation_id, error.call_id, error.parent_session_id)} info={error.info is info} headers={error.info.headers is headers} cause={error.cause is cause} secondary={error.secondary_errors == (secondary,)} counters={tuple(getattr(error, field) for field in _COUNTERS)}"
        )
        lines.append(f"  supplied {name} {tuple((field, getattr(error, field)) for field in fields)}")
        lines.append(f"  safe {name} {'private-secret' not in str(error) + repr(error)} text={str(error)!r}")
        for field in (*_COUNTERS, *auth_fields, *(() if isinstance(error, errors.ConfigurationError) else own_fields)):
            previous = getattr(error, field)
            lines.append(
                f"  readonly {name}.{field} {outcome(lambda error=error, field=field: setattr(error, field, None))} unchanged={getattr(error, field) is previous}"
            )
        for field in (*auth_fields, *(() if isinstance(error, errors.ConfigurationError) else own_fields)):
            lines.append(f"  property hints {name}.{field} {tuple(get_type_hints(getattr(constructor, field).fget))}")
        for keyword in ("message", "status_code", "oauth_error", "retry_stop_reason"):
            lines.append(
                f"  undeclared {name}.{keyword} {outcome(lambda constructor=constructor, required=required, keyword=keyword: constructor(**required, **{keyword: 'private-secret'}))}"
            )
        lines.append(f"  positional {name} {outcome(lambda constructor=constructor: constructor('private-secret'))}")
    parameters = signature(errors.AuthProviderClosedError).parameters
    lines.append(f"  closed inherited state default={parameters['state'].default!r}")
    record(lines, "closed state omitted", errors.AuthProviderClosedError)
    for name in (
        "AuthProviderExecutionError",
        "InsufficientScopeError",
        "TokenExpiredError",
        "AuthTimeoutError",
        "AuthStateUncertainError",
        "AuthReauthorizationRequiredError",
        "AuthStateConflictError",
    ):
        lines.append(f"  missing required {name} {outcome(getattr(errors, name))}")
    for name, values in (
        ("OAuthExchangeError", {"status_code": 400, "oauth_error": "invalid_client"}),
        ("OAuthExchangeError", {"status_code": 600}),
        ("OAuthExchangeError", {"status_code": True}),
        ("OAuthExchangeError", {"oauth_error": "slow_down"}),
        ("AuthTimeoutError", {"effective_timeout": -1.0, "timeout_kind": "phase"}),
        ("AuthTimeoutError", {"effective_timeout": 1.0, "timeout_kind": "total"}),
        ("AuthStateUncertainError", {"failure_kind": "unknown"}),
        ("AuthReauthorizationRequiredError", {"condition": "expired"}),
        ("AuthStateConflictError", {"action": "delete"}),
    ):
        lines.append(f"  oauth values {name} {values} {outcome(lambda name=name, values=values: str(getattr(errors, name)(**values)))}")


def _validation(errors: ModuleType, lines: list[str]) -> None:
    for state in (
        "UNKNOWN",
        "UNINITIALIZED",
        "LOAD_FAILED",
        "READY",
        "PERSIST_PENDING",
        "EXCHANGE_REJECTED",
        "UNCERTAIN",
        "REAUTH_REQUIRED",
        "CLOSING",
        "CLOSED",
        "CREATED",
        "EXCHANGING",
        "SUCCEEDED",
        "FAILED_NOT_SENT",
    ):
        record(lines, f"auth state {state}", lambda state=state: errors.AuthRefreshError(state=state).state)
        if state in {"CLOSING", "CLOSED"}:
            record(
                lines, f"closed state {state}", lambda state=state: errors.AuthProviderClosedError(state=state).state
            )
        else:
            record(lines, f"closed rejects {state}", lambda state=state: errors.AuthProviderClosedError(state=state))
    for phase in ("admission", "load", "connect", "read", "write", "pool", "validate", "store", "wait", "unknown"):
        record(lines, f"auth phase {phase}", lambda phase=phase: errors.AuthRefreshError(phase=phase).phase)
    for state in errors.DeliveryState:
        record(
            lines,
            f"auth delivery {state.value}",
            lambda state=state: errors.AuthRefreshError(delivery_state=state).delivery_state.value,
        )
    for callback in ("get", "invalidate", "refresh"):
        record(
            lines,
            f"provider callback {callback}",
            lambda callback=callback: errors.AuthProviderExecutionError(callback=callback).callback,
        )
    record(lines, "omitted auth state", lambda: errors.AuthRefreshError().state)
    record(lines, "omitted closed provider state", lambda: errors.AuthProviderClosedError().state)
    for field in ("state", "phase", "delivery_state"):
        for label, value in (("unknown", "private-secret"), ("none", None), ("bool", True), ("list", [])):
            record(
                lines,
                f"invalid auth {field} {label}",
                lambda field=field, value=value: errors.AuthRefreshError(**{field: value}),
            )
    for field in ("provider_id", "refresh_id"):
        record(lines, f"empty auth {field}", lambda field=field: getattr(errors.AuthRefreshError(**{field: ""}), field))
        for label, value in (("number", 1), ("bool", True), ("list", [])):
            record(
                lines,
                f"invalid auth {field} {label}",
                lambda field=field, value=value: errors.AuthRefreshError(**{field: value}),
            )
    for value in (None, 0, 3):
        record(
            lines,
            f"signer index {value!r}",
            lambda value=value: errors.SigningExecutionError(signer_index=value).signer_index,
        )
    for label, value in (("negative", -1), ("bool", True), ("float", 1.0), ("text", "private-secret")):
        record(
            lines, f"invalid signer index {label}", lambda value=value: errors.SigningExecutionError(signer_index=value)
        )
    for value in ("close", "aclose", "GET", None, 1, []):
        record(
            lines,
            f"invalid callback {value!r}",
            lambda value=value: errors.AuthProviderExecutionError(callback=value),
        )
    for name in ("AuthConfigurationError", "SigningConfigurationError"):
        constructor = getattr(errors, name)
        for label, value in (("symbol", "private-secret"), ("empty", ""), ("none", None), ("list", [])):
            record(
                lines,
                f"invalid {name} condition {label}",
                lambda constructor=constructor, value=value: constructor(condition=value),
            )
    for condition in ("expired", "nonpositive_expiry", "invalid_expiry"):
        for expiry in (None, datetime(2020, 1, 2, tzinfo=timezone.utc), datetime(2020, 1, 2)):
            error = errors.TokenExpiredError(condition=condition, expires_at=expiry)
            lines.append(f"  expiry {error.condition} {error.expires_at!r} same={error.expires_at is expiry}")
    for field, values in (
        ("condition", ("private-secret", None, 1, [])),
        ("expires_at", ("private-secret", 1, True, [], datetime(2020, 1, 2).date())),
    ):
        for index, value in enumerate(values):
            record(
                lines,
                f"invalid expiry {field} {index}",
                lambda field=field, value=value: errors.TokenExpiredError(**{"condition": "expired", field: value}),
            )


def _scopes(errors: ModuleType, lines: list[str]) -> None:
    for required, granted in (
        (("write", "read", "read"), ("read",)),
        (("read", "Read", "write", "Write"), ("read", "write")),
        (("read,write",), ("read", "write")),
        ((), ()),
        (("read",), ("read",)),
        ((), ("read",)),
        (("!", "#", "[", "]", "~"), ("~", "[", "!")),
        (("private-secret",), ()),
    ):
        error = errors.InsufficientScopeError(required_scopes=required, granted_scopes=granted)
        lines.append(
            f"  scopes required={error.required_scopes} granted={error.granted_scopes} missing={error.missing_scopes} safe={'private-secret' not in str(error) + repr(error)}"
        )
    required = ["write", "read", "read"]
    granted = ["read"]
    error = errors.InsufficientScopeError(required_scopes=required, granted_scopes=granted)
    required.append("later")
    granted.append("write")
    lines.append(f"  copied scopes {(error.required_scopes, error.granted_scopes, error.missing_scopes)}")
    for field in ("required_scopes", "granted_scopes"):
        for label, value in (
            ("unknown", None),
            ("text", "read"),
            ("iterator", iter(("read",))),
            ("set", {"read"}),
            ("number", (1,)),
            ("empty", ("",)),
            ("leading space", (" read",)),
            ("trailing space", ("read ",)),
            ("double space", ("read  write",)),
            ("quote", ('"',)),
            ("backslash", ("\\",)),
            ("control", ("\x1f",)),
            ("delete", ("\x7f",)),
            ("nonascii", ("r\xe9ad",)),
        ):
            record(
                lines,
                f"invalid scopes {field} {label}",
                lambda field=field, value=value: errors.InsufficientScopeError(**{
                    "required_scopes": ("read",),
                    "granted_scopes": (),
                    field: value,
                }),
            )
    lines.append(
        f"  derived missing keyword {outcome(lambda: errors.InsufficientScopeError(required_scopes=('read',), granted_scopes=(), missing_scopes=('read',)))}"
    )


def auth_errors(package: ModuleType, lines: list[str]) -> None:
    """Report the exact auth error contracts, safe diagnostics, and constructor validation."""
    errors, responses = (importlib.import_module(f"{package.__name__}.{name}") for name in ("errors", "responses"))
    _constructors(errors, responses, lines)
    _validation(errors, lines)
    _scopes(errors, lines)
