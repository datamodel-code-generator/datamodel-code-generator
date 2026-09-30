"""OAuth grants: token sets, provider options, and the explicit authorization code and device authorization flows.

Constructors and authorization requests perform no I/O. Token HTTP uses a provider-owned transport that verifies TLS,
never follows redirects, never retries, and never reuses resource credentials, signers, or resource settings.
"""

from __future__ import annotations

import base64
import threading
import weakref
from contextlib import suppress
from dataclasses import dataclass, field
from time import monotonic
from typing import TYPE_CHECKING, Final, Generic, Literal, TypeAlias, TypeVar, final

from typing_extensions import Self, TypeAliasType

from ..model_codecs.unset import UNSET, Unset
from .admission import AsyncTokenAcquirer, TokenAcquirer
from .auth import (
    AsyncCredentialProvider,
    AsyncTokenLoad,
    BearerCredential,
    CredentialContext,
    CredentialProvider,
    RefreshInfo,
    TokenLoad,
    TokenSet,
    TokenVersion,
    checked_scopes,
)
from .errors import (
    AuthConfigurationError,
    AuthProviderClosedError,
    AuthStateConflictError,
    AuthTimeoutError,
    BudgetExceededError,
    DeadlineExceededError,
    DeliveryState,
    OAuthExchangeError,
    RequestCancelledError,
    TokenExpiredError,
)
from .options import OAuthProviderOptions, SessionOptions
from .timing import TOKEN_INTERVAL, CancelToken, Deadline, absolute_deadline

if TYPE_CHECKING:
    from concurrent.futures import Future

    from .admission import CallAdmission
    from .oauth import AsyncTokenEndpoint, Endpoint, Exchanged, Progress, Session, TokenEndpoint
    from .refresh import AsyncTokens, SyncTokens
    from .transports import AsyncTransportAdapter, OwnedTransportAdapter, TransportAdapter

    EndpointT = TypeVar("EndpointT", bound=TokenEndpoint | AsyncTokenEndpoint)
    TokenTransport: TypeAlias = TransportAdapter | OwnedTransportAdapter[TransportAdapter]
    AsyncTokenTransport: TypeAlias = AsyncTransportAdapter | OwnedTransportAdapter[AsyncTransportAdapter]
else:
    EndpointT = TypeVar("EndpointT")
    TokenTransport = TypeAliasType("TokenTransport", "TransportAdapter | OwnedTransportAdapter[TransportAdapter]")
    AsyncTokenTransport = TypeAliasType(
        "AsyncTokenTransport", "AsyncTransportAdapter | OwnedTransportAdapter[AsyncTransportAdapter]"
    )

__all__ = (
    "AsyncAuthorizationCodeFlow",
    "AsyncClientCredentialsProvider",
    "AsyncDeviceAuthorizationFlow",
    "AsyncRefreshTokenProvider",
    "AuthorizationCodeFlow",
    "AuthorizationRequest",
    "ClientCredentialsProvider",
    "DeviceAuthorization",
    "DeviceAuthorizationFlow",
    "OAuthProviderOptions",
    "RefreshTokenProvider",
    "TokenSet",
)

