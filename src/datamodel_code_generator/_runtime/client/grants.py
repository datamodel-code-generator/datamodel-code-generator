"""OAuth grants: token sets, provider options, and the explicit authorization code flow.

Constructors and authorization requests perform no I/O. Token HTTP uses a provider-owned transport that verifies TLS,
never follows redirects, never retries, and never reuses resource credentials, signers, or resource settings.
"""

from __future__ import annotations

import base64
import threading
import weakref
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Generic, Literal, TypeVar, final

from typing_extensions import Self

from ..model_codecs.unset import UNSET, Unset
from .auth import AccessToken, AsyncCredentialProvider, CredentialProvider, checked_scopes, checked_type
from .errors import (
    AuthConfigurationError,
    AuthProviderClosedError,
    AuthReauthorizationRequiredError,
    AuthStateConflictError,
    AuthStateUncertainError,
    AuthTimeoutError,
    DeliveryState,
    OAuthExchangeError,
    TokenExpiredError,
)
from .options import TimeoutOptions, TransportOptions
from .timing import finite_number

if TYPE_CHECKING:
    from .oauth import AsyncTokenEndpoint, Endpoint, Exchanged, Session, TokenEndpoint
    from .transports import AsyncTransportAdapter, OwnedTransportAdapter, TransportAdapter

    EndpointT = TypeVar("EndpointT", bound=TokenEndpoint | AsyncTokenEndpoint)
else:
    EndpointT = TypeVar("EndpointT")

__all__ = (
    "AsyncAuthorizationCodeFlow",
    "AuthorizationCodeFlow",
    "AuthorizationRequest",
    "OAuthProviderOptions",
    "TokenSet",
)

_PHASE_DEFAULTS: Final = {"connect": 5.0, "read": 15.0, "write": 15.0, "pool": 5.0}
_VERIFIER_BYTES: Final = 32
RequestState = Literal[
    "CREATED", "EXCHANGING", "SUCCEEDED", "EXCHANGE_REJECTED", "REAUTH_REQUIRED", "UNCERTAIN", "FAILED_NOT_SENT"
]


def _positive_seconds(value: object, path: tuple[str, ...]) -> float:
    if (number := finite_number(value)) is None or number <= 0:
        raise AuthConfigurationError(field_path=path, condition="invalid_value")
    return number


def _positive_count(value: object, path: tuple[str, ...]) -> None:
    if type(value) is not int or value <= 0:
        raise AuthConfigurationError(field_path=path, condition="invalid_value")


def _refresh_token(value: object) -> None:
    if value is not None and (not isinstance(value, str) or not value):
        raise AuthConfigurationError(field_path=("refresh_token",), condition="invalid_value")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class OAuthProviderOptions:
    """Fixed session limits and token transport settings of an OAuth provider or flow.

    Every value is finite and explicit: omitted phase timeouts take the OAuth defaults, and None is never accepted.
    """

    refresh_timeout: float = 30.0
    phase_timeout: TimeoutOptions = field(default_factory=lambda: TimeoutOptions(**_PHASE_DEFAULTS))
    max_concurrent_refreshes: int = 1
    max_pending_refreshes: int = 32
    max_waiters: int = 1024
    allow_insecure_loopback: bool = False
    transport: TransportOptions = field(default_factory=TransportOptions)

    def __post_init__(self) -> None:
        """Validate every limit and resolve omitted phase timeouts before any provider uses them."""
        object.__setattr__(self, "refresh_timeout", _positive_seconds(self.refresh_timeout, ("refresh_timeout",)))
        checked_type(self.phase_timeout, (TimeoutOptions,), ("phase_timeout",))
        phases = {
            name: _positive_seconds(
                default if isinstance(value := getattr(self.phase_timeout, name), Unset) else value,
                ("phase_timeout", name),
            )
            for name, default in _PHASE_DEFAULTS.items()
        }
        object.__setattr__(self, "phase_timeout", TimeoutOptions(**phases))
        for name in ("max_concurrent_refreshes", "max_pending_refreshes", "max_waiters"):
            _positive_count(getattr(self, name), (name,))
        checked_type(self.allow_insecure_loopback, (bool,), ("allow_insecure_loopback",))
        checked_type(self.transport, (TransportOptions,), ("transport",))


@final
@dataclass(frozen=True, slots=True)
class TokenSet:
    """An access token with the refresh token and revision of its token family; its repr omits both tokens."""

    access_token: AccessToken = field(repr=False)
    refresh_token: str | None = field(repr=False)
    revision: int = 0

    def __post_init__(self) -> None:
        """Refuse other token types, empty refresh tokens, and revisions that are not nonnegative integers."""
        checked_type(self.access_token, (AccessToken,), ("access_token",))
        _refresh_token(self.refresh_token)
        if type(self.revision) is not int or self.revision < 0:
            raise AuthConfigurationError(field_path=("revision",), condition="invalid_value")


