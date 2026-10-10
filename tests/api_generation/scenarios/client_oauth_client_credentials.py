"""Acquire client credentials tokens through generated clients: inline, single-flight, and renewed once on rejection."""

from __future__ import annotations

import asyncio
import importlib
import threading
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.api_generation.scenarios.client_oauth import Script, failure_line, issued, token
from tests.api_generation.support.client_runtime import Exchange, failing, json_response, raw_response, record, run

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_OK: Final = raw_response(200, b"ok", "application/octet-stream")
_INVALID: Final = raw_response(401, b"", None, **{"www-authenticate": 'Bearer realm="api", error="invalid_token"'})
_SCOPE: Final = raw_response(401, b"", None, **{"www-authenticate": 'Bearer error="insufficient_scope"'})
_BARE: Final = raw_response(401)


class _Clock:
    """A clock a scenario moves forward, for the provider's token expiry."""

    def __init__(self, options: ModuleType) -> None:
        self.now = 0.0
        self.clock = options.Clock(monotonic=lambda: self.now, time=lambda: 1_700_000_000 + self.now)


def _outcome(lines: list[str], label: str, call: Callable[[], object]) -> None:
    try:
        result = call()
    except Exception as error:  # noqa: BLE001
        lines.append(f"  {label} ! {failure_line(error)}")
    else:
        lines.append(f"  {label} = {result!r}")


def _provider(auth: ModuleType, **settings: Any) -> Any:
    return auth.OauthClientCredentials(**{"client_id": "pets", "client_secret": "se:cr et", **settings})