_VERIFIER_BYTES: Final = 32
RequestState = Literal[
    "CREATED", "EXCHANGING", "SUCCEEDED", "EXCHANGE_REJECTED", "REAUTH_REQUIRED", "UNCERTAIN", "FAILED_NOT_SENT"
]
DeviceState = Literal[
    "UNINITIALIZED",
    "EXCHANGING",
    "READY",
    "SUCCEEDED",
    "EXCHANGE_REJECTED",
    "REAUTH_REQUIRED",
    "UNCERTAIN",
    "FAILED_NOT_SENT",
]
_OK: Final = 200
_DEVICE_SENDS: Final = 128
_SLOW_DOWN: Final = 5.0
_WAITING: Final = frozenset({"authorization_pending", "slow_down"})
_ENDED: Final = frozenset({"access_denied", "expired_token"})


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

    def _session(self, limit: Deadline | None = None) -> Session:
        from .oauth import Session  # noqa: PLC0415

        return Session.start(self._options.refresh_timeout, self._options.phase_timeout, limit)


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
        from .oauth import consumable_failure, token_material, unusable_success  # noqa: PLC0415

        if exchanged.outcome != "success":
            state, error = consumable_failure(exchanged)
            self._finish(issued, state)
            raise error
        assert exchanged.fields is not None
        assert exchanged.received is not None
        try:
            access, refresh, _ = token_material(exchanged.fields, exchanged.received, issued.scopes)
        except (ValueError, TokenExpiredError) as cause:
            self._finish(issued, "UNCERTAIN")
            raise unusable_success(exchanged, cause) from None
        self._finish(issued, "SUCCEEDED")
        return TokenSet(access, refresh)


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
        token_transport: TokenTransport | Unset = UNSET,
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
        token_transport: AsyncTokenTransport | Unset = UNSET,
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


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceAuthorization:
    """What the application shows the user to approve this device; its repr shows only the timing.

    `expires_in` is the lifetime the server declared and `interval` the initial wait between polls. `deadline` ends
    the transaction: the declared lifetime from the response's receipt, or an earlier session limit.
    """

    user_code: str = field(repr=False)
    verification_uri: str = field(repr=False)
    verification_uri_complete: str | None = field(repr=False)
    expires_in: float
    interval: float
    deadline: Deadline = field(repr=False)


@dataclass(slots=True)
class _Transaction:
    """The one device transaction a flow owns: its progress, and what each poll repeats, waits for, and spends."""

    status: DeviceState = "UNINITIALIZED"
    started: float = 0.0
    send_limit: int | None = _DEVICE_SENDS
    sends: int = 0
    scopes: tuple[str, ...] = ()
    device_code: str = field(default="", repr=False)
    deadline: Deadline | None = None
    interval: float = 0.0
    next_send: float = 0.0


@dataclass(frozen=True, slots=True)
class _Begin:
    """A begin that passed its preflight: the requested scopes and the session's start and limits."""

    scopes: tuple[str, ...]
    started: float
    limit: Deadline | None
    send_limit: int | None


def _session_limits(options: object, started: float) -> tuple[Deadline | None, int | None]:
    """Resolve a session's explicit deadline, the earlier of its two limits, and its network send limit."""
    if options is None:
        return None, _DEVICE_SENDS
    if not isinstance(options, SessionOptions):
        raise AuthConfigurationError(field_path=("session_options",), condition="invalid_type")
    limits = [
        *((started + options.total_timeout,) if isinstance(options.total_timeout, float) else ()),
        *((options.deadline.at,) if isinstance(options.deadline, Deadline) else ()),
    ]
    sends = _DEVICE_SENDS if isinstance(options.max_network_sends, Unset) else options.max_network_sends
    return (absolute_deadline(min(limits)) if limits else None), sends


def _cancel_token(value: object) -> CancelToken | None:
    if value is not None and not isinstance(value, CancelToken):
        raise AuthConfigurationError(field_path=("cancel_token",), condition="invalid_type")
    return value


