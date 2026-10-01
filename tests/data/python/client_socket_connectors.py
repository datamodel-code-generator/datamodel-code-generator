"""Drive generated WebSocket helpers through borrowed connectors: their contract, ownership, and every failure path."""

from __future__ import annotations

import asyncio
import importlib
import ssl
import threading
from typing import TYPE_CHECKING, Any, Final

from tests.data.python.client_runtime import arecord, record, run

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_JOINED: Final = b'{"kind": "joined", "user": "ann"}'


class _Connection:
    """A scripted connection: frames or failures for each receive, and a failure for send, ping, or close."""

    def __init__(self, harness: _Harness, *, frames: tuple[object, ...] = (), **failures: object) -> None:
        self.harness = harness
        self.lines = harness.lines
        self.frames = list(frames)
        self.failures = failures
        self.handshake_headers: Any = harness.responses.HeadersView((("x-socket", "1"),))
        self.subprotocol: Any = failures.pop("subprotocol", None)
        self.blocked: threading.Event | None = None
        self.released = threading.Event()
        self.entered = threading.Event()
        self.closed = threading.Event()
        self.hold: BaseException | None = None

    def _fail(self, name: str) -> None:
        if (failure := self.failures.get(name)) is not None:
            if callable(failure) and not isinstance(failure, BaseException):
                failure()
                return
            raise failure  # ty: ignore[invalid-raise]

    def send(self, data: bytes, *, text: bool, deadline: object) -> None:
        self.lines.append(f"    connection send text={text} {data!r} deadline={deadline is not None}")
        if self.blocked is not None:
            self.blocked.set()
            self.released.wait(5)
        self._fail("send")

    def receive(self, *, deadline: object) -> Any:
        if (hold := self.hold) is not None:
            self.entered.set()
            self.closed.wait(5)
            raise hold
        self._fail("receive")
        item = self.frames.pop(0) if self.frames else TimeoutError()
        if isinstance(item, BaseException):
            raise item
        return item

    def ping(self, payload: bytes, *, deadline: object) -> float:
        self.lines.append(f"    connection ping {payload!r} deadline={deadline is not None}")
        self._fail("ping")
        return 0.25

    def close(self, *, code: int = 1000, reason: str = "", timeout: float = 5) -> None:
        self.lines.append(f"    connection close {code} {reason!r} timeout={timeout}")
        self.closed.set()
        self._fail("close")

    def abort(self) -> None:
        self.lines.append("    connection abort")
        self.closed.set()


class _AsyncConnection(_Connection):
    """The asyncio form of the scripted connection."""

    async def send(self, data: bytes, *, text: bool, deadline: object) -> None:  # ty: ignore[invalid-method-override]
        self.lines.append(f"    connection send text={text} {data!r} deadline={deadline is not None}")
        if self.blocked is not None:
            self.blocked.set()
            await asyncio.sleep(10)
        self._fail("send")

    async def receive(self, *, deadline: object) -> Any:  # ty: ignore[invalid-method-override]
        if (hold := self.hold) is not None:
            self.entered.set()
            while not self.closed.is_set():
                await asyncio.sleep(0)
            raise hold
        self._fail("receive")
        if not self.frames:
            await asyncio.sleep(10)
        item = self.frames.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def ping(self, payload: bytes, *, deadline: object) -> float:  # ty: ignore[invalid-method-override]
        return super().ping(payload, deadline=deadline)

    async def aclose(self, *, code: int = 1000, reason: str = "", timeout: float = 5) -> None:
        super().close(code=code, reason=reason, timeout=timeout)


class _Static:
    """A bearer provider of one fixed token."""

    def __init__(self, auth: ModuleType) -> None:
        self.credential = auth.BearerCredential(auth.AccessToken("static-material"), auth.TokenVersion())

    def get(self, context: object) -> Any:
        del context
        return self.credential

    def invalidate(self, version: object) -> None:
        del version

    def refresh(self, context: object) -> Any:
        return self.get(context)


