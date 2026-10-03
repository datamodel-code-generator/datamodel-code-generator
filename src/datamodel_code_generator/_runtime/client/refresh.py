"""Inline OAuth token acquisition: a provider's current token, renewed under its lock by the caller that needs it.

One caller at a time sends a token request; callers arriving meanwhile wait for the lock and use the token it obtained.
Nothing runs in the background and nothing is persisted: a refresh token provider hands each refreshed token set to its
`on_token_refreshed` callback once it is current.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .auth import BearerCredential, CredentialContext, TokenSet, TokenVersion, checked_scopes
from .errors import (
    AuthConfigurationError,
    AuthProviderClosedError,
    AuthReauthorizationRequiredError,
    AuthTimeoutError,
    DeliveryState,
    OAuthExchangeError,
    TokenExpiredError,
)
from .oauth import Session, receipt, token_material

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from .auth import AccessToken
    from .oauth import AsyncTokenEndpoint, Exchanged, TokenEndpoint
    from .options import OAuthProviderOptions


def refresh_margin(ttl: float) -> float:
    """Return how long before its expiry a token is renewed: a tenth of its lifetime, at most thirty seconds."""
    return min(30.0, ttl * 0.1)


@dataclass(frozen=True, slots=True)
class Published:
    """Material a provider serves, and the monotonic time from which a caller renews it, or None to keep it."""

    material: BearerCredential
    refresh_at: float | None


def published(access: AccessToken, received: datetime, at: float, *, renewable: bool = True) -> Published:
    """Return new material for an access token received at the wall-clock and monotonic times.

    A token a request can renew is renewed a tenth of its lifetime, at most thirty seconds, before it expires; another
    one is served until it expires.
    """
    material = BearerCredential(access, TokenVersion())
    if (expires_at := access.expires_at) is None:
        return Published(material, None)
    ttl = (expires_at - received).total_seconds()
    return Published(material, at + ttl - (refresh_margin(ttl) if renewable else 0.0))


def checked_audience(audience: object) -> str | None:
    """Refuse a fixed audience that is neither None nor a nonempty string."""
    if audience is not None and (not isinstance(audience, str) or not audience):
        raise AuthConfigurationError(field_path=("audience",), condition="invalid_value")
    return audience


def checked_context(context: object, audience: str | None) -> CredentialContext:
    """Refuse a context of another type or one requiring an audience other than the configured one."""
    if not isinstance(context, CredentialContext):
        raise AuthConfigurationError(field_path=("context",), condition="invalid_type")
    if context.audience is not None and context.audience != audience:
        raise AuthConfigurationError(field_path=("audience",), condition="audience_mismatch")
    return context


def checked_token_set(value: object, audience: str | None) -> TokenSet:
    """Refuse a token set whose access token has a naive expiry, another type than Bearer, or another audience."""
    if not isinstance(value, TokenSet):
        raise AuthConfigurationError(field_path=("token_set",), condition="invalid_type")
    access = value.access_token
    if (expires_at := access.expires_at) is not None and expires_at.utcoffset() is None:
        raise AuthConfigurationError(field_path=("token_set", "access_token", "expires_at"), condition="invalid_value")
    if access.token_type.lower() != "bearer":
        raise AuthConfigurationError(
            field_path=("token_set", "access_token", "token_type"), condition="unsupported_token_type"
        )
    if access.audience is not None and access.audience != audience:
        raise AuthConfigurationError(field_path=("audience",), condition="audience_mismatch")
    return value


def exchange_error(exchanged: Exchanged, cause: BaseException | None = None) -> Exception:
    """Return the error of a token request that obtained no usable token, keeping only safe response facts."""
    delivery = exchanged.delivery
    if exchanged.outcome in {"success", "rejected", "http_status", "malformed_response"}:
        phase: Literal["validate", "unknown"] = (
            "validate" if exchanged.outcome in {"success", "malformed_response"} else "unknown"
        )
        return OAuthExchangeError(
            status_code=exchanged.status_code,
            oauth_error=exchanged.oauth_error,
            delivery_state=delivery,
            phase=phase,
            cause=exchanged.cause if cause is None else cause,
        )
    if (timeout_kind := exchanged.timeout_kind) is not None:
        assert exchanged.timeout is not None
        return AuthTimeoutError(
            effective_timeout=exchanged.timeout,
            timeout_kind=timeout_kind,
            delivery_state=delivery,
            phase=exchanged.phase,
            cause=exchanged.cause,
        )
    return OAuthExchangeError(delivery_state=delivery, phase=exchanged.phase, cause=exchanged.cause)


class ClientCredentialsGrant:
    """The client credentials grant's configuration, its request form, and the token set an answer yields."""

    __slots__ = ("audience", "form", "scopes")

    def __init__(self, method: object, scopes: object, audience: object) -> None:
        """Refuse public clients, then validate the requested scopes and the fixed audience."""
        if method == "none":
            raise AuthConfigurationError(field_path=("client_auth_method",), condition="invalid_value")
        self.scopes = checked_scopes(scopes, "scopes")
        self.audience = checked_audience(audience)
        self.form = (
            ("grant_type", "client_credentials"),
            *((("scope", " ".join(self.scopes)),) if self.scopes else ()),
            *((("audience", self.audience),) if self.audience is not None else ()),
        )

    @staticmethod
    def initial(options: OAuthProviderOptions) -> None:
        """Return no material: the first caller acquires it."""
        del options

    def request(self) -> tuple[tuple[str, str], ...]:
        """Return the form of the next token request."""
        return self.form

    def answered(self, exchanged: Exchanged) -> tuple[Published, TokenSet]:
        """Return the material and the token set of a successful answer, or raise the request's error."""
        if exchanged.outcome != "success":
            raise exchange_error(exchanged)
        access, _ = _access(exchanged, self.scopes)
        assert exchanged.received is not None
        assert exchanged.receipt is not None
        return published(access, exchanged.received, exchanged.receipt), TokenSet(access, None)


