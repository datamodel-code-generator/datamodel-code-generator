"""Refresh a token set over TLS, through generated clients, and from injected transports and secrets."""

from __future__ import annotations

import importlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import parse_qs

from tests.data.python.client_oauth import (
    LIMIT,
    Adapter,
    AsyncAdapter,
    AsyncResponse,
    AsyncSecret,
    Caller,
    Response,
    Secret,
    failure_line,
    json_reply,
    stop,
)
from tests.data.python.client_runtime import Exchange, raw_response, record, run
from tests.data.python.fixture_server import _contexts

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_TOKEN: Final = "https://auth.example.com/token"
_ROTATED: Final = {"access_token": "access-2", "token_type": "Bearer", "expires_in": 3600, "refresh_token": "refresh-2"}
_REJECTED: Final = {"WWW-Authenticate": 'Bearer error="invalid_token"'}


def _context(auth: ModuleType, *scopes: str, audience: str | None = None) -> Any:
    return auth.CredentialContext(
        scheme="oauth",
        required_scopes=scopes,
        audience=audience,
        origin="https://api.example.com",
        deadline=None,
        cancel_token=None,
    )


def _tokens(  # noqa: PLR0913
    auth: ModuleType,
    access: str = "access-1",
    refresh: str | None = "refresh-1",
    *,
    minutes: float | None = 60,
    scopes: tuple[str, ...] | None = None,
    token_type: str = "Bearer",
    audience: str | None = None,
) -> Any:
    expires_at = None if minutes is None else datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return auth.TokenSet(auth.AccessToken(access, token_type, expires_at, scopes, audience), refresh)


def _material(credential: Any) -> str:
    token = credential.token
    minutes = (
        None
        if token.expires_at is None
        else round((token.expires_at - datetime.now(timezone.utc)).total_seconds() / 60)
    )
    return f"{token.value} scopes={token.scopes} expires_in_minutes={minutes}"


def _set_line(tokens: Any) -> str:
    return f"{tokens!r} access={tokens.access_token.value} refresh={tokens.refresh_token}"


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


def _reply(responses: ModuleType, payload: object, status: int = 200) -> Response:
    return Response(responses, status, json.dumps(payload).encode())


class _Once(Secret):
    """A client secret provider whose first lookup alone fails."""

    def get(self, context: object) -> object:
        try:
            return super().get(context)
        finally:
            self.failure = None


class _Sent(Adapter):
    """An injected token transport recording the refresh token of every request it receives."""

    def __init__(self, transports: ModuleType, *replies: object, **settings: Any) -> None:
        super().__init__(transports, *replies, **settings)
        self.refresh_tokens: list[str] = []

    def send(self, request: Any, context: object) -> object:
        self.refresh_tokens.extend(parse_qs(request.body.content.decode())["refresh_token"])
        return super().send(request, context)


class _Saved:
    """An `on_token_refreshed` callback keeping every token set it receives, failing once when asked to."""

    def __init__(self, *, failure: BaseException | None = None) -> None:
        self.saved: list[str] = []
        self.failure = failure

    def __call__(self, tokens: Any) -> None:
        self.saved.append(_set_line(tokens))
        if (failure := self.failure) is not None:
            self.failure = None
            raise failure


async def _saved_function(tokens: Any) -> None:
    del tokens


async def _failing_save(tokens: Any) -> None:
    del tokens
    msg = "storage"
    raise RuntimeError(msg)


def _async_saved(saved: list[str]) -> Callable[[Any], Any]:
    async def save(tokens: Any) -> None:
        saved.append(_set_line(tokens))

    return save