class _Connector:
    """A borrowed connector that hands out the queued connections, or raises the queued failures, in order."""

    def __init__(self, harness: _Harness) -> None:
        self.harness = harness
        self.lines = harness.lines
        self.queue: list[object] = []
        self.verbose = False

    def opened(self, request: Any, context: Any, options: Any, transport: Any) -> Any:
        if self.verbose:
            headers = [(name, value) for name, value in request.headers.items() if name.lower() != "user-agent"]
            self.lines.append(
                f"    open {request.url} {headers} subprotocols={request.subprotocols} {request!r} "
                f"{options!r} {transport!r}"
            )
        item = self.queue.pop(0)
        if callable(item) and not isinstance(item, (BaseException, _Connection)):
            item = item(context)
        if isinstance(item, BaseException):
            raise item
        return item

    def open(self, request: Any, *, context: Any, options: Any, transport: Any) -> Any:
        return self.opened(request, context, options, transport)

    def close(self) -> None:
        self.lines.append("    connector closed")


class _AsyncConnector(_Connector):
    async def open(self, request: Any, *, context: Any, options: Any, transport: Any) -> Any:  # ty: ignore[invalid-method-override]
        return self.opened(request, context, options, transport)


class _Harness:
    """A generated WebSocket package's public modules and its models."""

    def __init__(self, package: ModuleType, lines: list[str]) -> None:
        self.package = package
        self.lines = lines
        self.options, self.protocols, self.errors, self.responses = (
            importlib.import_module(f"{package.__name__}.{name}")
            for name in ("options", "protocols", "errors", "responses")
        )
        self.models = importlib.import_module(f"{package.__name__}_models")
        self.room = importlib.import_module(f"{package.__name__}.types.rooms").RoomSocketRequestCodecs.parameter(
            location="path", name="room"
        ).from_wire("r1")

    def frame(self, data: bytes, *, text: bool = True) -> Any:
        return self.protocols.WSFrame(data=data, text=text)

    def client(self, connector: object, **settings: Any) -> Any:
        options = self.options
        return options.ClientOptions(
            base_url="https://api.example.com",
            retry=options.RetryOptions(initial_delay=0, jitter="none"),
            protocols=options.ProtocolClientOptions(
                websocket_connector=connector,
                websocket_transport=self.protocols.WebSocketTransportOptions(proxy="http://user:secret@proxy.test:3128"),
            ),
            **settings,
        )

    def rejected(self, status: int, *headers: tuple[str, str]) -> Any:
        return self.errors.HandshakeResponse(
            status_code=status, headers=self.responses.HeadersView(headers), body_prefix=b"", truncated=False
        )

    def transport_error(self, delivery: str) -> Any:
        errors = self.errors
        return errors.TransportError(delivery_state=errors.DeliveryState[delivery], phase="write")


def socket_connectors(package: ModuleType, lines: list[str]) -> None:
    """Open sessions through borrowed connectors, synchronously and with asyncio, and fail each step on purpose."""
    harness = _Harness(package, lines)
    _construction(harness)
    connector = _Connector(harness)
    with package.Client(options=harness.client(connector)) as api:
        _contract(harness, connector, api)
        _handshake_failures(harness, connector, api)
        _send_failures(harness, connector, api)
        _receive_failures(harness, connector, api)
        _queued_sends(harness, connector, api)
        _held_receives(harness, connector, api)
        _cancelled(harness, connector)
    _default_transport(harness, connector)
    lines.append("  borrowed connector left open")
    run(lambda: _async_connectors(harness))


