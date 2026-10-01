"""Run the device authorization flow against a local TLS endpoint and injected token transports."""

from __future__ import annotations

import asyncio
import importlib
import json
import threading
import time
from typing import TYPE_CHECKING, Any, Final

from tests.data.python.client_oauth import (
    GRANTED,
    Adapter,
    AsyncAdapter,
    AsyncResponse,
    AsyncSecret,
    Response,
    Secret,
    atoken_outcome,
    closed_port,
    failure_line,
    json_reply,
    stop,
    token_outcome,
)
from tests.data.python.client_runtime import Exchange, run
from tests.data.python.fixture_server import _contexts

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_DEVICE: Final = "https://auth.example.com/device"
_TOKEN: Final = "https://auth.example.com/token"
_REQUIRED: Final = {
    "device_code": "device-1",
    "user_code": "WDJB-MJHT",
    "verification_uri": "https://example.com/device",
    "expires_in": 120,
}
_QUICK: Final = {**_REQUIRED, "interval": 0.01}
_PENDING: Final = {"error": "authorization_pending"}


def _outcome(call: Callable[[], object]) -> str:
    try:
        result = call()
    except Exception as error:  # noqa: BLE001
        return failure_line(error)
    return repr(result)


async def _aoutcome(call: Callable[[], Any]) -> str:
    try:
        result = await call()
    except Exception as error:  # noqa: BLE001
        return failure_line(error)
    return repr(result)


def _began(authorization: Any) -> str:
    return (
        f"{authorization!r} user_code={authorization.user_code} uri={authorization.verification_uri}"
        f" complete={authorization.verification_uri_complete}"
    )


def _reply(responses: ModuleType, status: int, payload: object) -> Response:
    return Response(responses, status, json.dumps(payload).encode())


class _SlowClose(Response):
    """A response whose close takes longer than the device code it carries lives."""

    def close(self) -> None:
        time.sleep(0.6)


def _areply(responses: ModuleType, status: int, payload: object) -> AsyncResponse:
    return AsyncResponse(responses, status, json.dumps(payload).encode())


