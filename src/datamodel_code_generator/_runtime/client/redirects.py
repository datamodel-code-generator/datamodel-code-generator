"""Redirect eligibility without resource ownership or request mutation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal

from .errors import DeliveryState, RedirectPolicyError
from .retry import replay_safe
from .urls import URLValidationError
from .urls import redirect_target as resolve_target

if TYPE_CHECKING:
    from .options import ResolvedRedirectOptions
    from .responses import HeadersView
    from .retry import IdempotencyPlan
    from .urls import Origin

_READ_METHODS: Final = frozenset({"GET", "HEAD"})
_SEE_OTHER: Final = 303


@dataclass(frozen=True, slots=True, kw_only=True)
class RedirectState:
    """Current-hop facts and the original call's destination constraints."""

    method: str
    url: str = field(repr=False)
    current_origin: Origin = field(repr=False)
    initial_origin: Origin = field(repr=False)
    allowed_origins: frozenset[Origin] = field(repr=False)
    redirect_count: int
    visited: frozenset[tuple[str, str]] = field(repr=False)
    retry_safety: Literal["method_default", "idempotent", "never"]
    idempotency: IdempotencyPlan | None
    key_expires_at: float | None
    body_replayable: bool


@dataclass(frozen=True, slots=True)
class RedirectTarget:
    """A permitted next hop for the call runtime to build and admit."""

    method: str
    url: str = field(repr=False)
    origin: Origin = field(repr=False)
    drop_body: bool
    cross_origin: bool


def _method(status: int, state: RedirectState, redirects: ResolvedRedirectOptions, now: float) -> str:
    if state.retry_safety == "never" or (state.key_expires_at is not None and now >= state.key_expires_at):
        raise RedirectPolicyError(delivery_state=DeliveryState.RESPONSE_STARTED)
    if status == _SEE_OTHER:
        if state.method in _READ_METHODS or redirects.allow_303_to_get:
            return "GET"
    elif state.body_replayable:
        if status in {301, 302}:
            if state.method in _READ_METHODS:
                return state.method
        elif replay_safe(state.method, state.retry_safety, state.idempotency, state.key_expires_at, now=now):
            return state.method
    raise RedirectPolicyError(delivery_state=DeliveryState.RESPONSE_STARTED)


def redirect_target(
    status: int,
    headers: HeadersView,
    state: RedirectState,
    redirects: ResolvedRedirectOptions,
    *,
    now: float,
) -> RedirectTarget:
    """Return a permitted hop, or preserve a terminal redirect-policy failure."""
    locations = headers.get_all("Location")
    if len(locations) != 1 or state.redirect_count >= redirects.max_redirects:
        raise RedirectPolicyError(delivery_state=DeliveryState.RESPONSE_STARTED)
    try:
        target = resolve_target(state.url, locations[0])
    except URLValidationError as cause:
        raise RedirectPolicyError(delivery_state=DeliveryState.RESPONSE_STARTED, cause=cause) from cause
    if target.origin != state.initial_origin and target.origin not in state.allowed_origins:
        raise RedirectPolicyError(delivery_state=DeliveryState.RESPONSE_STARTED)
    if state.current_origin[0] == "https" and target.origin[0] == "http" and not redirects.allow_https_downgrade:
        raise RedirectPolicyError(delivery_state=DeliveryState.RESPONSE_STARTED)
    method = _method(status, state, redirects, now)
    if (method, target.url) in state.visited:
        raise RedirectPolicyError(delivery_state=DeliveryState.RESPONSE_STARTED)
    return RedirectTarget(
        method, target.url, target.origin, status == _SEE_OTHER, target.origin != state.current_origin
    )
