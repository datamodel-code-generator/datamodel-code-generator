"""OAuth client credentials and refresh token providers: bearer tokens renewed inline under a lock.

The call that needs a token requests it while holding the provider's lock, and calls arriving meanwhile wait for that
token. Nothing runs in the background and nothing is persisted: a refresh token provider hands each refreshed token set
to its `on_token_refreshed` callback.
"""

from __future__ import annotations

import base64
import inspect
import threading
import weakref
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, ClassVar, Final, Literal, cast
from urllib.parse import quote_plus, urlencode

import httpx2
from typing_extensions import TypeIs

from .auth import SchemeAuth, TokenSource
from .errors import OAUTH_ERROR_CODES, AuthError, ConfigurationError, OAuthErrorCode, SDKError
from .security import SecurityScheme
from .timing import SYSTEM_CLOCK, Clock, checked_instance
from .urls import URLValidationError, canonical_origin

if TYPE_CHECKING:
    from asyncio import AbstractEventLoop, Lock
    from collections.abc import AsyncGenerator, Callable, Generator, Sequence

    from .auth import Secret
    from .security import AsyncSend, Send

__all__ = ("ClientCredentials", "RefreshToken", "TokenSet")

_LOOPBACK: Final = frozenset({"localhost", "127.0.0.1", "::1"})
_BEARER: Final = SecurityScheme(name="oauth", kind="bearer", location="header", wire_name="Authorization")
_SUCCESS: Final = range(200, 300)


def refresh_margin(ttl: float) -> float:
    """Return how long before its expiry a token is renewed: a tenth of its lifetime, at most thirty seconds."""
    return min(30.0, ttl * 0.1)


@dataclass(frozen=True, slots=True)
class TokenSet:
    """An access token, its timezone-aware expiry, and the refresh token that renews it; its repr omits both tokens."""

    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        """Refuse empty tokens and a naive expiry."""
        _text(self.access_token, "access_token")
        _text(self.refresh_token, "refresh_token", optional=True)
        _aware(self.expires_at)


def _text(value: object, name: str, *, optional: bool = False) -> None:
    """Refuse a value that is not a nonempty string, or None when it is optional."""
    if not ((optional and value is None) or (isinstance(value, str) and value)):
        raise ConfigurationError(field_path=(name,), reason="invalid_value")


def _aware(value: object) -> None:
    """Refuse an expiry that is not None or a timezone-aware datetime."""
    if value is not None and (not isinstance(value, datetime) or value.utcoffset() is None):
        raise ConfigurationError(field_path=("expires_at",), reason="invalid_value")


def _settings(client_secret: object, http_client: object, clock: object) -> None:
    """Refuse a client secret, token client, or clock of another type."""
    if client_secret is not None and not (isinstance(client_secret, str) or callable(client_secret)):
        raise ConfigurationError(field_path=("client_secret",), reason="invalid_type")
    checked_instance(http_client, (httpx2.Client, httpx2.AsyncClient, type(None)), ("http_client",))
    checked_instance(clock, (Clock, type(None)), ("clock",))


def _scope_list(value: object) -> tuple[str, ...]:
    """Return requested scopes in order, refusing a string or a scope that is not a nonempty string."""
    if isinstance(value, str) or not isinstance(value, (tuple, list)):
        raise ConfigurationError(field_path=("scopes",), reason="invalid_value")
    items = tuple(cast("tuple[object, ...] | list[object]", value))
    for scope in items:
        _text(scope, "scopes")
    return cast("tuple[str, ...]", items)


@dataclass(frozen=True, slots=True)
class _Held:
    """The token served, and the monotonic times from which it is renewed and at which it expires."""

    value: str
    refresh_at: float | None
    expires_at: float | None


def _url(value: object) -> str:
    """Accept an HTTPS token URL, or plain HTTP to a loopback host."""
    if not isinstance(value, str):
        raise ConfigurationError(field_path=("token_url",), reason="missing_value" if value is None else "invalid_type")
    try:
        scheme, host, _ = canonical_origin(value)
    except (URLValidationError, ValueError):
        raise ConfigurationError(field_path=("token_url",), reason="invalid_url") from None
    if scheme != "https" and host not in _LOOPBACK:
        raise ConfigurationError(field_path=("token_url",), reason="insecure_url")
    return value


def _failed(error: Exception) -> Exception:
    """Return the error of an application callable that failed: its own SDK error, or a provider failure."""
    if isinstance(error, SDKError):
        return error
    return AuthError(reason="provider_failed", cause=error)


def _secret(value: Secret | None) -> str | None:
    """Return the client secret for one token request, calling a callable secret."""
    if not callable(value):
        return value
    try:
        secret = value()
    except Exception as error:  # noqa: BLE001 - The application's callable failed; its cause is kept.
        raise _failed(error) from None
    return _string(secret)