def _construction(harness: _Harness) -> None:
    """Refuse a connector without open, and one of the other execution mode, when the client is built."""
    lines, options, package = harness.lines, harness.options, harness.package
    record(lines, "connector without open", lambda: options.ProtocolClientOptions(websocket_connector=object()))
    record(lines, "transport of another type", lambda: options.ProtocolClientOptions(websocket_transport=object()))
    record(lines, "asyncio connector for a synchronous client", lambda: package.Client(options=harness.client(_AsyncConnector(harness))))

    async def mismatch() -> None:
        await arecord(lines, "synchronous connector for an asyncio client", lambda: _construct(package, harness))

    run(mismatch)
    record(lines, "SOCKS proxy", lambda: harness.protocols.WebSocketTransportOptions(proxy="socks5://proxy.test:1080"))
    record(lines, "proxy URL without a host", lambda: harness.protocols.WebSocketTransportOptions(proxy="http://:8080"))
    record(lines, "proxy URL with a bad port", lambda: harness.protocols.WebSocketTransportOptions(proxy="http://proxy.test:x"))
    record(lines, "proxy URL of another type", lambda: harness.protocols.WebSocketTransportOptions(proxy=8080))
    context = ssl.create_default_context()
    record(
        lines,
        "proxy TLS context without an HTTPS proxy",
        lambda: harness.protocols.WebSocketTransportOptions(proxy="http://proxy.test:8080", proxy_ssl_context=context),
    )
    transport = harness.protocols.WebSocketTransportOptions(proxy="https://proxy.test:8443", proxy_ssl_context=context)
    lines.append(f"  proxy TLS context with an HTTPS proxy {transport.proxy_ssl_context is context}")
    record(lines, "TLS context of another type", lambda: harness.protocols.WebSocketTransportOptions(ssl_context=object()))
    record(lines, "compression of another kind", lambda: harness.protocols.WSOptions(compression="gzip"))
    record(lines, "zero message size", lambda: harness.protocols.WSOptions(max_message_bytes=0))


def _default_transport(harness: _Harness, connector: _Connector) -> None:
    """Hand a connector the default transport settings of a client that sets none."""
    options = harness.options
    protocols = options.ProtocolClientOptions(websocket_connector=connector)
    with harness.package.Client(options=options.ClientOptions(base_url="https://api.example.com", protocols=protocols)) as api:
        connector.verbose = True
        connector.queue.append(_Connection(harness))
        api.protocols.feed.text.connect().close()
        connector.verbose = False


async def _construct(package: ModuleType, harness: _Harness) -> Any:
    return package.AsyncClient(options=harness.client(_Connector(harness)))


def _contract(harness: _Harness, connector: _Connector, api: Any) -> None:
    """Hand the connector one resolved handshake per open, then send, receive, ping, and close through the session."""
    lines = harness.lines
    connector.verbose = True
    connection = _Connection(
        harness, subprotocol="chat.v2", frames=(harness.frame(_JOINED), harness.frame(b"plain", text=True))
    )
    connector.queue.append(connection)
    session = api.protocols.rooms.chat.connect(
        room=harness.room, ws_options=harness.protocols.WSOptions(send_timeout=None, pong_timeout=None)
    )
    lines.append(f"  session {session!r} headers {list(session.response.headers.items())}")
    lines.append(f"    received {session.receive().sequence}")
    session.send(harness.models.ClientMessage(text="hi"))
    lines.append(f"    ping {session.ping(b'probe')!r}")
    record(lines, "feed message of another type", session.receive)
    connector.queue.append(_Connection(harness))
    feed = api.protocols.feed.text.connect()
    feed.send("text")
    feed.close(1001)
    connector.queue.append(_Connection(harness, frames=(harness.frame(b"caf\xc3\xa9"), harness.frame(b"\xff"))))
    auth = importlib.import_module(f"{harness.package.__name__}.auth")
    signed = api.with_options(harness.options.RequestOptions(auth=auth.AuthConfig({"bearer": _Static(auth)})))
    secure = signed.protocols.secure.chat.connect()
    secure.send(b"bytes")
    secure.send(bytearray(b"array"))
    lines.append(f"    text {secure.receive().data!r}")
    record(lines, "text that is not UTF-8", secure.receive)
    secure.close()
    connector.verbose = False


