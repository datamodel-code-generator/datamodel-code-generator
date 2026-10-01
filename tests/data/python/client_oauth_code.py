"""Run the authorization code flow against a local TLS token endpoint and injected token transports."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import json
import ssl
import sys
import threading
import time
from typing import TYPE_CHECKING, Any, Final
from unittest.mock import patch

import httpx2

from tests.data.python.client_oauth import (
    GRANTED,
    Adapter,
    AsyncAdapter,
    AsyncResponse,
    AsyncSecret,
    Late,
    Reported,
    Response,
    Secret,
    atoken_outcome,
    closed_port,
    delayed,
    failure_line,
    json_reply,
    stop,
    token_outcome,
)
from tests.data.python.client_runtime import Exchange, run
from tests.data.python.fixture_server import _contexts

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from types import ModuleType

_AUTHORIZE: Final = "https://auth.example.com/authorize"
_REDIRECT: Final = "https://app.example.com/callback"


class _Pending:
    """A synchronous provider returning a coroutine instead of material."""

    def get(self, context: object) -> object:
        del context

        async def material() -> None:
            pass

        return material()


class _Closing:
    """A client secret provider during whose lookup the flow is closed."""

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


class _Uncapable:
    """A transport that declares no capabilities."""

    def send(self, request: object, context: object) -> object:
        del request, context
        msg = "a transport without capabilities is rejected before it sends"
        raise RuntimeError(msg)

    def close(self) -> None:
        pass


class _Masks:
    """Replace the random values of authorization requests and the fixture port in recorded lines."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def request(self, request: Any) -> Any:
        verifier = request.code_verifier
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        index = len(self.values) // 3
        self.values.update({
            request.state: f"<state{index}>",
            verifier: f"<verifier{index}>",
            challenge: f"<challenge{index}>",
        })
        return request

    def apply(self, lines: list[str], ports: tuple[int, ...]) -> None:
        for index, line in enumerate(lines):
            for value, mask in self.values.items():
                line = line.replace(value, mask)
            for port in ports:
                line = line.replace(f":{port}", ":<port>")
            lines[index] = line


def _insecure_context(*, keep_certificates: bool = False) -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    if not keep_certificates:
        context.verify_mode = ssl.CERT_NONE
    return context


def _without_h2(call: Callable[[], object]) -> object:
    with patch.dict(sys.modules, {"h2": None}):
        return call()