def _string(value: object) -> str:
    """Return a client secret a callable returned, refusing a value of another type."""
    if not isinstance(value, str):
        raise ConfigurationError(field_path=("client_secret",), reason="invalid_type")
    return value


def _expiry(fields: dict[str, object], received: float) -> float | None:
    """Return the monotonic expiry of a token response's positive expires_in, or None without one."""
    if "expires_in" not in fields:
        return None
    seconds = fields["expires_in"]
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 < seconds < float("inf"):
        raise AuthError(reason="invalid_expiry")
    return received + seconds


def _is_oauth_error(value: object) -> TypeIs[OAuthErrorCode]:
    return value in OAUTH_ERROR_CODES


def _answer(response: httpx2.Response, *, refreshing: bool) -> dict[str, object]:
    """Return the members of a successful token response, or raise the error of any other answer."""
    try:
        data: object = response.json()
    except ValueError:
        data = None
    fields = cast("dict[str, object]", data) if isinstance(data, dict) else {}
    status = response.status_code
    token, kind = fields.get("access_token"), fields.get("token_type")
    if status in _SUCCESS and isinstance(token, str) and token and isinstance(kind, str) and kind.lower() == "bearer":
        return fields
    code = fields.get("error")
    reason: Literal["oauth_error", "reauthorization_required"] = (
        "reauthorization_required" if refreshing and code == "invalid_grant" else "oauth_error"
    )
    raise AuthError(
        reason=reason,
        status_code=status,
        oauth_error=code if _is_oauth_error(code) else None,
    )


