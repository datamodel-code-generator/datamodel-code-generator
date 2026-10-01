"""Refresh a rotating token family over TLS, through generated clients, and from injected transports and secrets."""

from __future__ import annotations

import asyncio
import importlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Final
from unittest.mock import patch
from urllib.parse import parse_qs

from tests.data.python.client_oauth import (
    LIMIT,
    Adapter,
    AsyncAdapter,
    AsyncResponse,
    AsyncSecret,
    Response,
    Secret,
    failure_line,
    json_reply,
    started,
    stop,
    watched,
)
from tests.data.python.client_runtime import Exchange, raw_response, record, run
from tests.data.python.fixture_server import _contexts

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_TOKEN: Final = "https://auth.example.com/token"
_ROTATED: Final = {"access_token": "access-2", "token_type": "Bearer", "expires_in": 3600, "refresh_token": "refresh-2"}
_REJECTED: Final = {"WWW-Authenticate": 'Bearer error="invalid_token"'}


def _context(
    auth: ModuleType, *scopes: str, audience: str | None = None, deadline: object = None, cancel_token: object = None
) -> Any:
    return auth.CredentialContext(
        scheme="oauth",
        required_scopes=scopes,
        audience=audience,
        origin="https://api.example.com",
        deadline=deadline,
        cancel_token=cancel_token,
    )


def _tokens(  # noqa: PLR0913
    auth: ModuleType,
    access: str = "access-1",
    refresh: str | None = "refresh-1",
    *,
    revision: int = 0,
    minutes: float | None = 60,
    scopes: tuple[str, ...] | None = None,
    token_type: str = "Bearer",
) -> Any:
    expires_at = None if minutes is None else datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return auth.TokenSet(auth.AccessToken(access, token_type, expires_at, scopes), refresh, revision)


def _material(credential: Any) -> str:
    token = credential.token
    minutes = (
        None
        if token.expires_at is None
        else round((token.expires_at - datetime.now(timezone.utc)).total_seconds() / 60)
    )
    return f"{token.value} scopes={token.scopes} expires_in_minutes={minutes}"


def _outcome(call: Callable[[], object]) -> str:
    try:
        result = call()
    except Exception as error:  # noqa: BLE001
        return failure_line(error)
    return _material(result) if hasattr(result, "token") else repr(result)


async def _aoutcome(call: Callable[[], Any]) -> str:
    try:
        result = await call()
    except Exception as error:  # noqa: BLE001
        return failure_line(error)
    return _material(result) if hasattr(result, "token") else repr(result)


def _failure(call: Callable[[], object]) -> Any:
    try:
        call()
    except Exception as error:  # noqa: BLE001
        return error
    return None


def _workers() -> int:
    return sum(thread.name.startswith("oauth-refresh") for thread in threading.enumerate())


def _reply(responses: ModuleType, payload: object, status: int = 200) -> Response:
    return Response(responses, status, json.dumps(payload).encode())