def _configuration(auth: ModuleType, options: ModuleType, transports: ModuleType, lines: list[str]) -> None:
    """Refuse every invalid endpoint, client identity, option, transport, and token set before any I/O."""
    flow, async_flow = auth.AuthorizationCodeFlow, auth.AsyncAuthorizationCodeFlow
    token = "https://token.example.com/token"
    secret = auth.StaticCredentialProvider(auth.ApiKeyCredential("secret"))
    cases: tuple[tuple[str, Callable[[], object]], ...] = (
        ("authorization url type", lambda: flow(1, token, client_id="c", client_auth_method="none")),
        ("authorization url scheme", lambda: flow("ftp://a.example.com", token, client_id="c", client_auth_method="none")),
        ("authorization url user", lambda: flow("https://u@a.example.com", token, client_id="c", client_auth_method="none")),
        ("authorization url fragment", lambda: flow(f"{_AUTHORIZE}#f", token, client_id="c", client_auth_method="none")),
        ("authorization url syntax", lambda: flow("https://[a", token, client_id="c", client_auth_method="none")),
        ("authorization url empty fragment", lambda: flow(f"{_AUTHORIZE}#", token, client_id="c", client_auth_method="none")),
        ("authorization url newline", lambda: flow(f"{_AUTHORIZE}\n", token, client_id="c", client_auth_method="none")),
        (
            "authorization url with a flow parameter",
            lambda: flow(f"{_AUTHORIZE}?state=x", token, client_id="c", client_auth_method="none"),
        ),
        ("token url port", lambda: flow(_AUTHORIZE, "https://t.example.com:99999/token", client_id="c", client_auth_method="none")),
        ("insecure token url", lambda: flow(_AUTHORIZE, "http://token.example.com", client_id="c", client_auth_method="none")),
        (
            "insecure loopback without permission",
            lambda: flow(_AUTHORIZE, "http://127.0.0.1/token", client_id="c", client_auth_method="none"),
        ),
        ("empty client id", lambda: flow(_AUTHORIZE, token, client_id="", client_auth_method="none")),
        ("client id control character", lambda: flow(_AUTHORIZE, token, client_id="c\n", client_auth_method="none")),
        ("unknown client auth", lambda: flow(_AUTHORIZE, token, client_id="c", client_auth_method="private_key_jwt")),
        ("secret beside none", lambda: flow(_AUTHORIZE, token, client_id="c", client_auth_method="none", client_secret=secret)),
        ("missing secret", lambda: flow(_AUTHORIZE, token, client_id="c")),
        ("async secret", lambda: flow(_AUTHORIZE, token, client_id="c", client_secret=AsyncSecret())),
        ("secret without get", lambda: flow(_AUTHORIZE, token, client_id="c", client_secret=object())),
        ("sync secret of an async flow", lambda: async_flow(_AUTHORIZE, token, client_id="c", client_secret=secret)),
        ("options type", lambda: flow(_AUTHORIZE, token, client_id="c", client_auth_method="none", options={})),
        ("zero refresh timeout", lambda: auth.OAuthProviderOptions(refresh_timeout=0)),
        ("boolean refresh timeout", lambda: auth.OAuthProviderOptions(refresh_timeout=True)),
        ("infinite refresh timeout", lambda: auth.OAuthProviderOptions(refresh_timeout=float("inf"))),
        ("overflowing refresh timeout", lambda: auth.OAuthProviderOptions(refresh_timeout=10**400)),
        ("phase timeout type", lambda: auth.OAuthProviderOptions(phase_timeout={"read": 1})),
        ("disabled phase", lambda: auth.OAuthProviderOptions(phase_timeout=options.TimeoutOptions(read=None))),
        ("zero waiters", lambda: auth.OAuthProviderOptions(max_waiters=0)),
        ("boolean refreshes", lambda: auth.OAuthProviderOptions(max_concurrent_refreshes=True)),
        ("loopback flag type", lambda: auth.OAuthProviderOptions(allow_insecure_loopback=1)),
        ("transport type", lambda: auth.OAuthProviderOptions(transport={})),
        (
            "unverified tls",
            lambda: flow(
                _AUTHORIZE, token, client_id="c", client_auth_method="none",
                options=auth.OAuthProviderOptions(transport=options.TransportOptions(verify=False)),
            ),
        ),
        (
            "unverified tls context",
            lambda: flow(
                _AUTHORIZE, token, client_id="c", client_auth_method="none",
                options=auth.OAuthProviderOptions(transport=options.TransportOptions(ssl_context=_insecure_context())),
            ),
        ),
        (
            "tls context without hostname check",
            lambda: flow(
                _AUTHORIZE, token, client_id="c", client_auth_method="none",
                options=auth.OAuthProviderOptions(
                    transport=options.TransportOptions(ssl_context=_insecure_context(keep_certificates=True))
                ),
            ),
        ),
        (
            "http2 without its extra",
            lambda: _without_h2(
                lambda: flow(
                    _AUTHORIZE, token, client_id="c", client_auth_method="none",
                    options=auth.OAuthProviderOptions(transport=options.TransportOptions(http2=True)),
                )
            ),
        ),
        (
            "transport retries",
            lambda: flow(
                _AUTHORIZE, token, client_id="c", client_auth_method="none",
                options=auth.OAuthProviderOptions(transport=options.TransportOptions(retry_owner="transport")),
            ),
        ),
        (
            "settings beside injected transport",
            lambda: flow(
                _AUTHORIZE, token, client_id="c", client_auth_method="none",
                options=auth.OAuthProviderOptions(transport=options.TransportOptions(http2=True)),
                token_transport=Adapter(transports),
            ),
        ),
        (
            "async transport",
            lambda: flow(_AUTHORIZE, token, client_id="c", client_auth_method="none", token_transport=AsyncAdapter(transports)),
        ),
        (
            "sync transport of an async flow",
            lambda: async_flow(
                _AUTHORIZE, token, client_id="c", client_auth_method="none", token_transport=Adapter(transports)
            ),
        ),
        (
            "transport without send",
            lambda: flow(_AUTHORIZE, token, client_id="c", client_auth_method="none", token_transport=_Pending()),
        ),
        (
            "transport without capabilities",
            lambda: flow(_AUTHORIZE, token, client_id="c", client_auth_method="none", token_transport=_Uncapable()),
        ),
        (
            "transport retrying internally",
            lambda: flow(
                _AUTHORIZE, token, client_id="c", client_auth_method="none",
                token_transport=Adapter(transports, retries=None),
            ),
        ),
        ("token set type", lambda: auth.TokenSet("access", None)),
        ("empty refresh token", lambda: auth.TokenSet(auth.AccessToken("a"), "")),
        ("boolean revision", lambda: auth.TokenSet(auth.AccessToken("a"), None, True)),
    )
    for label, call in cases:
        lines.append(f"  {label} = {token_outcome(call)}")
    unlabeled = auth.OAuthProviderOptions(phase_timeout=options.TimeoutOptions(read=2))
    lines.append(f"  resolved phases = {unlabeled.phase_timeout}")
    loopback = auth.OAuthProviderOptions(allow_insecure_loopback=True)
    permitted_loopback = flow(
        _AUTHORIZE, "http://[::1]:8080/token", client_id="c", client_auth_method="none", options=loopback
    )
    lines.append(f"  permitted loopback = {type(permitted_loopback).__name__}")
    permitted = flow(_AUTHORIZE, token, client_id="c", client_auth_method="none")
    for label, redirect, scopes in (
        ("lone scope string", _REDIRECT, "read"),
        ("spaced scope", _REDIRECT, ("read write",)),
        ("non-ASCII scope", _REDIRECT, ("réad",)),
        ("null scopes", _REDIRECT, None),
        ("non-string scope", _REDIRECT, (1,)),
        ("non-ASCII redirect", "https://app.example.com/ré", ()),
        ("redirect type", None, ()),
        ("relative redirect", "/callback", ()),
        ("redirect fragment", f"{_REDIRECT}#top", ()),
        ("redirect space", "https://app.example.com/call back", ()),
        ("redirect syntax", "https://[app", ()),
    ):
        lines.append(f"  {label} = {token_outcome(lambda redirect=redirect, scopes=scopes: permitted.authorization_request(redirect, scopes))}")