class _Provider(TokenSource):
    """What both grants share: the token endpoint, client authentication, the held token, and its locks."""

    token_url: ClassVar[str | None] = None
    _refreshing: ClassVar[bool] = False

    def __init__(  # noqa: PLR0913
        self,
        *,
        client_id: str,
        client_secret: Secret | None,
        client_auth_method: str,
        token_url: str | None,
        http_client: httpx2.Client | httpx2.AsyncClient | None,
        clock: Clock | None,
    ) -> None:
        _text(client_id, "client_id")
        if client_auth_method not in {"none", "client_secret_basic", "client_secret_post"}:
            raise ConfigurationError(field_path=("client_auth_method",), reason="invalid_value")
        if (client_auth_method == "none") != (client_secret is None):
            reason = "forbidden_value" if client_secret is not None else "missing_value"
            raise ConfigurationError(field_path=("client_secret",), reason=reason)
        _settings(client_secret, http_client, clock)
        self._url = _url(token_url if token_url is not None else type(self).token_url)
        self._client_id = client_id
        self._secret = client_secret
        self._method = client_auth_method
        self._http_client = http_client
        self._clock = SYSTEM_CLOCK if clock is None else clock
        self._held: _Held | None = None
        self._callback: Callable[[TokenSet], object] | None = None
        self._lock = threading.Lock()
        self._alocks: weakref.WeakKeyDictionary[AbstractEventLoop, Lock] = weakref.WeakKeyDictionary()

    def _form(self) -> list[tuple[str, str]]:
        raise NotImplementedError

    def _adopt(self, fields: dict[str, object], received: float) -> TokenSet:
        raise NotImplementedError

    def _notify(self, tokens: TokenSet) -> object:
        """Hand a refreshed token set to the callback, wrapping what it raises as the provider's failure."""
        if (callback := self._callback) is None:
            return None
        try:
            return callback(tokens)
        except Exception as error:  # noqa: BLE001 - The application's callback failed; its cause is kept.
            raise _failed(error) from None

    def _request(self) -> httpx2.Request:
        """Return the next token request, authenticating the client as its method prescribes."""
        form = self._form()
        headers = {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"}
        secret = _secret(self._secret)
        match self._method:
            case "client_secret_basic":
                pair = f"{quote_plus(self._client_id)}:{quote_plus(secret or '')}"
                headers["Authorization"] = f"Basic {base64.b64encode(pair.encode()).decode('ascii')}"
            case "client_secret_post":
                form.extend((("client_id", self._client_id), ("client_secret", secret or "")))
            case _:
                form.append(("client_id", self._client_id))
        return httpx2.Request("POST", self._url, headers=headers, content=urlencode(form).encode("ascii"))

    def _usable(self, stale: str | None) -> str | None:
        """Return the held token a caller may use without a request: a replacement of `stale`, or one not yet due."""
        if (held := self._held) is None:
            return None
        if stale is not None:
            return held.value if held.value != stale else None
        return held.value if held.refresh_at is None or self._clock.monotonic() < held.refresh_at else None

    def _kept(self, stale: str | None) -> str | None:
        """Return the unexpired token an unforced caller keeps using after a failed early renewal."""
        held = self._held
        if stale is not None or held is None or held.expires_at is None:
            return None
        return held.value if self._clock.monotonic() < held.expires_at else None

    def _received(self, response: httpx2.Response) -> TokenSet:
        """Make a token response's token current and return its token set."""
        return self._adopt(_answer(response, refreshing=self._refreshing), self._clock.monotonic())

    def _hold(self, tokens: TokenSet, received: float, expires_at: float | None, *, renewable: bool = True) -> None:
        """Serve a token set's access token, renewing it early when a request can renew it."""
        refresh_at = expires_at
        if expires_at is not None and renewable:
            refresh_at = expires_at - refresh_margin(expires_at - received)
        self._held = _Held(tokens.access_token, refresh_at, expires_at)

    def _missing(self) -> ConfigurationError:
        reason = "missing_value" if self._http_client is None else "invalid_mode"
        return ConfigurationError(field_path=("http_client",), reason=reason)

    def token(self, send: Send | None, stale: str | None = None) -> str:
        """Return a usable token, requesting one under the provider's lock when none is.

        The token request goes through the provider's own HTTP client, or else through `send`.
        """
        client = self._http_client
        if isinstance(client, httpx2.Client):
            send = _token_send(client)
        elif client is not None or send is None:
            raise self._missing()
        with self._lock:
            if (usable := self._usable(stale)) is not None:
                return usable
            try:
                tokens = self._received(_sent(send, self._request()))
            except Exception:
                if (kept := self._kept(stale)) is None:
                    raise
                return kept
            if inspect.iscoroutine(result := self._notify(tokens)):
                result.close()
                raise ConfigurationError(field_path=("on_token_refreshed",), reason="invalid_mode")
            return tokens.access_token

    async def atoken(self, send: AsyncSend | None, stale: str | None = None) -> str:
        """Return a usable token as `token` does, under an asyncio lock of the running event loop.

        Each event loop has its own lock, so renewals on different loops, and synchronous ones, run independently.
        """
        import asyncio  # noqa: PLC0415

        client = self._http_client
        if isinstance(client, httpx2.AsyncClient):
            send = _atoken_send(client)
        elif client is not None or send is None:
            raise self._missing()
        loop = asyncio.get_running_loop()
        if (lock := self._alocks.get(loop)) is None:
            lock = self._alocks.setdefault(loop, asyncio.Lock())
        async with lock:
            if (usable := self._usable(stale)) is not None:
                return usable
            try:
                tokens = self._received(await _asent(send, self._request()))
            except Exception:
                if (kept := self._kept(stale)) is None:
                    raise
                return kept
            if inspect.isawaitable(result := self._notify(tokens)):
                try:
                    await result
                except Exception as error:  # noqa: BLE001 - The application's callback failed; its cause is kept.
                    raise _failed(error) from None
            return tokens.access_token

    def _auth(self) -> SchemeAuth:
        return SchemeAuth(((_BEARER, self),), origin=None, replayable=_byte_stream, challenge_less=False)

    def sync_auth_flow(self, request: httpx2.Request) -> Generator[httpx2.Request, httpx2.Response, None]:
        """Place the token on a request of any HTTPX2 client, requesting tokens through the provider's own client."""
        return self._auth().sync_auth_flow(request)

    def async_auth_flow(self, request: httpx2.Request) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        """Place the token on a request of any asyncio HTTPX2 client, as the synchronous flow does."""
        return self._auth().async_auth_flow(request)


def _byte_stream(request: httpx2.Request) -> bool:
    """Return whether HTTPX2 can send a request of any client again: one whose body is bytes."""
    return isinstance(request.stream, httpx2.ByteStream)


def _token_send(client: httpx2.Client) -> Send:
    """Return a sender of token requests through a client, without its Auth and without following redirects."""

    def send(request: httpx2.Request) -> httpx2.Response:
        return client.send(request, auth=None, follow_redirects=False)

    return send


def _atoken_send(client: httpx2.AsyncClient) -> AsyncSend:
    """Return an asyncio sender of token requests through a client, as the synchronous sender sends them."""

    async def send(request: httpx2.Request) -> httpx2.Response:
        return await client.send(request, auth=None, follow_redirects=False)

    return send


def _sent(send: Send, request: httpx2.Request) -> httpx2.Response:
    try:
        return send(request)
    except httpx2.HTTPError as error:
        raise _transport(error) from None


async def _asent(send: AsyncSend, request: httpx2.Request) -> httpx2.Response:
    try:
        return await send(request)
    except httpx2.HTTPError as error:
        raise _transport(error) from None


def _transport(error: httpx2.HTTPError) -> AuthError:
    """Return the error of a token request that failed in transport, keeping the native failure as its cause."""
    return AuthError(
        reason="timeout" if isinstance(error, httpx2.TimeoutException) else "oauth_error",
        cause=error,
    )


class ClientCredentials(_Provider):
    """The OAuth client credentials grant: a confidential client's own token, acquired when a call first needs it.

    It is renewed once a tenth of its lifetime, at most thirty seconds, remains, and once when a resource rejects it.
    Token requests send the scopes in order and the audience when one is given.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        client_id: str,
        client_secret: Secret,
        scopes: Sequence[str] = (),
        audience: str | None = None,
        token_url: str | None = None,
        client_auth_method: Literal["client_secret_basic", "client_secret_post"] = "client_secret_basic",
        http_client: httpx2.Client | httpx2.AsyncClient | None = None,
        clock: Clock | None = None,
    ) -> None:
        """Validate the endpoint, the client authentication, the scopes, and the audience without I/O."""
        requested = _scope_list(scopes)
        _text(audience, "audience", optional=True)
        super().__init__(
            client_id=client_id,
            client_secret=client_secret,
            client_auth_method=client_auth_method,
            token_url=token_url,
            http_client=http_client,
            clock=clock,
        )
        self._scopes = requested
        self._audience = audience

    def _form(self) -> list[tuple[str, str]]:
        form = [("grant_type", "client_credentials")]
        if self._scopes:
            form.append(("scope", " ".join(self._scopes)))
        if self._audience is not None:
            form.append(("audience", self._audience))
        return form

    def _adopt(self, fields: dict[str, object], received: float) -> TokenSet:
        expires_at = _expiry(fields, received)
        tokens = TokenSet(str(fields["access_token"]))
        self._hold(tokens, received, expires_at)
        return tokens


def _refresh_settings(token_set: object, callback: object) -> None:
    """Refuse a token set of another type and a callback that cannot be called."""
    checked_instance(token_set, (TokenSet,), ("token_set",))
    if callback is not None and not callable(callback):
        raise ConfigurationError(field_path=("on_token_refreshed",), reason="invalid_type")


class RefreshToken(_Provider):
    """The OAuth refresh token grant for one token set, which this provider alone refreshes.

    The access token is renewed once a tenth of its lifetime, at most thirty seconds, remains, and once when a resource
    rejects it. Each refreshed token set becomes current, then goes to `on_token_refreshed`, which may persist it.
    """

    _refreshing: ClassVar[bool] = True

    def __init__(  # noqa: PLR0913
        self,
        token_set: TokenSet,
        *,
        client_id: str,
        client_secret: Secret | None = None,
        client_auth_method: Literal["none", "client_secret_basic", "client_secret_post"] = "none",
        on_token_refreshed: Callable[[TokenSet], object] | None = None,
        token_url: str | None = None,
        http_client: httpx2.Client | httpx2.AsyncClient | None = None,
        clock: Clock | None = None,
    ) -> None:
        """Validate the endpoint, the client authentication, the token set, and the callback without I/O."""
        _refresh_settings(token_set, on_token_refreshed)
        super().__init__(
            client_id=client_id,
            client_secret=client_secret,
            client_auth_method=client_auth_method,
            token_url=token_url,
            http_client=http_client,
            clock=clock,
        )
        self._tokens = token_set
        self._callback = on_token_refreshed
        now = self._clock.monotonic()
        expires_at = (
            None
            if token_set.expires_at is None
            else now + (token_set.expires_at - datetime.fromtimestamp(self._clock.time(), timezone.utc)).total_seconds()
        )
        self._hold(token_set, now, expires_at, renewable=token_set.refresh_token is not None)

    def _form(self) -> list[tuple[str, str]]:
        if (refresh_token := self._tokens.refresh_token) is None:
            raise AuthError(reason="reauthorization_required")
        return [("grant_type", "refresh_token"), ("refresh_token", refresh_token)]

    def _adopt(self, fields: dict[str, object], received: float) -> TokenSet:
        expires_at = _expiry(fields, received)
        issued = fields.get("refresh_token")
        try:
            expiry = (
                None
                if expires_at is None
                else datetime.fromtimestamp(self._clock.time(), timezone.utc) + timedelta(seconds=expires_at - received)
            )
        except OverflowError:
            raise AuthError(reason="invalid_expiry") from None
        tokens = self._tokens = TokenSet(
            str(fields["access_token"]),
            issued if isinstance(issued, str) and issued else self._tokens.refresh_token,
            expiry,
        )
        self._hold(tokens, received, expires_at)
        return tokens