def _configuration(
    auth: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    """Refuse invalid endpoints, clients, session limits, and begin input before any transaction starts."""
    flow, async_flow = auth.DeviceAuthorizationFlow, auth.AsyncDeviceAuthorizationFlow
    secret = auth.StaticCredentialProvider(auth.ApiKeyCredential("secret"))
    cases: tuple[tuple[str, Callable[[], object]], ...] = (
        ("device url type", lambda: flow(None, _TOKEN, client_id="c", client_auth_method="none")),
        ("insecure device url", lambda: flow("http://auth.example.com/device", _TOKEN, client_id="c", client_auth_method="none")),
        ("insecure token url", lambda: flow(_DEVICE, "http://auth.example.com/token", client_id="c", client_auth_method="none")),
        ("missing secret", lambda: flow(_DEVICE, _TOKEN, client_id="c")),
        ("async secret", lambda: flow(_DEVICE, _TOKEN, client_id="c", client_secret=AsyncSecret())),
        ("sync secret of an async flow", lambda: async_flow(_DEVICE, _TOKEN, client_id="c", client_secret=secret)),
        (
            "sync transport of an async flow",
            lambda: async_flow(_DEVICE, _TOKEN, client_id="c", client_auth_method="none", token_transport=Adapter(transports)),
        ),
        ("options type", lambda: flow(_DEVICE, _TOKEN, client_id="c", client_auth_method="none", options=1)),
        ("boolean total timeout", lambda: options.SessionOptions(total_timeout=True)),
        ("negative total timeout", lambda: options.SessionOptions(total_timeout=-1)),
        ("NaN total timeout", lambda: options.SessionOptions(total_timeout=float("nan"))),
        ("deadline type", lambda: options.SessionOptions(deadline=5)),
        ("negative sends", lambda: options.SessionOptions(max_network_sends=-1)),
        ("boolean sends", lambda: options.SessionOptions(max_network_sends=True)),
        ("reconnect limit", lambda: options.SessionOptions(max_reconnects=0)),
    )
    for label, call in cases:
        lines.append(f"  {label} = {_outcome(call)}")
    lines.append(f"  session options = {options.SessionOptions(total_timeout=2, max_network_sends=None)}")
    adapter = Adapter(transports, _reply(responses, 200, _QUICK))
    with flow(_DEVICE, _TOKEN, client_id="c", client_auth_method="none", token_transport=adapter) as device:
        for label, call in (
            ("poll before begin", device.poll),
            ("lone scope string", lambda: device.begin("read")),
            ("null scopes", lambda: device.begin(None)),
            ("session options type", lambda: device.begin((), session_options={})),
            ("no network sends", lambda: device.begin((), session_options=options.SessionOptions(max_network_sends=0))),
            ("expired session", lambda: device.begin((), session_options=options.SessionOptions(total_timeout=0))),
            ("poll after refused begins", device.poll),
            ("begin after refused begins", lambda: _began(device.begin(()))),
            ("begin again while ready", lambda: device.begin(())),
            ("begin again with invalid input", lambda: device.begin("read")),
        ):
            lines.append(f"  {label} = {_outcome(call)}")
    lines.append(f"  begin after close = {_outcome(lambda: device.begin(()))}")
    lines.append(f"  poll after close = {_outcome(device.poll)}")


def _wire(auth: ModuleType, options: ModuleType, lines: list[str]) -> int:
    """Begin and poll over TLS with each client authentication, every begin field rule, and each poll answer."""
    exchange = Exchange(lines)
    port = exchange.port()
    device_url, token_url = f"https://localhost:{port}/device", f"https://localhost:{port}/token"
    oauth = auth.OAuthProviderOptions(transport=options.TransportOptions(ssl_context=_contexts()[1]))
    secret = auth.StaticCredentialProvider(auth.ApiKeyCredential("se:cr et"))

    def flow(method: str = "none", provider: object = None) -> Any:
        return auth.DeviceAuthorizationFlow(
            device_url, token_url, client_id="client id", client_secret=provider, client_auth_method=method, options=oauth
        )

    for method, provider in (("client_secret_basic", secret), ("client_secret_post", secret), ("none", None)):
        exchange.respond(json_reply(200, _QUICK), json_reply(400, _PENDING), json_reply(200, GRANTED))
        with flow(method, provider) as device:
            lines.append(f"  {method} begin = {_began(device.begin(('write', 'read', 'read')))}")
            lines.append(f"  {method} poll = {token_outcome(device.poll)}")
            lines.append(f"  {method} poll again = {_outcome(device.poll)}")
            lines.append(f"  {method} begin again = {_outcome(lambda device=device: device.begin(()))}")
    exchange.respond(json_reply(200, _REQUIRED))
    with flow() as device:
        lines.append(f"  required fields only = {_began(device.begin(()))}")
    exchange.respond(json_reply(200, {**_REQUIRED, "interval": 30}))
    with flow() as device:
        began = device.begin((), session_options=options.SessionOptions(total_timeout=10))
        lines.append(f"  session limit shortens the lifetime = {began.deadline.remaining() <= 10}")
        lines.append(f"  first poll after the lifetime = {_outcome(device.poll)}")
    exchange.respond(json_reply(200, _REQUIRED))
    with flow() as device:
        began = device.begin(())
        lines.append(f"  declared lifetime = {119 < began.deadline.remaining() <= 120}")
    exchange.respond(json_reply(200, _REQUIRED))
    with flow() as device:
        began = device.begin((), session_options=options.SessionOptions(total_timeout=None, deadline=None))
        lines.append(f"  lifetime without session limits = {119 < began.deadline.remaining() <= 120}")
    exchange.respond(json_reply(200, _REQUIRED))
    with flow() as device:
        began = device.begin(
            (), session_options=options.SessionOptions(total_timeout=100, deadline=options.Deadline.after(10))
        )
        lines.append(f"  earlier of the session limits = {began.deadline.remaining() <= 10}")
    exchange.respond(json_reply(200, _QUICK), json_reply(200, _QUICK))
    with flow() as wide, flow() as narrow:
        wide.begin(("read", "write"))
        narrow.begin(("read",))
        exchange.respond(json_reply(200, {"access_token": "narrow", "token_type": "Bearer"}))
        lines.append(f"  later flow first = {token_outcome(narrow.poll)}")
        exchange.respond(json_reply(200, {"access_token": "wide", "token_type": "Bearer"}))
        lines.append(f"  earlier flow keeps its scopes = {token_outcome(wide.poll)}")
    polls: tuple[tuple[str, tuple[str, ...], dict[str, object], tuple[Callable[..., Any], ...]], ...] = (
        (
            "complete uri and interval",
            ("read",),
            {**_QUICK, "verification_uri_complete": "https://example.com/device?user_code=WDJB-MJHT"},
            (json_reply(200, GRANTED),),
        ),
        (
            "token members in the begin response are ignored",
            ("read", "write"),
            {**_QUICK, "scope": None, "access_token": 1, "token_type": None, "extra": [1]},
            (json_reply(400, _PENDING), json_reply(200, {"access_token": "a", "token_type": "bearer"})),
        ),
        ("explicit scope narrows the grant", ("read", "write"), _QUICK, (json_reply(200, {**GRANTED, "scope": "read"}),)),
        ("empty request keeps an empty grant", (), _QUICK, (json_reply(200, {"access_token": "a", "token_type": "Bearer"}),)),
        ("access denied", ("read",), _QUICK, (json_reply(400, {"error": "access_denied"}),)),
        ("expired device code", ("read",), _QUICK, (json_reply(400, _PENDING), json_reply(400, {"error": "expired_token"}))),
        ("invalid grant", ("read",), _QUICK, (json_reply(400, {"error": "invalid_grant"}),)),
        ("invalid client", ("read",), _QUICK, (json_reply(401, {"error": "invalid_client"}),)),
        ("unknown error code", ("read",), _QUICK, (json_reply(400, {"error": "try_later"}),)),
        ("malformed error", ("read",), _QUICK, (json_reply(400, {"error": "authorization_pending", "error_uri": "a b"}),)),
        ("unavailable", ("read",), _QUICK, (json_reply(503, b""),)),
        ("missing access token", ("read",), _QUICK, (json_reply(200, {"token_type": "Bearer"}),)),
        ("null scope", ("read",), _QUICK, (json_reply(200, {**GRANTED, "scope": None}),)),
        ("empty scope", ("read",), _QUICK, (json_reply(200, {**GRANTED, "scope": ""}),)),
        ("double-spaced scope", ("read",), _QUICK, (json_reply(200, {**GRANTED, "scope": "read  write"}),)),
        ("invalid expiry", ("read",), _QUICK, (json_reply(200, {**GRANTED, "expires_in": "3600"}),)),
        ("unsupported token type", ("read",), _QUICK, (json_reply(200, {**GRANTED, "token_type": "mac"}),)),
        ("success without JSON", ("read",), _QUICK, (json_reply(200, b"<html>"),)),
    )
    for label, scopes, begin, replies in polls:
        exchange.respond(json_reply(200, begin), *replies)
        with flow() as device:
            device.begin(scopes)
            lines.append(f"  {label} = {token_outcome(device.poll)}")
            lines.append(f"    poll again = {_outcome(device.poll)}")
    exchange.respond(json_reply(200, {**_QUICK, "expires_in": 4.5}), json_reply(400, {"error": "slow_down"}))
    with flow() as device:
        device.begin(())
        started = time.monotonic()
        lines.append(f"  slow down moves the next poll past the deadline = {_outcome(device.poll)}")
        lines.append(f"    without waiting for it = {time.monotonic() - started < 3}")
    exchange.respond(json_reply(200, {**_QUICK, "interval": 0.2}), json_reply(400, _PENDING), json_reply(200, GRANTED))
    with flow() as device:
        device.begin(())
        started = time.monotonic()
        lines.append(f"  pending waits the interval = {token_outcome(device.poll)}")
        lines.append(f"    waited twice = {time.monotonic() - started >= 0.4}")
    begins: tuple[tuple[str, Callable[..., Any]], ...] = (
        *(
            (f"missing {name}", json_reply(200, {key: value for key, value in _REQUIRED.items() if key != name}))
            for name in _REQUIRED
        ),
        ("empty user code", json_reply(200, {**_REQUIRED, "user_code": ""})),
        ("numeric device code", json_reply(200, {**_REQUIRED, "device_code": 1})),
        ("device code with a control character", json_reply(200, {**_REQUIRED, "device_code": "device\u0001"})),
        ("user code with an escape sequence", json_reply(200, {**_REQUIRED, "user_code": "\u001b[2J"})),
        ("relative verification uri", json_reply(200, {**_REQUIRED, "verification_uri": "/device"})),
        ("verification uri fragment", json_reply(200, {**_REQUIRED, "verification_uri": "https://example.com/d#x"})),
        ("null complete uri", json_reply(200, {**_REQUIRED, "verification_uri_complete": None})),
        ("relative complete uri", json_reply(200, {**_REQUIRED, "verification_uri_complete": "device"})),
        ("null lifetime", json_reply(200, {**_REQUIRED, "expires_in": None})),
        ("zero lifetime", json_reply(200, {**_REQUIRED, "expires_in": 0})),
        ("negative lifetime", json_reply(200, {**_REQUIRED, "expires_in": -1})),
        ("string lifetime", json_reply(200, {**_REQUIRED, "expires_in": "120"})),
        ("boolean lifetime", json_reply(200, {**_REQUIRED, "expires_in": True})),
        ("null interval", json_reply(200, {**_REQUIRED, "interval": None})),
        ("zero interval", json_reply(200, {**_REQUIRED, "interval": 0})),
        ("error beside success", json_reply(200, {**_REQUIRED, "error": "invalid_request"})),
        ("created status", json_reply(201, _REQUIRED)),
        ("begin without JSON", json_reply(200, b"{")),
        ("begin rejected", json_reply(400, {"error": "invalid_scope"})),
        ("begin client rejected", json_reply(401, {"error": "invalid_client"})),
        ("begin unavailable", json_reply(503, b"")),
    )
    for label, reply in begins:
        exchange.respond(reply)
        with flow() as device:
            lines.append(f"  {label} = {_outcome(lambda device=device: device.begin(()))}")
            lines.append(f"    begin again = {_outcome(lambda device=device: device.begin(()))}")
            lines.append(f"    poll = {_outcome(device.poll)}")
    refused = f"https://localhost:{closed_port()}"
    with auth.DeviceAuthorizationFlow(
        f"{refused}/device", token_url, client_id="c", client_auth_method="none", options=oauth
    ) as device:
        lines.append(f"  refused begin = {_outcome(lambda: device.begin(()))}")
        lines.append(f"    begin again = {_outcome(lambda: device.begin(()))}")
    exchange.respond(json_reply(200, _QUICK))
    with auth.DeviceAuthorizationFlow(
        device_url, f"{refused}/token", client_id="c", client_auth_method="none", options=oauth
    ) as device:
        device.begin(())
        lines.append(f"  refused poll = {_outcome(device.poll)}")
        lines.append(f"    poll again = {_outcome(device.poll)}")
    stop(exchange)
    return port


def _faults(
    auth: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, errors: ModuleType,
    lines: list[str],
) -> None:
    """Classify injected failures, limits, cancellation, interruption, and closing by what the transaction sent."""
    begun = _reply(responses, 200, {**_QUICK, "interval": 0.05})
    pending = _reply(responses, 400, _PENDING)
    connect = errors.PhaseTimeoutError(effective_timeout=5.0, delivery_state=errors.DeliveryState.NOT_SENT, phase="connect")
    read = errors.PhaseTimeoutError(effective_timeout=15.0, delivery_state=errors.DeliveryState.MAYBE_SENT, phase="read")

    def flow(
        *replies: object, secret: object = None, total: float = 30.0, evidence: bool = False, clock: Any = None
    ) -> Any:
        return auth.DeviceAuthorizationFlow(
            _DEVICE, _TOKEN, client_id="c", client_secret=secret,
            client_auth_method="client_secret_post" if secret else "none",
            options=auth.OAuthProviderOptions(refresh_timeout=total, **({} if clock is None else {"clock": clock})),
            token_transport=Adapter(transports, *replies, evidence=evidence),
        )

    for label, device, session in (
        ("begin phase timeout", flow(read), None),
        ("begin within a short provider session", flow(connect, total=1.0, evidence=True), None),
        ("begin cut by the session limit", flow(read), options.SessionOptions(total_timeout=0.5)),
        ("begin adapter failure", flow(RuntimeError("adapter")), None),
        ("begin secret failure", flow(secret=Secret(failure=RuntimeError("secret"))), None),
        ("begin secret slower than the session", flow(secret=Secret(auth.ApiKeyCredential("s"), delay=0.3), total=0.1), None),
    ):
        with device:
            lines.append(f"  {label} = {_outcome(lambda device=device, session=session: device.begin((), session_options=session))}")
            lines.append(f"    begin again = {_outcome(lambda device=device: device.begin(()))}")
    for label, device, session in (
        ("poll phase timeout", flow(begun, read), lambda: None),
        ("poll adapter failure", flow(begun, RuntimeError("adapter")), lambda: None),
        ("send limit", flow(begun, pending), lambda: options.SessionOptions(max_network_sends=2)),
        (
            "unlimited sends",
            flow(begun, pending, _reply(responses, 200, GRANTED)),
            lambda: options.SessionOptions(max_network_sends=None),
        ),
        (
            "poll intervals on a frozen clock",
            flow(begun, pending, _reply(responses, 200, GRANTED), clock=options.Clock(monotonic=lambda: 1000.0)),
            lambda: None,
        ),
        (
            "session deadline",
            flow(_reply(responses, 200, {**_QUICK, "interval": 0.2}), pending),
            lambda: options.SessionOptions(deadline=options.Deadline.after(0.3)),
        ),
        ("poll cut by the session limit", flow(begun, read), lambda: options.SessionOptions(total_timeout=0.5)),
    ):
        with device:
            device.begin((), session_options=session())
            lines.append(f"  {label} = {token_outcome(device.poll)}")
            lines.append(f"    poll again = {_outcome(device.poll)}")
    token = options.CancelToken()
    token.cancel()
    with flow(begun) as device:
        device.begin(())
        lines.append(f"  cancelled token = {_outcome(lambda: device.poll(cancel_token=token))}")
        lines.append(f"    poll again = {_outcome(device.poll)}")
    with flow(begun, _reply(responses, 200, GRANTED)) as device:
        device.begin(())
        lines.append(f"  cancel token type = {_outcome(lambda: device.poll(cancel_token=object()))}")
        lines.append(f"    poll after refused input = {token_outcome(lambda: device.poll(cancel_token=options.CancelToken()))}")
    for label, interrupt in (("interrupted send", (begun, KeyboardInterrupt())), ("interrupted secret", (begun,))):
        secret = Secret(auth.ApiKeyCredential("s")) if label == "interrupted send" else _Interrupting(auth)
        with flow(*interrupt, secret=secret) as device:
            device.begin(())
            try:
                device.poll()
            except KeyboardInterrupt:
                lines.append(f"  {label} = KeyboardInterrupt")
            lines.append(f"    poll again = {_outcome(device.poll)}")
    adapter = Adapter(
        transports,
        _SlowClose(responses, 200, json.dumps({**_QUICK, "expires_in": 0.5}).encode()),
        _reply(responses, 200, GRANTED),
    )
    with auth.DeviceAuthorizationFlow(
        _DEVICE, _TOKEN, client_id="c", client_auth_method="none", token_transport=adapter
    ) as device:
        device.begin(())
        lines.append(f"  device code outlived by closing its response = {_outcome(device.poll)} unsent replies={len(adapter.replies)}")
    slow = _reply(responses, 200, {**_QUICK, "interval": 5})
    token = options.CancelToken()
    with flow(slow) as device:
        device.begin(())
        timer = threading.Timer(0.1, token.cancel)
        timer.start()
        lines.append(f"  cancelled while waiting = {_outcome(lambda: device.poll(cancel_token=token))}")
        timer.join()
    device = flow(slow)
    device.begin(())
    timer = threading.Timer(0.1, device.close)
    timer.start()
    lines.append(f"  closed while waiting = {_outcome(device.poll)}")
    timer.join()
    lines.append(f"    poll again = {_outcome(device.poll)}")
    device = flow(_reply(responses, 200, {**_QUICK, "interval": 1e11, "expires_in": 1e12}))
    device.begin(())
    timer = threading.Timer(0.1, device.close)
    timer.start()
    lines.append(f"  closed during an interval beyond any wait = {_outcome(device.poll)}")
    timer.join()
    gate = threading.Event()
    gate.set()
    gated = Adapter(transports, begun, _reply(responses, 200, GRANTED), gate=gate)
    polled: list[str] = []
    with auth.DeviceAuthorizationFlow(
        _DEVICE, _TOKEN, client_id="c", client_auth_method="none", token_transport=gated
    ) as device:
        device.begin(())
        gate.clear()
        gated.entered.clear()
        sending = threading.Thread(target=lambda: polled.append(token_outcome(device.poll)))
        sending.start()
        gated.entered.wait()
        lines.append(f"  concurrent poll = {_outcome(device.poll)}")
        gate.set()
        sending.join()
    lines.append(f"    first poll = {polled[0]}")


class _Interrupting:
    """A client secret provider interrupted by the user once the poll has started."""

    def __init__(self, auth: ModuleType) -> None:
        self.material = auth.ApiKeyCredential("s")
        self.calls = 0

    def get(self, context: object) -> object:
        del context
        self.calls += 1
        if self.calls > 1:
            raise KeyboardInterrupt
        return self.material


async def _async_flows(
    auth: ModuleType, options: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> int:
    """Begin and poll with asyncio over TLS and through injected transports, including cancellation and closing."""
    exchange = Exchange(lines)
    port = exchange.port()
    oauth = auth.OAuthProviderOptions(transport=options.TransportOptions(ssl_context=_contexts()[1]))
    secret = auth.AsyncStaticCredentialProvider(auth.ApiKeyCredential("async secret"))
    exchange.respond(json_reply(200, _QUICK), json_reply(400, _PENDING), json_reply(200, {**GRANTED, "scope": "read"}))
    async with auth.AsyncDeviceAuthorizationFlow(
        f"https://localhost:{port}/device", f"https://localhost:{port}/token", client_id="c", client_secret=secret,
        options=oauth,
    ) as device:
        lines.append(f"  async begin = {_began(await device.begin(('read', 'write')))}")
        lines.append(f"  async poll = {await atoken_outcome(device.poll)}")
    stop(exchange)
    async with auth.AsyncDeviceAuthorizationFlow(
        _DEVICE, _TOKEN, client_id="c", client_secret=AsyncSecret(failure=RuntimeError("secret")),
        token_transport=AsyncAdapter(transports),
    ) as device:
        lines.append(f"  async begin secret failure = {await _aoutcome(lambda: device.begin(()))}")
        lines.append(f"    begin again = {await _aoutcome(lambda: device.begin(()))}")
    begun = _areply(responses, 200, {**_QUICK, "interval": 5})

    def flow(*replies: object, hold: asyncio.Event | None = None, clock: Any = None) -> Any:
        return auth.AsyncDeviceAuthorizationFlow(
            _DEVICE, _TOKEN, client_id="c", client_auth_method="none",
            options=auth.OAuthProviderOptions(**({} if clock is None else {"clock": clock})),
            token_transport=AsyncAdapter(transports, *replies, hold=hold),
        )

    async with flow(
        _areply(responses, 200, {**_QUICK, "interval": 0.05}),
        _areply(responses, 400, _PENDING),
        _areply(responses, 200, GRANTED),
        clock=options.Clock(monotonic=lambda: 1000.0),
    ) as device:
        await device.begin(())
        lines.append(f"  async poll intervals on a frozen clock = {await atoken_outcome(device.poll)}")

    async with flow(begun) as device:
        await device.begin(())
        waiting = asyncio.create_task(device.poll())
        await asyncio.sleep(0.05)
        lines.append(f"  async concurrent poll = {await _aoutcome(device.poll)}")
        waiting.cancel()
        try:
            await waiting
        except asyncio.CancelledError:
            lines.append("  async cancelled while waiting = CancelledError")
        lines.append(f"    poll again = {await _aoutcome(device.poll)}")
    hold = asyncio.Event()
    device = flow(_areply(responses, 200, _QUICK), _areply(responses, 200, GRANTED), hold=hold)
    hold.set()
    await device.begin(())
    hold.clear()
    sending = asyncio.create_task(device.poll())
    await asyncio.sleep(0.05)
    sending.cancel()
    try:
        await sending
    except asyncio.CancelledError:
        lines.append("  async cancelled while sending = CancelledError")
    lines.append(f"    poll again = {await _aoutcome(device.poll)}")
    await device.aclose()
    token = options.CancelToken()
    async with flow(begun) as device:
        await device.begin(())
        asyncio.get_running_loop().call_later(0.1, token.cancel)
        lines.append(f"  async cancel token = {await _aoutcome(lambda: device.poll(cancel_token=token))}")
    device = flow(begun)
    await device.begin(())
    asyncio.get_running_loop().call_later(0.1, lambda: asyncio.ensure_future(device.aclose()))
    lines.append(f"  async closed while waiting = {await _aoutcome(device.poll)}")
    lines.append(f"    begin after close = {await _aoutcome(lambda: device.begin(()))}")
    return port


def oauth_device(package: ModuleType, lines: list[str]) -> None:
    """Exercise device authorization begins, polls, limits, and every classified outcome in both execution modes."""
    auth, options, transports, responses, errors = (
        importlib.import_module(f"{package.__name__}.{name}")
        for name in ("auth", "options", "transports", "responses", "errors")
    )
    _configuration(auth, options, transports, responses, lines)
    ports = [_wire(auth, options, lines)]
    _faults(auth, options, transports, responses, errors, lines)

    async def flows() -> None:
        ports.append(await _async_flows(auth, options, transports, responses, lines))

    run(flows)
    shared = auth.AsyncDeviceAuthorizationFlow(
        _DEVICE, _TOKEN, client_id="c", client_auth_method="none",
        token_transport=AsyncAdapter(transports, _areply(responses, 200, _QUICK)),
    )

    async def begin() -> str:
        return await _aoutcome(lambda: shared.begin(()))

    async def poll() -> str:
        return await _aoutcome(shared.poll)

    lines.append(f"  first loop = {asyncio.run(begin())}")
    lines.append(f"  another loop = {asyncio.run(poll())}")
    for index, line in enumerate(lines):
        for port in ports:
            line = line.replace(f":{port}", ":<port>")
        lines[index] = line
