"""Exercise authentication exceptions through generated public constructors."""

from __future__ import annotations

import importlib
from inspect import Parameter, signature
from typing import TYPE_CHECKING, get_args, get_type_hints

from tests.api_generation.support.client_runtime import outcome

if TYPE_CHECKING:
    from types import ModuleType


_AUTH_FIELDS = ("reason", "status_code", "oauth_error")
_CONFIGURATION_FIELDS = ("reason", "field_path", "source_uri", "source_pointer", "helper_id")


def _constructors(errors: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    headers = responses.HeadersView((("Authorization", "private-secret"),))
    info = responses.ResponseInfo(status_code=401, headers=headers, elapsed=0.25, content_type=None, attempt_count=2)
    cause = ValueError("private-secret")
    for name, required, fields in (
        ("AuthError", {"reason": "provider_failed"}, _AUTH_FIELDS),
        ("ConfigurationError", {}, _CONFIGURATION_FIELDS),
    ):
        constructor = getattr(errors, name)
        bare = constructor(**required)
        parameters = signature(constructor).parameters
        lines.extend((
            f"  family {name} bases={tuple(base.__name__ for base in constructor.__bases__)}",
            (
                f"  signature {name} "
                f"keyword-only={all(value.kind is Parameter.KEYWORD_ONLY for value in parameters.values())} "
                f"required={tuple(key for key, value in parameters.items() if value.default is Parameter.empty)}"
            ),
            f"  hints {name} {tuple(get_type_hints(constructor.__init__))}",
            (
                f"  defaults {name} fields={tuple((field, getattr(bare, field)) for field in fields)} "
                f"common={bare.operation_id, bare.info, bare.cause, bare.attempt_count, bare.elapsed, bare.request_id}"
            ),
        ))
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
        lines.extend((
            (
                f"  metadata {name} operation={error.operation_id} info={error.info is info or error.info} "
                f"cause={error.cause is cause} measurements={error.attempt_count, error.elapsed, error.request_id}"
            ),
            f"  supplied {name} {tuple((field, getattr(error, field)) for field in fields)}",
            f"  safe {name} {'private-secret' not in str(error) + repr(error)} text={str(error)!r}",
            f"  positional {name} {outcome(lambda constructor=constructor: constructor('private-secret'))}",
        ))
    lines.append(f"  missing required AuthError {outcome(errors.AuthError)}")
    lines.extend(
        f"  oauth values {values} {outcome(lambda values=values: str(errors.AuthError(**values)))}"
        for values in (
            {"reason": "oauth_error", "status_code": 400, "oauth_error": "invalid_client"},
            {"reason": "reauthorization_required", "oauth_error": "invalid_grant"},
        )
    )
    lines.append(f"  reasons {get_args(errors.AuthReason)}")


def auth_errors(package: ModuleType, lines: list[str]) -> None:
    """Report the auth and configuration error contracts and their safe diagnostics."""
    errors, responses = (importlib.import_module(f"{package.__name__}.{name}") for name in ("errors", "responses"))
    _constructors(errors, responses, lines)