def _configuration(auth: ModuleType, transports: ModuleType, lines: list[str]) -> None:
    """Refuse invalid token sets, callbacks, client authentication, scopes, and audiences without I/O."""
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
            "token for another audience",
            lambda: provider(
                _TOKEN, client_id="c", token_set=_tokens(auth, audience="other"), client_secret=secret, audience="api"
            ),
        ),
        (
            "token for an audience without one configured",
            lambda: provider(_TOKEN, client_id="c", token_set=_tokens(auth, audience="other"), client_secret=secret),
        ),
        (
            "callback that cannot be called",
            lambda: provider(_TOKEN, client_id="c", token_set=tokens, client_secret=secret, on_token_refreshed=1),
        ),
        (
            "coroutine callback of a sync provider",
            lambda: provider(
                _TOKEN, client_id="c", token_set=tokens, client_secret=secret, on_token_refreshed=_saved_function
            ),
        ),
        (
            "sync callback of an async provider",
            lambda: async_provider(
                _TOKEN, client_id="c", token_set=tokens, client_auth_method="none", on_token_refreshed=_Saved()
            ),
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
        (
            "lone scope string",
            lambda: provider(_TOKEN, client_id="c", token_set=tokens, client_secret=secret, scopes="a"),
        ),
        (
            "empty audience",
            lambda: provider(_TOKEN, client_id="c", token_set=tokens, client_secret=secret, audience=""),
        ),
        ("options type", lambda: provider(_TOKEN, client_id="c", token_set=tokens, client_secret=secret, options=1)),
    )
    for label, call in cases:
        lines.append(f"  {label} = {_outcome(call)}")
    adapter = Adapter(transports)
    with provider(_TOKEN, client_id="c", token_set=tokens, client_auth_method="none", token_transport=adapter) as idle:
        for label, call in (
            ("context type", lambda: idle.get(None)),
            ("audience of another resource", lambda: idle.get(_context(auth, audience="api"))),
            ("invalidate with another type", lambda: idle.invalidate("version")),
            ("get", lambda: idle.get(_context(auth))),
        ):
            lines.append(f"  {label} = {_outcome(call)}")
    lines.append(f"  sends before close = {adapter.sends}")
    for label, call in (("get after close", lambda: idle.get(_context(auth))), ("close again", idle.close)):
        lines.append(f"  {label} = {_outcome(call)}")