class _DeviceFlow(_Flow[EndpointT]):
    """What both device authorization flows share: the transaction's state machine and its classification."""

    __slots__ = ("_device", "_transaction")

    def __init__(self, device_url: object, endpoint: EndpointT, options: OAuthProviderOptions) -> None:
        super().__init__(endpoint, options)
        self._device = _endpoint(device_url, "device_authorization_url", options)
        self._transaction = _Transaction()

    def _preflight(self, scopes: object, session_options: object) -> _Begin:
        """Refuse a second begin first, then validate the input and limits before the transaction starts.

        A begin refused by its input or limits leaves the flow UNINITIALIZED, so it can be retried.
        """
        self._open()
        with self._lock:
            if (status := self._transaction.status) != "UNINITIALIZED":
                raise AuthStateConflictError(action="begin", state=status, delivery_state=DeliveryState.NOT_SENT)
        requested = checked_scopes(scopes, "scopes")
        started = monotonic()
        limit, send_limit = _session_limits(session_options, started)
        if send_limit == 0:
            raise BudgetExceededError(budget_kind="network", limit=0, used=0)
        if limit is not None and limit.at <= started:
            raise DeadlineExceededError(
                deadline_at=limit.at, elapsed=0.0, delivery_state=DeliveryState.NOT_SENT, phase="auth"
            )
        return _Begin(requested, started, limit, send_limit)

    def _claim(self, action: Literal["begin", "poll"], expected: DeviceState) -> _Transaction:
        """Move to EXCHANGING once from the expected state; the caller holds the lock."""
        transaction = self._transaction
        if transaction.status != expected:
            raise AuthStateConflictError(action=action, state=transaction.status, delivery_state=DeliveryState.NOT_SENT)
        transaction.status = "EXCHANGING"
        return transaction

    def _start(self, begin: _Begin) -> _Transaction:
        """Start the flow's only transaction with the begin's scope snapshot and session limits."""
        with self._lock:
            transaction = self._claim("begin", "UNINITIALIZED")
            transaction.started, transaction.send_limit, transaction.scopes = (
                begin.started,
                begin.send_limit,
                begin.scopes,
            )
            return transaction

    def _resume(self) -> _Transaction:
        """Start polling a READY transaction, refusing a concurrent or later poll."""
        with self._lock:
            return self._claim("poll", "READY")

    def _finish(self, transaction: _Transaction, status: DeviceState) -> None:
        with self._lock:
            transaction.status = status

    def _interrupted(self, transaction: _Transaction, *, sent: bool) -> None:
        """End a transaction that an exception left mid-exchange, keeping any state already committed."""
        with self._lock:
            if transaction.status == "EXCHANGING":
                transaction.status = "UNCERTAIN" if sent else "FAILED_NOT_SENT"

    def _counted(self, transaction: _Transaction, progress: Progress) -> None:
        """Count an exchange against the session's sends once it reached the transport."""
        if progress.sent:
            with self._lock:
                transaction.sends += 1

    def _rejected(
        self, transaction: _Transaction, exchanged: Exchanged, cause: BaseException | None = None
    ) -> OAuthExchangeError:
        """End the transaction on a response it cannot continue from, keeping only safe response facts."""
        self._finish(transaction, "EXCHANGE_REJECTED")
        return OAuthExchangeError(
            status_code=exchanged.status_code,
            oauth_error=exchanged.oauth_error,
            state="EXCHANGE_REJECTED",
            delivery_state=exchanged.delivery,
            phase="unknown" if exchanged.outcome in {"rejected", "http_status"} else "validate",
            cause=exchanged.cause if cause is None else cause,
        )

    def _failed(self, transaction: _Transaction, exchanged: Exchanged, session: Session) -> Exception:
        """End the transaction on a transport failure: no send is repeated after one may have been lost."""
        state: DeviceState = "FAILED_NOT_SENT" if exchanged.outcome == "unsent" else "UNCERTAIN"
        self._finish(transaction, state)
        delivery = exchanged.delivery
        if exchanged.timeout_kind == "provider" and session.limited:
            return DeadlineExceededError(
                deadline_at=session.deadline.at,
                elapsed=monotonic() - transaction.started,
                delivery_state=delivery,
                phase="send",
                cause=exchanged.cause,
            )
        if exchanged.timeout_kind is not None:
            assert exchanged.timeout is not None
            return AuthTimeoutError(
                effective_timeout=exchanged.timeout,
                timeout_kind=exchanged.timeout_kind,
                state=state,
                delivery_state=delivery,
                phase=exchanged.phase,
                cause=exchanged.cause,
            )
        return OAuthExchangeError(state=state, delivery_state=delivery, phase=exchanged.phase, cause=exchanged.cause)

    def _began(
        self, transaction: _Transaction, exchanged: Exchanged, session: Session, limit: Deadline | None
    ) -> DeviceAuthorization:
        """Publish READY only for a complete, valid device authorization response."""
        from .oauth import device_grant  # noqa: PLC0415

        if exchanged.outcome not in {"success", "rejected", "http_status", "malformed_response"}:
            raise self._failed(transaction, exchanged, session)
        if exchanged.outcome != "success" or exchanged.status_code != _OK:
            raise self._rejected(transaction, exchanged)
        assert exchanged.fields is not None
        assert exchanged.receipt is not None
        try:
            grant = device_grant(exchanged.fields)
        except ValueError as cause:
            raise self._rejected(transaction, exchanged, cause) from None
        expiry = exchanged.receipt + grant.expires_in
        deadline = limit if limit is not None and limit.at < expiry else absolute_deadline(expiry)
        with self._lock:
            transaction.device_code, transaction.deadline = grant.device_code, deadline
            transaction.interval, transaction.next_send = grant.interval, exchanged.receipt + grant.interval
            transaction.status = "READY"
        return DeviceAuthorization(
            user_code=grant.user_code,
            verification_uri=grant.verification_uri,
            verification_uri_complete=grant.verification_uri_complete,
            expires_in=grant.expires_in,
            interval=grant.interval,
            deadline=deadline,
        )

    def _polled(self, transaction: _Transaction, exchanged: Exchanged, session: Session) -> TokenSet | None:
        """Return the token set, or None to wait again for a pending authorization; any other answer ends it."""
        from .oauth import token_material  # noqa: PLC0415

        if exchanged.outcome == "rejected" and exchanged.error in _WAITING:
            assert exchanged.receipt is not None
            with self._lock:
                transaction.interval += _SLOW_DOWN if exchanged.error == "slow_down" else 0.0
                transaction.next_send = exchanged.receipt + transaction.interval
            return None
        if exchanged.outcome != "success":
            raise self._ended(transaction, exchanged, session)
        assert exchanged.fields is not None
        assert exchanged.received is not None
        try:
            access, refresh, _ = token_material(exchanged.fields, exchanged.received, transaction.scopes)
        except (ValueError, TokenExpiredError) as cause:
            raise self._rejected(transaction, exchanged, cause) from None
        self._finish(transaction, "SUCCEEDED")
        return TokenSet(access, refresh)

    def _ended(self, transaction: _Transaction, exchanged: Exchanged, session: Session) -> Exception:
        """Commit the end of a transaction whose poll did not succeed and return the error it raises."""
        from .oauth import DeviceAuthorizationEndedError  # noqa: PLC0415

        if exchanged.outcome == "rejected" and exchanged.error in _ENDED:
            assert exchanged.error is not None
            self._finish(transaction, "REAUTH_REQUIRED")
            return OAuthExchangeError(
                status_code=exchanged.status_code,
                state="REAUTH_REQUIRED",
                delivery_state=exchanged.delivery,
                cause=DeviceAuthorizationEndedError(exchanged.error),
            )
        if exchanged.outcome in {"rejected", "http_status", "malformed_response"}:
            return self._rejected(transaction, exchanged)
        return self._failed(transaction, exchanged, session)

    def _pause(self, transaction: _Transaction, cancel_token: CancelToken | None) -> float:
        """Return how long to wait before the next poll, refusing a poll the limits no longer allow."""
        self._open()
        if cancel_token is not None and cancel_token.cancelled:
            raise RequestCancelledError(source="cancel_token", delivery_state=DeliveryState.NOT_SENT)
        deadline = transaction.deadline
        assert deadline is not None
        now = monotonic()
        if transaction.next_send >= deadline.at or now >= deadline.at:
            raise DeadlineExceededError(
                deadline_at=deadline.at,
                elapsed=now - transaction.started,
                delivery_state=DeliveryState.NOT_SENT,
                phase="sleep",
            )
        if (send_limit := transaction.send_limit) is not None and transaction.sends >= send_limit:
            raise BudgetExceededError(budget_kind="network", limit=send_limit, used=transaction.sends)
        remaining = max(0.0, transaction.next_send - now)
        return remaining if cancel_token is None or remaining == 0 else min(remaining, TOKEN_INTERVAL)

    @staticmethod
    def _begin_form(scopes: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
        return (("scope", " ".join(scopes)),) if scopes else ()

    @staticmethod
    def _poll_form(transaction: _Transaction) -> tuple[tuple[str, str], ...]:
        from .oauth import DEVICE_GRANT_TYPE  # noqa: PLC0415

        return (("grant_type", DEVICE_GRANT_TYPE), ("device_code", transaction.device_code))


class DeviceAuthorizationFlow(_DeviceFlow["TokenEndpoint"]):
    """The OAuth device authorization grant: one instance owns exactly one begin and its polls.

    It never opens the verification URI; the application shows the user code and URI, then polls until the user
    approves. Close it to release the token transport it owns and to end a poll that is waiting.
    """

    __slots__ = ("_wake",)

    def __init__(  # noqa: PLR0913
        self,
        device_authorization_url: str,
        token_url: str,
        *,
        client_id: str,
        client_secret: CredentialProvider | None = None,
        client_auth_method: Literal["none", "client_secret_basic", "client_secret_post"] = "client_secret_basic",
        options: OAuthProviderOptions | None = None,
        token_transport: TokenTransport | Unset = UNSET,
    ) -> None:
        """Validate the endpoints, client authentication, and transport without I/O or an HTTP client."""
        from .auth_policy import sync_provider  # noqa: PLC0415
        from .oauth import TokenEndpoint, client_authentication  # noqa: PLC0415

        resolved = _options(options)
        authentication = client_authentication(client_id, client_auth_method, client_secret, sync_provider)
        endpoint = _endpoint(token_url, "token_url", resolved)
        super().__init__(
            device_authorization_url,
            TokenEndpoint(endpoint, authentication, resolved.transport, token_transport),
            resolved,
        )
        self._wake = threading.Event()

    def begin(self, scopes: tuple[str, ...], *, session_options: SessionOptions | None = None) -> DeviceAuthorization:
        """Request a device authorization for the scopes, and return what the application shows the user.

        The session starts now: `session_options` can end it earlier than the response's lifetime and limits the
        network sends of the begin and every poll, 128 by default.
        """
        from .oauth import Progress  # noqa: PLC0415

        begin = self._preflight(scopes, session_options)
        endpoint = self._endpoint
        endpoint.prepare()
        session, progress = self._session(begin.limit), Progress()
        transaction = self._start(begin)
        try:
            exchanged = endpoint.exchange(
                self._begin_form(begin.scopes), session, progress, "FAILED_NOT_SENT", self._device
            )
            self._counted(transaction, progress)
            return self._began(transaction, exchanged, session, begin.limit)
        except BaseException:
            self._interrupted(transaction, sent=progress.sent)
            raise

    def poll(self, *, cancel_token: CancelToken | None = None) -> TokenSet:
        """Poll the token endpoint at the server's interval until the user approves, returning the token set.

        Pending answers wait within this call; slow_down adds five seconds to the interval. Any other outcome, the
        deadline, the send limit, the cancel token, or closing the flow ends the transaction.
        """
        from .oauth import Progress  # noqa: PLC0415

        self._open()
        token = _cancel_token(cancel_token)
        endpoint = self._endpoint
        transaction = self._resume()
        progress = Progress()
        try:
            while True:
                progress = Progress()
                while (pause := self._pause(transaction, token)) > 0:
                    self._wake.wait(min(pause, threading.TIMEOUT_MAX))
                session = self._session(transaction.deadline)
                exchanged = endpoint.exchange(self._poll_form(transaction), session, progress, "FAILED_NOT_SENT")
                self._counted(transaction, progress)
                if (tokens := self._polled(transaction, exchanged, session)) is not None:
                    return tokens
        except BaseException:
            self._interrupted(transaction, sent=progress.sent)
            raise

    def close(self) -> None:
        """End a waiting poll and close the owned token transport; later calls raise AuthProviderClosedError."""
        try:
            self._endpoint.close()
        finally:
            self._wake.set()

    def __enter__(self) -> Self:
        """Return the flow."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Close the flow."""
        self.close()


class AsyncDeviceAuthorizationFlow(_DeviceFlow["AsyncTokenEndpoint"]):
    """The asyncio device authorization grant, bound to the event loop it was created on or first used from."""

    __slots__ = ("_wake",)

    def __init__(  # noqa: PLR0913
        self,
        device_authorization_url: str,
        token_url: str,
        *,
        client_id: str,
        client_secret: AsyncCredentialProvider | None = None,
        client_auth_method: Literal["none", "client_secret_basic", "client_secret_post"] = "client_secret_basic",
        options: OAuthProviderOptions | None = None,
        token_transport: AsyncTokenTransport | Unset = UNSET,
    ) -> None:
        """Validate the endpoints, client authentication, and transport without I/O or an HTTP client."""
        import asyncio  # noqa: PLC0415

        from .auth_policy import async_provider  # noqa: PLC0415
        from .oauth import AsyncTokenEndpoint, client_authentication  # noqa: PLC0415

        resolved = _options(options)
        authentication = client_authentication(client_id, client_auth_method, client_secret, async_provider)
        endpoint = _endpoint(token_url, "token_url", resolved)
        super().__init__(
            device_authorization_url,
            AsyncTokenEndpoint(endpoint, authentication, resolved.transport, token_transport),
            resolved,
        )
        self._wake = asyncio.Event()

    async def begin(
        self, scopes: tuple[str, ...], *, session_options: SessionOptions | None = None
    ) -> DeviceAuthorization:
        """Request a device authorization for the scopes, as the synchronous flow does."""
        from .oauth import Progress  # noqa: PLC0415

        begin = self._preflight(scopes, session_options)
        endpoint = self._endpoint
        endpoint.prepare()
        session, progress = self._session(begin.limit), Progress()
        transaction = self._start(begin)
        try:
            exchanged = await endpoint.exchange(
                self._begin_form(begin.scopes), session, progress, "FAILED_NOT_SENT", self._device
            )
            self._counted(transaction, progress)
            return self._began(transaction, exchanged, session, begin.limit)
        except BaseException:
            self._interrupted(transaction, sent=progress.sent)
            raise

    async def poll(self, *, cancel_token: CancelToken | None = None) -> TokenSet:
        """Poll until the user approves, as the synchronous flow does, on the loop the flow is bound to."""
        from .oauth import Progress, within  # noqa: PLC0415

        self._open()
        token = _cancel_token(cancel_token)
        endpoint = self._endpoint
        endpoint.prepare()
        transaction = self._resume()
        progress = Progress()
        try:
            while True:
                progress = Progress()
                while (pause := self._pause(transaction, token)) > 0:
                    with suppress(TimeoutError):
                        await within(self._wake.wait(), pause)
                session = self._session(transaction.deadline)
                exchanged = await endpoint.exchange(self._poll_form(transaction), session, progress, "FAILED_NOT_SENT")
                self._counted(transaction, progress)
                if (tokens := self._polled(transaction, exchanged, session)) is not None:
                    return tokens
        except BaseException:
            self._interrupted(transaction, sent=progress.sent)
            raise

    async def aclose(self) -> None:
        """End a waiting poll and close the owned token transport; later calls raise AuthProviderClosedError."""
        try:
            await self._endpoint.aclose()
        finally:
            self._wake.set()

    async def __aenter__(self) -> Self:
        """Return the flow."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Close the flow."""
        await self.aclose()


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


def _family(
    token_set: object, load: object, scopes: object, audience: object, *, asynchronous: bool
) -> tuple[TokenSet | None, tuple[str, ...], str | None]:
    """Validate a refresh token family's token set or load, configured scopes, and audience."""
    from .auth_policy import async_load, sync_load  # noqa: PLC0415
    from .refresh import checked_audience  # noqa: PLC0415
    from .rotation import checked_token_set  # noqa: PLC0415

    if token_set is None and load is None:
        raise AuthConfigurationError(field_path=("token_set",), condition="missing_value")
    if load is not None and not (async_load(load) if asynchronous else sync_load(load)):
        raise AuthConfigurationError(field_path=("load",), condition="invalid_mode")
    initial = None if token_set is None else checked_token_set(token_set)
    return initial, checked_scopes(scopes, "scopes"), checked_audience(audience)


@final
class RefreshTokenProvider(_SharedTokens):
    """The OAuth refresh token grant for one token family, which this provider alone renews, following its rotation.

    Concurrent callers join one refresh, which runs on a worker of the provider under its own deadline, so a caller
    leaving never cancels it. The access token is renewed once a tenth of its lifetime, at most thirty seconds, remains.
    A refresh token a request may have delivered is never sent again: a refresh whose outcome is unknown, rejected, or
    requiring reauthorization stops the family until `replace_token_set`. Close it to release its transport.
    """

    __slots__ = ("_rotation",)

    def __init__(  # noqa: PLR0913
        self,
        token_url: str,
        *,
        client_id: str,
        token_set: TokenSet | None = None,
        load: TokenLoad | None = None,
        client_secret: CredentialProvider | None = None,
        client_auth_method: Literal["none", "client_secret_basic", "client_secret_post"] = "client_secret_basic",
        scopes: tuple[str, ...] = (),
        audience: str | None = None,
        options: OAuthProviderOptions | None = None,
        token_transport: TokenTransport | Unset = UNSET,
    ) -> None:
        """Validate the endpoint, client authentication, token set or load, scopes, audience, and transport."""
        from .auth_policy import sync_provider  # noqa: PLC0415
        from .oauth import TokenEndpoint, client_authentication  # noqa: PLC0415
        from .rotation import SyncRotation, cache_key  # noqa: PLC0415

        resolved = _options(options)
        initial, requested, checked = _family(token_set, load, scopes, audience, asynchronous=False)
        authentication = client_authentication(client_id, client_auth_method, client_secret, sync_provider)
        url = _endpoint(token_url, "token_url", resolved)
        key = cache_key(url, authentication.client_id, authentication.method, checked, requested)
        endpoint = TokenEndpoint(url, authentication, resolved.transport, token_transport)
        self._rotation = SyncRotation(resolved, endpoint, initial, checked, key=key, load=load)
        super().__init__(self._rotation)

    def replace_token_set(self, token_set: TokenSet, *, persist: bool = True) -> None:
        """Adopt a token set of a higher revision, restarting a stopped family.

        Its refresh token must not be one the family spent, and without one its access token must be unexpired.
        Without a token store, `persist` changes nothing.
        """
        del persist
        self._rotation.replace(token_set)

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
        client_secret: AsyncCredentialProvider | None = None,
        client_auth_method: Literal["none", "client_secret_basic", "client_secret_post"] = "client_secret_basic",
        scopes: tuple[str, ...] = (),
        audience: str | None = None,
        options: OAuthProviderOptions | None = None,
        token_transport: AsyncTokenTransport | Unset = UNSET,
    ) -> None:
        """Validate the endpoint, client authentication, token set or load, scopes, audience, and transport."""
        from .auth_policy import async_provider  # noqa: PLC0415
        from .oauth import AsyncTokenEndpoint, client_authentication  # noqa: PLC0415
        from .rotation import AsyncRotation, cache_key  # noqa: PLC0415

        resolved = _options(options)
        initial, requested, checked = _family(token_set, load, scopes, audience, asynchronous=True)
        authentication = client_authentication(client_id, client_auth_method, client_secret, async_provider)
        url = _endpoint(token_url, "token_url", resolved)
        key = cache_key(url, authentication.client_id, authentication.method, checked, requested)
        endpoint = AsyncTokenEndpoint(url, authentication, resolved.transport, token_transport)
        self._rotation = AsyncRotation(resolved, endpoint, initial, checked, key=key, load=load)
        super().__init__(self._rotation)

    async def replace_token_set(self, token_set: TokenSet, *, persist: bool = True) -> None:
        """Adopt a newer token set as the synchronous provider does, on the event loop the provider is bound to."""
        del persist
        self._rotation.replace(token_set)

    async def reload_token_set(self) -> TokenSet | None:
        """Load the persisted token set once as the synchronous provider does, on the provider's event loop."""
        return await self._rotation.reload()


def _options(options: object) -> OAuthProviderOptions:
    if options is None:
        return OAuthProviderOptions()
    if not isinstance(options, OAuthProviderOptions):
        raise AuthConfigurationError(field_path=("options",), condition="invalid_type")
    return options
