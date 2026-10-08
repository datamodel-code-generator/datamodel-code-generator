"""Acquire client credentials tokens over TLS, through generated clients, and from scripted transports and secrets."""

from __future__ import annotations

import asyncio
import importlib
import json
import ssl
import sys
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Final
from unittest.mock import patch

import httpx2
import pytest

from tests.data.python.client_oauth import (
    LIMIT,
    AsyncSecret,
    Caller,
    Late,
    Response,
    Script,
    Secret,
    closed_port,
    delayed,
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


def _context(auth: ModuleType, *, audience: str | None = None, deadline: object = None) -> Any:
    return auth.CredentialContext(
        scheme="oauth",
        required_scopes=(),
        audience=audience,
        origin="https://api.example.com",
        deadline=deadline,
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


@pytest.mark.abnormal_path("C JSON stack exhaustion cannot be reproduced safely and portably across interpreters.")
def _exhausted_parser(call: Callable[[], object]) -> str:
    """Exercise a token response whose external JSON decoder exhausts its stack."""
    with patch.object(json, "loads", side_effect=RecursionError):
        return _outcome(call)


def _reply(payload: object) -> Response:
    return Response(200, json.dumps(payload).encode())


class _Once(Secret):
    """A client secret provider whose first lookup alone fails or is slow."""

    def get(self, context: object) -> object:
        try:
            return super().get(context)
        finally:
            self.failure, self.delay = None, 0


class _Pending:
    """A synchronous secret provider returning a coroutine instead of material."""

    def get(self, context: object) -> object:
        del context

        async def material() -> None:
            pass

        return material()


class _Closing:
    """A client secret provider during whose lookup the provider is closed."""

    def __init__(self, auth: ModuleType) -> None:
        self.material = auth.ApiKeyCredential("s")
        self.close: Callable[[], object] = lambda: None

    def get(self, context: object) -> object:
        del context
        self.close()
        return self.material


class _AsyncClosing(_Closing):
    async def get(self, context: object) -> object:  # ty: ignore[invalid-method-override]
        del context
        await self.close()
        return self.material


def _insecure_context(*, keep_certificates: bool = False) -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    if not keep_certificates:
        context.verify_mode = ssl.CERT_NONE
    return context


def _without_h2(call: Callable[[], object]) -> object:
    with patch.dict(sys.modules, {"h2": None}):
        return call()


def _configuration(auth: ModuleType, options: ModuleType, lines: list[str]) -> None:
    """Refuse invalid endpoints, client authentication, scopes, audiences, options, and native clients without I/O."""
    provider, async_provider = auth.ClientCredentialsProvider, auth.AsyncClientCredentialsProvider
    secret = auth.StaticCredentialProvider(auth.ApiKeyCredential("secret"))
    async_secret = auth.AsyncStaticCredentialProvider(auth.ApiKeyCredential("secret"))

    def with_transport(**transport: Any) -> object:
        return provider(
            _TOKEN,
            client_id="c",
            client_secret=secret,
            options=auth.OAuthProviderOptions(transport=options.TransportOptions(**transport)),
        )

    cases: tuple[tuple[str, Callable[[], object]], ...] = (
        ("token url type", lambda: provider(None, client_id="c", client_secret=secret)),
        ("token url scheme", lambda: provider("ftp://auth.example.com", client_id="c", client_secret=secret)),
        ("token url user", lambda: provider("https://u@auth.example.com", client_id="c", client_secret=secret)),
        ("token url fragment", lambda: provider(f"{_TOKEN}#f", client_id="c", client_secret=secret)),
        ("token url syntax", lambda: provider("https://[a", client_id="c", client_secret=secret)),
        ("token url newline", lambda: provider(f"{_TOKEN}\n", client_id="c", client_secret=secret)),
        ("token url port", lambda: provider("https://t.example.com:99999/token", client_id="c", client_secret=secret)),
        ("insecure token url", lambda: provider("http://auth.example.com/token", client_id="c", client_secret=secret)),
        (
            "insecure loopback without permission",
            lambda: provider("http://127.0.0.1/token", client_id="c", client_secret=secret),
        ),
        ("public client", lambda: provider(_TOKEN, client_id="c", client_secret=secret, client_auth_method="none")),
        (
            "unknown client authentication",
            lambda: provider(_TOKEN, client_id="c", client_secret=secret, client_auth_method="private_key_jwt"),
        ),
        ("client id type", lambda: provider(_TOKEN, client_id=1, client_secret=secret)),
        ("empty client id", lambda: provider(_TOKEN, client_id="", client_secret=secret)),
        ("client id control character", lambda: provider(_TOKEN, client_id="c\n", client_secret=secret)),
        ("missing secret", lambda: provider(_TOKEN, client_id="c", client_secret=None)),
        ("async secret", lambda: provider(_TOKEN, client_id="c", client_secret=AsyncSecret())),
        ("secret without get", lambda: provider(_TOKEN, client_id="c", client_secret=object())),
        ("sync secret of an async provider", lambda: async_provider(_TOKEN, client_id="c", client_secret=secret)),
        (
            "sync client of an async provider",
            lambda: async_provider(_TOKEN, client_id="c", client_secret=async_secret, http_client=Script().client()),
        ),
        (
            "async client",
            lambda: provider(_TOKEN, client_id="c", client_secret=secret, http_client=Script().async_client()),
        ),
        (
            "settings beside an injected client",
            lambda: provider(
                _TOKEN,
                client_id="c",
                client_secret=secret,
                options=auth.OAuthProviderOptions(transport=options.TransportOptions(http2=True)),
                http_client=Script().client(),
            ),
        ),
        ("unverified tls", lambda: with_transport(verify=False)),
        ("unverified tls context", lambda: with_transport(ssl_context=_insecure_context())),
        (
            "tls context without hostname check",
            lambda: with_transport(ssl_context=_insecure_context(keep_certificates=True)),
        ),
        ("http2 without its extra", lambda: _without_h2(lambda: with_transport(http2=True))),
        ("lone scope string", lambda: provider(_TOKEN, client_id="c", client_secret=secret, scopes="read")),
        ("scope with a space", lambda: provider(_TOKEN, client_id="c", client_secret=secret, scopes=("a b",))),
        ("empty audience", lambda: provider(_TOKEN, client_id="c", client_secret=secret, audience="")),
        ("audience type", lambda: provider(_TOKEN, client_id="c", client_secret=secret, audience=1)),
        ("options type", lambda: provider(_TOKEN, client_id="c", client_secret=secret, options=1)),
        ("zero refresh timeout", lambda: auth.OAuthProviderOptions(refresh_timeout=0)),
        ("boolean refresh timeout", lambda: auth.OAuthProviderOptions(refresh_timeout=True)),
        ("infinite refresh timeout", lambda: auth.OAuthProviderOptions(refresh_timeout=float("inf"))),
        ("overflowing refresh timeout", lambda: auth.OAuthProviderOptions(refresh_timeout=10**400)),
        ("phase timeout type", lambda: auth.OAuthProviderOptions(phase_timeout={"read": 1})),
        ("disabled phase", lambda: auth.OAuthProviderOptions(phase_timeout=options.TimeoutOptions(read=None))),
        ("loopback flag type", lambda: auth.OAuthProviderOptions(allow_insecure_loopback=1)),
        ("transport type", lambda: auth.OAuthProviderOptions(transport={})),
        ("clock type", lambda: auth.OAuthProviderOptions(clock=object())),
        ("token set type", lambda: auth.TokenSet("access", None)),
        ("empty refresh token", lambda: auth.TokenSet(auth.AccessToken("a"), "")),
    )
    for label, call in cases:
        lines.append(f"  {label} = {_outcome(call)}")
    lines.append(
        f"  resolved phases = {auth.OAuthProviderOptions(phase_timeout=options.TimeoutOptions(read=2)).phase_timeout}"
    )
    loopback = provider(
        "http://[::1]:8080/token",
        client_id="c",
        client_secret=secret,
        options=auth.OAuthProviderOptions(allow_insecure_loopback=True),
    )
    lines.append(f"  permitted loopback = {type(loopback).__name__}")
    native = Script().client()
    with provider(_TOKEN, client_id="c", client_secret=secret, http_client=native) as idle:
        for label, call in (
            ("context type", lambda: idle.get(None)),
            ("audience of another resource", lambda: idle.get(_context(auth, audience="api"))),
            ("invalidate with another type", lambda: idle.invalidate("version")),
            ("invalidate an unknown version", lambda: idle.invalidate(auth.TokenVersion())),
        ):
            lines.append(f"  {label} = {_outcome(call)}")
    for label, call in (
        ("get after close", lambda: idle.get(_context(auth))),
        ("refresh after close", lambda: idle.refresh(_context(auth))),
        ("invalidate after close", lambda: idle.invalidate(auth.TokenVersion())),
        ("close again", idle.close),
    ):
        lines.append(f"  {label} = {_outcome(call)}")
    lines.append(f"    borrowed client closed = {native.is_closed}")


_ANSWERS: Final[tuple[tuple[str, Callable[[httpx2.Request], httpx2.Response]], ...]] = (
    ("missing access token", json_reply(200, {"token_type": "Bearer"})),
    ("empty access token", json_reply(200, {"access_token": "", "token_type": "Bearer"})),
    ("unsupported token type", json_reply(200, {"access_token": "a", "token_type": "mac"})),
    ("missing token type", json_reply(200, {"access_token": "a"})),
    ("zero lifetime", json_reply(200, {**_ISSUED, "expires_in": 0})),
    ("string expiry", json_reply(200, {**_ISSUED, "expires_in": "3600"})),
    ("boolean expiry", json_reply(200, {**_ISSUED, "expires_in": True})),
    ("overflowing expiry", json_reply(200, {**_ISSUED, "expires_in": 1e20})),
    ("infinite expiry", json_reply(200, b'{"access_token":"a","token_type":"Bearer","expires_in":1e400}')),
    ("null expiry", json_reply(200, {**_ISSUED, "expires_in": None})),
    (
        "huge integer expiry",
        json_reply(200, b'{"access_token":"a","token_type":"Bearer","expires_in":1' + b"0" * 400 + b"}"),
    ),
    ("null scope", json_reply(200, {**_ISSUED, "scope": None})),
    ("empty scope", json_reply(200, {**_ISSUED, "scope": ""})),
    ("double-spaced scope", json_reply(200, {**_ISSUED, "scope": "read  write"})),
    ("empty refresh token", json_reply(200, {**_ISSUED, "refresh_token": ""})),
    ("success with an error member", json_reply(200, {**_ISSUED, "error": "invalid_request"})),
    ("success without JSON", json_reply(200, b"not json")),
    ("success array", json_reply(200, [])),
    ("deeply nested success", json_reply(200, b"[" * 60000)),
    ("repeated member", json_reply(200, b'{"access_token":"a","access_token":"b","token_type":"Bearer"}')),
    ("NaN member", json_reply(200, b'{"access_token":"a","token_type":"Bearer","expires_in":NaN}')),
    ("undecodable body", json_reply(200, b'{"access_token":"\xff"}')),
    ("text media", json_reply(200, _ISSUED, "text/plain")),
    ("latin-1 charset", json_reply(200, _ISSUED, "application/json; charset=latin-1")),
    ("gzip coding", json_reply(200, _ISSUED, **{"Content-Encoding": "gzip"})),
    ("oversized body", json_reply(200, {**_ISSUED, "padding": "x" * 70000})),
    ("invalid request", json_reply(400, {"error": "invalid_request", "error_description": "missing field"})),
    ("rejected scope", json_reply(400, {"error": "invalid_scope", "error_uri": "https://e.example.com/doc"})),
    ("unknown error code", json_reply(400, {"error": "try_later"})),
    ("error description quote", json_reply(400, {"error": "invalid_request", "error_description": 'a"b'})),
    ("error uri space", json_reply(400, {"error": "invalid_request", "error_uri": "https://e.example.com/a b"})),
    ("error uri host syntax", json_reply(400, {"error": "invalid_request", "error_uri": "https://[e/doc"})),
    ("error uri port", json_reply(400, {"error": "invalid_request", "error_uri": "https://e.example.com:x/"})),
    ("error uri colon segment", json_reply(400, {"error": "invalid_request", "error_uri": "1a:doc"})),
    ("error uri second fragment", json_reply(400, {"error": "invalid_request", "error_uri": "/doc#a#b"})),
    ("error uri bracket", json_reply(400, {"error": "invalid_request", "error_uri": "/doc[1]"})),
    ("relative error uri", json_reply(400, {"error": "invalid_request", "error_uri": "/doc?topic=scope#top"})),
    ("error beside a token", json_reply(400, {"error": "invalid_request", "access_token": "a"})),
    ("error without JSON", json_reply(400, b"{")),
    ("oversized error", json_reply(400, {"error": "invalid_request", "error_description": "x" * 70000})),
    ("rejected client", json_reply(401, {"error": "invalid_client"})),
    ("unauthorized without invalid_client", json_reply(401, {"error": "invalid_grant"})),
    ("unauthorized without JSON", json_reply(401, b"no")),
    ("forbidden", json_reply(403, {"error": "invalid_request"})),
    ("unavailable", json_reply(503, b"")),
    ("redirect", lambda _: httpx2.Response(302, headers={"location": "https://other.example.com/token"})),
)


def _wire(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> int:
    """Acquire over TLS with each client authentication, classify every answer, then serve and renew the token."""
    exchange = Exchange(lines)
    port = exchange.port()
    token_url = f"https://localhost:{port}/token"
    oauth = auth.OAuthProviderOptions(transport=options.TransportOptions(ssl_context=_contexts()[1]))
    secret = auth.StaticCredentialProvider(auth.ApiKeyCredential("se:cr et"))

    def provider(method: str = "client_secret_basic", **arguments: object) -> Any:
        return auth.ClientCredentialsProvider(
            token_url,
            client_id="client id",
            client_secret=secret,
            client_auth_method=method,
            options=oauth,
            **arguments,
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
        lines.append(
            f"    audience of another resource = {_outcome(lambda: shared.get(_context(auth, audience='other')))}"
        )
    exchange.respond(json_reply(200, {"access_token": "lasting", "token_type": "bearer"}))
    with provider("client_secret_post") as lasting:
        first = lasting.get(_context(auth))
        lines.append(f"  post = {_material(first)}")
        lines.append(f"    cached without expiry = {lasting.get(_context(auth)) is first}")
    exchange.respond(json_reply(200, _ISSUED, 'application/json; charset="UTF-8"'))
    with provider() as charset:
        lines.append(f"  charset parameter = {_outcome(lambda: charset.get(_context(auth)))}")
    for label, reply in _ANSWERS:
        exchange.respond(reply, json_reply(200, _ISSUED))
        with provider(scopes=("read",)) as answered:
            lines.append(f"  {label} = {_outcome(lambda answered=answered: answered.get(_context(auth)))}")
            lines.append(f"    later get = {_outcome(lambda answered=answered: answered.get(_context(auth)))}")
    exchange.respond(json_reply(200, b"[" * 60000))
    with provider() as nested:
        lines.append(f"  deeply nested success with exhausted parser = {_exhausted_parser(lambda: nested.get(_context(auth)))}")
    slow = auth.OAuthProviderOptions(refresh_timeout=1.0, transport=options.TransportOptions(ssl_context=_contexts()[1]))
    exchange.respond(delayed(1.5, json_reply(200, _ISSUED)))
    with auth.ClientCredentialsProvider(token_url, client_id="c", client_secret=secret, options=slow) as expiring:
        lines.append(f"  session expires while reading = {_outcome(lambda: expiring.get(_context(auth)))}")
    refused = auth.ClientCredentialsProvider(
        f"https://localhost:{closed_port()}/token", client_id="c", client_secret=secret, options=oauth
    )
    with refused:
        lines.append(f"  refused = {_outcome(lambda: refused.get(_context(auth)))}")
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
        ok,
        raw_response(
            403, b"forbidden", "application/octet-stream", **{"WWW-Authenticate": 'Bearer error="insufficient_scope"'}
        ),
        raw_response(403, b"forbidden", "application/octet-stream"),
    )
    first_line = len(lines)
    retry = options.RetryOptions(initial_delay=0)
    with (
        provider(scopes=("read", "write")) as shared,
        provider(scopes=("read", "write")) as narrow,
        exchange.client() as native,
        package.Client(
            http_client=native, options=options.ClientOptions(auth=auth.AuthConfig({"oauth": shared}), retry=retry)
        ) as api,
    ):
        record(lines, "generated call", api.auth.with_response.oauth_read)
        record(lines, "next call uses the cached token", api.auth.with_response.oauth_scopes)
        record(lines, "rejected token renewed", api.auth.with_response.oauth_read)
        narrowed = options.RequestOptions(auth=auth.AuthConfig({"oauth": narrow}))
        record(lines, "narrow grant accepted by resource", lambda: api.auth.with_response.oauth_read(options=narrowed))
        record(lines, "narrow grant refused by resource", lambda: api.auth.with_response.oauth_scopes(options=narrowed))
        record(
            lines, "403 did not renew or expand grants", lambda: api.auth.with_response.oauth_scopes(options=narrowed)
        )
        lines.append(
            f"    token_requests={sum(line.startswith('  > POST') for line in lines[first_line:])}"
            f" resource_arrivals={sum(line.startswith('  > GET') for line in lines[first_line:])}"
            f" narrow_grants={narrow.get(_context(auth)).token.scopes}"
        )
    exchange.respond(json_reply(200, _ISSUED), ok)
    with provider(scopes=("read",)) as kept:
        with (
            exchange.client() as native,
            package.Client(
                http_client=native, options=options.ClientOptions(auth=auth.AuthConfig({"oauth": kept}))
            ) as api,
        ):
            record(lines, "provider of a closed client", api.auth.with_response.oauth_read)
        lines.append(f"    provider once the client closed = {_outcome(lambda: kept.get(_context(auth)))}")


def _expiry(package: ModuleType, auth: ModuleType, lines: list[str]) -> None:
    """Renew a token inline a tenth of its lifetime, at most thirty seconds, before it expires."""
    now = time.monotonic()
    secret = auth.StaticCredentialProvider(auth.ApiKeyCredential("s"))
    clock = importlib.import_module(f"{package.__name__}.options").Clock(monotonic=lambda: now)
    timed = auth.OAuthProviderOptions(clock=clock)
    for ttl in (3600, 100, 1):
        script = Script(*(_reply({**_ISSUED, "access_token": f"{ttl}-{index}", "expires_in": ttl}) for index in (1, 2)))
        with auth.ClientCredentialsProvider(
            _TOKEN, client_id="c", client_secret=secret, options=timed, http_client=script.client()
        ) as shared:
            start = now
            first = shared.get(_context(auth))
            margin = min(30.0, ttl * 0.1)
            now = start + ttl - margin - 0.001
            cached = shared.get(_context(auth)) is first
            now = start + ttl - margin
            lines.append(
                f"  lifetime {ttl}s kept for {ttl - margin:g}s = {cached}, then renewed to"
                f" {_outcome(lambda shared=shared: shared.get(_context(auth)))} sends={script.sends}"
            )
    script = Script(
        _reply({**_ISSUED, "access_token": "kept", "expires_in": 100}),
        Response(503),
        _reply({**_ISSUED, "access_token": "renewed", "expires_in": 100}),
        Response(503),
        Response(503),
    )
    with auth.ClientCredentialsProvider(
        _TOKEN, client_id="c", client_secret=secret, options=timed, http_client=script.client()
    ) as shared:
        start = now
        shared.get(_context(auth))
        now = start + 95
        lines.append(f"  failed renewal before the token expires = {_outcome(lambda: shared.get(_context(auth)))}")
        lines.append(f"    next call renews = {_outcome(lambda: shared.get(_context(auth)))} sends={script.sends}")
        now = start + 190
        renewed = shared.get(_context(auth))
        lines.append(f"    failed renewal keeps the token = {_material(renewed)} sends={script.sends}")
        forced = _outcome(lambda: shared.refresh(_context(auth)))
        lines.append(f"    forced refresh failure = {forced} sends={script.sends}")
    closing = _Closing(auth)
    script = Script(*(_reply({**_ISSUED, "access_token": token, "expires_in": 100}) for token in ("kept", "renewed")))
    with auth.ClientCredentialsProvider(
        _TOKEN, client_id="c", client_secret=closing, options=timed, http_client=script.client()
    ) as shared:
        start = now
        shared.get(_context(auth))
        closing.close = shared.close
        now = start + 95
        lines.append(
            f"  closed during an early renewal = {_outcome(lambda: shared.get(_context(auth)))} sends={script.sends}"
        )
        lines.append(f"    get after close = {_outcome(lambda: shared.get(_context(auth)))}")


def _single_flight(auth: ModuleType, lines: list[str]) -> None:
    """Let a caller arriving during an acquisition wait for the lock and use the token it obtained."""
    gate = threading.Event()
    script = Script(_reply(_ISSUED), gate=gate)
    with auth.ClientCredentialsProvider(
        _TOKEN,
        client_id="c",
        client_secret=auth.StaticCredentialProvider(auth.ApiKeyCredential("s")),
        http_client=script.client(),
    ) as shared:
        first = Caller(lambda: shared.get(_context(auth)), _outcome)
        first.start()
        script.entered.wait(LIMIT)
        second = Caller(lambda: shared.get(_context(auth)), _outcome)
        second.start()
        options = importlib.import_module(f"{auth.__name__.rpartition('.')[0]}.options")
        frozen = options.Clock(monotonic=lambda: 1000.0)
        bounded = _context(auth, deadline=options.Deadline.after(0, clock=frozen))
        lines.append(f"  caller whose deadline ends while waiting = {_outcome(lambda: shared.get(bounded))}")
        gate.set()
        for caller in (first, second):
            caller.join(LIMIT)
        lines.append(f"  concurrent callers = {[caller.line for caller in (first, second)]} sends={script.sends}")


def _faults(auth: ModuleType, errors: ModuleType, lines: list[str]) -> None:
    """Classify native transport and secret failures by what they sent, leaving nothing behind for later calls."""
    granted = json.dumps(_ISSUED).encode()
    secret = auth.StaticCredentialProvider(auth.ApiKeyCredential("s"))

    def provider(*replies: object, client_secret: object = secret, total: float = 30.0) -> Any:
        return auth.ClientCredentialsProvider(
            _TOKEN,
            client_id="c",
            client_secret=client_secret,
            options=auth.OAuthProviderOptions(refresh_timeout=total),
            http_client=Script(*replies, _reply(_ISSUED)).client(),
        )

    for label, shared in (
        ("phase timeout before sending", provider(httpx2.ConnectTimeout("connect"))),
        ("unsent timeout within a short session", provider(httpx2.ConnectTimeout("connect"), total=1.0)),
        ("phase timeout after sending", provider(httpx2.ReadTimeout("read"))),
        ("pool timeout", provider(httpx2.PoolTimeout("pool"))),
        ("connect failure proven unsent", provider(httpx2.ConnectError("refused"))),
        ("write failure", provider(httpx2.WriteError("reset"))),
        ("failure in an unknown phase", provider(httpx2.RemoteProtocolError("malformed"))),
        ("timeout in an unknown phase", provider(httpx2.TimeoutException("unclassified"))),
        (
            "timeout in an unknown phase after the session",
            provider(Late(1.0, httpx2.TimeoutException("unclassified")), total=0.5),
        ),
        ("transport failure", provider(RuntimeError("transport"))),
        ("body failure", provider(Response(200, b"{", failure=httpx2.ReadError("reset")))),
        ("body failure of another kind", provider(Response(200, b"{", failure=RuntimeError()))),
        ("body arriving after the session", provider(Response(200, granted, pause=1.0), total=0.5)),
        ("body ending after the session", provider(Response(200, granted, tail=1.0), total=0.5)),
        ("close failure after a complete answer", provider(Response(200, granted, close_failure=RuntimeError()))),
        ("secret failure", provider(client_secret=_Once(auth.ApiKeyCredential("s"), failure=RuntimeError("secret")))),
        (
            "secret auth failure",
            provider(
                client_secret=_Once(
                    auth.ApiKeyCredential("s"), failure=errors.ConfigurationError(reason="missing_value")
                )
            ),
        ),
        (
            "secret slower than the session",
            provider(client_secret=_Once(auth.ApiKeyCredential("s"), delay=1.0), total=0.5),
        ),
        ("secret material type", provider(client_secret=Secret(auth.BasicCredential("u", "p")))),
        ("secret outside visible ASCII", provider(client_secret=Secret(auth.ApiKeyCredential("sé")))),
        ("secret coroutine", provider(client_secret=_Pending())),
    ):
        with shared:
            lines.append(f"  {label} = {_outcome(lambda shared=shared: shared.get(_context(auth)))}")
            lines.append(f"    later get = {_outcome(lambda shared=shared: shared.get(_context(auth)))}")
    with provider(KeyboardInterrupt()) as shared:
        try:
            shared.get(_context(auth))
        except KeyboardInterrupt:
            lines.append("  interrupted send = KeyboardInterrupt")
        lines.append(f"    later get = {_outcome(lambda: shared.get(_context(auth)))}")
    options = importlib.import_module(f"{auth.__name__.rpartition('.')[0]}.options")
    with provider() as shared:
        passed = _context(auth, deadline=options.Deadline.after(0))
        lines.append(f"  caller deadline already passed = {_outcome(lambda: shared.get(passed))}")
    script = Script(_reply(_ISSUED))
    native = script.client()
    shared = auth.ClientCredentialsProvider(_TOKEN, client_id="c", client_secret=secret, http_client=native)
    shared.get(_context(auth))
    shared.close()
    shared.close()
    lines.append(f"  borrowed client after closing twice = closed={native.is_closed} sends={script.sends}")
    native.close()
    closing = _Closing(auth)
    racing = Script(_reply(_ISSUED))
    shared = auth.ClientCredentialsProvider(_TOKEN, client_id="c", client_secret=closing, http_client=racing.client())
    closing.close = shared.close
    lines.append(f"  closed while the secret is fetched = {_outcome(lambda: shared.get(_context(auth)))}")
    lines.append(f"    nothing sent = {racing.sends == 0}")


async def _async_wire(package: ModuleType, auth: ModuleType, options: ModuleType, lines: list[str]) -> int:
    """Acquire with asyncio over TLS and through a generated client."""
    exchange = Exchange(lines)
    port = exchange.port()
    oauth = auth.OAuthProviderOptions(transport=options.TransportOptions(ssl_context=_contexts()[1]))
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
        client_secret=auth.AsyncStaticCredentialProvider(auth.ApiKeyCredential("async secret")),
        client_auth_method="client_secret_post",
        scopes=("read",),
        options=oauth,
    ) as shared:
        first = await shared.get(_context(auth))
        lines.append(f"  async post = {_material(first)}")
        lines.append(f"    cached = {await shared.get(_context(auth)) is first}")
        refreshed = await shared.refresh(_context(auth))
        lines.append(f"    refresh = {_material(refreshed)}")
        await shared.invalidate(refreshed.version)
        lines.append(f"    get after invalidation = {await _aoutcome(lambda: shared.get(_context(auth)))}")
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


async def _async_faults(auth: ModuleType, lines: list[str]) -> None:
    """Classify asyncio token requests by their answers, failures, and the session deadline."""
    granted = json.dumps(_ISSUED).encode()
    secret = auth.AsyncStaticCredentialProvider(auth.ApiKeyCredential("s"))

    def provider(script: Script, client_secret: object = secret, total: float = 30.0) -> Any:
        return auth.AsyncClientCredentialsProvider(
            _TOKEN,
            client_id="c",
            client_secret=client_secret,
            options=auth.OAuthProviderOptions(refresh_timeout=total),
            http_client=script.async_client(),
        )

    for label, replies, client_secret, total in (
        ("async scripted answer", (Response(200, granted),), secret, 30.0),
        ("async body failure", (Response(200, b"{", failure=httpx2.ReadTimeout("read")),), secret, 30.0),
        ("async oversized body", (Response(200, b"x" * 70000),), secret, 30.0),
        ("async close failure", (Response(200, granted, close_failure=RuntimeError()),), secret, 30.0),
        ("async send failure", (httpx2.ReadTimeout("read"),), secret, 30.0),
        ("async connect failure", (httpx2.ConnectError("refused"),), secret, 30.0),
        ("async secret failure", (), AsyncSecret(failure=RuntimeError("secret")), 30.0),
        ("async secret outlives the session", (), AsyncSecret(auth.ApiKeyCredential("s"), delay=5), 0.1),
        ("async body slower than the session", (Response(200, granted, pause=1.0),), secret, 0.5),
        ("async body arriving after the session", (Response(200, granted, pause=1.0, blocking=True),), secret, 0.5),
        ("async body ending after the session", (Response(200, granted, tail=1.0, blocking=True),), secret, 0.5),
    ):
        async with provider(Script(*replies), client_secret, total) as shared:
            lines.append(f"  {label} = {await _aoutcome(lambda shared=shared: shared.get(_context(auth)))}")
    options = importlib.import_module(f"{auth.__name__.rpartition('.')[0]}.options")
    async with provider(Script()) as shared:
        passed = _context(auth, deadline=options.Deadline.after(0))
        lines.append(f"  async caller deadline already passed = {await _aoutcome(lambda: shared.get(passed))}")
    async with provider(Script(Response(200, granted), hold=asyncio.Event()), total=0.1) as shared:
        lines.append(f"  async session expires while sending = {await _aoutcome(lambda: shared.get(_context(auth)))}")
    closing = _AsyncClosing(auth)
    racing = Script(Response(200, granted))
    shared = provider(racing, closing)
    closing.close = shared.aclose
    lines.append(f"  async closed while the secret is fetched = {await _aoutcome(lambda: shared.get(_context(auth)))}")
    lines.append(f"    nothing sent = {racing.sends == 0}")
    script = Script(Response(200, granted))
    async with script.async_client() as native:
        shared = auth.AsyncClientCredentialsProvider(_TOKEN, client_id="c", client_secret=secret, http_client=native)
        await shared.get(_context(auth))
        await shared.aclose()
        await shared.aclose()
        lines.append(f"  async borrowed client after closing twice = closed={native.is_closed} sends={script.sends}")


async def _async_single_flight(auth: ModuleType, lines: list[str]) -> None:
    """Let tasks arriving during an acquisition, forced or not, use the token it obtained; a cancelled one leaves."""

    def provider(script: Script) -> Any:
        return auth.AsyncClientCredentialsProvider(
            _TOKEN,
            client_id="c",
            client_secret=auth.AsyncStaticCredentialProvider(auth.ApiKeyCredential("s")),
            http_client=script.async_client(),
        )

    hold = asyncio.Event()
    script = Script(_reply(_ISSUED), _reply({**_ISSUED, "access_token": "access-2"}), hold=hold)
    async with provider(script) as shared:
        first = asyncio.create_task(_aoutcome(lambda: shared.get(_context(auth))))
        await asyncio.to_thread(script.entered.wait, LIMIT)
        joiners = [
            asyncio.create_task(_aoutcome(lambda: shared.get(_context(auth)))),
            asyncio.create_task(_aoutcome(lambda: shared.refresh(_context(auth)))),
        ]
        leaving = asyncio.create_task(shared.get(_context(auth)))
        await asyncio.sleep(0)
        leaving.cancel()
        try:
            await leaving
        except asyncio.CancelledError:
            lines.append("  async waiter cancelled = CancelledError")
        hold.set()
        lines.append(f"  async concurrent tasks = {[await task for task in (first, *joiners)]} sends={script.sends}")
        lines.append(f"    forced refresh afterwards = {await _aoutcome(lambda: shared.refresh(_context(auth)))}")
    hold = asyncio.Event()
    script = Script(_reply(_ISSUED), _reply({**_ISSUED, "access_token": "access-2"}), hold=hold)
    async with provider(script) as shared:
        cancelled = asyncio.create_task(shared.get(_context(auth)))
        await asyncio.to_thread(script.entered.wait, LIMIT)
        cancelled.cancel()
        try:
            await cancelled
        except asyncio.CancelledError:
            lines.append("  async caller cancelled during its token request = CancelledError")
        hold.set()
        lines.append(f"    next caller = {await _aoutcome(lambda: shared.get(_context(auth)))} sends={script.sends}")


async def _async_renewal(package: str, auth: ModuleType, lines: list[str]) -> None:
    """Keep an asyncio token whose early renewal failed until it expires."""
    now = time.monotonic()
    clock = importlib.import_module(f"{package}.options").Clock(monotonic=lambda: now)
    script = Script(_reply({**_ISSUED, "access_token": "kept", "expires_in": 100}), Response(503), Response(503))
    async with auth.AsyncClientCredentialsProvider(
        _TOKEN,
        client_id="c",
        client_secret=auth.AsyncStaticCredentialProvider(auth.ApiKeyCredential("s")),
        options=auth.OAuthProviderOptions(clock=clock),
        http_client=script.async_client(),
    ) as shared:
        start = now
        await shared.get(_context(auth))
        now = start + 95
        lines.append(f"  async failed renewal before the token expires = {await _aoutcome(lambda: shared.get(_context(auth)))}")
        now = start + 101
        expired = await _aoutcome(lambda: shared.get(_context(auth)))
        lines.append(f"    once the token expired = {expired} sends={script.sends}")


def _loops(auth: ModuleType, lines: list[str]) -> None:
    """Bind an asyncio provider to its first event loop, and refuse callers outside asyncio."""

    def provider() -> Any:
        return auth.AsyncClientCredentialsProvider(
            _TOKEN,
            client_id="c",
            client_secret=auth.AsyncStaticCredentialProvider(auth.ApiKeyCredential("s")),
            http_client=Script(_reply(_ISSUED)).async_client(),
        )

    shared = provider()
    unscheduled = shared.get(_context(auth))
    try:
        unscheduled.send(None)
    except Exception as error:  # noqa: BLE001
        lines.append(f"  without a running asyncio loop = {failure_line(error)}")

    async def get() -> str:
        return await _aoutcome(lambda: shared.get(_context(auth)))

    async def close() -> str:
        return await _aoutcome(shared.aclose)

    lines.append(f"  first loop = {asyncio.run(get())}")
    lines.append(f"  another loop = {asyncio.run(get())}")
    lines.append(f"  close from another loop = {asyncio.run(close())}")
    pinned: list[Any] = []

    async def construct() -> None:
        pinned.append(provider())

    asyncio.run(construct())

    async def constructed() -> str:
        return await _aoutcome(lambda: pinned[0].get(_context(auth)))

    lines.append(f"  loop other than the constructing one = {asyncio.run(constructed())}")


def oauth_client_credentials(package: ModuleType, lines: list[str]) -> None:
    """Exercise client credentials acquisition, caching, renewal, and failures in both execution modes."""
    auth, options, errors = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("auth", "options", "errors")
    )
    _configuration(auth, options, lines)
    ports = [_wire(package, auth, options, lines)]
    _expiry(package, auth, lines)
    _single_flight(auth, lines)
    _faults(auth, errors, lines)

    async def flows() -> None:
        ports.append(await _async_wire(package, auth, options, lines))
        await _async_faults(auth, lines)
        await _async_single_flight(auth, lines)
        await _async_renewal(package.__name__, auth, lines)

    run(flows)
    _loops(auth, lines)
    for index, line in enumerate(lines):
        for port in ports:
            line = line.replace(f":{port}", ":<port>")
        lines[index] = line