def _wire(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> int:
    """Refresh over TLS with each client authentication, handing each new token set to the callback."""
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
    saved = _Saved()
    with provider(
        _tokens(auth, minutes=-1, scopes=("read", "write")),
        scopes=("write", "read"),
        audience="api",
        on_token_refreshed=saved,
    ) as family:
        first = family.get(_context(auth))
        lines.append(f"  basic = {_material(first)}")
        lines.append(f"    cached for the configured audience = {family.get(_context(auth, audience='api')) is first}")
        refreshed = family.refresh(_context(auth, "read"))
        lines.append(f"    refresh keeping the refresh token = {_material(refreshed)}")
        family.invalidate(first.version)
        lines.append(f"    stale invalidation keeps the token = {family.get(_context(auth, 'read')) is refreshed}")
        lines.append(f"    token sets handed to the callback = {saved.saved}")
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
    failures: tuple[tuple[str, Callable[..., Any]], ...] = (
        ("invalid grant", json_reply(400, {"error": "invalid_grant"})),
        ("rejected scope", json_reply(400, {"error": "invalid_scope"})),
        ("rejected client", json_reply(401, {"error": "invalid_client"})),
        ("unauthorized without invalid_client", json_reply(401, {"error": "invalid_grant"})),
        ("unavailable", json_reply(503, b"")),
        ("success without JSON", json_reply(200, b"<html>")),
        ("missing access token", json_reply(200, {"token_type": "Bearer"})),
    )
    for label, reply in failures:
        exchange.respond(reply, json_reply(200, _ROTATED))
        with provider(_tokens(auth, minutes=-1)) as failing:
            lines.append(f"  {label} = {_outcome(lambda failing=failing: failing.get(_context(auth)))}")
            lines.append(f"    later get = {_outcome(lambda failing=failing: failing.get(_context(auth)))}")
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
    """Authenticate generated calls with the provider's token, refreshing it once a resource rejects it."""
    ok = raw_response(200, b"ok", "application/octet-stream")
    forbidden = raw_response(
        403, b"forbidden", "application/octet-stream", **{"WWW-Authenticate": 'Bearer error="insufficient_scope"'}
    )
    exchange.respond(
        ok,
        raw_response(401, b"rejected", "application/octet-stream", **_REJECTED),
        json_reply(200, {**_ROTATED, "scope": "read"}),
        ok,
        forbidden,
        forbidden,
        json_reply(200, {**_ROTATED, "scope": "read"}),
        ok,
        forbidden,
    )
    first_line = len(lines)
    retry = options.RetryOptions(initial_delay=0)
    with (
        provider(_tokens(auth, scopes=("read", "write")), scopes=("read", "write")) as family,
        provider(_tokens(auth, minutes=-1, scopes=("read",)), scopes=("read", "write")) as narrow,
        exchange.client() as native,
        package.Client(
            http_client=native, options=options.ClientOptions(auth=auth.AuthConfig({"oauth": family}), retry=retry)
        ) as api,
    ):
        record(lines, "generated call", api.auth.with_response.oauth_read)
        record(lines, "rejected token refreshed", api.auth.with_response.oauth_read)
        record(lines, "refreshed narrow grant refused by resource", api.auth.with_response.oauth_scopes)
        record(lines, "403 leaves the refreshed grant unchanged", api.auth.with_response.oauth_scopes)
        narrowed = options.RequestOptions(auth=auth.AuthConfig({"oauth": narrow}))
        record(
            lines,
            "expired narrow grant refreshed and accepted",
            lambda: api.auth.with_response.oauth_read(options=narrowed),
        )
        record(
            lines, "write operation refused by resource", lambda: api.auth.with_response.oauth_scopes(options=narrowed)
        )
        lines.append(
            f"    token_requests={sum(line.startswith('  > POST') for line in lines[first_line:])}"
            f" resource_arrivals={sum(line.startswith('  > GET') for line in lines[first_line:])}"
            f" refreshed_grants={family.get(_context(auth)).token.scopes}"
        )


def _expiry(
    package: ModuleType, auth: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    """Renew a token a tenth of its lifetime early, but serve one without a refresh token until it expires."""
    now = time.monotonic()
    timed = auth.OAuthProviderOptions(
        clock=importlib.import_module(f"{package.__name__}.options").Clock(monotonic=lambda: now)
    )
    for refresh in ("refresh-1", None):
        adapter = _Sent(transports, _reply(responses, _ROTATED))
        with auth.RefreshTokenProvider(
            _TOKEN,
            client_id="c",
            token_set=_tokens(auth, refresh=refresh, minutes=100 / 60),
            client_auth_method="none",
            options=timed,
            token_transport=adapter,
        ) as family:
            start = now
            first = family.get(_context(auth))
            now = start + 95
            lines.append(
                f"  lifetime 100s with refresh token {refresh} after 95s"
                f" = {_outcome(lambda family=family: family.get(_context(auth)))}"
                f" same={family.get(_context(auth)) is first if refresh is None else None}"
            )
            now = start + 101
            lines.append(
                f"    after 101s = {_outcome(lambda family=family: family.get(_context(auth)))}"
                f" sent={adapter.refresh_tokens}"
            )


def _faults(
    auth: ModuleType, transports: ModuleType, responses: ModuleType, errors: ModuleType, lines: list[str]
) -> None:
    """Keep the token set of a refresh that failed, so the next call sends the same refresh token again."""
    read = errors.PhaseTimeoutError(
        effective_timeout=15.0, delivery_state=errors.DeliveryState.MAYBE_SENT, phase="read"
    )
    unsent = errors.TransportError(delivery_state=errors.DeliveryState.NOT_SENT, phase="connect")
    secret = auth.StaticCredentialProvider(auth.ApiKeyCredential("s"))

    def provider(adapter: Adapter, *, client_secret: object = secret, **arguments: Any) -> Any:
        return auth.RefreshTokenProvider(
            _TOKEN,
            client_id="c",
            token_set=arguments.pop("token_set", _tokens(auth, minutes=-1)),
            client_secret=client_secret,
            token_transport=adapter,
            **arguments,
        )

    rotated = _reply(responses, _ROTATED)
    for label, adapter, settings in (
        ("connect failure proven unsent", _Sent(transports, unsent, rotated, evidence=True), {}),
        ("phase timeout after sending", _Sent(transports, read, rotated), {}),
        ("adapter failure", _Sent(transports, RuntimeError("adapter"), rotated), {}),
        (
            "secret failure",
            _Sent(transports, rotated),
            {"client_secret": _Once(auth.ApiKeyCredential("s"), failure=RuntimeError("secret"))},
        ),
    ):
        with provider(adapter, **settings) as family:
            lines.append(f"  {label} = {_outcome(lambda family=family: family.get(_context(auth)))}")
            lines.append(f"    later get = {_outcome(lambda family=family: family.get(_context(auth)))}")
            lines.append(f"    refresh tokens sent = {adapter.refresh_tokens}")
    adapter = _Sent(transports, KeyboardInterrupt(), rotated)
    with provider(adapter) as family:
        try:
            family.get(_context(auth))
        except KeyboardInterrupt:
            lines.append("  interrupted send = KeyboardInterrupt")
        lines.append(f"    later get = {_outcome(lambda: family.get(_context(auth)))} sent={adapter.refresh_tokens}")
    saved = _Saved(failure=RuntimeError("storage"))
    adapter = _Sent(transports, rotated, _reply(responses, {**_ROTATED, "refresh_token": "refresh-3"}))
    with provider(adapter, on_token_refreshed=saved) as family:
        lines.append(f"  callback failure = {_outcome(lambda: family.get(_context(auth)))}")
        current = family.get(_context(auth))
        lines.append(f"    refreshed token stays current = {_material(current)}")
        family.invalidate(current.version)
        lines.append(f"    next refresh = {_outcome(lambda: family.get(_context(auth)))}")
        lines.append(f"    callback calls = {saved.saved} sent={adapter.refresh_tokens}")
    gate = threading.Event()
    adapter = _Sent(transports, rotated, gate=gate)
    with provider(adapter) as family:
        first = Caller(lambda: family.get(_context(auth)), _outcome)
        first.start()
        adapter.entered.wait(LIMIT)
        second = Caller(lambda: family.get(_context(auth)), _outcome)
        second.start()
        gate.set()
        for caller in (first, second):
            caller.join(LIMIT)
        lines.append(
            f"  one refresh for concurrent callers = {first.line} / {second.line} sent={adapter.refresh_tokens}"
        )
    adapter = _Sent(transports)
    with provider(adapter, token_set=_tokens(auth, refresh=None)) as family:
        current = family.get(_context(auth))
        lines.append(f"  without a refresh token = {_material(current)}")
        family.invalidate(current.version)
        lines.append(f"    get once invalidated = {_outcome(lambda: family.get(_context(auth)))} sends={adapter.sends}")
    with provider(_Sent(transports), token_set=_tokens(auth, refresh=None)) as family:
        lines.append(f"  forced refresh without a refresh token = {_outcome(lambda: family.refresh(_context(auth)))}")
    with provider(_Sent(transports), token_set=_tokens(auth, audience="api"), audience="api") as family:
        lines.append(f"  token for the configured audience = {_outcome(lambda: family.get(_context(auth)))}")


async def _async(auth: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    """Refresh an asyncio token set, hand it to a coroutine callback, and require reauthorization on invalid_grant."""
    adapter = AsyncAdapter(
        transports,
        AsyncResponse(responses, 200, json.dumps(_ROTATED).encode()),
        AsyncResponse(responses, 400, json.dumps({"error": "invalid_grant"}).encode()),
    )
    saved: list[str] = []
    family = auth.AsyncRefreshTokenProvider(
        _TOKEN,
        client_id="c",
        token_set=_tokens(auth, minutes=-1),
        client_auth_method="none",
        on_token_refreshed=_async_saved(saved),
        token_transport=adapter,
    )
    async with family:
        first = await family.get(_context(auth))
        lines.append(f"  async refresh = {_material(first)}")
        lines.append(f"    cached = {await family.get(_context(auth)) is first}")
        await family.invalidate(first.version)
        lines.append(f"    invalid grant = {await _aoutcome(lambda: family.refresh(_context(auth)))}")
        lines.append(f"    callback calls = {saved} sends={adapter.sends}")
    lines.append(f"    get after close = {await _aoutcome(lambda: family.get(_context(auth)))}")
    async with auth.AsyncRefreshTokenProvider(
        _TOKEN,
        client_id="c",
        token_set=_tokens(auth, minutes=-1),
        client_auth_method="none",
        on_token_refreshed=_failing_save,
        token_transport=AsyncAdapter(transports, AsyncResponse(responses, 200, json.dumps(_ROTATED).encode())),
    ) as failing:
        lines.append(f"  async callback failure = {await _aoutcome(lambda: failing.get(_context(auth)))}")
        lines.append(f"    refreshed token stays current = {await _aoutcome(lambda: failing.get(_context(auth)))}")


def oauth_refresh(package: ModuleType, lines: list[str]) -> None:
    """Exercise token set refreshes, the refresh callback, and failures in both execution modes."""
    auth, options, transports, responses, errors = (
        importlib.import_module(f"{package.__name__}.{name}")
        for name in ("auth", "options", "transports", "responses", "errors")
    )
    _configuration(auth, transports, lines)
    port = _wire(package, auth, options, lines)
    _expiry(package, auth, transports, responses, lines)
    _faults(auth, transports, responses, errors, lines)

    async def flows() -> None:
        await _async(auth, transports, responses, lines)

    run(flows)
    for index, line in enumerate(lines):
        lines[index] = line.replace(f":{port}", ":<port>")
