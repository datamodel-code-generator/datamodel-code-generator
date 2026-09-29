"""Acquire client credentials tokens over TLS, through generated clients, and from injected transports and secrets."""

from __future__ import annotations

import asyncio
import contextvars
import importlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Final
from unittest.mock import patch

from tests.data.python.client_oauth import (
    Adapter,
    AsyncAdapter,
    AsyncResponse,
    AsyncSecret,
    Response,
    Secret,
    closed_port,
    failure_line,
    json_reply,
    stop,
)
from tests.data.python.client_runtime import Exchange, describe, raw_response, record, run
from tests.data.python.fixture_server import _contexts

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_TOKEN: Final = "https://auth.example.com/token"
_ISSUED: Final = {"access_token": "access-1", "token_type": "Bearer", "expires_in": 3600}
_REJECTED: Final = {"WWW-Authenticate": 'Bearer error="invalid_token"'}
_CALLER: Final = contextvars.ContextVar("caller", default="unset")


def _context(auth: ModuleType, *, audience: str | None = None) -> Any:
    return auth.CredentialContext(
        scheme="oauth",
        required_scopes=(),
        audience=audience,
        origin="https://api.example.com",
        deadline=None,
        cancel_token=None,
    )


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


def _reply(responses: ModuleType, payload: object) -> Response:
    return Response(responses, 200, json.dumps(payload).encode())


class _Once(Secret):
    """A client secret provider whose first lookup alone fails or is slow."""

    def get(self, context: object) -> object:
        try:
            return super().get(context)
        finally:
            self.failure, self.delay = None, 0


class _Seen(AsyncSecret):
    """An asyncio client secret provider recording the caller context variable its lookups see."""

    def __init__(self, material: object) -> None:
        super().__init__(material)
        self.seen: list[str] = []

    async def get(self, context: object) -> object:
        self.seen.append(_CALLER.get())
        return await super().get(context)


class _Stubborn(AsyncSecret):
    """An asyncio client secret provider whose first lookup ignores its cancellation and ends only once released."""

    def __init__(self, material: object) -> None:
        super().__init__(material)
        self.release = asyncio.Event()
        self.lookups = 0

    async def get(self, context: object) -> object:
        self.lookups += 1
        if self.lookups == 1:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                await self.release.wait()
        return await super().get(context)


class _ClosingFailure(Adapter):
    def close(self) -> None:
        super().close()
        raise RuntimeError


class _GatedClose(Adapter):
    """An owned transport whose close waits until released."""

    def __init__(self, transports: ModuleType) -> None:
        super().__init__(transports)
        self.closing = threading.Event()
        self.release = threading.Event()

    def close(self) -> None:
        self.closing.set()
        self.release.wait(10)
        super().close()


class _AsyncClosingFailure(AsyncAdapter):
    async def aclose(self) -> None:
        await super().aclose()
        raise RuntimeError


def _foreign(errors: ModuleType) -> BaseException:
    class ForeignTimeoutError(errors.AuthTimeoutError):
        def __init__(self, state: str) -> None:
            super().__init__(effective_timeout=1.0, timeout_kind="provider", state=state)

    return ForeignTimeoutError("UNCERTAIN")


