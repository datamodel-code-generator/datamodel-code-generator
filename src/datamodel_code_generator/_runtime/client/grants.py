"""OAuth grants: token sets, provider options, and the client credentials and refresh token providers.

Constructors perform no I/O. Token HTTP uses a provider-owned transport that verifies TLS, never follows redirects,
never retries, and never reuses resource credentials, signers, or resource settings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, TypeAlias, final

from typing_extensions import Self, TypeAliasType

from ..model_codecs.unset import UNSET, Unset
from .admission import AsyncTokenAcquirer, TokenAcquirer
from .auth import (
    AsyncCredentialProvider,
    AsyncTokenLoad,
    AsyncTokenStore,
    BearerCredential,
    CredentialContext,
    CredentialProvider,
    RefreshInfo,
    TokenLoad,
    TokenSet,
    TokenStore,
    TokenVersion,
    checked_scopes,
)
from .errors import AuthConfigurationError
from .options import OAuthProviderOptions

if TYPE_CHECKING:
    from concurrent.futures import Future

    from .admission import CallAdmission
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


class _SharedTokens(TokenAcquirer):
    """What the SDK's synchronous token providers share: one family's shared acquisitions, and its release."""

    __slots__ = ("_tokens",)

    def __init__(self, tokens: SyncTokens) -> None:
        self._tokens = tokens

    def get(self, context: CredentialContext) -> BearerCredential:
        """Return the shared token, joining or starting its acquisition when none is usable."""
        return self._tokens.obtain(context, force=False)

    def refresh(self, context: CredentialContext) -> BearerCredential:
        """Acquire a new token, or join the acquisition already running, whatever the cache holds."""
        return self._tokens.obtain(context, force=True)

    def invalidate(self, version: TokenVersion) -> None:
        """Forget the cached token if it is the version a resource rejected; another version stays usable."""
        self._tokens.invalidate(version)

    def refresh_snapshot(self, refresh_id: str) -> RefreshInfo | None:
        """Return a recent acquisition's snapshot by its refresh id, or None once it is unknown or evicted."""
        return self._tokens.snapshot(refresh_id)

    def acquire_for(self, context: CredentialContext, admission: CallAdmission) -> BearerCredential:
        """Return the shared token for a client call, which accounts for a new acquisition it starts."""
        return self._tokens.obtain(context, force=False, admission=admission)

    def exchange_needed(self, version: TokenVersion) -> bool:
        """Return whether replacing a rejected version needs a new acquisition rather than a running or newer one."""
        return self._tokens.exchange_needed(version)

    def close(self) -> None:
        """Refuse new acquisitions, let a running one finish within its deadline, then close the owned transport.

        Every close raises the failure of that release.
        """
        self._tokens.close()

    def request_close(self) -> Future[None]:
        """Start closing without waiting, and return the release every close awaits.

        The owned transport closes once no acquisition runs, or once the running one's session ended.
        """
        return self._tokens.request_close()

    def __enter__(self) -> Self:
        """Return the provider."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the provider."""
        self.close()


def grant_identity(provider: object) -> tuple[str | None, tuple[str, ...]] | None:
    """Return the audience and requested scopes of an OAuth token provider of the SDK, or None for any other."""
    if isinstance(provider, (_SharedTokens, _AsyncSharedTokens)):
        return provider._tokens.identity()  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
    return None


class _AsyncSharedTokens(AsyncTokenAcquirer):
    """What the SDK's asyncio token providers share, bound to the event loop they were created on or first used from."""

    __slots__ = ("_tokens",)

    def __init__(self, tokens: AsyncTokens) -> None:
        self._tokens = tokens

    async def get(self, context: CredentialContext) -> BearerCredential:
        """Return the shared token, joining or starting its acquisition when none is usable."""
        return await self._tokens.obtain(context, force=False)

    async def refresh(self, context: CredentialContext) -> BearerCredential:
        """Acquire a new token, or join the acquisition already running, whatever the cache holds."""
        return await self._tokens.obtain(context, force=True)

    async def invalidate(self, version: TokenVersion) -> None:
        """Forget the cached token if it is the version a resource rejected; another version stays usable."""
        self._tokens.invalidate(version)

    def refresh_snapshot(self, refresh_id: str) -> RefreshInfo | None:
        """Return a recent acquisition's snapshot by its refresh id, or None once it is unknown or evicted."""
        return self._tokens.snapshot(refresh_id)

    async def acquire_for(self, context: CredentialContext, admission: CallAdmission) -> BearerCredential:
        """Return the shared token for a client call, which accounts for a new acquisition it starts."""
        return await self._tokens.obtain(context, force=False, admission=admission)

    def exchange_needed(self, version: TokenVersion) -> bool:
        """Return whether replacing a rejected version needs a new acquisition rather than a running or newer one."""
        return self._tokens.exchange_needed(version)

    async def aclose(self) -> None:
        """Close once in a task of its own; a cancelled caller leaves it running for a later aclose to await."""
        await self._tokens.aclose()

    async def __aenter__(self) -> Self:
        """Return the provider."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Close the provider."""
        await self.aclose()