def _wire(auth: ModuleType, options: ModuleType, lines: list[str], masks: _Masks) -> int:
    """Exchange codes over TLS with each client authentication and every classified endpoint answer."""
    exchange = Exchange(lines)
    port = exchange.port()
    token = f"https://localhost:{port}/token"
    oauth = auth.OAuthProviderOptions(transport=options.TransportOptions(ssl_context=_contexts()[1]))
    secret = auth.StaticCredentialProvider(auth.ApiKeyCredential("se:cr et"))
    for method, provider in (
        ("client_secret_basic", secret),
        ("client_secret_post", secret),
        ("none", None),
    ):
        exchange.respond(json_reply(200, GRANTED))
        with auth.AuthorizationCodeFlow(
            f"{_AUTHORIZE}?audience=api", token, client_id="client id", client_secret=provider,
            client_auth_method=method, options=oauth,
        ) as flow:
            request = masks.request(flow.authorization_request(_REDIRECT, ("write", "read", "read")))
            lines.append(f"  {method} state and verifier lengths = {len(request.state)} {len(request.code_verifier)}")
            lines.append(f"  {method} authorization url = {request.url}")
            lines.append(f"  {method} exchange = {token_outcome(lambda flow=flow, request=request: flow.exchange_code('code-1', request.state, request))}")
    replies: tuple[tuple[str, tuple[str, ...], Callable[[httpx2.Request], httpx2.Response]], ...] = (
        ("explicit scope narrows the grant", ("read", "write"), json_reply(200, {**GRANTED, "scope": "read"})),
        ("empty request keeps an empty grant", (), json_reply(200, {"access_token": "a", "token_type": "bearer"})),
        ("charset parameter", ("read",), json_reply(200, GRANTED, 'application/json; charset="UTF-8"')),
        ("missing access token", ("read",), json_reply(200, {"token_type": "Bearer"})),
        ("empty access token", ("read",), json_reply(200, {"access_token": "", "token_type": "Bearer"})),
        ("unsupported token type", ("read",), json_reply(200, {"access_token": "a", "token_type": "mac"})),
        ("missing token type", ("read",), json_reply(200, {"access_token": "a"})),
        ("zero expiry", ("read",), json_reply(200, {**GRANTED, "expires_in": 0})),
        ("string expiry", ("read",), json_reply(200, {**GRANTED, "expires_in": "3600"})),
        ("boolean expiry", ("read",), json_reply(200, {**GRANTED, "expires_in": True})),
        ("overflowing expiry", ("read",), json_reply(200, {**GRANTED, "expires_in": 1e20})),
        ("infinite expiry", ("read",), json_reply(200, b'{"access_token":"a","token_type":"Bearer","expires_in":1e400}')),
        ("null expiry", ("read",), json_reply(200, {**GRANTED, "expires_in": None})),
        (
            "integer expiry beyond a float",
            ("read",),
            json_reply(200, b'{"access_token":"a","token_type":"Bearer","expires_in":1' + b"0" * 400 + b"}"),
        ),
        ("null scope", ("read",), json_reply(200, {**GRANTED, "scope": None})),
        ("empty scope", ("read",), json_reply(200, {**GRANTED, "scope": ""})),
        ("double-spaced scope", ("read",), json_reply(200, {**GRANTED, "scope": "read  write"})),
        ("empty refresh token", ("read",), json_reply(200, {**GRANTED, "refresh_token": ""})),
        ("null refresh token", ("read",), json_reply(200, {**GRANTED, "refresh_token": None})),
        ("success with an error member", ("read",), json_reply(200, {**GRANTED, "error": "invalid_request"})),
        ("success without JSON", ("read",), json_reply(200, b"not json")),
        ("success array", ("read",), json_reply(200, [])),
        ("deeply nested success", ("read",), json_reply(200, b"[" * 60000)),
        ("repeated member", ("read",), json_reply(200, b'{"access_token":"a","access_token":"b","token_type":"Bearer"}')),
        ("NaN member", ("read",), json_reply(200, b'{"access_token":"a","token_type":"Bearer","expires_in":NaN}')),
        ("undecodable body", ("read",), json_reply(200, b'{"access_token":"\xff"}')),
        ("text media", ("read",), json_reply(200, GRANTED, "text/plain")),
        ("latin-1 charset", ("read",), json_reply(200, GRANTED, "application/json; charset=latin-1")),
        ("gzip coding", ("read",), json_reply(200, GRANTED, **{"Content-Encoding": "gzip"})),
        ("oversized body", ("read",), json_reply(200, {**GRANTED, "padding": "x" * 70000})),
        ("invalid request", ("read",), json_reply(400, {"error": "invalid_request", "error_description": "missing field"})),
        ("unauthorized client", ("read",), json_reply(400, {"error": "unauthorized_client"})),
        ("unsupported grant", ("read",), json_reply(400, {"error": "unsupported_grant_type"})),
        ("invalid scope", ("read",), json_reply(400, {"error": "invalid_scope", "error_uri": "https://e.example.com/doc"})),
        ("invalid grant", ("read",), json_reply(400, {"error": "invalid_grant"})),
        ("unknown error code", ("read",), json_reply(400, {"error": "slow_down"})),
        ("error description quote", ("read",), json_reply(400, {"error": "invalid_request", "error_description": 'a"b'})),
        ("error uri space", ("read",), json_reply(400, {"error": "invalid_request", "error_uri": "https://e.example.com/a b"})),
        ("error uri host syntax", ("read",), json_reply(400, {"error": "invalid_request", "error_uri": "https://[e/doc"})),
        ("error uri port", ("read",), json_reply(400, {"error": "invalid_request", "error_uri": "https://e.example.com:x/"})),
        ("error uri colon segment", ("read",), json_reply(400, {"error": "invalid_request", "error_uri": "1a:doc"})),
        ("error uri second fragment", ("read",), json_reply(400, {"error": "invalid_request", "error_uri": "/doc#a#b"})),
        ("error uri bracket", ("read",), json_reply(400, {"error": "invalid_request", "error_uri": "/doc[1]"})),
        ("relative error uri", ("read",), json_reply(400, {"error": "invalid_request", "error_uri": "/doc?topic=scope#top"})),
        ("error beside a token", ("read",), json_reply(400, {"error": "invalid_request", "access_token": "a"})),
        ("error without JSON", ("read",), json_reply(400, b"<html>")),
        ("deeply nested error", ("read",), json_reply(400, b"[" * 60000)),
        ("oversized error", ("read",), json_reply(400, {"error": "invalid_request", "error_description": "x" * 70000})),
        ("invalid client", ("read",), json_reply(401, {"error": "invalid_client"})),
        ("unauthorized invalid request", ("read",), json_reply(401, {"error": "invalid_request"})),
        ("unauthorized without JSON", ("read",), json_reply(401, b"no")),
        ("unauthorized oversized", ("read",), json_reply(401, b"x" * 70000)),
        ("forbidden", ("read",), json_reply(403, {"error": "invalid_request"})),
        ("rate limited", ("read",), json_reply(429, {"error": "invalid_request"})),
        ("unavailable", ("read",), json_reply(503, b"")),
        ("redirect", ("read",), lambda _: httpx2.Response(302, headers={"location": "https://other.example.com/token"})),
    )
    with auth.AuthorizationCodeFlow(_AUTHORIZE, token, client_id="c", client_auth_method="none", options=oauth) as flow:
        for label, scopes, reply in replies:
            exchange.respond(reply)
            request = masks.request(flow.authorization_request(_REDIRECT, scopes))
            lines.append(f"  {label} = {token_outcome(lambda request=request: flow.exchange_code('code', request.state, request))}")
    exchange.respond(
        json_reply(200, {"access_token": "narrow", "token_type": "Bearer"}),
        json_reply(200, {"access_token": "wide", "token_type": "Bearer"}),
    )
    with auth.AuthorizationCodeFlow(
        f"{_AUTHORIZE}?", token, client_id="c", client_auth_method="none", options=oauth
    ) as flow:
        wide = masks.request(flow.authorization_request(_REDIRECT, ("read", "write")))
        narrow = masks.request(flow.authorization_request(_REDIRECT, ("read",)))
        lines.append(f"  query after a trailing question mark = {narrow.url.split('&', 1)[0]}")
        lines.append(f"  later request first = {token_outcome(lambda: flow.exchange_code('code', narrow.state, narrow))}")
        lines.append(f"  earlier request keeps its scopes = {token_outcome(lambda: flow.exchange_code('code', wide.state, wide))}")
    slow = auth.OAuthProviderOptions(refresh_timeout=1.0, transport=options.TransportOptions(ssl_context=_contexts()[1]))
    exchange.respond(delayed(1.5, json_reply(200, GRANTED)))
    with auth.AuthorizationCodeFlow(_AUTHORIZE, token, client_id="c", client_auth_method="none", options=slow) as flow:
        request = masks.request(flow.authorization_request(_REDIRECT, ()))
        lines.append(f"  session expires while reading = {token_outcome(lambda: flow.exchange_code('code', request.state, request))}")
    refused = f"https://localhost:{closed_port()}/token"
    with auth.AuthorizationCodeFlow(_AUTHORIZE, refused, client_id="c", client_auth_method="none", options=oauth) as flow:
        request = masks.request(flow.authorization_request(_REDIRECT, ()))
        lines.append(f"  connection refused = {token_outcome(lambda: flow.exchange_code('code', request.state, request))}")
        lines.append(f"  refused request again = {token_outcome(lambda: flow.exchange_code('code', request.state, request))}")
    stop(exchange)
    return port