def _random() -> str:
    import secrets  # noqa: PLC0415

    return base64.urlsafe_b64encode(secrets.token_bytes(_VERIFIER_BYTES)).rstrip(b"=").decode("ascii")


def _challenge(verifier: str) -> str:
    import hashlib  # noqa: PLC0415

    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


@final
class AuthorizationRequest:
    """One authorization attempt: the URL to open, its state, and its PKCE verifier, usable for one exchange.

    Its repr shows none of the three. The flow that created it keeps its redirect URI, scopes, and progress, and
    refuses a request it did not create.
    """

    __slots__ = ("__weakref__", "_code_verifier", "_state", "_url")

    def __init__(self, *, url: str, state: str, code_verifier: str) -> None:
        """Keep the values of one authorization request; only the flow that created it can exchange it."""
        self._url = url
        self._state = state
        self._code_verifier = code_verifier

    @property
    def url(self) -> str:
        """Return the authorization URL for the application to open; the SDK never opens it."""
        return self._url

    @property
    def state(self) -> str:
        """Return the state value the authorization response must return."""
        return self._state

    @property
    def code_verifier(self) -> str:
        """Return the PKCE verifier whose S256 challenge the URL carries."""
        return self._code_verifier

    def __repr__(self) -> str:
        """Omit the URL, state, and verifier."""
        return "AuthorizationRequest()"


@dataclass(slots=True)
class _Issued:
    """What a flow keeps about a request it issued: the values its exchange repeats, and its progress."""

    redirect_uri: str
    scopes: tuple[str, ...]
    status: RequestState = "CREATED"


class _Flow(Generic[EndpointT]):
    """What every OAuth flow shares: its token endpoint, its options, and the lock guarding its state.

    Closing the flow closes its endpoint, which then refuses every later exchange.
    """

    __slots__ = ("_endpoint", "_lock", "_options")

    def __init__(self, endpoint: EndpointT, options: OAuthProviderOptions) -> None:
        self._endpoint: EndpointT = endpoint
        self._options = options
        self._lock = threading.Lock()

    def _open(self) -> None:
        if self._endpoint.closed:
            raise AuthProviderClosedError(state="CLOSED", delivery_state=DeliveryState.NOT_SENT)

    def _session(self) -> Session:
        from .oauth import Session  # noqa: PLC0415

        return Session.start(self._options.refresh_timeout, self._options.phase_timeout)


def _endpoint(value: object, field: str, options: OAuthProviderOptions) -> Endpoint:
    from .oauth import endpoint_url  # noqa: PLC0415

    return endpoint_url(value, field, allow_insecure_loopback=options.allow_insecure_loopback)