@final
class ClientCredentialsProvider(_SharedTokens):
    """The OAuth client credentials grant: a confidential client's own token, acquired when first needed and shared.

    Concurrent callers join one acquisition, which runs on a worker of the provider under its own deadline, so a caller
    leaving never cancels it. The token is renewed once a tenth of its lifetime, at most thirty seconds, remains; a
    failed acquisition leaves nothing behind, and a later call acquires again. Close it to release its transport.
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
        """Validate the endpoint, client authentication, scopes, audience, and transport without I/O or threads."""
        from .auth_policy import sync_provider  # noqa: PLC0415
        from .oauth import TokenEndpoint, client_authentication  # noqa: PLC0415
        from .refresh import ClientCredentialsGrant, SyncClientCredentials  # noqa: PLC0415

        resolved = _options(options)
        grant = ClientCredentialsGrant(client_auth_method, scopes, audience)
        authentication = client_authentication(client_id, client_auth_method, client_secret, sync_provider)
        endpoint = TokenEndpoint(
            _endpoint(token_url, "token_url", resolved), authentication, resolved.transport, token_transport
        )
        super().__init__(SyncClientCredentials(resolved, endpoint, grant))


@final
class AsyncClientCredentialsProvider(_AsyncSharedTokens):
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
        """Validate the endpoint, client authentication, scopes, audience, and transport without I/O or tasks."""
        from .auth_policy import async_provider  # noqa: PLC0415
        from .oauth import AsyncTokenEndpoint, client_authentication  # noqa: PLC0415
        from .refresh import AsyncClientCredentials, ClientCredentialsGrant  # noqa: PLC0415

        resolved = _options(options)
        grant = ClientCredentialsGrant(client_auth_method, scopes, audience)
        authentication = client_authentication(client_id, client_auth_method, client_secret, async_provider)
        endpoint = AsyncTokenEndpoint(
            _endpoint(token_url, "token_url", resolved), authentication, resolved.transport, token_transport
        )
        super().__init__(AsyncClientCredentials(resolved, endpoint, grant))


def _family(  # noqa: PLR0913
    token_set: object, load: object, store: object, scopes: object, audience: object, *, asynchronous: bool
) -> tuple[TokenSet | None, tuple[str, ...], str | None]:
    """Validate a refresh token family's token set or load, its store, configured scopes, and audience.

    A store needs a load, which reads what it stored.
    """
    from .auth_policy import async_load, async_store, sync_load, sync_store  # noqa: PLC0415
    from .refresh import checked_audience  # noqa: PLC0415
    from .rotation import checked_token_set  # noqa: PLC0415

    if store is not None and load is None:
        raise AuthConfigurationError(field_path=("load",), condition="missing_value")
    if token_set is None and load is None:
        raise AuthConfigurationError(field_path=("token_set",), condition="missing_value")
    if load is not None and not (async_load(load) if asynchronous else sync_load(load)):
        raise AuthConfigurationError(field_path=("load",), condition="invalid_mode")
    if store is not None and not (async_store(store) if asynchronous else sync_store(store)):
        raise AuthConfigurationError(field_path=("store",), condition="invalid_mode")
    initial = None if token_set is None else checked_token_set(token_set)
    return initial, checked_scopes(scopes, "scopes"), checked_audience(audience)


@final
class RefreshTokenProvider(_SharedTokens):
    """The OAuth refresh token grant for one token family, which this provider alone renews, following its rotation.

    Concurrent callers join one refresh, which runs on a worker of the provider under its own deadline, so a caller
    leaving never cancels it. The access token is renewed once a tenth of its lifetime, at most thirty seconds, remains.
    A refresh token a request may have delivered is never sent again: a refresh whose outcome is unknown, rejected, or
    requiring reauthorization stops the family until `replace_token_set`. With a token store, a refreshed token set
    becomes current once stored, and one whose store failed waits for `retry_store`. Close it to release its transport.
    """

    __slots__ = ("_rotation",)

    def __init__(  # noqa: PLR0913
        self,
        token_url: str,
        *,
        client_id: str,
        token_set: TokenSet | None = None,
        load: TokenLoad | None = None,
        store: TokenStore | None = None,
        client_secret: CredentialProvider | None = None,
        client_auth_method: Literal["none", "client_secret_basic", "client_secret_post"] = "client_secret_basic",
        scopes: tuple[str, ...] = (),
        audience: str | None = None,
        options: OAuthProviderOptions | None = None,
        token_transport: TokenTransport | Unset = UNSET,
    ) -> None:
        """Validate the endpoint, client authentication, token set or callbacks, scopes, audience, and transport."""
        from .auth_policy import sync_provider  # noqa: PLC0415
        from .oauth import TokenEndpoint, client_authentication  # noqa: PLC0415
        from .rotation import SyncRotation, cache_key  # noqa: PLC0415

        resolved = _options(options)
        initial, requested, checked = _family(token_set, load, store, scopes, audience, asynchronous=False)
        authentication = client_authentication(client_id, client_auth_method, client_secret, sync_provider)
        url = _endpoint(token_url, "token_url", resolved)
        key = cache_key(url, authentication.client_id, authentication.method, checked, requested)
        endpoint = TokenEndpoint(url, authentication, resolved.transport, token_transport)
        self._rotation = SyncRotation(
            resolved, endpoint, initial, checked, scopes=requested, key=key, load=load, store=store
        )
        super().__init__(self._rotation)

    def replace_token_set(self, token_set: TokenSet, *, persist: bool = True) -> None:
        """Adopt a token set of a higher revision, restarting a stopped family.

        Its refresh token must not be one the family spent, and without one its access token must be unexpired. With a
        token store and `persist`, it becomes current once stored, expecting the stored revision last confirmed; a
        failed store leaves it waiting for `retry_store`. Otherwise it becomes current at once and nothing is stored.
        """
        self._rotation.replace(token_set, persist=persist)

    def retry_store(self, *, expected_revision: int | Unset | None = UNSET) -> TokenSet:
        """Store the token set whose store failed once more, and return it once current.

        The store expects the revision the failed one did, or `expected_revision` when given, such as the
        `observed_revision` of a conflict the application resolved. Concurrent calls expecting the same revision share
        one store; it sends no token request, and needs a failed store and no other job.
        """
        return self._rotation.retry_store(_expected_revision(expected_revision))

    def reload_token_set(self) -> TokenSet | None:
        """Load the persisted token set once, keeping it if it is newer, and return the current token set.

        A usable newer token set recovers a family whose initial load failed or that stopped; the result is None once
        the family stopped. It needs a token load, and no refresh may run.
        """
        return self._rotation.reload()


@final
class AsyncRefreshTokenProvider(_AsyncSharedTokens):
    """The asyncio refresh token grant, bound to the event loop it was created on or first used from."""

    __slots__ = ("_rotation",)

    def __init__(  # noqa: PLR0913
        self,
        token_url: str,
        *,
        client_id: str,
        token_set: TokenSet | None = None,
        load: AsyncTokenLoad | None = None,
        store: AsyncTokenStore | None = None,
        client_secret: AsyncCredentialProvider | None = None,
        client_auth_method: Literal["none", "client_secret_basic", "client_secret_post"] = "client_secret_basic",
        scopes: tuple[str, ...] = (),
        audience: str | None = None,
        options: OAuthProviderOptions | None = None,
        token_transport: AsyncTokenTransport | Unset = UNSET,
    ) -> None:
        """Validate the endpoint, client authentication, token set or callbacks, scopes, audience, and transport."""
        from .auth_policy import async_provider  # noqa: PLC0415
        from .oauth import AsyncTokenEndpoint, client_authentication  # noqa: PLC0415
        from .rotation import AsyncRotation, cache_key  # noqa: PLC0415

        resolved = _options(options)
        initial, requested, checked = _family(token_set, load, store, scopes, audience, asynchronous=True)
        authentication = client_authentication(client_id, client_auth_method, client_secret, async_provider)
        url = _endpoint(token_url, "token_url", resolved)
        key = cache_key(url, authentication.client_id, authentication.method, checked, requested)
        endpoint = AsyncTokenEndpoint(url, authentication, resolved.transport, token_transport)
        self._rotation = AsyncRotation(
            resolved, endpoint, initial, checked, scopes=requested, key=key, load=load, store=store
        )
        super().__init__(self._rotation)

    async def replace_token_set(self, token_set: TokenSet, *, persist: bool = True) -> None:
        """Adopt a newer token set as the synchronous provider does, on the event loop the provider is bound to."""
        await self._rotation.replace(token_set, persist=persist)

    async def retry_store(self, *, expected_revision: int | Unset | None = UNSET) -> TokenSet:
        """Store the token set whose store failed once more as the synchronous provider does, on the provider's loop."""
        return await self._rotation.retry_store(_expected_revision(expected_revision))

    async def reload_token_set(self) -> TokenSet | None:
        """Load the persisted token set once as the synchronous provider does, on the provider's event loop."""
        return await self._rotation.reload()


def _expected_revision(value: object) -> int | Unset | None:
    """Accept a stored revision to expect, None for none stored, or UNSET to keep the one the failed store expected."""
    if value is None or isinstance(value, Unset):
        return value
    if not isinstance(value, int) or isinstance(value, bool):
        raise AuthConfigurationError(field_path=("expected_revision",), condition="invalid_type")
    if value < 0:
        raise AuthConfigurationError(field_path=("expected_revision",), condition="invalid_value")
    return value


def _options(options: object) -> OAuthProviderOptions:
    if options is None:
        return OAuthProviderOptions()
    if not isinstance(options, OAuthProviderOptions):
        raise AuthConfigurationError(field_path=("options",), condition="invalid_type")
    return options
