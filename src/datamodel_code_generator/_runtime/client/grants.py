"""OAuth grants: token sets, provider options, and the client credentials and refresh token providers.

Constructors perform no I/O. Token HTTP uses a provider-owned transport that verifies TLS, never follows redirects,
never retries, and never reuses resource credentials, signers, or resource settings.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable  # noqa: TC003 - Public annotations support get_type_hints().
from typing import TYPE_CHECKING, Literal, TypeAlias, final

from typing_extensions import Self, TypeAliasType

from ..model_codecs.unset import UNSET, Unset
from .auth import (
    AsyncCredentialProvider,
    BearerCredential,
    CredentialContext,
    CredentialProvider,
    TokenSet,
    TokenVersion,
)
from .errors import ConfigurationError
from .options import OAuthProviderOptions

if TYPE_CHECKING:
    from .oauth import Endpoint
    from .refresh import AsyncTokens, SyncTokens
    from .transports import AsyncTransportAdapter, OwnedTransportAdapter, TransportAdapter

    TokenTransport: TypeAlias = TransportAdapter | OwnedTransportAdapter[TransportAdapter]
    AsyncTokenTransport: TypeAlias = AsyncTransportAdapter | OwnedTransportAdapter[AsyncTransportAdapter]
else:
    TokenTransport = TypeAliasType("TokenTransport", "TransportAdapter | OwnedTransportAdapter[TransportAdapter]")
    AsyncTokenTransport = TypeAliasType(
        "AsyncTokenTransport", "AsyncTransportAdapter | OwnedTransportAdapter[AsyncTransportAdapter]"
    )

__all__ = (
    "AsyncClientCredentialsProvider",
    "AsyncRefreshTokenProvider",
    "ClientCredentialsProvider",
    "OAuthProviderOptions",
    "RefreshTokenProvider",
    "TokenSet",
    "grant_identity",
)


def _endpoint(value: object, field: str, options: OAuthProviderOptions) -> Endpoint:
    from .oauth import endpoint_url  # noqa: PLC0415

    return endpoint_url(value, field, allow_insecure_loopback=options.allow_insecure_loopback)


def _options(options: object) -> OAuthProviderOptions:
    if options is None:
        return OAuthProviderOptions()
    if not isinstance(options, OAuthProviderOptions):
        raise ConfigurationError(field_path=("options",), reason="invalid_type")
    return options


def _callback(value: object, *, asynchronous: bool) -> None:
    """Refuse a token callback that is not callable, or not a coroutine function exactly when the provider is async."""
    if value is not None and (not callable(value) or inspect.iscoroutinefunction(value) != asynchronous):
        raise ConfigurationError(field_path=("on_token_refreshed",), reason="invalid_mode")


class _Tokens:
    """What the SDK's synchronous token providers share: a token renewed inline by the call that needs it."""

    __slots__ = ("_tokens",)

    def __init__(self, tokens: SyncTokens) -> None:
        self._tokens = tokens

    def get(self, context: CredentialContext) -> BearerCredential:
        """Return the current token, or acquire one while holding the provider's lock when none is usable."""
        return self._tokens.obtain(context, force=False)

    def refresh(self, context: CredentialContext) -> BearerCredential:
        """Acquire a new token, unless another caller replaced the current one while this caller waited."""
        return self._tokens.obtain(context, force=True)

    def invalidate(self, version: TokenVersion) -> None:
        """Forget the current token if it is the version a resource rejected; another version stays usable."""
        self._tokens.invalidate(version)

    def close(self) -> None:
        """Close the token transport the provider owns; later calls raise the provider_closed AuthError."""
        self._tokens.close()

    def __enter__(self) -> Self:
        """Return the provider."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the provider."""
        self.close()


class _AsyncTokens:
    """What the SDK's asyncio token providers share, bound to the event loop they were created on or first used from."""

    __slots__ = ("_tokens",)

    def __init__(self, tokens: AsyncTokens) -> None:
        self._tokens = tokens

    async def get(self, context: CredentialContext) -> BearerCredential:
        """Return the current token, or acquire one while holding the provider's lock when none is usable."""
        return await self._tokens.obtain(context, force=False)

    async def refresh(self, context: CredentialContext) -> BearerCredential:
        """Acquire a new token, unless another caller replaced the current one while this caller waited."""
        return await self._tokens.obtain(context, force=True)

    async def invalidate(self, version: TokenVersion) -> None:
        """Forget the current token if it is the version a resource rejected; another version stays usable."""
        self._tokens.invalidate(version)

    async def aclose(self) -> None:
        """Close the token transport the provider owns; later calls raise the provider_closed AuthError."""
        await self._tokens.aclose()

    async def __aenter__(self) -> Self:
        """Return the provider."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Close the provider."""
        await self.aclose()


def grant_identity(provider: object) -> tuple[str | None, tuple[str, ...]] | None:
    """Return the audience and requested scopes of an OAuth token provider of the SDK, or None for any other."""
    if isinstance(provider, (_Tokens, _AsyncTokens)):
        return provider._tokens.identity()  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    return None