def _handshake_failures(harness: _Harness, connector: _Connector, api: Any) -> None:
    """Map what a connector raises or returns: statuses, broken evidence, bad headers, and unclassified failures."""
    lines = harness.lines
    chat = api.protocols.rooms.chat

    def broken(failure: object) -> Callable[[Any], object]:
        def open_(context: Any) -> object:
            context.trace.phase_started("teleport")
            return failure

        return open_

    bad_headers = _Connection(harness)
    bad_headers.handshake_headers = {"x-socket": "1"}
    bad_subprotocol = _Connection(harness, subprotocol=b"chat.v2")
    for label, items in (
        ("status then 101", (harness.rejected(503), _Connection(harness, subprotocol="chat.v2"))),
        ("broken evidence with a status", (broken(harness.rejected(503)),)),
        ("refusal with 101", (harness.rejected(101),)),
        ("broken evidence with a connection", (broken(_Connection(harness)),)),
        ("headers of another type", (bad_headers,)),
        ("subprotocol of another type", (bad_subprotocol,)),
        ("unclassified failure", (RuntimeError("connector bug"),)),
    ):
        connector.queue.extend(items)
        session = record(lines, label, lambda: chat.connect(room=harness.room))
        if session is not None:
            session.close()
    options = harness.options
    moved = harness.rejected(302, ("Location", "/rooms/r2/socket"))
    connector.queue.extend((moved, moved))
    record(
        lines,
        "redirect without a parent send slot",
        lambda: chat.connect(
            room=harness.room,
            options=options.RequestOptions(redirects=options.RedirectOptions(enabled=True)),
            session_options=options.SessionOptions(max_network_sends=1),
        ),
    )
    record(
        lines,
        "no call send slot",
        lambda: chat.connect(room=harness.room, options=options.RequestOptions(max_network_sends=0)),
    )
    connector.queue.clear()


def _send_failures(harness: _Harness, connector: _Connector, api: Any) -> None:
    """Keep a session open after a send that sent nothing, and end it after one that may have gone or broke."""
    lines, errors = harness.lines, harness.errors
    for label, failure in (
        ("nothing sent before the timeout", TimeoutError()),
        ("message that may have gone", harness.transport_error("MAYBE_SENT")),
        ("transport failure before sending", harness.transport_error("NOT_SENT")),
        ("unclassified send failure", RuntimeError("send bug")),
        ("closed before sending", errors.WebSocketClosedError(code=1001, reason="away", clean=True)),
    ):
        connection = _Connection(harness, send=failure)
        connector.queue.append(connection)
        session = api.protocols.feed.text.connect()
        record(lines, label, lambda session=session: session.send("message"))
        lines.append(f"    progress {dict(session.progress)} {session!r}")
        session.close()


def _receive_failures(harness: _Harness, connector: _Connector, api: Any) -> None:
    """End a session after a receive or ping fails, and keep a close's failure beside the dropped connection."""
    lines, errors = harness.lines, harness.errors
    for label, receive, ping in (
        ("transport failure while receiving", harness.transport_error("RESPONSE_STARTED"), None),
        ("unclassified receive failure", RuntimeError("receive bug"), None),
        ("message over the limit", errors.ProtocolSizeError(kind="message", limit=8, observed=9, unit="bytes"), None),
        ("ping timeout", None, TimeoutError()),
        ("closed before the ping", None, errors.WebSocketClosedError(code=None, reason="", clean=False)),
        ("transport failure while pinging", None, harness.transport_error("RESPONSE_STARTED")),
    ):
        failures = {"receive": receive} if receive is not None else {"ping": ping}
        connector.queue.append(_Connection(harness, **failures))
        session = api.protocols.feed.text.connect()
        record(lines, label, session.receive if receive is not None else session.ping)
        session.close()
    connector.queue.append(_Connection(harness, close=OSError("close failed")))
    session = api.protocols.feed.text.connect()
    record(lines, "failing close", session.close)
    connector.queue.append(_Connection(harness, frames=(errors.WebSocketClosedError(code=1000, reason="", clean=True),)))
    session = api.protocols.feed.text.connect(ws_options=harness.protocols.WSOptions(idle_timeout=None))
    record(lines, "server closed", session.receive)


def _queued_sends(harness: _Harness, connector: _Connector, api: Any) -> None:
    """Time out a send that waits behind a blocked one, and let the next one take the turn it left."""
    lines = harness.lines
    connection = _Connection(harness)
    connection.blocked = threading.Event()
    connector.queue.append(connection)
    session = api.protocols.feed.text.connect(ws_options=harness.protocols.WSOptions(send_timeout=0.05))
    first = threading.Thread(target=session.send, args=("first",))
    first.start()
    connection.blocked.wait(5)
    connection.blocked = None
    record(lines, "send that waits too long", lambda: session.send("second"))
    connection.released.set()
    first.join(5)
    record(lines, "send after the queue moved on", lambda: session.send("third"))
    lines.append(f"    sends delivered {dict(session.progress)['messages_sent']}")
    session.close()