class RefreshTokenGrant:
    """The refresh token grant: the current token set, which each successful refresh replaces."""

    __slots__ = ("audience", "scopes", "token_set")

    def __init__(self, token_set: object, scopes: object, audience: object) -> None:
        """Validate the fixed audience, the requested scopes, and the token set the provider starts from."""
        self.audience = checked_audience(audience)
        self.scopes = checked_scopes(scopes, "scopes")
        self.token_set = checked_token_set(token_set, self.audience)

    def initial(self, options: OAuthProviderOptions) -> Published:
        """Return the material of the token set the provider starts from, renewed once due like a refreshed one."""
        tokens = self.token_set
        received, at = receipt(options.clock)
        return published(tokens.access_token, received, at, renewable=tokens.refresh_token is not None)

    def request(self) -> tuple[tuple[str, str], ...]:
        """Return the form refreshing the current token set, or refuse one without a refresh token."""
        if (refresh_token := self.token_set.refresh_token) is None:
            raise AuthReauthorizationRequiredError(condition="no_refresh_token", delivery_state=DeliveryState.NOT_SENT)
        return (("grant_type", "refresh_token"), ("refresh_token", refresh_token))

    def answered(self, exchanged: Exchanged) -> tuple[Published, TokenSet]:
        """Make the token set a successful answer yields current, or raise the request's error.

        An omitted refresh token keeps the one sent, and an omitted scope keeps the grants of the access token it
        replaces; invalid_grant requires a new authorization.
        """
        if exchanged.outcome == "rejected" and exchanged.oauth_error == "invalid_grant":
            raise AuthReauthorizationRequiredError(condition="invalid_grant", delivery_state=exchanged.delivery)
        if exchanged.outcome != "success":
            raise exchange_error(exchanged)
        used = self.token_set
        access, issued = _access(exchanged, used.access_token.scopes)
        assert exchanged.received is not None
        assert exchanged.receipt is not None
        tokens = self.token_set = TokenSet(access, used.refresh_token if issued is None else issued)
        return published(access, exchanged.received, exchanged.receipt), tokens


def _access(exchanged: Exchanged, scopes: tuple[str, ...] | None) -> tuple[AccessToken, str | None]:
    """Return the access and refresh tokens of a successful answer, or raise the error of an unusable one."""
    assert exchanged.fields is not None
    assert exchanged.received is not None
    try:
        return token_material(exchanged.fields, exchanged.received, scopes)
    except (ValueError, TokenExpiredError) as cause:
        raise exchange_error(exchanged, cause) from None


Grant = ClientCredentialsGrant | RefreshTokenGrant


