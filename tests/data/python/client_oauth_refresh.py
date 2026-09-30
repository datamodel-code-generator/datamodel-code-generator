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


def _context(auth: ModuleType, *scopes: str, audience: str | None = None, cancel_token: object = None) -> Any:
    return auth.CredentialContext(
        scheme="oauth",
        required_scopes=scopes,
        audience=audience,
        origin="https://api.example.com",
        deadline=None,
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
            _Sent(transports, Response(responses, 200, json.dumps(_ROTATED).encode(), pause=0.3), rotated),
            {"total": 0.1},
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
    with provider(adapter, total=0.1) as family:
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
        options=auth.OAuthProviderOptions(refresh_timeout=0.05),
        token_transport=blocked,
    )
    async with family:
        first = asyncio.create_task(_aoutcome(lambda: family.get(_context(auth))))
        await asyncio.sleep(0)
        time.sleep(1.2)
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