def _held_receives(harness: _Harness, connector: _Connector, api: Any) -> None:
    """End a receive waiting on a connection that its own close, or its client's, closes underneath it."""
    lines, errors = harness.lines, harness.errors
    ended = errors.WebSocketClosedError(code=1000, reason="", clean=True)
    for label, hold, iterated in (
        ("receive the session's close interrupted", harness.transport_error("RESPONSE_STARTED"), False),
        ("receive the session's close ended", ended, False),
        ("iteration the session's close ended", ended, True),
    ):
        connection = _Connection(harness)
        connection.hold = hold
        connector.queue.append(connection)
        session = api.protocols.feed.text.connect()
        outcome: list[str] = []
        call = (lambda session=session: list(session)) if iterated else session.receive
        waiting = threading.Thread(target=lambda call=call, outcome=outcome: outcome.append(_outcome(call)))
        waiting.start()
        connection.entered.wait(5)
        session.close()
        waiting.join(5)
        lines.append(f"  {label}: {outcome[0]}")
    closing = harness.package.Client(options=harness.client(connector, cleanup_timeout=0.05))
    connection = _Connection(harness)
    connection.hold = errors.WebSocketClosedError(code=1001, reason="", clean=True)
    connector.queue.append(connection)
    session = closing.protocols.feed.text.connect()
    outcome = []
    waiting = threading.Thread(target=lambda: outcome.append(_outcome(session.receive)))
    waiting.start()
    connection.entered.wait(5)
    record(lines, "client close with a receive waiting", closing.close)
    waiting.join(5)
    lines.append(f"  receive the client's close ended: {outcome[0]}")


async def _messages(session: Any) -> list[object]:
    return [message async for message in session]


def _outcome(call: Callable[[], object]) -> str:
    try:
        call()
    except Exception as error:  # noqa: BLE001
        return f"{type(error).__name__} {getattr(error, 'state', '')}".strip()
    return "returned"


def _cancelled(harness: _Harness, connector: _Connector) -> None:
    """Stop a synchronous queued send and receive at the cancel token's interval."""
    lines, options = harness.lines, harness.options
    token = options.CancelToken()
    with harness.package.Client(options=harness.client(connector, cancel_token=token)) as api:
        connection = _Connection(harness)
        connection.blocked = threading.Event()
        connector.queue.append(connection)
        session = api.protocols.feed.text.connect()
        first = threading.Thread(target=session.send, args=("first",))
        first.start()
        connection.blocked.wait(5)
        connection.blocked = None
        token.cancel()
        record(lines, "send cancelled while it waits", lambda: session.send("second"))
        connection.released.set()
        first.join(5)
        session.close()
    token = options.CancelToken()
    with harness.package.Client(options=harness.client(connector, cancel_token=token)) as api:
        connector.queue.append(_Connection(harness, receive=token.cancel))
        session = api.protocols.feed.text.connect()
        record(lines, "receive cancelled at the token interval", session.receive)
        session.close()