def _faults(auth: ModuleType, transports: ModuleType, responses: ModuleType, errors: ModuleType, lines: list[str]) -> None:
    """Classify injected transport and secret failures by delivery evidence, and each request's single use."""
    flow_type = auth.AuthorizationCodeFlow
    token = "https://token.example.com/token"
    granted = json.dumps(GRANTED).encode()
    not_sent = errors.TransportError(delivery_state=errors.DeliveryState.NOT_SENT, phase="connect")
    connect_timeout = errors.PhaseTimeoutError(
        effective_timeout=5.0, delivery_state=errors.DeliveryState.NOT_SENT, phase="connect"
    )
    read_timeout = errors.PhaseTimeoutError(
        effective_timeout=15.0, delivery_state=errors.DeliveryState.MAYBE_SENT, phase="read"
    )
    read_failure = errors.TransportError(delivery_state=errors.DeliveryState.RESPONSE_STARTED, phase="read")
    unknown_phase = errors.TransportError(delivery_state=errors.DeliveryState.MAYBE_SENT)
    unknown_timeout = errors.TransportError(
        delivery_state=errors.DeliveryState.MAYBE_SENT, cause=httpx2.ReadTimeout("unclassified")
    )
    headers = responses.HeadersView((("content-type", "application/json"),))
    cases: tuple[tuple[str, tuple[object, ...], float, bool], ...] = (
        ("unsent connect failure", (not_sent,), 30.0, True),
        ("unsent claim without delivery evidence", (not_sent,), 30.0, False),
        ("unsent phase timeout", (connect_timeout,), 30.0, True),
        ("unsent timeout within a short session", (connect_timeout,), 1.0, True),
        ("read timeout after sending", (read_timeout,), 30.0, False),
        ("failure in an unknown phase", (unknown_phase,), 30.0, False),
        ("timeout in an unknown phase", (unknown_timeout,), 30.0, False),
        ("timeout in an unknown phase after the session", (Late(1.0, unknown_timeout),), 0.5, False),
        ("failure after the response started", (Reported(RuntimeError("adapter"), headers),), 30.0, False),
        ("adapter programming failure", (RuntimeError("adapter"),), 30.0, False),
        ("invalid response status", (Response(responses, "200", granted),), 30.0, False),
        ("body failure", (Response(responses, 200, b"{", failure=read_failure),), 30.0, False),
        ("reported body failure", (Response(responses, 200, b"{", failure=RuntimeError(), reported=True),), 30.0, False),
        ("body arriving after the session", (Response(responses, 200, granted, pause=1.0),), 0.5, False),
        ("body ending after the session", (Response(responses, 200, granted, tail=1.0),), 0.5, False),
        ("close failure after a complete answer", (Response(responses, 200, granted, close_failure=RuntimeError()),), 30.0, False),
    )
    for label, replies, total, evidence in cases:
        adapter = Adapter(transports, *replies, evidence=evidence)
        options = auth.OAuthProviderOptions(refresh_timeout=total)
        with flow_type(_AUTHORIZE, token, client_id="c", client_auth_method="none", options=options,
                       token_transport=adapter) as flow:
            request = flow.authorization_request(_REDIRECT, ())
            lines.append(f"  {label} = {token_outcome(lambda flow=flow, request=request: flow.exchange_code('code', request.state, request))}")
        lines.append(f"    borrowed transport closes={adapter.closes}")
    owned = Adapter(transports, Response(responses, 200, granted))
    flow = flow_type(_AUTHORIZE, token, client_id="c", client_auth_method="none",
                     token_transport=transports.OwnedTransportAdapter(owned))
    other = flow_type(_AUTHORIZE, token, client_id="c", client_auth_method="none", token_transport=owned)
    request = flow.authorization_request(_REDIRECT, ())
    lines.append(f"  foreign request = {token_outcome(lambda: other.exchange_code('code', request.state, request))}")
    lines.append(f"  empty code = {token_outcome(lambda: flow.exchange_code('', request.state, request))}")
    surrogate = "c\ud800"
    lines.append(f"  code outside visible ASCII = {token_outcome(lambda: flow.exchange_code(surrogate, request.state, request))}")
    lines.append(f"  state outside ASCII = {token_outcome(lambda: flow.exchange_code('code', 'é', request))}")
    lines.append(f"  state mismatch = {token_outcome(lambda: flow.exchange_code('code', request.state + 'x', request))}")
    lines.append(f"  non-string state = {token_outcome(lambda: flow.exchange_code('code', None, request))}")
    lines.append(f"  exchange after refused input = {token_outcome(lambda: flow.exchange_code('code', request.state, request))}")
    lines.append(f"  exchange after success = {token_outcome(lambda: flow.exchange_code('code', request.state, request))}")
    flow.close()
    flow.close()
    lines.append(f"  owned transport closes once = {owned.closes}")
    lines.append(f"  request after close = {token_outcome(lambda: flow.authorization_request(_REDIRECT, ()))}")
    lines.append(f"  exchange after close = {token_outcome(lambda: flow.exchange_code('code', request.state, request))}")
    lines.append(f"  request repr = {request!r}")
    for label, provider in (
        ("secret provider failure", Secret(failure=RuntimeError("secret"))),
        ("secret provider auth failure", Secret(failure=errors.AuthConfigurationError(condition="missing_value"))),
        ("secret of another kind", Secret(auth.BasicCredential("u", "p"))),
        ("secret outside visible ASCII", Secret(auth.ApiKeyCredential("sé"))),
        ("secret coroutine", _Pending()),
        ("secret outlives the session", Secret(auth.ApiKeyCredential("s"), delay=0.15)),
    ):
        options = auth.OAuthProviderOptions(refresh_timeout=0.1)
        adapter = Adapter(transports, Response(responses, 200, granted))
        with flow_type(_AUTHORIZE, token, client_id="c", client_secret=provider, options=options,
                       token_transport=adapter) as flow:
            request = flow.authorization_request(_REDIRECT, ())
            lines.append(f"  {label} = {token_outcome(lambda flow=flow, request=request: flow.exchange_code('code', request.state, request))}")
            lines.append(f"    request again = {token_outcome(lambda flow=flow, request=request: flow.exchange_code('code', request.state, request))}")
    for label, failure in (("interrupted send", KeyboardInterrupt()), ("interrupted secret", KeyboardInterrupt())):
        provider = Secret(failure=failure) if label == "interrupted secret" else None
        adapter = Adapter(transports, failure)
        with flow_type(_AUTHORIZE, token, client_id="c", client_auth_method="client_secret_post" if provider else "none",
                       client_secret=provider, token_transport=adapter) as flow:
            request = flow.authorization_request(_REDIRECT, ())
            try:
                flow.exchange_code("code", request.state, request)
            except KeyboardInterrupt:
                lines.append(f"  {label} = KeyboardInterrupt")
            lines.append(f"    request again = {token_outcome(lambda flow=flow, request=request: flow.exchange_code('code', request.state, request))}")
    closing = _Closing(auth)
    racing = Adapter(transports, Response(responses, 200, granted))
    flow = flow_type(_AUTHORIZE, token, client_id="c", client_secret=closing, token_transport=racing)
    closing.close = flow.close
    request = flow.authorization_request(_REDIRECT, ())
    lines.append(f"  closed while the secret is fetched = {token_outcome(lambda: flow.exchange_code('code', request.state, request))}")
    lines.append(f"    nothing sent = {len(racing.replies) == 1}")
    gate = threading.Event()
    blocked = Adapter(transports, Response(responses, 200, granted), gate=gate)
    first: list[str] = []
    with flow_type(_AUTHORIZE, token, client_id="c", client_auth_method="none", token_transport=blocked) as flow:
        request = flow.authorization_request(_REDIRECT, ())
        exchanging = threading.Thread(
            target=lambda: first.append(token_outcome(lambda: flow.exchange_code("code", request.state, request)))
        )
        exchanging.start()
        blocked.entered.wait()
        lines.append(f"  concurrent exchange = {token_outcome(lambda: flow.exchange_code('code', request.state, request))}")
        gate.set()
        exchanging.join()
        lines.append(f"    first exchange = {first[0]}")