def _acquisition(package: ModuleType, auth: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    provider = _provider(auth, scopes=("read", "write"), audience="https://api.example.com")
    with exchange.client() as http, package.Client(http_client=http, oauth=provider) as api:
        exchange.respond(issued("access-1"), _OK, _OK)
        record(lines, "first call requests a token", api.auth.oauth_read)
        record(lines, "second call shares it", api.auth.oauth_scopes)
    posted = _provider(auth, client_secret=lambda: "se:cr et", client_auth_method="client_secret_post")
    with exchange.client() as http, package.Client(http_client=http, oauth=posted) as api:
        exchange.respond(issued("access-2", expires_in=None), _OK)
        record(lines, "client secret post", api.auth.oauth_read)
    moved = _provider(auth, token_url="https://tokens.example.com/oauth/token")
    with exchange.client(follow_redirects=True) as http, package.Client(http_client=http, bearer=moved) as api:
        exchange.respond(raw_response(307, b"", None, location="https://elsewhere.example.com/token"))
        record(lines, "token url override, never redirected", api.auth.bearer)
    lines.append(f"  declared token url {auth.OauthClientCredentials.token_url}")
    for label, build in (
        ("base without token url", lambda: auth.ClientCredentials(client_id="pets", client_secret="s")),
        ("plain http token url", lambda: _provider(auth, token_url="http://auth.example.com/token")),
        ("relative token url", lambda: _provider(auth, token_url="/token")),
        ("public client", lambda: _provider(auth, client_auth_method="none")),
        ("scope string", lambda: _provider(auth, scopes="read")),
        ("empty audience", lambda: _provider(auth, audience="")),
        ("client secret of another type", lambda: _provider(auth, client_secret=7)),
        ("token client of another type", lambda: _provider(auth, http_client=object())),
    ):
        _outcome(lines, label, lambda build=build: type(build()).__name__)
    _outcome(
        lines, "loopback http token url", lambda: type(_provider(auth, token_url="http://127.0.0.1:1/token")).__name__
    )


def _rejections(package: ModuleType, auth: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    with (
        exchange.client() as http,
        package.Client(http_client=http, oauth=_provider(auth), bearer=_provider(auth)) as api,
    ):
        exchange.respond(issued("access-1"), _INVALID, issued("access-2"), _OK)
        record(lines, "invalid token renews once", api.auth.oauth_read)
        exchange.respond(_INVALID, issued("access-3"), _INVALID)
        record(lines, "a second rejection is final", api.auth.oauth_read)
        exchange.respond(_SCOPE)
        record(lines, "insufficient scope", api.auth.oauth_read)
        exchange.respond(_BARE)
        record(lines, "challenge-less 401 undeclared", api.auth.oauth_read)
        exchange.respond(issued("access-4"), _BARE, issued("access-5"), _OK)
        record(lines, "challenge-less 401 declared", api.auth.challenge_less)
        exchange.respond(_INVALID, issued("access-6"), _OK)
        record(lines, "replayable body", lambda: api.auth.unsafe_auth(body=b"payload"))
        exchange.respond(_INVALID)
        record(lines, "one-shot body", lambda: api.auth.unsafe_auth(body=iter((b"pay", b"load"))))
    with exchange.client(follow_redirects=True) as http, package.Client(http_client=http, oauth=_provider(auth)) as api:
        exchange.respond(
            issued("access-7"),
            raw_response(302, b"", None, location="https://other.example.com/elsewhere"),
            _INVALID,
        )
        record(lines, "rejection after a redirect", api.auth.oauth_read)


def _faults(package: ModuleType, auth: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    options = importlib.import_module(f"{package.__name__}.options")
    for label, reply in (
        ("rejected client", json_response(401, {"error": "invalid_client", "error_description": "se:cr et"})),
        ("unknown error code", json_response(400, {"error": "slow_down"})),
        ("server error", raw_response(503, b"down", "text/plain")),
        ("not json", raw_response(200, b"access-x", "text/plain")),
        ("another token type", json_response(200, {"access_token": "access-x", "token_type": "mac"})),
        (
            "nonpositive expiry",
            json_response(200, {"access_token": "access-x", "token_type": "Bearer", "expires_in": 0}),
        ),
        (
            "boolean expiry",
            json_response(200, {"access_token": "access-x", "token_type": "bearer", "expires_in": True}),
        ),
        ("connect failure", failing(httpx2.ConnectError)),
        ("read timeout", failing(httpx2.ReadTimeout)),
    ):
        with exchange.client() as http, package.Client(http_client=http, oauth=_provider(auth)) as api:
            exchange.respond(reply)
            _outcome(lines, label, api.auth.oauth_read)

    def secret() -> str:
        msg = "se:cr et vault down"
        raise OSError(msg)

    errors = importlib.import_module(f"{package.__name__}.errors")

    def refused() -> str:
        raise errors.ConfigurationError(field_path=("vault",), reason="missing_value")

    for label, value in (
        ("secret callable failure", secret),
        ("secret callable's own error", refused),
        ("secret callable of another type", lambda: 7),
    ):
        with (
            exchange.client() as http,
            package.Client(http_client=http, oauth=_provider(auth, client_secret=value)) as api,
        ):
            _outcome(lines, label, api.auth.oauth_read)
    with httpx2.Client() as native:
        _outcome(
            lines,
            "provider as a native auth without a token client",
            lambda: native.get("https://api.example.com/bearer", auth=_provider(auth)),
        )
    script = Script(token("access-native"))
    with exchange.client() as http, package.Client(http_client=http) as api:
        exchange.respond(_OK)
        provider = _provider(auth, http_client=script.client())
        record(
            lines,
            "provider as a call's native auth",
            lambda: api.auth.anonymous(options=options.RequestOptions(auth=provider)),
        )
        exchange.respond(_INVALID, _OK)
        script.replies.append(token("access-native-2"))
        record(
            lines,
            "native auth renews once",
            lambda: api.auth.anonymous(options=options.RequestOptions(auth=provider)),
        )
        lines.append(f"  native token requests {script.forms}")
    async_script = Script(token("access-x"))
    with (
        exchange.client() as http,
        package.Client(http_client=http, oauth=_provider(auth, http_client=async_script.async_client())) as api,
    ):
        _outcome(lines, "asyncio token client in a synchronous call", api.auth.oauth_read)


def _renewal(package: ModuleType, auth: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    clock = _Clock(importlib.import_module(f"{package.__name__}.options"))
    provider = _provider(auth, clock=clock.clock)
    with exchange.client() as http, package.Client(http_client=http, oauth=provider) as api:
        exchange.respond(issued("access-1", expires_in=100), _OK)
        record(lines, "token of a hundred seconds", api.auth.oauth_read)
        clock.now = 89.0
        exchange.respond(_OK)
        record(lines, "before its renewal is due", api.auth.oauth_read)
        clock.now = 95.0
        exchange.respond(json_response(503, {}), _OK)
        record(lines, "failed early renewal keeps the token", api.auth.oauth_read)
        clock.now = 101.0
        exchange.respond(json_response(503, {}))
        _outcome(lines, "failed renewal after expiry", api.auth.oauth_read)
        exchange.respond(issued("access-2", expires_in=100), _OK)
        record(lines, "next call acquires again", api.auth.oauth_read)


def _token_client(package: ModuleType, auth: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    script = Script(token("access-own"))
    seen: list[str] = []

    def hook(request: httpx2.Request) -> None:
        seen.append(str(request.url))

    provider = _provider(auth, http_client=script.client())
    with (
        exchange.client(event_hooks={"request": [hook]}) as http,
        package.Client(http_client=http, oauth=provider) as api,
    ):
        exchange.respond(_OK)
        record(lines, "own token client", api.auth.oauth_read)
    lines.append(f"  token requests {script.forms} resource client saw {seen}")


def _concurrent(package: ModuleType, auth: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    gate = threading.Event()

    def held(request: httpx2.Request) -> httpx2.Response:
        gate.wait(10)
        return token("access-shared")(request)

    script = Script(held)
    provider = _provider(auth, http_client=script.client())
    results: list[object] = []
    with exchange.client() as http, package.Client(http_client=http, oauth=provider) as api:
        exchange.respond(
            raw_response(200, b"one", "application/octet-stream"), raw_response(200, b"two", "application/octet-stream")
        )
        first = threading.Thread(target=lambda: results.append(api.auth.oauth_read()))
        first.start()
        script.entered.wait(10)
        second = threading.Thread(target=lambda: results.append(api.auth.oauth_read()))
        second.start()
        gate.set()
        first.join(10)
        second.join(10)
    lines.append(f"  concurrent calls results={sorted(map(repr, results))} token requests={script.sends}")


async def _aoutcome(lines: list[str], label: str, call: Callable[[], Any]) -> None:
    try:
        result = await call()
    except Exception as error:  # noqa: BLE001
        lines.append(f"  {label} ! {failure_line(error)}")
    else:
        lines.append(f"  {label} = {result!r}")


async def _async_faults(package: ModuleType, auth: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    options = importlib.import_module(f"{package.__name__}.options")
    clock = _Clock(options)
    async with (
        exchange.async_client() as http,
        package.AsyncClient(http_client=http, oauth=_provider(auth, clock=clock.clock)) as api,
    ):
        exchange.respond(issued("access-a3", expires_in=100), _OK)
        await _aoutcome(lines, "async token of a hundred seconds", api.auth.oauth_read)
        clock.now = 95.0
        exchange.respond(failing(httpx2.ConnectError), _OK)
        await _aoutcome(lines, "async failed early renewal keeps the token", api.auth.oauth_read)
        clock.now = 101.0
        exchange.respond(failing(httpx2.ConnectError))
        await _aoutcome(lines, "async failed renewal after expiry", api.auth.oauth_read)
    sync_script = Script(token("access-x"))
    provider = _provider(auth, http_client=sync_script.client())
    async with exchange.async_client() as http, package.AsyncClient(http_client=http, oauth=provider) as api:
        await _aoutcome(lines, "synchronous token client in an asyncio call", api.auth.oauth_read)
    script = Script(token("access-async-native"))
    provider = _provider(auth, http_client=script.async_client())
    async with exchange.async_client() as http, package.AsyncClient(http_client=http) as api:
        exchange.respond(_OK)
        native = options.RequestOptions(auth=provider)
        await _aoutcome(lines, "async provider as a call's native auth", lambda: api.auth.anonymous(options=native))
    lines.append(f"  async native token requests {script.forms}")


async def _async_tokens(package: ModuleType, auth: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    await _async_faults(package, auth, exchange, lines)
    async with exchange.async_client() as http, package.AsyncClient(http_client=http, oauth=_provider(auth)) as api:
        exchange.respond(issued("access-a1"), _OK, _INVALID, issued("access-a2"), _OK)
        lines.append(f"  async first = {await api.auth.oauth_read()!r}")
        lines.append(f"  async renewed = {await api.auth.oauth_read()!r}")
    script = Script(token("access-gathered"))
    provider = _provider(auth, http_client=script.async_client())
    async with exchange.async_client() as http, package.AsyncClient(http_client=http, oauth=provider) as api:
        exchange.respond(_OK, _OK)
        gathered = await asyncio.gather(api.auth.oauth_read(), api.auth.oauth_read())
        lines.append(f"  async gathered={gathered} token requests={script.sends}")
    hold = asyncio.Event()
    script = Script(token("access-after"), hold=hold)
    provider = _provider(auth, http_client=script.async_client())
    async with exchange.async_client() as http, package.AsyncClient(http_client=http, oauth=provider) as api:
        task = asyncio.create_task(api.auth.oauth_read())
        while not script.entered.is_set():
            await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            lines.append("  async cancelled while its token request was held")
        hold.set()
        exchange.respond(_OK)
        lines.append(f"  async after cancellation = {await api.auth.oauth_read()!r} token requests={script.sends}")


async def _loop_round(package: ModuleType, exchange: Exchange, provider: Any, script: Script, lines: list[str]) -> None:
    """Send two calls at once on this event loop while the token request they share is held."""
    hold = script.hold = asyncio.Event()
    script.entered.clear()
    async with exchange.async_client() as http, package.AsyncClient(http_client=http, oauth=provider) as api:
        exchange.respond(_OK, _OK)
        tasks = [asyncio.create_task(api.auth.oauth_read()) for _ in range(2)]
        await asyncio.to_thread(script.entered.wait, 30)
        hold.set()
        lines.append(f"  loop gathered={await asyncio.gather(*tasks)} token requests={script.sends}")


def _event_loops(package: ModuleType, auth: ModuleType, exchange: Exchange, lines: list[str]) -> None:
    """Reuse one provider on a second event loop once its token is due, with calls waiting on each loop."""
    clock = _Clock(importlib.import_module(f"{package.__name__}.options"))
    script = Script(token("access-loop1", expires_in=100), token("access-loop2", expires_in=100))
    provider = _provider(auth, http_client=script.async_client(), clock=clock.clock)
    run(lambda: _loop_round(package, exchange, provider, script, lines))
    clock.now = 100
    run(lambda: _loop_round(package, exchange, provider, script, lines))


def oauth_client_credentials(package: ModuleType, lines: list[str]) -> None:
    """Request, share, renew, and refuse client credentials tokens through the generated OAuth provider."""
    auth = importlib.import_module(f"{package.__name__}.auth")
    exchange = Exchange(lines)
    for title, part in (
        ("acquisition", _acquisition),
        ("rejections", _rejections),
        ("faults", _faults),
        ("renewal", _renewal),
        ("token client", _token_client),
        ("concurrency", _concurrent),
    ):
        lines.append(title)
        part(package, auth, exchange, lines)
    lines.append("async")
    run(lambda: _async_tokens(package, auth, lines))
    lines.append("event loops")
    _event_loops(package, auth, exchange, lines)