async def _async_connectors(harness: _Harness) -> None:
    """Open asyncio sessions through a borrowed connector and fail their sends, receives, and pings."""
    lines, errors = harness.lines, harness.errors
    connector = _AsyncConnector(harness)
    async with harness.package.AsyncClient(options=harness.client(connector)) as api:
        connector.queue.append(_AsyncConnection(harness, subprotocol="chat.v2", frames=(harness.frame(_JOINED),)))
        session = await api.protocols.rooms.chat.connect(room=harness.room)
        lines.append(f"  async session {session!r} received {(await session.receive()).sequence}")
        await session.send(harness.models.ClientMessage(text="hi"))
        lines.append(f"    async ping {await session.ping()!r}")
        await session.aclose()
        for label, failure in (
            ("async nothing sent before the timeout", TimeoutError()),
            ("async message that may have gone", harness.transport_error("MAYBE_SENT")),
            ("async transport failure before sending", harness.transport_error("NOT_SENT")),
            ("async closed before sending", errors.WebSocketClosedError(code=1001, reason="away", clean=True)),
        ):
            connector.queue.append(_AsyncConnection(harness, send=failure))
            session = await api.protocols.feed.text.connect()
            await arecord(lines, label, lambda session=session: session.send("message"))
            await session.aclose()
        for label, ping in (
            ("async ping timeout", TimeoutError()),
            ("async closed before the ping", errors.WebSocketClosedError(code=None, reason="", clean=False)),
            ("async transport failure while pinging", harness.transport_error("RESPONSE_STARTED")),
        ):
            connector.queue.append(_AsyncConnection(harness, ping=ping))
            session = await api.protocols.feed.text.connect()
            await arecord(lines, label, session.ping)
            await session.aclose()
        connector.queue.append(_AsyncConnection(harness, close=OSError("close failed")))
        session = await api.protocols.feed.text.connect()
        await arecord(lines, "async failing close", session.aclose)
        connection = _AsyncConnection(harness)
        connection.blocked = threading.Event()
        connector.queue.append(connection)
        session = await api.protocols.feed.text.connect(ws_options=harness.protocols.WSOptions(send_timeout=0.05))
        blocked = asyncio.create_task(session.send("first"))
        await asyncio.sleep(0)
        await arecord(lines, "async send that waits too long", lambda: session.send("second"))
        await arecord(lines, "async send stopped at its timeout", lambda: blocked)
        for label, frames in (
            ("async receive at the connection's deadline", (TimeoutError(),)),
            ("async server closed", (errors.WebSocketClosedError(code=1000, reason="", clean=True),)),
        ):
            connector.queue.append(_AsyncConnection(harness, frames=frames))
            session = await api.protocols.feed.text.connect()
            await arecord(lines, label, session.receive)
            await session.aclose()
        bad_headers = _AsyncConnection(harness)
        bad_headers.handshake_headers = {"x-socket": "1"}
        connector.queue.append(bad_headers)
        await arecord(lines, "async headers of another type", api.protocols.feed.text.connect)
        options = harness.options
        await arecord(
            lines,
            "async no call send slot",
            lambda: api.protocols.feed.text.connect(options=options.RequestOptions(max_network_sends=0)),
        )
        moved = harness.rejected(302, ("Location", "/rooms/r2/socket"))
        connector.queue.extend((moved, moved))
        await arecord(
            lines,
            "async redirect without a parent send slot",
            lambda: api.protocols.feed.text.connect(
                options=options.RequestOptions(redirects=options.RedirectOptions(enabled=True)),
                session_options=options.SessionOptions(max_network_sends=1),
            ),
        )
        connector.queue.clear()
        connection = _AsyncConnection(harness)
        connection.hold = harness.transport_error("RESPONSE_STARTED")
        connector.queue.append(connection)
        session = await api.protocols.feed.text.connect()
        waiting = asyncio.create_task(session.receive())
        while not connection.entered.is_set():
            await asyncio.sleep(0)
        await session.aclose()
        await arecord(lines, "async receive the session's close interrupted", lambda: waiting)
        connection = _AsyncConnection(harness)
        connection.hold = harness.errors.WebSocketClosedError(code=1000, reason="", clean=True)
        connector.queue.append(connection)
        session = await api.protocols.feed.text.connect()
        iterating = asyncio.create_task(_messages(session))
        while not connection.entered.is_set():
            await asyncio.sleep(0)
        await session.aclose()
        await arecord(lines, "async iteration the session's close ended", lambda: iterating)
        connection = _AsyncConnection(harness)
        connection.blocked = threading.Event()
        connector.queue.append(connection)
        session = await api.protocols.feed.text.connect()
        sending = asyncio.create_task(session.send("first"))
        await asyncio.sleep(0)
        sending.cancel()
        cancelled = (await asyncio.gather(sending, return_exceptions=True))[0]
        lines.append(f"  async send cancelled by its task {type(cancelled).__name__} {session!r}")