class _Blocking(AsyncResponse):
    """An asyncio token response whose pauses block the event loop, so only the endpoint's own checks see them end."""

    async def iter_raw_bytes(self) -> AsyncIterator[bytes]:  # ty: ignore[invalid-method-override]
        half = len(self.body) // 2
        yield self.body[:half]
        time.sleep(self.pause)
        yield self.body[half:]
        time.sleep(self.tail)


async def _async_flows(
    auth: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, errors: ModuleType,
    lines: list[str], masks: _Masks,
) -> int:
    """Exchange codes with asyncio over TLS and through injected transports, including cancellation."""
    exchange = Exchange(lines)
    port = exchange.port()
    token = f"https://localhost:{port}/token"
    oauth = auth.OAuthProviderOptions(transport=options.TransportOptions(ssl_context=_contexts()[1]))
    secret = auth.AsyncStaticCredentialProvider(auth.ApiKeyCredential("async secret"))
    exchange.respond(json_reply(200, {**GRANTED, "scope": "read"}), json_reply(400, {"error": "invalid_grant"}))
    async with auth.AsyncAuthorizationCodeFlow(_AUTHORIZE, token, client_id="c", client_secret=secret, options=oauth) as flow:
        for label in ("async exchange", "async invalid grant"):
            request = masks.request(flow.authorization_request(_REDIRECT, ("read", "write")))
            lines.append(f"  {label} = {await atoken_outcome(lambda request=request: flow.exchange_code('code', request.state, request))}")
    stop(exchange)
    granted = json.dumps(GRANTED).encode()
    read_timeout = errors.PhaseTimeoutError(
        effective_timeout=15.0, delivery_state=errors.DeliveryState.MAYBE_SENT, phase="read"
    )
    for label, replies, provider, total in (
        ("async injected answer", (AsyncResponse(responses, 200, granted),), None, 30.0),
        ("async body failure", (AsyncResponse(responses, 200, b"{", failure=read_timeout),), None, 30.0),
        ("async oversized body", (AsyncResponse(responses, 200, b"x" * 70000),), None, 30.0),
        ("async close failure", (AsyncResponse(responses, 200, granted, close_failure=RuntimeError()),), None, 30.0),
        ("async send failure", (read_timeout,), None, 30.0),
        ("async secret failure", (), AsyncSecret(failure=RuntimeError("secret")), 30.0),
        ("async secret outlives the session", (), AsyncSecret(auth.ApiKeyCredential("s"), delay=0.15), 0.1),
        ("async body slower than the session", (AsyncResponse(responses, 200, granted, pause=1.0),), None, 0.5),
        ("async body arriving after the session", (_Blocking(responses, 200, granted, pause=1.0),), None, 0.5),
        ("async body ending after the session", (_Blocking(responses, 200, granted, tail=1.0),), None, 0.5),
    ):
        adapter = AsyncAdapter(transports, *replies)
        async with auth.AsyncAuthorizationCodeFlow(
            _AUTHORIZE, token, client_id="c", client_secret=provider,
            client_auth_method="client_secret_basic" if provider else "none",
            options=auth.OAuthProviderOptions(refresh_timeout=total), token_transport=adapter,
        ) as flow:
            request = flow.authorization_request(_REDIRECT, ())
            lines.append(f"  {label} = {await atoken_outcome(lambda flow=flow, request=request: flow.exchange_code('code', request.state, request))}")
    async with auth.AsyncAuthorizationCodeFlow(
        _AUTHORIZE, token, client_id="c", client_secret=AsyncSecret(auth.ApiKeyCredential("s"), delay=5),
        options=auth.OAuthProviderOptions(refresh_timeout=0.1), token_transport=AsyncAdapter(transports),
    ) as flow:
        request = flow.authorization_request(_REDIRECT, ())
        started = time.monotonic()
        lines.append(f"  async slow secret = {await atoken_outcome(lambda: flow.exchange_code('code', request.state, request))}")
        lines.append(f"    stopped at the session deadline = {time.monotonic() - started < 2}")
    release = asyncio.Event()
    held = AsyncAdapter(transports, AsyncResponse(responses, 200, granted), hold=release)
    closing = _AsyncClosing(auth)
    racing = AsyncAdapter(transports, AsyncResponse(responses, 200, granted))
    flow = auth.AsyncAuthorizationCodeFlow(
        _AUTHORIZE, token, client_id="c", client_secret=closing, token_transport=racing
    )
    closing.close = flow.aclose
    request = flow.authorization_request(_REDIRECT, ())
    lines.append(f"  async closed while the secret is fetched = {await atoken_outcome(lambda: flow.exchange_code('code', request.state, request))}")
    lines.append(f"    nothing sent = {len(racing.replies) == 1}")
    async with auth.AsyncAuthorizationCodeFlow(
        _AUTHORIZE, token, client_id="c", client_auth_method="none", token_transport=held
    ) as flow:
        request = flow.authorization_request(_REDIRECT, ())
        exchanging = asyncio.create_task(flow.exchange_code("code", request.state, request))
        await asyncio.to_thread(held.entered.wait)
        lines.append(f"  async concurrent exchange = {await atoken_outcome(lambda: flow.exchange_code('code', request.state, request))}")
        release.set()
        lines.append(f"    first exchange = {await atoken_outcome(lambda: exchanging)}")
    hold = asyncio.Event()
    slow = AsyncAdapter(transports, AsyncResponse(responses, 200, granted), hold=hold)
    async with auth.AsyncAuthorizationCodeFlow(
        _AUTHORIZE, token, client_id="c", client_auth_method="none",
        options=auth.OAuthProviderOptions(refresh_timeout=0.1), token_transport=slow,
    ) as flow:
        request = flow.authorization_request(_REDIRECT, ())
        lines.append(f"  async session expires while sending = {await atoken_outcome(lambda: flow.exchange_code('code', request.state, request))}")
    cancelled = AsyncAdapter(transports, AsyncResponse(responses, 200, granted), hold=asyncio.Event())
    owned = transports.OwnedTransportAdapter(cancelled)
    flow = auth.AsyncAuthorizationCodeFlow(_AUTHORIZE, token, client_id="c", client_auth_method="none", token_transport=owned)
    request = flow.authorization_request(_REDIRECT, ())
    task = asyncio.create_task(flow.exchange_code("code", request.state, request))
    await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        lines.append("  async cancelled exchange = CancelledError")
    lines.append(f"    request again = {await atoken_outcome(lambda: flow.exchange_code('code', request.state, request))}")
    await flow.aclose()
    await flow.aclose()
    lines.append(f"  async owned transport closes once = {cancelled.closes}")
    lines.append(f"  async exchange after close = {await atoken_outcome(lambda: flow.exchange_code('code', request.state, request))}")
    return port