def _configuration(auth: ModuleType, transports: ModuleType, lines: list[str]) -> None:
    """Refuse invalid endpoints, client authentication, scopes, audiences, and options without threads or I/O."""
    provider, async_provider = auth.ClientCredentialsProvider, auth.AsyncClientCredentialsProvider
    secret = auth.StaticCredentialProvider(auth.ApiKeyCredential("secret"))
    async_secret = auth.AsyncStaticCredentialProvider(auth.ApiKeyCredential("secret"))
    cases: tuple[tuple[str, Callable[[], object]], ...] = (
        ("token url type", lambda: provider(None, client_id="c", client_secret=secret)),
        ("insecure token url", lambda: provider("http://auth.example.com/token", client_id="c", client_secret=secret)),
        ("public client", lambda: provider(_TOKEN, client_id="c", client_secret=secret, client_auth_method="none")),
        (
            "unknown client authentication",
            lambda: provider(_TOKEN, client_id="c", client_secret=secret, client_auth_method="private_key_jwt"),
        ),
        ("client id type", lambda: provider(_TOKEN, client_id=1, client_secret=secret)),
        ("missing secret", lambda: provider(_TOKEN, client_id="c", client_secret=None)),
        ("async secret", lambda: provider(_TOKEN, client_id="c", client_secret=AsyncSecret())),
        ("sync secret of an async provider", lambda: async_provider(_TOKEN, client_id="c", client_secret=secret)),
        (
            "sync transport of an async provider",
            lambda: async_provider(_TOKEN, client_id="c", client_secret=async_secret, token_transport=Adapter(transports)),
        ),
        ("lone scope string", lambda: provider(_TOKEN, client_id="c", client_secret=secret, scopes="read")),
        ("scope with a space", lambda: provider(_TOKEN, client_id="c", client_secret=secret, scopes=("a b",))),
        ("empty audience", lambda: provider(_TOKEN, client_id="c", client_secret=secret, audience="")),
        ("audience type", lambda: provider(_TOKEN, client_id="c", client_secret=secret, audience=1)),
        ("options type", lambda: provider(_TOKEN, client_id="c", client_secret=secret, options=1)),
    )
    for label, call in cases:
        lines.append(f"  {label} = {_outcome(call)}")
    before = _workers()
    with provider(_TOKEN, client_id="c", client_secret=secret, token_transport=Adapter(transports)) as idle:
        lines.append(f"  workers after construction = {_workers() - before}")
        for label, call in (
            ("context type", lambda: idle.get(None)),
            ("audience of another resource", lambda: idle.get(_context(auth, audience="api"))),
            ("invalidate with another type", lambda: idle.invalidate("version")),
            ("invalidate an unknown version", lambda: idle.invalidate(auth.TokenVersion())),
            ("snapshot of an unknown id", lambda: idle.refresh_snapshot("unknown")),
            ("snapshot of a non-string id", lambda: idle.refresh_snapshot(None)),
        ):
            lines.append(f"  {label} = {_outcome(call)}")
    lines.append(f"  workers after close = {_workers() - before}")
    for label, call in (
        ("get after close", lambda: idle.get(_context(auth))),
        ("refresh after close", lambda: idle.refresh(_context(auth))),
        ("invalidate after close", lambda: idle.invalidate(auth.TokenVersion())),
        ("close again", idle.close),
    ):
        lines.append(f"  {label} = {_outcome(call)}")