@final
class ClientCredentialsProvider(_Tokens):
    """The OAuth client credentials grant: a confidential client's own token, acquired when first needed and shared.

    The caller needing a token acquires it while holding the provider's lock, and concurrent callers wait for that
    token. It is renewed once a tenth of its lifetime, at most thirty seconds, remains. Close it to release its
    transport.
    """

    __slots__ = ()

    def __init__(  # noqa: PLR0913
        self,
        token_url: str,
        *,
        client_id: str,
        client_secret: CredentialProvider,
        client_auth_method: Literal["client_secret_basic", "client_secret_post"] = "client_secret_basic",
        scopes: tuple[str, ...] = (),
        audience: str | None = None,
        options: OAuthProviderOptions | None = None,
        token_transport: TokenTransport | Unset = UNSET,
    ) -> None:
        """Validate the endpoint, client authentication, scopes, audience, and transport without I/O."""
        from .auth_policy import sync_provider  # noqa: PLC0415
        from .oauth import TokenEndpoint, client_authentication  # noqa: PLC0415
        from .refresh import ClientCredentialsGrant, SyncTokens  # noqa: PLC0415

        resolved = _options(options)
        grant = ClientCredentialsGrant(client_auth_method, scopes, audience)
        authentication = client_authentication(client_id, client_auth_method, client_secret, sync_provider)
        endpoint = TokenEndpoint(
            _endpoint(token_url, "token_url", resolved), authentication, resolved.transport, token_transport
        )
        super().__init__(SyncTokens(resolved, grant, endpoint))


@final
class AsyncClientCredentialsProvider(_AsyncTokens):
    """The asyncio client credentials grant, bound to the event loop it was created on or first used from."""

    __slots__ = ()

    def __init__(  # noqa: PLR0913
        self,
        token_url: str,
        *,
        client_id: str,
        client_secret: AsyncCredentialProvider,
        client_auth_method: Literal["client_secret_basic", "client_secret_post"] = "client_secret_basic",
        scopes: tuple[str, ...] = (),
        audience: str | None = None,
        options: OAuthProviderOptions | None = None,
        token_transport: AsyncTokenTransport | Unset = UNSET,
    ) -> None:
        """Validate the endpoint, client authentication, scopes, audience, and transport without I/O."""
        from .auth_policy import async_provider  # noqa: PLC0415
        from .oauth import AsyncTokenEndpoint, client_authentication  # noqa: PLC0415
        from .refresh import AsyncTokens, ClientCredentialsGrant  # noqa: PLC0415

        resolved = _options(options)
        grant = ClientCredentialsGrant(client_auth_method, scopes, audience)
        authentication = client_authentication(client_id, client_auth_method, client_secret, async_provider)
        endpoint = AsyncTokenEndpoint(
            _endpoint(token_url, "token_url", resolved), authentication, resolved.transport, token_transport
        )
        super().__init__(AsyncTokens(resolved, grant, endpoint))


@final
class RefreshTokenProvider(_Tokens):
    """The OAuth refresh token grant for one token set, which this provider alone refreshes.

    The caller needing a token refreshes it while holding the provider's lock, and concurrent callers wait for that
    token. The access token is renewed once a tenth of its lifetime, at most thirty seconds, remains. Each refreshed
    token set becomes current, then goes to `on_token_refreshed`, which may persist it. Close it to release its
    transport.
    """

    __slots__ = ()

    def __init__(  # noqa: PLR0913
        self,
        token_url: str,
        *,
        client_id: str,
        token_set: TokenSet,
        on_token_refreshed: Callable[[TokenSet], object] | None = None,
        client_secret: CredentialProvider | None = None,
        client_auth_method: Literal["none", "client_secret_basic", "client_secret_post"] = "client_secret_basic",
        scopes: tuple[str, ...] = (),
        audience: str | None = None,
        options: OAuthProviderOptions | None = None,
        token_transport: TokenTransport | Unset = UNSET,
    ) -> None:
        """Validate the endpoint, client authentication, token set, callback, scopes, audience, and transport."""
        from .auth_policy import sync_provider  # noqa: PLC0415
        from .oauth import TokenEndpoint, client_authentication  # noqa: PLC0415
        from .refresh import RefreshTokenGrant, SyncTokens  # noqa: PLC0415

        resolved = _options(options)
        grant = RefreshTokenGrant(token_set, scopes, audience)
        _callback(on_token_refreshed, asynchronous=False)
        authentication = client_authentication(client_id, client_auth_method, client_secret, sync_provider)
        endpoint = TokenEndpoint(
            _endpoint(token_url, "token_url", resolved), authentication, resolved.transport, token_transport
        )
        super().__init__(SyncTokens(resolved, grant, endpoint, on_token_refreshed))


@final
class AsyncRefreshTokenProvider(_AsyncTokens):
    """The asyncio refresh token grant, bound to the event loop it was created on or first used from.

    Its `on_token_refreshed` callback is a coroutine function.
    """

    __slots__ = ()

    def __init__(  # noqa: PLR0913
        self,
        token_url: str,
        *,
        client_id: str,
        token_set: TokenSet,
        on_token_refreshed: Callable[[TokenSet], Awaitable[object]] | None = None,
        client_secret: AsyncCredentialProvider | None = None,
        client_auth_method: Literal["none", "client_secret_basic", "client_secret_post"] = "client_secret_basic",
        scopes: tuple[str, ...] = (),
        audience: str | None = None,
        options: OAuthProviderOptions | None = None,
        token_transport: AsyncTokenTransport | Unset = UNSET,
    ) -> None:
        """Validate the endpoint, client authentication, token set, callback, scopes, audience, and transport."""
        from .auth_policy import async_provider  # noqa: PLC0415
        from .oauth import AsyncTokenEndpoint, client_authentication  # noqa: PLC0415
        from .refresh import AsyncTokens, RefreshTokenGrant  # noqa: PLC0415

        resolved = _options(options)
        grant = RefreshTokenGrant(token_set, scopes, audience)
        _callback(on_token_refreshed, asynchronous=True)
        authentication = client_authentication(client_id, client_auth_method, client_secret, async_provider)
        endpoint = AsyncTokenEndpoint(
            _endpoint(token_url, "token_url", resolved), authentication, resolved.transport, token_transport
        )
        super().__init__(AsyncTokens(resolved, grant, endpoint, on_token_refreshed))