def oauth_code(package: ModuleType, lines: list[str]) -> None:
    """Exercise authorization requests, code exchanges, and every classified outcome in both execution modes."""
    auth, options, transports, responses, errors = (
        importlib.import_module(f"{package.__name__}.{name}")
        for name in ("auth", "options", "transports", "responses", "errors")
    )
    masks = _Masks()
    _configuration(auth, options, transports, lines)
    sync_port = _wire(auth, options, lines, masks)
    _faults(auth, transports, responses, errors, lines)
    ports: list[int] = [sync_port]
    pinned: list[Any] = []

    def flow() -> Any:
        return auth.AsyncAuthorizationCodeFlow(
            _AUTHORIZE, "https://token.example.com/token", client_id="c", client_auth_method="none",
            token_transport=AsyncAdapter(transports, AsyncResponse(responses, 200, json.dumps(GRANTED).encode())),
        )

    async def flows() -> None:
        ports.append(await _async_flows(auth, options, transports, responses, errors, lines, masks))
        pinned.append(flow())

    run(flows)
    constructed = pinned[0]
    request = constructed.authorization_request(_REDIRECT, ())

    async def constructed_loop() -> str:
        return await atoken_outcome(lambda: constructed.exchange_code("code", request.state, request))

    lines.append(f"  loop other than the constructing one = {asyncio.run(constructed_loop())}")
    shared = flow()
    request = shared.authorization_request(_REDIRECT, ())
    unscheduled = shared.exchange_code("code", request.state, request)
    try:
        unscheduled.send(None)
    except Exception as error:  # noqa: BLE001
        lines.append(f"  without a running asyncio loop = {failure_line(error)}")

    async def exchanged() -> str:
        return await atoken_outcome(lambda: shared.exchange_code("code", request.state, request))

    lines.append(f"  first loop = {asyncio.run(exchanged())}")
    second = shared.authorization_request(_REDIRECT, ())

    async def other_loop() -> str:
        return await atoken_outcome(lambda: shared.exchange_code("code", second.state, second))

    lines.append(f"  another loop = {asyncio.run(other_loop())}")
    masks.apply(lines, tuple(ports))