def _wire(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> int:
    """Acquire over TLS with each client authentication, then serve, renew, invalidate, and recover the token."""
    exchange = Exchange(lines)
    port = exchange.port()
    token_url = f"https://localhost:{port}/token"
    oauth = auth.OAuthProviderOptions(transport=options.TransportOptions(ssl_context=_contexts()[1]))
    secret = auth.StaticCredentialProvider(auth.ApiKeyCredential("se:cr et"))

    def provider(method: str = "client_secret_basic", **arguments: object) -> Any:
        return auth.ClientCredentialsProvider(
            token_url, client_id="client id", client_secret=secret, client_auth_method=method, options=oauth, **arguments
        )

    exchange.respond(
        json_reply(200, _ISSUED),
        json_reply(200, {**_ISSUED, "access_token": "access-2", "scope": "read"}),
        json_reply(200, {**_ISSUED, "access_token": "access-3"}),
    )
    with provider(scopes=("write", "read", "read"), audience="api") as shared:
        first = shared.get(_context(auth))
        lines.append(f"  basic = {_material(first)}")
        lines.append(f"    cached for the configured audience = {shared.get(_context(auth, audience='api')) is first}")
        refreshed = shared.refresh(_context(auth))
        lines.append(f"    refresh = {_material(refreshed)} new version = {refreshed.version is not first.version}")
        shared.invalidate(first.version)
        lines.append(f"    stale invalidation keeps the token = {shared.get(_context(auth)) is refreshed}")
        shared.invalidate(refreshed.version)
        lines.append(f"    get after invalidation = {_outcome(lambda: shared.get(_context(auth)))}")
        lines.append(f"    audience of another resource = {_outcome(lambda: shared.get(_context(auth, audience='other')))}")
    exchange.respond(json_reply(200, {"access_token": "lasting", "token_type": "bearer"}))
    with provider("client_secret_post") as lasting:
        first = lasting.get(_context(auth))
        lines.append(f"  post = {_material(first)}")
        lines.append(f"    cached without expiry = {lasting.get(_context(auth)) is first}")
    failures: tuple[tuple[str, Callable[..., Any]], ...] = (
        ("rejected scope", json_reply(400, {"error": "invalid_scope"})),
        ("unknown error code", json_reply(400, {"error": "try_later"})),
        ("rejected client", json_reply(401, {"error": "invalid_client"})),
        ("unauthorized without invalid_client", json_reply(401, {"error": "invalid_grant"})),
        ("unavailable", json_reply(503, b"")),
        ("error without JSON", json_reply(400, b"{")),
        ("success without JSON", json_reply(200, b"<html>")),
        ("missing access token", json_reply(200, {"token_type": "Bearer"})),
        ("zero lifetime", json_reply(200, {**_ISSUED, "expires_in": 0})),
        ("empty scope", json_reply(200, {**_ISSUED, "scope": ""})),
        ("error beside success", json_reply(200, {**_ISSUED, "error": "invalid_request"})),
    )
    for label, reply in failures:
        exchange.respond(reply, json_reply(200, _ISSUED))
        with provider(scopes=("read",)) as failing:
            error = _failure(lambda failing=failing: failing.get(_context(auth)))
            lines.append(f"  {label} = {failure_line(error)}")
            lines.append(f"    snapshot = {failing.refresh_snapshot(error.refresh_id)}")
            lines.append(f"    later get = {_outcome(lambda failing=failing: failing.get(_context(auth)))}")
    refused = auth.ClientCredentialsProvider(
        f"https://localhost:{closed_port()}/token", client_id="c", client_secret=secret, options=oauth
    )
    with refused:
        error = _failure(lambda: refused.get(_context(auth)))
        lines.append(f"  refused = {failure_line(error)}")
    lines.append(f"    snapshot after close = {refused.refresh_snapshot(error.refresh_id)}")
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
    """Authenticate generated calls with a shared token, renewing it once a resource rejects it."""
    ok = raw_response(200, b"ok", "application/octet-stream")
    exchange.respond(
        json_reply(200, {**_ISSUED, "scope": "read write"}),
        ok,
        ok,
        raw_response(401, b"rejected", "application/octet-stream", **_REJECTED),
        json_reply(200, {**_ISSUED, "access_token": "access-2", "scope": "read write"}),
        ok,
        json_reply(200, {**_ISSUED, "scope": "read"}),
    )
    retry = options.RetryOptions(initial_delay=0)
    with (
        provider(scopes=("read", "write")) as shared,
        provider(scopes=("read",)) as narrow,
        exchange.client() as native,
        package.Client(
            http_client=native, options=options.ClientOptions(auth=auth.AuthConfig({"oauth": shared}), retry=retry)
        ) as api,
    ):
        record(lines, "generated call", api.auth.with_response.oauth_read)
        record(lines, "next call uses the cached token", api.auth.with_response.oauth_scopes)
        record(lines, "rejected token renewed", api.auth.with_response.oauth_read)
        narrowed = options.RequestOptions(auth=auth.AuthConfig({"oauth": narrow}))
        record(lines, "granted scope too narrow", lambda: api.auth.with_response.oauth_scopes(options=narrowed))
    exchange.respond(json_reply(200, _ISSUED), ok)
    owned = provider(scopes=("read",))
    with (
        exchange.client() as native,
        package.Client(
            http_client=native,
            options=options.ClientOptions(auth=auth.AuthConfig({"oauth": auth.OwnedCredentialProvider(owned)})),
        ) as api,
    ):
        record(lines, "owned provider", api.auth.with_response.oauth_read)
    lines.append(f"  owned provider once the client closed = {_outcome(lambda: owned.get(_context(auth)))}")


def _expiry(package: ModuleType, auth: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    """Renew a token a tenth of its lifetime, at most thirty seconds, before it expires, and age out old snapshots."""
    now = time.monotonic()
    secret = auth.StaticCredentialProvider(auth.ApiKeyCredential("s"))
    with ExitStack() as stack:
        for name in ("oauth", "refresh", "timing"):
            module = importlib.import_module(f"{package.__name__}._runtime.client.{name}")
            stack.enter_context(patch.object(module, "monotonic", lambda: now))
        for ttl in (3600, 100, 1):
            adapter = Adapter(
                transports,
                *(_reply(responses, {**_ISSUED, "access_token": f"{ttl}-{index}", "expires_in": ttl}) for index in (1, 2)),
            )
            with auth.ClientCredentialsProvider(_TOKEN, client_id="c", client_secret=secret, token_transport=adapter) as shared:
                start = now
                first = shared.get(_context(auth))
                margin = min(30.0, ttl * 0.1)
                now = start + ttl - margin - 0.001
                cached = shared.get(_context(auth)) is first
                now = start + ttl - margin
                lines.append(
                    f"  lifetime {ttl}s kept for {ttl - margin:g}s = {cached}, then {_outcome(lambda shared=shared: shared.get(_context(auth)))}"
                )
        adapter = Adapter(transports, RuntimeError("adapter"), RuntimeError("adapter"))
        with auth.ClientCredentialsProvider(_TOKEN, client_id="c", client_secret=secret, token_transport=adapter) as shared:
            old = _failure(lambda: shared.get(_context(auth))).refresh_id
            now += 200
            recent = _failure(lambda: shared.get(_context(auth))).refresh_id
            now += 101
            lines.append(
                f"  snapshots after five minutes = {shared.refresh_snapshot(old)}, recent kept = {shared.refresh_snapshot(recent) is not None}"
            )


def _faults(
    auth: ModuleType, transports: ModuleType, responses: ModuleType, errors: ModuleType, lines: list[str]
) -> None:
    """Classify transport, secret, and worker failures by what they sent, leaving nothing behind for later calls."""
    connect = errors.PhaseTimeoutError(effective_timeout=5.0, delivery_state=errors.DeliveryState.NOT_SENT, phase="connect")
    read = errors.PhaseTimeoutError(effective_timeout=15.0, delivery_state=errors.DeliveryState.MAYBE_SENT, phase="read")
    unsent = errors.TransportError(delivery_state=errors.DeliveryState.NOT_SENT, phase="connect")
    secret = auth.StaticCredentialProvider(auth.ApiKeyCredential("s"))

    def provider(*replies: object, client_secret: object = secret, total: float = 30.0, evidence: bool = False) -> Any:
        return auth.ClientCredentialsProvider(
            _TOKEN,
            client_id="c",
            client_secret=client_secret,
            options=auth.OAuthProviderOptions(refresh_timeout=total),
            token_transport=Adapter(transports, *replies, _reply(responses, _ISSUED), evidence=evidence),
        )

    for label, shared in (
        ("phase timeout before sending", provider(connect, evidence=True)),
        ("phase timeout after sending", provider(read)),
        ("connect failure proven unsent", provider(unsent, evidence=True)),
        ("adapter failure", provider(RuntimeError("adapter"))),
        ("body slower than the session", provider(Response(responses, 200, json.dumps(_ISSUED).encode(), pause=0.3), total=0.1)),
        ("interrupted send", provider(KeyboardInterrupt())),
        ("secret failure", provider(client_secret=_Once(auth.ApiKeyCredential("s"), failure=RuntimeError("secret")))),
        ("secret auth failure naming its own refresh", provider(client_secret=_Once(auth.ApiKeyCredential("s"), failure=errors.AuthTimeoutError(
            effective_timeout=1.0, timeout_kind="provider", state="UNCERTAIN", refresh_id="vault-refresh")))),
        ("secret failure of a foreign error class", provider(client_secret=_Once(auth.ApiKeyCredential("s"), failure=_foreign(errors)))),
        ("secret slower than the session", provider(client_secret=_Once(auth.ApiKeyCredential("s"), delay=0.3), total=0.1)),
        ("secret material type", provider(client_secret=Secret(auth.BasicCredential("u", "p")))),
    ):
        with shared:
            lines.append(f"  {label} = {_outcome(lambda shared=shared: shared.get(_context(auth)))}")
            lines.append(f"    later get = {_outcome(lambda shared=shared: shared.get(_context(auth)))}")
    adapter = Adapter(transports, _reply(responses, _ISSUED))
    with auth.ClientCredentialsProvider(_TOKEN, client_id="c", client_secret=secret, token_transport=adapter) as shared:
        with patch.object(threading.Thread, "start", side_effect=RuntimeError("can't start new thread")):
            lines.append(f"  worker thread refusing to start = {_outcome(lambda: shared.get(_context(auth)))}")
        lines.append(f"    later get = {_outcome(lambda: shared.get(_context(auth)))} sends={adapter.sends}")
    adapter = Adapter(transports, _reply(responses, _ISSUED))
    with auth.ClientCredentialsProvider(_TOKEN, client_id="c", client_secret=secret, token_transport=adapter) as shared:
        try:
            with patch.object(ThreadPoolExecutor, "submit", side_effect=KeyboardInterrupt):
                shared.get(_context(auth))
        except KeyboardInterrupt:
            lines.append("  interrupted while starting the job = KeyboardInterrupt")
        lines.append(f"    later get = {_outcome(lambda: shared.get(_context(auth)))} sends={adapter.sends}")
    owned = Adapter(transports, _reply(responses, _ISSUED))
    shared = auth.ClientCredentialsProvider(
        _TOKEN, client_id="c", client_secret=secret, token_transport=transports.OwnedTransportAdapter(owned)
    )
    shared.get(_context(auth))
    shared.close()
    shared.close()
    lines.append(f"  owned transport closes once = {owned.closes}")
    failing = _ClosingFailure(transports)
    shared = auth.ClientCredentialsProvider(
        _TOKEN, client_id="c", client_secret=secret, token_transport=transports.OwnedTransportAdapter(failing)
    )
    lines.append(f"  owned transport close failure = {_outcome(shared.close)}")
    lines.append(f"    close again = {_outcome(shared.close)} closes={failing.closes}")
    gated = _GatedClose(transports)
    shared = auth.ClientCredentialsProvider(
        _TOKEN, client_id="c", client_secret=secret, token_transport=transports.OwnedTransportAdapter(gated)
    )
    first = threading.Thread(target=shared.close)
    first.start()
    gated.closing.wait(10)
    second = threading.Thread(target=shared.close)
    second.start()
    second.join(0.2)
    lines.append(f"  close while another close releases the transport waits = {second.is_alive()}")
    lines.append(f"    get meanwhile = {_outcome(lambda: shared.get(_context(auth)))}")
    gated.release.set()
    first.join(10)
    second.join(10)
    lines.append(f"    both returned = {not (first.is_alive() or second.is_alive())} closes={gated.closes}")


async def _async_wire(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> int:
    """Acquire with asyncio over TLS and through a generated client, outside the callers' context."""
    exchange = Exchange(lines)
    port = exchange.port()
    oauth = auth.OAuthProviderOptions(transport=options.TransportOptions(ssl_context=_contexts()[1]))
    secret = _Seen(auth.ApiKeyCredential("async secret"))
    ok = raw_response(200, b"ok", "application/octet-stream")
    exchange.respond(
        json_reply(200, _ISSUED),
        json_reply(200, {**_ISSUED, "access_token": "access-2"}),
        json_reply(200, {**_ISSUED, "access_token": "access-3"}),
        raw_response(401, b"rejected", "application/octet-stream", **_REJECTED),
        json_reply(200, {**_ISSUED, "access_token": "access-4"}),
        ok,
    )
    async with auth.AsyncClientCredentialsProvider(
        f"https://localhost:{port}/token",
        client_id="c",
        client_secret=secret,
        client_auth_method="client_secret_post",
        scopes=("read",),
        options=oauth,
    ) as shared:
        _CALLER.set("caller")
        first = await shared.get(_context(auth))
        lines.append(f"  async post = {_material(first)}")
        lines.append(f"    cached = {await shared.get(_context(auth)) is first}")
        refreshed = await shared.refresh(_context(auth))
        lines.append(f"    refresh = {_material(refreshed)}")
        await shared.invalidate(refreshed.version)
        lines.append(f"    get after invalidation = {await _aoutcome(lambda: shared.get(_context(auth)))}")
        lines.append(f"    lookups outside the caller's context = {secret.seen}")
        async with (
            exchange.async_client() as native,
            package.AsyncClient(
                http_client=native,
                options=options.ClientOptions(
                    auth=auth.AuthConfig({"oauth": shared}), retry=options.RetryOptions(initial_delay=0)
                ),
            ) as api,
        ):
            lines.append(f"  async rejected token renewed = {describe(await api.auth.with_response.oauth_read())}")
    lines.append(f"    get after close = {await _aoutcome(lambda: shared.get(_context(auth)))}")
    stop(exchange)
    return port


async def _async_faults(auth: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]) -> None:
    """Fail an asyncio acquisition from its secret, then outlive a session by ignoring cancellation."""
    granted = AsyncResponse(responses, 200, json.dumps(_ISSUED).encode())
    async with auth.AsyncClientCredentialsProvider(
        _TOKEN,
        client_id="c",
        client_secret=AsyncSecret(failure=RuntimeError("secret")),
        token_transport=AsyncAdapter(transports),
    ) as shared:
        lines.append(f"  async secret failure = {await _aoutcome(lambda: shared.get(_context(auth)))}")
    stubborn = _Stubborn(auth.ApiKeyCredential("s"))
    async with auth.AsyncClientCredentialsProvider(
        _TOKEN,
        client_id="c",
        client_secret=stubborn,
        options=auth.OAuthProviderOptions(refresh_timeout=0.1),
        token_transport=AsyncAdapter(transports, granted),
    ) as shared:
        asyncio.get_running_loop().call_later(0.3, stubborn.release.set)
        lines.append(f"  async secret ignoring its cancellation = {await _aoutcome(lambda: shared.get(_context(auth)))}")
        lines.append(f"    later get = {await _aoutcome(lambda: shared.get(_context(auth)))}")


def oauth_client_credentials(package: ModuleType, lines: list[str]) -> None:
    """Exercise client credentials acquisition, caching, renewal, and failures in both execution modes."""
    auth, options, transports, responses, errors = (
        importlib.import_module(f"{package.__name__}.{name}")
        for name in ("auth", "options", "transports", "responses", "errors")
    )
    _configuration(auth, transports, lines)
    ports = [_wire(package, auth, options, lines)]
    _expiry(package, auth, transports, responses, lines)
    _faults(auth, transports, responses, errors, lines)

    async def flows() -> None:
        ports.append(await _async_wire(package, auth, options, lines))
        await _async_faults(auth, transports, responses, lines)

    run(flows)
    shared = auth.AsyncClientCredentialsProvider(
        _TOKEN,
        client_id="c",
        client_secret=auth.AsyncStaticCredentialProvider(auth.ApiKeyCredential("s")),
        token_transport=AsyncAdapter(transports, AsyncResponse(responses, 200, json.dumps(_ISSUED).encode())),
    )

    async def get() -> str:
        return await _aoutcome(lambda: shared.get(_context(auth)))

    async def close() -> str:
        return await _aoutcome(shared.aclose)

    lines.append(f"  first loop = {asyncio.run(get())}")
    lines.append(f"  another loop = {asyncio.run(get())}")
    lines.append(f"  close from another loop = {asyncio.run(close())}")
    for index, line in enumerate(lines):
        for port in ports:
            line = line.replace(f":{port}", ":<port>")
        lines[index] = line
