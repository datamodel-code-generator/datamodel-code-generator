"""Exercise generated redirect failures and response counter records through their public constructors."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from tests.data.python.client_runtime import record

if TYPE_CHECKING:
    from types import ModuleType


def retry_errors(package: ModuleType, lines: list[str]) -> None:
    """Report preserved redirect metadata, safe diagnostics, and fixed body availability."""
    errors = importlib.import_module(f"{package.__name__}.errors")
    responses = importlib.import_module(f"{package.__name__}.responses")
    headers = responses.HeadersView((("Location", "https://private.example/secret"), ("Set-Cookie", "secret-cookie")))
    counters = {
        "resource_attempt_count": 2,
        "redirect_count": 1,
        "auth_exchange_count": 1,
        "network_send_count": 4,
        "network_send_budget_used": 4,
        "auth_exchange_budget_used": 1,
        "auth_refresh_ids": ("refresh-one",),
        "auth_refresh_pending": 1,
        "wire_send_count": 3,
    }
    info = responses.ResponseInfo(
        status_code=307,
        headers=headers,
        call_id="redirect-call",
        elapsed=0.25,
        content_type="text/plain",
        **counters,
    )
    cause = ValueError("secret-cause")
    secondary = OSError("secret-cleanup")
    error = errors.RedirectPolicyError(
        delivery_state=errors.DeliveryState.RESPONSE_STARTED,
        body_available=False,
        operation_id="redirect_operation",
        call_id="redirect-call",
        parent_session_id="parent-session",
        info=info,
        cause=cause,
        secondary_errors=(secondary,),
        **counters,
    )
    record(lines, "redirect error", lambda: (str(error), error.reason_code, error.body_available))
    record(
        lines,
        "redirect metadata",
        lambda: (
            type(error).__bases__ == (errors.SDKError,),
            isinstance(error, errors.TransportError),
            error.info is info,
            error.cause is cause,
            error.secondary_errors == (secondary,),
            error.parent_session_id,
        ),
    )
    record(
        lines,
        "redirect counters",
        lambda: tuple((name, getattr(error, name), getattr(info, name)) for name in counters),
    )
    record(lines, "redirect safe text", lambda: ("secret" not in str(error), "secret" not in repr(error)))
    record(lines, "redirect received headers", lambda: error.info.headers is headers)
    bare = errors.RedirectPolicyError(delivery_state=errors.DeliveryState.NOT_SENT)
    record(
        lines,
        "redirect without response",
        lambda: (bare.info is None, bare.body_available, tuple((name, getattr(bare, name)) for name in counters)),
    )
    for state in errors.DeliveryState:
        record(lines, f"redirect {state.value}", lambda state=state: errors.RedirectPolicyError(delivery_state=state))
    for value in (True, None, 0, "false"):
        record(
            lines,
            f"redirect invalid body {value!r}",
            lambda value=value: errors.RedirectPolicyError(
                delivery_state=errors.DeliveryState.RESPONSE_STARTED, body_available=value
            ),
        )
    record(lines, "redirect invalid delivery", lambda: errors.RedirectPolicyError(delivery_state="RESPONSE_STARTED"))
    try:
        error.body_available = True
    except AttributeError:
        record(lines, "redirect availability unchanged", lambda: error.body_available)
    else:
        record(lines, "redirect availability overwritten", lambda: error.body_available)
    try:
        error.delivery_state = errors.DeliveryState.NOT_SENT
    except AttributeError:
        record(lines, "redirect delivery unchanged", lambda: error.delivery_state.value)
    else:
        record(lines, "redirect delivery overwritten", lambda: error.delivery_state.value)
    record(
        lines,
        "wire counter omitted",
        lambda: (
            responses.ResponseInfo(
                status_code=200,
                headers=responses.HeadersView(),
                call_id="counter-call",
                elapsed=0.0,
                content_type=None,
            ).wire_send_count
        ),
    )
    for count in (None, 0, 3):
        record(
            lines,
            f"wire counter {count!r}",
            lambda count=count: (
                responses.ResponseInfo(
                    status_code=200,
                    headers=responses.HeadersView(),
                    call_id="counter-call",
                    elapsed=0.0,
                    content_type=None,
                    wire_send_count=count,
                ).wire_send_count
            ),
        )