class _CodeFlow(_Flow[EndpointT]):
    """What both authorization code flows share: validation, request creation, and outcome classification."""

    __slots__ = ("_authorization_url", "_requests")

    def __init__(self, authorization_url: object, endpoint: EndpointT, options: OAuthProviderOptions) -> None:
        from .oauth import authorization_url as checked_url  # noqa: PLC0415

        super().__init__(endpoint, options)
        self._authorization_url = checked_url(
            authorization_url, allow_insecure_loopback=options.allow_insecure_loopback
        )
        self._requests: weakref.WeakKeyDictionary[AuthorizationRequest, _Issued] = weakref.WeakKeyDictionary()

    def authorization_request(self, redirect_uri: str, scopes: tuple[str, ...]) -> AuthorizationRequest:
        """Validate the scopes first, then create fresh state and PKCE values and the authorization URL.

        The canonical scopes sent in the URL become the request's snapshot, inherited by a token response that
        omits its scope member.
        """
        from .oauth import redirect_uri as checked_redirect  # noqa: PLC0415
        from .oauth import request_url  # noqa: PLC0415

        self._open()
        requested = checked_scopes(scopes, "scopes")
        redirect = checked_redirect(redirect_uri)
        state, verifier = _random(), _random()
        query = [
            ("response_type", "code"),
            ("client_id", self._endpoint.authentication.client_id),
            ("redirect_uri", redirect),
            *((("scope", " ".join(requested)),) if requested else ()),
            ("state", state),
            ("code_challenge", _challenge(verifier)),
            ("code_challenge_method", "S256"),
        ]
        request = AuthorizationRequest(
            url=request_url(self._authorization_url, query), state=state, code_verifier=verifier
        )
        with self._lock:
            self._requests[request] = _Issued(redirect, requested)
        return request

    def _issued(self, request: object) -> _Issued:
        """Return the record of a request this flow created."""
        with self._lock:
            issued = self._requests.get(request) if isinstance(request, AuthorizationRequest) else None
        if issued is None:
            raise AuthConfigurationError(field_path=("request",), condition="foreign_request")
        return issued

    def _begin(self, issued: _Issued, request: AuthorizationRequest, code: object, returned_state: object) -> None:
        """Move from CREATED to EXCHANGING once, after refusing input defects without consuming the request."""
        import hmac  # noqa: PLC0415

        from .oauth import authorization_code  # noqa: PLC0415

        with self._lock:
            if issued.status != "CREATED":
                raise AuthStateConflictError(
                    action="exchange_code", state=issued.status, delivery_state=DeliveryState.NOT_SENT
                )
            authorization_code(code)
            if (
                not isinstance(returned_state, str)
                or not returned_state.isascii()
                or not hmac.compare_digest(returned_state, request.state)
            ):
                raise AuthConfigurationError(field_path=("returned_state",), condition="state_mismatch")
            issued.status = "EXCHANGING"

    def _finish(self, issued: _Issued, status: RequestState) -> None:
        with self._lock:
            issued.status = status

    def _interrupted(self, issued: _Issued, *, sent: bool) -> None:
        """End a request that an exception left mid-exchange, keeping any state already committed."""
        with self._lock:
            if issued.status == "EXCHANGING":
                issued.status = "UNCERTAIN" if sent else "FAILED_NOT_SENT"

    @staticmethod
    def _form(code: str, request: AuthorizationRequest, issued: _Issued) -> tuple[tuple[str, str], ...]:
        return (
            ("grant_type", "authorization_code"),
            ("code", code),
            ("redirect_uri", issued.redirect_uri),
            ("code_verifier", request.code_verifier),
        )

    def _completed(self, exchanged: Exchanged, issued: _Issued) -> TokenSet:
        """Commit the request's terminal state from the answer, then return its token set or raise its failure."""
        from .oauth import failure_kind, token_material  # noqa: PLC0415

        if exchanged.outcome != "success":
            raise self._failure(exchanged, issued)
        assert exchanged.fields is not None
        assert exchanged.received is not None
        try:
            access, refresh, _ = token_material(exchanged.fields, exchanged.received, issued.scopes)
        except (ValueError, TokenExpiredError) as cause:
            self._finish(issued, "UNCERTAIN")
            raise AuthStateUncertainError(
                failure_kind=failure_kind(cause),
                status_code=exchanged.status_code,
                state="UNCERTAIN",
                delivery_state=exchanged.delivery,
                phase="validate",
                cause=cause,
            ) from None
        self._finish(issued, "SUCCEEDED")
        return TokenSet(access, refresh)

    def _failure(self, exchanged: Exchanged, issued: _Issued) -> Exception:
        """Commit the terminal state of an exchange that did not succeed and return the error it raises."""
        delivery = exchanged.delivery
        if exchanged.outcome == "rejected" and exchanged.oauth_error == "invalid_grant":
            self._finish(issued, "REAUTH_REQUIRED")
            return AuthReauthorizationRequiredError(
                condition="invalid_grant", state="REAUTH_REQUIRED", delivery_state=delivery
            )
        if exchanged.outcome == "rejected":
            self._finish(issued, "EXCHANGE_REJECTED")
            return OAuthExchangeError(
                status_code=exchanged.status_code,
                oauth_error=exchanged.oauth_error,
                state="EXCHANGE_REJECTED",
                delivery_state=delivery,
            )
        if exchanged.outcome == "unsent":
            self._finish(issued, "FAILED_NOT_SENT")
            if exchanged.timeout_kind is None:
                return OAuthExchangeError(
                    state="FAILED_NOT_SENT", delivery_state=delivery, phase=exchanged.phase, cause=exchanged.cause
                )
            assert exchanged.timeout is not None
            return AuthTimeoutError(
                effective_timeout=exchanged.timeout,
                timeout_kind=exchanged.timeout_kind,
                state="FAILED_NOT_SENT",
                delivery_state=delivery,
                phase=exchanged.phase,
                cause=exchanged.cause,
            )
        self._finish(issued, "UNCERTAIN")
        return AuthStateUncertainError(
            failure_kind=_uncertain_kind(exchanged),
            status_code=exchanged.status_code,
            state="UNCERTAIN",
            delivery_state=delivery,
            phase="validate" if exchanged.outcome == "malformed_response" else exchanged.phase,
            cause=exchanged.cause,
        )


def _uncertain_kind(exchanged: Exchanged) -> Literal["transport", "deadline", "http_status", "malformed_response"]:
    """Name what left an exchange's outcome unknown: the session deadline, the answer, or the transport."""
    if exchanged.timeout_kind == "provider":
        return "deadline"
    if exchanged.outcome == "http_status":
        return "http_status"
    if exchanged.outcome == "malformed_response":
        return "malformed_response"
    return "transport"