class _Tokens:
    """What both modes share: the grant, its current material, and the options bounding each token request."""

    __slots__ = ("_cache", "_grant", "_options")

    def __init__(self, options: OAuthProviderOptions, grant: Grant) -> None:
        self._options = options
        self._grant = grant
        self._cache = grant.initial(options)

    def identity(self) -> tuple[str | None, tuple[str, ...]]:
        """Return the audience and the scopes the provider requests, which name no secret."""
        return self._grant.audience, self._grant.scopes

    def seen(self, context: object) -> tuple[CredentialContext, TokenVersion | None]:
        """Check a caller's context, and return it with the version the caller saw before waiting for the lock."""
        checked = checked_context(context, self._grant.audience)
        return checked, None if (cache := self._cache) is None else cache.material.version

    def current(self, *, closed: bool, force: bool, seen: TokenVersion | None) -> BearerCredential | None:
        """Return the material a caller may use without a token request, or None; the caller holds the lock.

        A forced caller uses only material that replaced the version it saw before waiting.
        """
        if closed:
            raise AuthProviderClosedError(delivery_state=DeliveryState.NOT_SENT)
        if (cache := self._cache) is None:
            return None
        if force:
            return None if cache.material.version is seen else cache.material
        if cache.refresh_at is None or self._options.clock.monotonic() < cache.refresh_at:
            return cache.material
        return None

    def session(self, context: CredentialContext) -> Session:
        """Start a token request's session, ending by the caller's deadline at the latest."""
        options = self._options
        return Session.start(options.refresh_timeout, options.phase_timeout, options.clock, context.deadline)

    def adopt(self, exchanged: Exchanged) -> tuple[BearerCredential, TokenSet]:
        """Make the material of a successful answer current, or raise the request's error; the caller holds the lock."""
        cache, tokens = self._grant.answered(exchanged)
        self._cache = cache
        return cache.material, tokens

    def invalidate(self, version: object) -> None:
        """Forget the current material if it is the version a resource rejected; another version stays usable."""
        if not isinstance(version, TokenVersion):
            raise AuthConfigurationError(field_path=("version",), condition="invalid_type")
        if (cache := self._cache) is not None and cache.material.version is version:
            self._cache = None


class SyncTokens(_Tokens):
    """A synchronous provider's tokens, renewed under a thread lock by the caller that needs them."""

    __slots__ = ("_endpoint", "_lock", "_notify")

    def __init__(
        self,
        options: OAuthProviderOptions,
        grant: Grant,
        endpoint: TokenEndpoint,
        notify: Callable[[TokenSet], object] | None = None,
    ) -> None:
        """Keep the grant and its endpoint; nothing is sent until a caller needs a token."""
        super().__init__(options, grant)
        self._endpoint = endpoint
        self._notify = notify
        self._lock = threading.Lock()

    def obtain(self, context: object, *, force: bool) -> BearerCredential:
        """Return usable material, or send one token request for it while holding the lock."""
        checked, seen = self.seen(context)
        with self._lock:
            endpoint = self._endpoint
            if (material := self.current(closed=endpoint.closed, force=force, seen=seen)) is not None:
                return material
            form = self._grant.request()
            endpoint.prepare()
            material, tokens = self.adopt(endpoint.exchange(form, self.session(checked)))
            if (notify := self._notify) is not None:
                notify(tokens)
            return material

    def invalidate(self, version: object) -> None:
        """Forget the current material if it is the rejected version, never a token another caller just obtained."""
        with self._lock:
            super().invalidate(version)

    def close(self) -> None:
        """Close an owned token transport; later calls raise AuthProviderClosedError."""
        self._endpoint.close()


class AsyncTokens(_Tokens):
    """An asyncio provider's tokens, renewed under an asyncio lock on the event loop the provider belongs to."""

    __slots__ = ("_endpoint", "_lock", "_notify")

    def __init__(
        self,
        options: OAuthProviderOptions,
        grant: Grant,
        endpoint: AsyncTokenEndpoint,
        notify: Callable[[TokenSet], Awaitable[object]] | None = None,
    ) -> None:
        """Keep the grant and its endpoint; nothing is sent until a caller needs a token."""
        import asyncio  # noqa: PLC0415

        super().__init__(options, grant)
        self._endpoint = endpoint
        self._notify = notify
        self._lock = asyncio.Lock()

    async def obtain(self, context: object, *, force: bool) -> BearerCredential:
        """Return usable material, or send one token request for it while holding the lock."""
        checked, seen = self.seen(context)
        endpoint = self._endpoint
        endpoint.bind()
        async with self._lock:
            if (material := self.current(closed=endpoint.closed, force=force, seen=seen)) is not None:
                return material
            form = self._grant.request()
            endpoint.prepare()
            material, tokens = self.adopt(await endpoint.exchange(form, self.session(checked)))
            if (notify := self._notify) is not None:
                await notify(tokens)
            return material

    async def aclose(self) -> None:
        """Close an owned token transport; later calls raise AuthProviderClosedError."""
        self._endpoint.bind()
        await self._endpoint.aclose()
