"""Exercise generated redirect refusals and response measurements through their public constructors."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from tests.data.python.client_runtime import record

if TYPE_CHECKING:
    from types import ModuleType


def retry_errors(package: ModuleType, lines: list[str]) -> None:
    """Report preserved redirect metadata, safe diagnostics, and the response measurements."""
    errors = importlib.import_module(f"{package.__name__}.errors")
    responses = importlib.import_module(f"{package.__name__}.responses")
    headers = responses.HeadersView((("Location", "https://private.example/secret"), ("Set-Cookie", "secret-cookie")))
    info = responses.ResponseInfo(
        status_code=307,
        headers=headers,
        elapsed=0.25,
        content_type="text/plain",
        request_id="redirect-request",
        attempt_count=2,
    )
    cause = ValueError("secret-cause")
    error = errors.ConfigurationError(
        field_path=("redirects",),
        reason="redirect_refused",
        operation_id="redirect_operation",
        info=info,
        cause=cause,
    )
    record(lines, "redirect error", lambda: (str(error), error.reason))
    record(
        lines,
        "redirect metadata",
        lambda: (
            type(error).__bases__ == (errors.SDKError,),
            isinstance(error, errors.APIConnectionError),
            error.info is info,
            error.cause is cause,
        ),
    )
    record(lines, "redirect measurements", lambda: (error.attempt_count, error.elapsed, error.request_id))
    record(lines, "redirect safe text", lambda: ("secret" not in str(error), "secret" not in repr(error)))
    record(lines, "redirect received headers", lambda: error.info.headers is headers)
    bare = errors.ConfigurationError(field_path=("redirects",), reason="redirect_refused")
    record(
        lines,
        "redirect without response",
        lambda: (bare.info is None, bare.attempt_count, bare.elapsed, bare.request_id),
    )
    record(
        lines,
        "attempts omitted",
        lambda: (
            responses.ResponseInfo(
                status_code=200,
                headers=responses.HeadersView(),
                elapsed=0.0,
                content_type=None,
            ).attempt_count
        ),
    )