class AuthorizationCodeFlow(_CodeFlow["TokenEndpoint"]):
    """The OAuth authorization code grant with PKCE S256, for applications that obtain the code themselves.

    It creates authorization requests, never opens a browser or starts a callback server, and exchanges each
    request's code at most once. Close it to release the token transport it owns.
    """

    __slots__ = ()

    def __init__(  # noqa: PLR0913
        self,
        authorization_url: str,
        token_url: str,
        *,
        client_id: str,
        client_secret: CredentialProvider | None = None,
        client_auth_method: Literal["none", "client_secret_basic", "client_secret_post"] = "client_secret_basic",
        options: OAuthProviderOptions | None = None,
        token_transport: TransportAdapter | OwnedTransportAdapter[TransportAdapter] | Unset = UNSET,
    ) -> None:
        """Validate the endpoints, client authentication, and transport without I/O or an HTTP client."""
        from .auth_policy import sync_provider  # noqa: PLC0415
        from .oauth import TokenEndpoint, client_authentication  # noqa: PLC0415

        resolved = _options(options)
        authentication = client_authentication(client_id, client_auth_method, client_secret, sync_provider)
        endpoint = _endpoint(token_url, "token_url", resolved)
        super().__init__(
            authorization_url, TokenEndpoint(endpoint, authentication, resolved.transport, token_transport), resolved
        )

    def exchange_code(self, code: str, returned_state: str, request: AuthorizationRequest) -> TokenSet:
        """Exchange the authorization code of the request once, returning its token set with revision 0.

        A request is consumed by its first exchange whatever the outcome; a state mismatch or an invalid code is
        refused before the exchange starts. Errors keep no response body or error description.
        """
        from .oauth import Progress  # noqa: PLC0415

        endpoint = self._endpoint
        endpoint.prepare()
        issued = self._issued(request)
        self._begin(issued, request, code, returned_state)
        progress = Progress()
        try:
            exchanged = endpoint.exchange(
                self._form(code, request, issued), self._session(), progress, "FAILED_NOT_SENT"
            )
            return self._completed(exchanged, issued)
        except BaseException:
            self._interrupted(issued, sent=progress.sent)
            raise

    def close(self) -> None:
        """Close the token transport this flow owns; later requests and exchanges raise AuthProviderClosedError."""
        self._endpoint.close()

    def __enter__(self) -> Self:
        """Return the flow."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the flow."""
        self.close()


class AsyncAuthorizationCodeFlow(_CodeFlow["AsyncTokenEndpoint"]):
    """The asyncio authorization code grant with PKCE S256, bound to the first event loop that exchanges a code."""

    __slots__ = ()

    def __init__(  # noqa: PLR0913
        self,
        authorization_url: str,
        token_url: str,
        *,
        client_id: str,
        client_secret: AsyncCredentialProvider | None = None,
        client_auth_method: Literal["none", "client_secret_basic", "client_secret_post"] = "client_secret_basic",
        options: OAuthProviderOptions | None = None,
        token_transport: AsyncTransportAdapter | OwnedTransportAdapter[AsyncTransportAdapter] | Unset = UNSET,
    ) -> None:
        """Validate the endpoints, client authentication, and transport without I/O or an HTTP client."""
        from .auth_policy import async_provider  # noqa: PLC0415
        from .oauth import AsyncTokenEndpoint, client_authentication  # noqa: PLC0415

        resolved = _options(options)
        authentication = client_authentication(client_id, client_auth_method, client_secret, async_provider)
        endpoint = _endpoint(token_url, "token_url", resolved)
        super().__init__(
            authorization_url,
            AsyncTokenEndpoint(endpoint, authentication, resolved.transport, token_transport),
            resolved,
        )

    async def exchange_code(self, code: str, returned_state: str, request: AuthorizationRequest) -> TokenSet:
        """Exchange the authorization code of the request once, as the synchronous flow does."""
        from .oauth import Progress  # noqa: PLC0415

        endpoint = self._endpoint
        endpoint.prepare()
        issued = self._issued(request)
        self._begin(issued, request, code, returned_state)
        progress = Progress()
        try:
            exchanged = await endpoint.exchange(
                self._form(code, request, issued), self._session(), progress, "FAILED_NOT_SENT"
            )
            return self._completed(exchanged, issued)
        except BaseException:
            self._interrupted(issued, sent=progress.sent)
            raise

    async def aclose(self) -> None:
        """Close the token transport this flow owns; later requests and exchanges raise AuthProviderClosedError."""
        await self._endpoint.aclose()

    async def __aenter__(self) -> Self:
        """Return the flow."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Close the flow."""
        await self.aclose()


def _options(options: object) -> OAuthProviderOptions:
    if options is None:
        return OAuthProviderOptions()
    if not isinstance(options, OAuthProviderOptions):
        raise AuthConfigurationError(field_path=("options",), condition="invalid_type")
    return options