def _expiry(package: ModuleType, auth: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    """Renew a token a tenth of its lifetime early, but serve one without a refresh token until it expires."""
    now = time.monotonic()
    with ExitStack() as stack:
        for name in ("oauth", "refresh", "rotation", "timing"):
            module = importlib.import_module(f"{package.__name__}._runtime.client.{name}")
            stack.enter_context(patch.object(module, "monotonic", lambda: now))
        for refresh in ("refresh-1", None):
            adapter = _Sent(transports, _reply(responses, _ROTATED))
            with auth.RefreshTokenProvider(
                _TOKEN,
                client_id="c",
                token_set=_tokens(auth, refresh=refresh, minutes=100 / 60),
                client_auth_method="none",
                token_transport=adapter,
            ) as family:
                start = now
                first = family.get(_context(auth))
                now = start + 95
                lines.append(
                    f"  lifetime 100s with refresh token {refresh} after 95s = {_outcome(lambda family=family: family.get(_context(auth)))}"
                    f" same={family.get(_context(auth)) is first if refresh is None else None}"
                )
                now = start + 101
                lines.append(f"    after 101s = {_outcome(lambda family=family: family.get(_context(auth)))} sent={adapter.refresh_tokens}")
        errors = importlib.import_module(f"{package.__name__}.errors")
        unsent = errors.TransportError(delivery_state=errors.DeliveryState.NOT_SENT, phase="connect")
        adapter = _Sent(transports, unsent, unsent, unsent, evidence=True)
        with auth.RefreshTokenProvider(
            _TOKEN,
            client_id="c",
            token_set=_tokens(auth, minutes=100 / 60),
            client_auth_method="none",
            token_transport=adapter,
        ) as family:
            start = now
            first = family.get(_context(auth))
            now = start + 95
            lines.append(
                f"  refresh proven unsent before the token expires = {_outcome(lambda: family.get(_context(auth)))}"
                f" same={family.get(_context(auth)) is first}"
            )
            now = start + 101
            lines.append(f"    once the token expired = {_outcome(lambda: family.get(_context(auth)))}")


class _Once(Secret):
    """A client secret provider whose first lookup alone fails or is slow."""

    def get(self, context: object) -> object:
        try:
            return super().get(context)
        finally:
            self.failure, self.delay = None, 0


class _Held:
    """A stand-in for the token request builder that holds a refresh until released."""

    def __init__(self, request: Callable[..., Any]) -> None:
        self.request = request
        self.released = threading.Event()

    def __call__(self, *arguments: Any) -> Any:
        self.released.wait(LIMIT)
        return self.request(*arguments)


class _HeldClose(Response):
    """A token response whose close waits for a gate."""

    def __init__(self, responses: ModuleType, status: int, payload: object, gate: threading.Event) -> None:
        super().__init__(responses, status, json.dumps(payload).encode())
        self.gate = gate

    def close(self) -> None:
        self.gate.wait(LIMIT)


class _Sent(Adapter):
    """An injected token transport recording the refresh token of every request it receives."""

    def __init__(self, transports: ModuleType, *replies: object, **settings: Any) -> None:
        super().__init__(transports, *replies, **settings)
        self.refresh_tokens: list[str] = []

    def send(self, request: Any, context: object) -> object:
        self.refresh_tokens.extend(parse_qs(request.body.content.decode())["refresh_token"])
        return super().send(request, context)


def _configuration(auth: ModuleType, transports: ModuleType, lines: list[str]) -> None:
    """Refuse invalid token sets, client authentication, scopes, and audiences without threads or I/O."""
    provider, async_provider = auth.RefreshTokenProvider, auth.AsyncRefreshTokenProvider
    tokens = _tokens(auth)
    secret = auth.StaticCredentialProvider(auth.ApiKeyCredential("secret"))
    naive = auth.TokenSet(auth.AccessToken("access-1", expires_at=datetime(2030, 1, 1)), "refresh-1")  # noqa: DTZ001
    cases: tuple[tuple[str, Callable[[], object]], ...] = (
        ("missing token set", lambda: provider(_TOKEN, client_id="c", client_secret=secret)),
        ("token set type", lambda: provider(_TOKEN, client_id="c", token_set="refresh-1", client_secret=secret)),
        ("naive expiry", lambda: provider(_TOKEN, client_id="c", token_set=naive, client_secret=secret)),
        (
            "token type other than Bearer",
            lambda: provider(_TOKEN, client_id="c", token_set=_tokens(auth, token_type="MAC"), client_secret=secret),
        ),
        (
            "public client with a secret",
            lambda: provider(_TOKEN, client_id="c", token_set=tokens, client_secret=secret, client_auth_method="none"),
        ),
        ("missing secret", lambda: provider(_TOKEN, client_id="c", token_set=tokens)),
        ("async secret", lambda: provider(_TOKEN, client_id="c", token_set=tokens, client_secret=AsyncSecret())),
        (
            "sync secret of an async provider",
            lambda: async_provider(_TOKEN, client_id="c", token_set=tokens, client_secret=secret),
        ),
        (
            "sync transport of an async provider",
            lambda: async_provider(
                _TOKEN, client_id="c", token_set=tokens, client_auth_method="none", token_transport=Adapter(transports)
            ),
        ),
        ("lone scope string", lambda: provider(_TOKEN, client_id="c", token_set=tokens, client_secret=secret, scopes="a")),
        ("empty audience", lambda: provider(_TOKEN, client_id="c", token_set=tokens, client_secret=secret, audience="")),
        ("options type", lambda: provider(_TOKEN, client_id="c", token_set=tokens, client_secret=secret, options=1)),
    )
    for label, call in cases:
        lines.append(f"  {label} = {_outcome(call)}")
    before = _workers()
    adapter = Adapter(transports)
    with provider(_TOKEN, client_id="c", token_set=tokens, client_auth_method="none", token_transport=adapter) as idle:
        lines.append(f"  workers after construction = {_workers() - before}")
        for label, call in (
            ("context type", lambda: idle.get(None)),
            ("audience of another resource", lambda: idle.get(_context(auth, audience="api"))),
            ("invalidate with another type", lambda: idle.invalidate("version")),
            ("snapshot of an unknown id", lambda: idle.refresh_snapshot("unknown")),
            ("replace with another type", lambda: idle.replace_token_set("refresh-2")),
            ("replace at the same revision", lambda: idle.replace_token_set(_tokens(auth, "access-2", "refresh-2"))),
            (
                "replace without a usable token",
                lambda: idle.replace_token_set(_tokens(auth, "access-2", None, revision=1, minutes=-1)),
            ),
            (
                "replace before the first acquisition",
                lambda: idle.replace_token_set(_tokens(auth, "access-2", "refresh-2", revision=1), persist=False),
            ),
            ("get", lambda: idle.get(_context(auth))),
        ):
            lines.append(f"  {label} = {_outcome(call)}")
    lines.append(f"  workers after close = {_workers() - before} sends={adapter.sends}")
    for label, call in (
        ("get after close", lambda: idle.get(_context(auth))),
        ("replace after close", lambda: idle.replace_token_set(_tokens(auth, "access-3", "refresh-3", revision=2))),
        ("close again", idle.close),
    ):
        lines.append(f"  {label} = {_outcome(call)}")


def _wire(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> int:
    """Rotate over TLS with each client authentication, then stop the family on each answer a refresh cannot trust."""
    exchange = Exchange(lines)
    port = exchange.port()
    token_url = f"https://localhost:{port}/token"
    oauth = auth.OAuthProviderOptions(transport=options.TransportOptions(ssl_context=_contexts()[1]))
    secret = auth.StaticCredentialProvider(auth.ApiKeyCredential("se:cr et"))

    def provider(tokens: Any, method: str = "client_secret_basic", **arguments: object) -> Any:
        return auth.RefreshTokenProvider(
            token_url,
            client_id="client id",
            token_set=tokens,
            client_secret=None if method == "none" else secret,
            client_auth_method=method,
            options=oauth,
            **arguments,
        )

    exchange.respond(
        json_reply(200, _ROTATED),
        json_reply(200, {"access_token": "access-3", "token_type": "Bearer", "expires_in": 3600, "scope": "read"}),
    )
    with provider(_tokens(auth, minutes=-1, scopes=("read", "write")), scopes=("write", "read"), audience="api") as family:
        first = family.get(_context(auth))
        lines.append(f"  basic = {_material(first)}")
        lines.append(f"    cached for the configured audience = {family.get(_context(auth, audience='api')) is first}")
        refreshed = family.refresh(_context(auth, "read"))
        lines.append(f"    refresh keeping the refresh token = {_material(refreshed)}")
        lines.append(f"    write caller = {_outcome(lambda: family.get(_context(auth, 'read', 'write')))}")
        family.invalidate(first.version)
        lines.append(f"    stale invalidation keeps the token = {family.get(_context(auth, 'read')) is refreshed}")
        for label, tokens in (
            ("replace at the current revision", _tokens(auth, "access-4", "refresh-4", revision=2)),
            ("replace with a spent refresh token", _tokens(auth, "access-4", "refresh-1", revision=3)),
            ("replace with a newer token set", _tokens(auth, "access-4", "refresh-4", revision=3)),
        ):
            lines.append(f"    {label} = {_outcome(lambda tokens=tokens: family.replace_token_set(tokens))}")
        lines.append(f"    get after the replacement = {_outcome(lambda: family.get(_context(auth)))}")
    exchange.respond(
        json_reply(200, {"access_token": "lasting", "token_type": "bearer"}),
        json_reply(200, {**_ROTATED, "access_token": "public", "refresh_token": "refresh-1"}),
    )
    with provider(_tokens(auth, minutes=-1), "client_secret_post") as lasting:
        first = lasting.get(_context(auth))
        lines.append(f"  post = {_material(first)}")
        lines.append(f"    cached without expiry = {lasting.get(_context(auth)) is first}")
    with provider(_tokens(auth, minutes=-1), "none") as public:
        lines.append(f"  public client keeping its refresh token = {_outcome(lambda: public.get(_context(auth)))}")
        public.invalidate(public.get(_context(auth)).version)
        lines.append(f"    no refresh token was spent = {_outcome(lambda: public.replace_token_set(_tokens(auth, revision=2)))}")
    failures: tuple[tuple[str, Callable[..., Any]], ...] = (
        ("invalid grant", json_reply(400, {"error": "invalid_grant"})),
        ("rejected scope", json_reply(400, {"error": "invalid_scope"})),
        ("rejected client", json_reply(401, {"error": "invalid_client"})),
        ("unauthorized without invalid_client", json_reply(401, {"error": "invalid_grant"})),
        ("unknown error code", json_reply(400, {"error": "try_later"})),
        ("unavailable", json_reply(503, b"")),
        ("error without JSON", json_reply(400, b"{")),
        ("success without JSON", json_reply(200, b"<html>")),
        ("missing access token", json_reply(200, {"token_type": "Bearer"})),
        ("zero lifetime", json_reply(200, {**_ROTATED, "expires_in": 0})),
    )
    for label, reply in failures:
        exchange.respond(reply)
        with provider(_tokens(auth, minutes=-1)) as failing:
            error = _failure(lambda failing=failing: failing.get(_context(auth)))
            lines.append(f"  {label} = {failure_line(error)}")
            lines.append(f"    snapshot = {failing.refresh_snapshot(error.refresh_id)}")
            lines.append(f"    later get = {_outcome(lambda failing=failing: failing.get(_context(auth)))}")
            lines.append(f"    later refresh = {_outcome(lambda failing=failing: failing.refresh(_context(auth)))}")
            lines.append(f"    exchange needed = {failing.exchange_needed(auth.TokenVersion())}")
    exchange.respond(json_reply(200, _ROTATED), json_reply(200, {**_ROTATED, "refresh_token": "refresh-1"}))
    with provider(_tokens(auth, minutes=-1)) as returning:
        rotated = returning.get(_context(auth))
        returning.invalidate(rotated.version)
        error = _failure(lambda: returning.get(_context(auth)))
        lines.append(f"  spent refresh token returned = {failure_line(error)}")
    _generated(package, auth, options, exchange, provider, lines)
    stop(exchange)
    return port


def _generated(
    package: ModuleType,
    auth: ModuleType,
    options: ModuleType,
    exchange: Exchange,
    provider: Callable[..., Any],
    lines: list[str],
) -> None:
    """Authenticate generated calls with the family's token, refreshing it once a resource rejects it."""
    ok = raw_response(200, b"ok", "application/octet-stream")
    exchange.respond(ok, raw_response(401, b"rejected", "application/octet-stream", **_REJECTED), json_reply(200, _ROTATED), ok)
    retry = options.RetryOptions(initial_delay=0)
    with (
        provider(_tokens(auth, scopes=("read", "write"))) as family,
        provider(_tokens(auth, minutes=-1, scopes=("read",))) as narrow,
        exchange.client() as native,
        package.Client(
            http_client=native, options=options.ClientOptions(auth=auth.AuthConfig({"oauth": family}), retry=retry)
        ) as api,
    ):
        record(lines, "generated call", api.auth.with_response.oauth_read)
        record(lines, "rejected token refreshed", api.auth.with_response.oauth_read)
        narrowed = options.RequestOptions(auth=auth.AuthConfig({"oauth": narrow}))
        record(lines, "write operation beyond the known grants", lambda: api.auth.with_response.oauth_scopes(options=narrowed))


def _faults(  # noqa: PLR0913, PLR0915
    package: ModuleType,
    auth: ModuleType,
    options: ModuleType,
    transports: ModuleType,
    responses: ModuleType,
    errors: ModuleType,
    lines: list[str],
) -> None:
    """Keep the refresh token of a refresh that sent nothing, and spend it once a request may have delivered it."""
    read = errors.PhaseTimeoutError(effective_timeout=15.0, delivery_state=errors.DeliveryState.MAYBE_SENT, phase="read")
    unsent = errors.TransportError(delivery_state=errors.DeliveryState.NOT_SENT, phase="connect")
    secret = auth.StaticCredentialProvider(auth.ApiKeyCredential("s"))

    def provider(adapter: Adapter, *, client_secret: object = secret, total: float = 30.0, **arguments: Any) -> Any:
        return auth.RefreshTokenProvider(
            _TOKEN,
            client_id="c",
            token_set=arguments.pop("token_set", _tokens(auth, minutes=-1)),
            client_secret=client_secret,
            options=auth.OAuthProviderOptions(refresh_timeout=total),
            token_transport=adapter,
            **arguments,
        )

    rotated = _reply(responses, _ROTATED)
    for label, adapter, settings in (
        ("connect failure proven unsent", _Sent(transports, unsent, rotated, evidence=True), {}),
        ("phase timeout after sending", _Sent(transports, read, rotated), {}),
        ("adapter failure", _Sent(transports, RuntimeError("adapter"), rotated), {}),
        (
            "body slower than the session",
            _Sent(transports, Response(responses, 200, json.dumps(_ROTATED).encode(), pause=1.0), rotated),
            {"total": 0.5},
        ),
        ("interrupted send", _Sent(transports, KeyboardInterrupt(), rotated), {}),
        (
            "secret failure",
            _Sent(transports, rotated),
            {"client_secret": _Once(auth.ApiKeyCredential("s"), failure=RuntimeError("secret"))},
        ),
        (
            "secret slower than the session",
            _Sent(transports, rotated),
            {"client_secret": _Once(auth.ApiKeyCredential("s"), delay=1.0), "total": 0.5},
        ),
    ):
        with provider(adapter, **settings) as family:
            lines.append(f"  {label} = {_outcome(lambda family=family: family.get(_context(auth)))}")
            lines.append(f"    later get = {_outcome(lambda family=family: family.get(_context(auth)))}")
            lines.append(f"    refresh tokens sent = {adapter.refresh_tokens}")
    unstuck = threading.Event()
    adapter = _Sent(transports, rotated, gate=unstuck)
    with provider(adapter, total=0.5) as family:
        lines.append(f"  answer after the session and its grace = {_outcome(lambda: family.get(_context(auth)))}")
        unstuck.set()
        lines.append(f"    later get = {_outcome(lambda: family.get(_context(auth)))} sent={adapter.refresh_tokens}")
    oauth = importlib.import_module(f"{package.__name__}._runtime.client.oauth")
    held = _Held(oauth.token_request)
    adapter = _Sent(transports, rotated)
    with (
        provider(adapter, client_secret=None, client_auth_method="none", total=0.5) as family,
        patch.object(oauth, "token_request", held),
    ):
        lines.append(f"  public refresh held past its session = {_outcome(lambda: family.get(_context(auth)))}")
        held.released.set()
        lines.append(f"    later get = {_outcome(lambda: family.get(_context(auth)))} sent={adapter.refresh_tokens}")
    held, submit = _Held(held.request), ThreadPoolExecutor.submit

    def interrupted(executor: ThreadPoolExecutor, *arguments: Any) -> Any:
        submit(executor, *arguments)
        raise KeyboardInterrupt

    adapter = _Sent(transports, rotated)
    with provider(adapter) as family:
        with patch.object(oauth, "token_request", held), patch.object(ThreadPoolExecutor, "submit", interrupted):
            try:
                family.get(_context(auth))
            except KeyboardInterrupt:
                lines.append("  interrupted once its worker started = KeyboardInterrupt")
        held.released.set()
        lines.append(f"    later get = {_outcome(lambda: family.get(_context(auth)))} sent={adapter.refresh_tokens}")
    adapter = _Sent(transports, rotated)
    with provider(adapter) as family:
        with patch.object(threading.Thread, "start", side_effect=RuntimeError("can't start new thread")):
            lines.append(f"  worker thread refusing to start = {_outcome(lambda: family.get(_context(auth)))}")
        lines.append(f"    later get = {_outcome(lambda: family.get(_context(auth)))} sent={adapter.refresh_tokens}")
    adapter = _Sent(transports, rotated)
    with provider(adapter, total=0.5) as family:
        deferred: list[Callable[[], object]] = []
        with patch.object(ThreadPoolExecutor, "submit", lambda _, work, *args: deferred.append(lambda: work(*args))):
            lines.append(f"  worker starting once the session ended = {_outcome(lambda: family.get(_context(auth)))}")
        for work in deferred:
            work()
        lines.append(f"    sent meanwhile = {adapter.refresh_tokens}")
        lines.append(f"    later get = {_outcome(lambda: family.get(_context(auth)))} sent={adapter.refresh_tokens}")
    gate = threading.Event()
    adapter = _Sent(transports, rotated, gate=gate)
    with provider(adapter) as family:
        starter = started(lambda: family.get(_context(auth)), adapter.entered, _outcome)
        token = watched(options)
        joiner = started(lambda: family.get(_context(auth, cancel_token=token)), token.checked, _outcome)
        lines.append(f"  replace while a refresh runs = {_outcome(lambda: family.replace_token_set(_tokens(auth, revision=5)))}")
        lines.append(f"    exchange needed meanwhile = {family.exchange_needed(auth.TokenVersion())}")
        gate.set()
        starter.join(LIMIT)
        joiner.join(LIMIT)
        lines.append(f"  one refresh for concurrent callers = {starter.line} / {joiner.line} sent={adapter.refresh_tokens}")
        current = family.get(_context(auth))
        lines.append(f"    exchange needed for the current version = {family.exchange_needed(current.version)}")
        lines.append(f"    exchange needed for another version = {family.exchange_needed(auth.TokenVersion())}")
    adapter = _Sent(transports)
    with provider(adapter, token_set=_tokens(auth, refresh=None)) as family:
        current = family.get(_context(auth))
        lines.append(f"  without a refresh token = {_material(current)}")
        lines.append(f"    exchange needed = {family.exchange_needed(current.version)}")
        family.invalidate(current.version)
        lines.append(f"    get once invalidated = {_outcome(lambda: family.get(_context(auth)))}")
        lines.append(f"    later refresh = {_outcome(lambda: family.refresh(_context(auth)))} sends={adapter.sends}")
    with provider(_Sent(transports), token_set=_tokens(auth, refresh=None)) as family:
        lines.append(f"  forced refresh without a refresh token = {_outcome(lambda: family.refresh(_context(auth)))}")
    adapter = _Sent(transports, rotated)
    with provider(adapter, token_set=_tokens(auth, minutes=-1, scopes=("read",))) as family:
        lines.append(f"  expired token without a scope = {_outcome(lambda: family.get(_context(auth, 'write')))} sends={adapter.sends}")
        lines.append(f"    caller within its grants = {_outcome(lambda: family.get(_context(auth, 'read')))}")
    adapter = _Sent(transports, _reply(responses, {**_ROTATED, "scope": "read"}))
    with provider(adapter, token_set=_tokens(auth, minutes=-1, scopes=("read", "write"))) as family:
        lines.append(f"  refresh narrowing the grants = {_outcome(lambda: family.get(_context(auth, 'read', 'write')))}")
        lines.append(f"    caller within the new grants = {_outcome(lambda: family.get(_context(auth, 'read')))} sends={adapter.sends}")
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    claimed = auth.TokenSet(auth.AccessToken("access-1", expires_at=expires_at, audience="other"), "refresh-1")
    for audience in ("api", "other"):
        with provider(_Sent(transports), token_set=claimed, audience=audience) as family:
            lines.append(
                f"  token for audience other with audience {audience} = {_outcome(lambda family=family: family.get(_context(auth)))}"
            )
    lapsed = auth.TokenSet(auth.AccessToken("access-1", expires_at=expires_at - timedelta(hours=2), audience="other"), "refresh-1")
    adapter = _Sent(transports, rotated)
    with provider(adapter, token_set=lapsed, audience="api") as family:
        lines.append(f"  expired token for audience other with audience api = {_outcome(lambda: family.get(_context(auth)))}")
        lines.append(f"    later get = {_outcome(lambda: family.get(_context(auth)))} sent={adapter.refresh_tokens}")


async def _async(auth: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    """Rotate, stop, and replace an asyncio family on the event loop it is bound to."""
    adapter = AsyncAdapter(
        transports,
        AsyncResponse(responses, 200, json.dumps(_ROTATED).encode()),
        AsyncResponse(responses, 400, json.dumps({"error": "invalid_grant"}).encode()),
    )
    family = auth.AsyncRefreshTokenProvider(
        _TOKEN, client_id="c", token_set=_tokens(auth, minutes=-1), client_auth_method="none", token_transport=adapter
    )
    async with family:
        first = await family.get(_context(auth))
        lines.append(f"  async rotation = {_material(first)}")
        lines.append(f"    cached = {await family.get(_context(auth)) is first}")
        await family.invalidate(first.version)
        lines.append(f"    invalid grant = {await _aoutcome(lambda: family.refresh(_context(auth)))}")
        lines.append(f"    later get = {await _aoutcome(lambda: family.get(_context(auth)))} sends={adapter.sends}")
        replaced = _tokens(auth, "access-9", "refresh-9", revision=5)
        lines.append(f"    replace = {await _aoutcome(lambda: family.replace_token_set(replaced))}")
        lines.append(f"    get after the replacement = {await _aoutcome(lambda: family.get(_context(auth)))}")
    lines.append(f"    get after close = {await _aoutcome(lambda: family.get(_context(auth)))}")
    blocked = AsyncAdapter(transports, AsyncResponse(responses, 200, json.dumps(_ROTATED).encode()))
    family = auth.AsyncRefreshTokenProvider(
        _TOKEN,
        client_id="c",
        token_set=_tokens(auth, minutes=-1),
        client_auth_method="none",
        options=auth.OAuthProviderOptions(refresh_timeout=0.5),
        token_transport=blocked,
    )
    async with family:
        first = asyncio.create_task(_aoutcome(lambda: family.get(_context(auth))))
        await asyncio.sleep(0)
        time.sleep(1.6)
        lines.append(f"  async refresh starting once its session ended = {await _aoutcome(lambda: family.get(_context(auth)))}")
        lines.append(f"    first caller = {await first} sends={blocked.sends}")


def oauth_refresh(package: ModuleType, lines: list[str]) -> None:
    """Exercise refresh token rotation, stopping, and replacement in both execution modes."""
    auth, options, transports, responses, errors = (
        importlib.import_module(f"{package.__name__}.{name}")
        for name in ("auth", "options", "transports", "responses", "errors")
    )
    _configuration(auth, transports, lines)
    port = _wire(package, auth, options, lines)
    _expiry(package, auth, transports, responses, lines)
    _faults(package, auth, options, transports, responses, errors, lines)

    async def flows() -> None:
        await _async(auth, transports, responses, lines)

    run(flows)
    for index, line in enumerate(lines):
        lines[index] = line.replace(f":{port}", ":<port>")


class _Load:
    """A token load answering from a script of token sets, None, and failures, maybe once a gate opens.

    Every load waits for the gate, or only the one with the held index.
    """

    def __init__(self, *answers: object, gate: threading.Event | None = None, held: int | None = None) -> None:
        self.answers = list(answers)
        self.gate = gate
        self.held = held
        self.contexts: list[Any] = []
        self.entered = threading.Event()

    def load(self, context: Any) -> object:
        self.contexts.append(context)
        self.entered.set()
        if self.gate is not None and self.held in {None, len(self.contexts) - 1}:
            self.gate.wait(LIMIT)
        if isinstance(answer := self.answers.pop(0), BaseException):
            raise answer
        return answer


class _AsyncLoad(_Load):
    """An asyncio token load, answering after a delay, or only the load with the held index after it."""

    def __init__(self, *answers: object, delay: float = 0, held: int | None = None) -> None:
        super().__init__(*answers, held=held)
        self.delay = delay

    async def load(self, context: Any) -> object:  # ty: ignore[invalid-method-override]
        self.contexts.append(context)
        if self.held in {None, len(self.contexts) - 1}:
            await asyncio.sleep(self.delay)
        if isinstance(answer := self.answers.pop(0), BaseException):
            raise answer
        return answer


def _set_line(tokens: Any) -> str:
    if tokens is None:
        return "None"
    return f"revision={tokens.revision} access={tokens.access_token.value} refresh={tokens.refresh_token}"


def _reloaded(call: Callable[[], object]) -> str:
    try:
        result = call()
    except Exception as error:  # noqa: BLE001
        return failure_line(error)
    return _set_line(result)


def _loaded(family: Any, load: _Load) -> Any:
    """Wait until the family's first load job ended, and return its snapshot."""
    load.entered.wait(LIMIT)
    session, deadline = load.contexts[0].session_id, time.monotonic() + LIMIT
    while (snapshot := family.refresh_snapshot(session)).state == "PENDING" and time.monotonic() < deadline:
        time.sleep(0.01)
    return snapshot


def _load_configuration(auth: ModuleType, transports: ModuleType, lines: list[str]) -> None:
    """Refuse a family without a token set or a load, and loads of the other mode; fingerprint the configuration."""
    provider, async_provider = auth.RefreshTokenProvider, auth.AsyncRefreshTokenProvider
    for label, call in (
        ("neither token set nor load", lambda: provider(_TOKEN, client_id="c", client_auth_method="none")),
        (
            "async load of a sync provider",
            lambda: provider(_TOKEN, client_id="c", load=_AsyncLoad(), client_auth_method="none"),
        ),
        (
            "sync load of an async provider",
            lambda: async_provider(_TOKEN, client_id="c", load=_Load(), client_auth_method="none"),
        ),
        (
            "reload without a load",
            lambda: provider(_TOKEN, client_id="c", token_set=_tokens(auth), client_auth_method="none").reload_token_set(),
        ),
    ):
        lines.append(f"  {label} = {_outcome(call)}")
    keys = []
    for url, audience, scopes in (
        (_TOKEN, None, ("b", "a")),
        ("https://auth.example.com:443/token", None, ("a", "b", "a")),
        ("https://auth.example.com/token?tenant=1", None, ("a", "b")),
        (_TOKEN, "api", ("a", "b")),
    ):
        load = _Load(_tokens(auth))
        with auth.RefreshTokenProvider(
            url,
            client_id="c",
            load=load,
            client_auth_method="none",
            scopes=scopes,
            audience=audience,
            token_transport=Adapter(transports),
        ) as family:
            family.get(_context(auth, audience=audience))
        keys.append(load.contexts[0].cache_key)
    lines.append(
        f"  cache keys prefixed={all(key.startswith('oauth-refresh-v1:') for key in keys)}"
        f" default port and scope order ignored={keys[0] == keys[1]} distinct otherwise={len(set(keys)) == 3}"
    )


def _loads(auth: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    """Load once before the first acquisition, keeping the newer token set, and stop on what cannot be trusted."""
    stored, same = _tokens(auth, "stored", "refresh-5", revision=3), _tokens(auth)
    for label, answer, initial in (
        ("nothing stored beside a token set", None, _tokens(auth)),
        ("newer token set stored", stored, _tokens(auth)),
        ("older token set stored", _tokens(auth, "old", "refresh-0"), _tokens(auth, revision=2)),
        ("same token set stored", same, same),
        ("another token set at the same revision", _tokens(auth, "other", "refresh-9", revision=3), stored),
        ("load failure", RuntimeError("load"), _tokens(auth)),
        ("load interrupted", KeyboardInterrupt(), _tokens(auth)),
        ("stored value of another type", "stored", _tokens(auth)),
        ("nothing stored", None, None),
        ("stored token set without a usable token", _tokens(auth, refresh=None, revision=1, minutes=-1), None),
        ("constructor token set without a usable token", None, _tokens(auth, refresh=None, minutes=-1)),
        ("stored token set needing a refresh", _tokens(auth, refresh="refresh-5", revision=1, minutes=-1), None),
    ):
        load = _Load(answer, None)
        adapter = _Sent(transports, _reply(responses, _ROTATED))
        with auth.RefreshTokenProvider(
            _TOKEN, client_id="c", token_set=initial, load=load, client_auth_method="none", token_transport=adapter
        ) as family:
            lines.append(f"  {label} = {_outcome(lambda family=family: family.get(_context(auth)))}")
            lines.append(
                f"    later get = {_outcome(lambda family=family: family.get(_context(auth)))}"
                f" loads={len(load.contexts)} sent={adapter.refresh_tokens}"
            )
    context = load.contexts[0]
    lines.append(
        f"  load context purpose={context.purpose} session={bool(context.session_id)}"
        f" within its session={context.deadline.remaining() > 0}"
    )
    gate = threading.Event()
    load = _Load(_tokens(auth), gate=gate)
    with auth.RefreshTokenProvider(
        _TOKEN, client_id="c", load=load, client_auth_method="none", token_transport=_Sent(transports)
    ) as family:
        starter = started(lambda: family.get(_context(auth)), load.entered, _outcome)
        token = watched(options)
        joiner = started(lambda: family.get(_context(auth, cancel_token=token)), token.checked, _outcome)
        gate.set()
        starter.join(LIMIT)
        joiner.join(LIMIT)
        lines.append(f"  concurrent callers share one load = {starter.line} / {joiner.line} loads={len(load.contexts)}")
    gate = threading.Event()
    load = _Load(_tokens(auth, minutes=-1), None, gate=gate)
    adapter = _Sent(transports, _reply(responses, _ROTATED))
    with auth.RefreshTokenProvider(
        _TOKEN, client_id="c", load=load, client_auth_method="none", token_transport=adapter
    ) as family:
        deadline = options.Deadline.after(0.1)
        lines.append(f"  caller leaving the load = {_outcome(lambda: family.get(_context(auth, deadline=deadline)))}")
        gate.set()
        lines.append(f"    load finished = {_loaded(family, load)} sent={adapter.refresh_tokens}")
        lines.append(f"    later get = {_outcome(lambda: family.get(_context(auth)))} sent={adapter.refresh_tokens}")
    gate = threading.Event()
    load = _Load(_tokens(auth, minutes=-1), gate=gate)
    adapter = _Sent(transports, _reply(responses, _ROTATED))
    with auth.RefreshTokenProvider(
        _TOKEN, client_id="c", load=load, client_auth_method="none", token_transport=adapter
    ) as family:
        token = options.CancelToken()
        caller = started(lambda: family.get(_context(auth, cancel_token=token)), load.entered, _outcome)
        token.cancel()
        gate.set()
        caller.join(LIMIT)
        lines.append(f"  caller cancelled while the load runs = {caller.line}")
        lines.append(f"    load finished = {_loaded(family, load)} sent={adapter.refresh_tokens}")
    gate = threading.Event()
    load = _Load(_tokens(auth), gate=gate)
    family = auth.RefreshTokenProvider(
        _TOKEN, client_id="c", load=load, client_auth_method="none", token_transport=_Sent(transports)
    )
    starter = started(lambda: family.get(_context(auth)), load.entered, _outcome)
    released = family.request_close()
    gate.set()
    starter.join(LIMIT)
    lines.append(f"  provider closing while its load runs = {starter.line} released={released.result(LIMIT)}")
    load = _Load(_tokens(auth, "stored", None, revision=4, minutes=-1), None)
    adapter = _Sent(transports, _reply(responses, _ROTATED))
    with auth.RefreshTokenProvider(
        _TOKEN,
        client_id="c",
        token_set=_tokens(auth, "constructor", "refresh-constructor", minutes=-1),
        load=load,
        client_auth_method="none",
        token_transport=adapter,
    ) as family:
        lines.append(f"  stored token set that can never serve = {_outcome(lambda: family.get(_context(auth)))}")
        lines.append(f"    reload with nothing stored = {_reloaded(family.reload_token_set)}")
        lines.append(f"    get = {_outcome(lambda: family.get(_context(auth)))} sent={adapter.refresh_tokens}")


def _reloads(auth: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    """Reload on request: recover a family that failed or stopped, and leave it as it was when nothing is newer."""
    newer = _tokens(auth, "reloaded", "refresh-7", revision=5)

    def provider(load: _Load, *replies: object, **settings: Any) -> tuple[Any, _Sent]:
        adapter = _Sent(transports, *replies)
        family = auth.RefreshTokenProvider(
            _TOKEN, client_id="c", load=load, client_auth_method="none", token_transport=adapter, **settings
        )
        return family, adapter

    family, _ = provider(_Load(RuntimeError("load"), newer))
    with family:
        lines.append(f"  initial load failed = {_outcome(lambda: family.get(_context(auth)))}")
        lines.append(f"    reload = {_reloaded(family.reload_token_set)}")
        lines.append(f"    get = {_outcome(lambda: family.get(_context(auth)))}")
    family, _ = provider(_Load(_tokens(auth, revision=2), None, _tokens(auth, "old", "refresh-0"), RuntimeError("load")))
    with family:
        family.get(_context(auth))
        for label in ("nothing stored", "older token set stored", "load failure"):
            lines.append(f"  reload with {label} = {_reloaded(family.reload_token_set)}")
        lines.append(f"    get = {_outcome(lambda: family.get(_context(auth)))}")
    family, _ = provider(_Load(_tokens(auth, revision=3), _tokens(auth, "other", "refresh-9", revision=3)))
    with family:
        family.get(_context(auth))
        lines.append(f"  reload conflicting at the same revision = {_reloaded(family.reload_token_set)}")
    unusable = _tokens(auth, "unusable", None, revision=6, minutes=-1)
    spent = _tokens(auth, "spent", "refresh-1", revision=7)
    family, adapter = provider(
        _Load(_tokens(auth, minutes=-1), None, unusable, spent, newer), _reply(responses, {}, status=503)
    )
    with family:
        lines.append(f"  stopped family = {_outcome(lambda: family.get(_context(auth)))}")
        for label in ("an unusable token set", "a spent refresh token", "a newer usable token set"):
            lines.append(f"    reload with {label} = {_reloaded(family.reload_token_set)}")
        lines.append(f"    get = {_outcome(lambda: family.get(_context(auth)))} sent={adapter.refresh_tokens}")
    family, _ = provider(_Load(RuntimeError("load"), unusable))
    with family:
        lines.append(f"  failed family = {_outcome(lambda: family.get(_context(auth)))}")
        lines.append(f"    reload with an unusable token set = {_reloaded(family.reload_token_set)}")
        lines.append(f"    get = {_outcome(lambda: family.get(_context(auth)))}")
    gate = threading.Event()
    load = _Load(_tokens(auth), newer, gate=gate)
    family, _ = provider(load)
    with family:
        gate.set()
        family.invalidate(family.get(_context(auth)).version)
        gate.clear()
        load.entered.clear()
        reloading = started(family.reload_token_set, load.entered, _reloaded)
        token = watched(options)
        joiner = started(lambda: family.get(_context(auth, cancel_token=token)), token.checked, _outcome)
        lines.append(f"  reload while another reload runs = {_reloaded(family.reload_token_set)}")
        lines.append(f"    exchange needed meanwhile = {family.exchange_needed(auth.TokenVersion())}")
        gate.set()
        reloading.join(LIMIT)
        joiner.join(LIMIT)
        lines.append(f"  get joining a reload = {joiner.line} after {reloading.line}")
    gate = threading.Event()
    load = _Load(_tokens(auth), gate=gate)
    family, _ = provider(load, options=auth.OAuthProviderOptions(refresh_timeout=0.5))
    load.answers.append(newer)
    with family:
        gate.set()
        family.get(_context(auth))
        gate.clear()
        lines.append(f"  reload outliving its session = {_reloaded(family.reload_token_set)}")
        gate.set()
        lines.append(f"    get = {_outcome(lambda: family.get(_context(auth)))}")
    lines.append(f"  reload once closed = {_reloaded(family.reload_token_set)}")


async def _async_loads(auth: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    """Load and reload an asyncio family on its event loop, within the load's session."""
    load = _AsyncLoad(_tokens(auth, "stored", "refresh-5", revision=3), _tokens(auth, "reloaded", "refresh-7", revision=5))
    async with auth.AsyncRefreshTokenProvider(
        _TOKEN, client_id="c", load=load, client_auth_method="none", token_transport=AsyncAdapter(transports)
    ) as family:
        lines.append(f"  async load = {await _aoutcome(lambda: family.get(_context(auth)))}")
        lines.append(f"    reload = {_set_line(await family.reload_token_set())}")
    load = _AsyncLoad(_tokens(auth), delay=0.3)
    async with auth.AsyncRefreshTokenProvider(
        _TOKEN,
        client_id="c",
        load=load,
        client_auth_method="none",
        options=auth.OAuthProviderOptions(refresh_timeout=0.1),
        token_transport=AsyncAdapter(transports),
    ) as family:
        lines.append(f"  async load outliving its session = {await _aoutcome(lambda: family.get(_context(auth)))}")
    load = _AsyncLoad(RuntimeError("load"))
    async with auth.AsyncRefreshTokenProvider(
        _TOKEN, client_id="c", load=load, client_auth_method="none", token_transport=AsyncAdapter(transports)
    ) as family:
        lines.append(f"  async load failure = {await _aoutcome(lambda: family.get(_context(auth)))}")
    load = _AsyncLoad(_tokens(auth, minutes=-1), None)
    adapter = AsyncAdapter(transports, AsyncResponse(responses, 200, json.dumps(_ROTATED).encode()))
    async with auth.AsyncRefreshTokenProvider(
        _TOKEN, client_id="c", load=load, client_auth_method="none", token_transport=adapter
    ) as family:
        lines.append(f"  async stored token set needing a refresh = {await _aoutcome(lambda: family.get(_context(auth)))}")
    rejected = AsyncResponse(responses, 400, json.dumps({"error": "invalid_grant"}).encode())
    for label, held, replies in (
        ("async load before a refresh outliving its session", 1, ()),
        ("async invalid grant reload outliving its session", 2, (rejected,)),
    ):
        load = _AsyncLoad(None, None, None, delay=1.0, held=held)
        adapter = AsyncAdapter(transports, *replies)
        async with auth.AsyncRefreshTokenProvider(
            _TOKEN,
            client_id="c",
            token_set=_tokens(auth),
            load=load,
            client_auth_method="none",
            options=auth.OAuthProviderOptions(refresh_timeout=0.5),
            token_transport=adapter,
        ) as family:
            await family.invalidate((await family.get(_context(auth))).version)
            lines.append(f"  {label} = {await _aoutcome(lambda family=family: family.get(_context(auth)))}")


def _refresh_loads(auth: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    """Read the persisted token set right before each refresh, and again once the endpoint answers invalid_grant."""
    stored = _tokens(auth, "stored", "refresh-5", revision=3)
    rejected = _reply(responses, {"error": "invalid_grant"}, status=400)
    expired = _tokens(auth, refresh="refresh-5", revision=3, minutes=-1, scopes=("read",))
    rows: tuple[tuple[str, tuple[object, ...], tuple[object, ...]], ...] = (
        ("newer usable token set before a refresh", (stored,), ()),
        ("newer expired token set before a refresh", (expired, None), (_reply(responses, _ROTATED),)),
        (
            "another token set at the same revision before a refresh",
            (_tokens(auth, "other", "refresh-9", revision=2), None),
            (_reply(responses, _ROTATED),),
        ),
        ("load failure before a refresh", (RuntimeError("load"), None), (_reply(responses, _ROTATED),)),
        ("newer token set that can never serve before a refresh", (_tokens(auth, "unusable", None, revision=3, minutes=-1),), ()),
        ("invalid grant recovered by a newer usable token set", (None, stored), (rejected,)),
        ("invalid grant with nothing newer stored", (None, None), (rejected,)),
        ("invalid grant with a failing load", (None, RuntimeError("load")), (rejected,)),
        (
            "invalid grant with another token set at the same revision",
            (None, _tokens(auth, "other", "refresh-9", revision=2)),
            (rejected,),
        ),
        ("invalid grant with a newer expired token set", (None, _tokens(auth, "stored", "refresh-5", revision=3, minutes=-1)), (rejected,)),
        ("invalid grant with a newer token set bringing back the rejected refresh token", (None, _tokens(auth, "stored", "refresh-1", revision=3)), (rejected,)),
        ("invalid grant with an interrupted load", (None, KeyboardInterrupt()), (rejected,)),
    )
    for label, answers, replies in rows:
        load = _Load(None, *answers)
        adapter = _Sent(transports, *replies)
        with auth.RefreshTokenProvider(
            _TOKEN,
            client_id="c",
            token_set=_tokens(auth, revision=2),
            load=load,
            client_auth_method="none",
            token_transport=adapter,
        ) as family:
            family.invalidate(family.get(_context(auth)).version)
            lines.append(f"  {label} = {_outcome(lambda family=family: family.get(_context(auth)))}")
            lines.append(
                f"    later get = {_outcome(lambda family=family: family.get(_context(auth)))}"
                f" sent={adapter.refresh_tokens} purposes={[context.purpose for context in load.contexts]}"
            )
            if label == "newer expired token set before a refresh":
                lines.append(f"    reload = {_reloaded(family.reload_token_set)}")
    load = _Load(None, None, _tokens(auth, "spent", "refresh-1", revision=5))
    adapter = _Sent(
        transports,
        _reply(responses, _ROTATED),
        _reply(responses, {**_ROTATED, "access_token": "access-3", "refresh_token": "refresh-3"}),
    )
    with auth.RefreshTokenProvider(
        _TOKEN, client_id="c", token_set=_tokens(auth), load=load, client_auth_method="none", token_transport=adapter
    ) as family:
        family.invalidate(family.get(_context(auth)).version)
        family.invalidate(family.get(_context(auth)).version)
        lines.append(
            f"  newer token set bringing back a spent refresh token = {_outcome(lambda: family.get(_context(auth)))}"
            f" sent={adapter.refresh_tokens}"
        )
    gate = threading.Event()
    load = _Load(None, _tokens(auth, "stored", "refresh-5", revision=3, minutes=-1), None, gate=gate, held=1)
    adapter = _Sent(transports, _reply(responses, _ROTATED))
    with auth.RefreshTokenProvider(
        _TOKEN,
        client_id="c",
        token_set=_tokens(auth),
        load=load,
        client_auth_method="none",
        options=auth.OAuthProviderOptions(refresh_timeout=0.5),
        token_transport=adapter,
    ) as family:
        family.invalidate(family.get(_context(auth)).version)
        lines.append(f"  load before a refresh outliving its session = {_outcome(lambda: family.get(_context(auth)))}")
        gate.set()
        lines.append(f"    later get = {_outcome(lambda: family.get(_context(auth)))} sent={adapter.refresh_tokens}")
    gate = threading.Event()
    load = _Load(None, None, stored)
    adapter = _Sent(transports, _HeldClose(responses, 400, {"error": "invalid_grant"}, gate))
    with auth.RefreshTokenProvider(
        _TOKEN,
        client_id="c",
        token_set=_tokens(auth, revision=2),
        load=load,
        client_auth_method="none",
        options=auth.OAuthProviderOptions(refresh_timeout=0.5),
        token_transport=adapter,
    ) as family:
        family.invalidate(family.get(_context(auth)).version)
        lines.append(f"  invalid grant read once the refresh ended = {_outcome(lambda: family.get(_context(auth)))}")
        gate.set()
        lines.append(
            f"    later get = {_outcome(lambda: family.get(_context(auth)))} sent={adapter.refresh_tokens}"
            f" purposes={[context.purpose for context in load.contexts]}"
        )
    gate = threading.Event()
    load = _Load(None, None, stored, gate=gate, held=2)
    adapter = _Sent(transports, rejected)
    with auth.RefreshTokenProvider(
        _TOKEN,
        client_id="c",
        token_set=_tokens(auth),
        load=load,
        client_auth_method="none",
        options=auth.OAuthProviderOptions(refresh_timeout=0.5),
        token_transport=adapter,
    ) as family:
        family.invalidate(family.get(_context(auth)).version)
        lines.append(f"  invalid grant reload outliving its session = {_outcome(lambda: family.get(_context(auth)))}")
        gate.set()
        lines.append(f"    later get = {_outcome(lambda: family.get(_context(auth)))} sent={adapter.refresh_tokens}")


def _load_budget(package: ModuleType, auth: ModuleType, options: ModuleType, transports: ModuleType, lines: list[str]) -> None:
    """Load without an exchange of the call's budget; a refresh the loaded token set needs still pays one.

    A refresh that finds a newer usable stored token set sends nothing, yet keeps the exchange it charged.
    """
    exchange = Exchange(lines)
    ok, rejected = raw_response(200, b"ok", "application/octet-stream"), raw_response(401, b"no", "text/plain", **_REJECTED)
    exchange.respond(rejected, ok)
    family = auth.RefreshTokenProvider(
        _TOKEN,
        client_id="c",
        token_set=_tokens(auth),
        load=_Load(None, _tokens(auth, "stored", "refresh-5", revision=3)),
        client_auth_method="none",
        token_transport=Adapter(transports),
    )
    with (
        family,
        exchange.client() as native,
        package.Client(
            http_client=native,
            options=options.ClientOptions(
                auth=auth.AuthConfig({"oauth": family}), retry=options.RetryOptions(initial_delay=0)
            ),
        ) as api,
    ):
        response = api.auth.with_response.oauth_empty()
        info = response.info
        lines.append(
            f"  call recovering with a newer stored token set = {info.status_code}"
            f" exchanges={info.auth_exchange_count} budget={info.auth_exchange_budget_used}"
        )
    for label, stored in (
        ("a usable", _tokens(auth, "stored", "refresh-5", revision=3)),
        ("an expired", _tokens(auth, "stored", "refresh-5", revision=3, minutes=-1)),
    ):
        exchange = Exchange(lines)
        exchange.respond(raw_response(200, b"ok", "application/octet-stream"))
        family = auth.RefreshTokenProvider(
            _TOKEN, client_id="c", load=_Load(stored), client_auth_method="none", token_transport=Adapter(transports)
        )
        with (
            family,
            exchange.client() as native,
            package.Client(
                http_client=native,
                options=options.ClientOptions(auth=auth.AuthConfig({"oauth": family}, max_token_exchanges=0)),
            ) as api,
        ):
            record(lines, f"call with no exchange left and {label} stored token", api.auth.with_response.oauth_empty)


def oauth_refresh_load(package: ModuleType, lines: list[str]) -> None:
    """Exercise the initial load and explicit reloads of refresh token families in both execution modes."""
    auth, options, transports, responses = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("auth", "options", "transports", "responses")
    )
    _load_configuration(auth, transports, lines)
    _loads(auth, options, transports, responses, lines)
    _reloads(auth, options, transports, responses, lines)
    _refresh_loads(auth, transports, responses, lines)
    _load_budget(package, auth, options, transports, lines)

    async def flows() -> None:
        await _async_loads(auth, transports, responses, lines)

    run(flows)
