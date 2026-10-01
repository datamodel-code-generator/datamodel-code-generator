"""Talk to local WebSocket servers through generated WebSocket helpers: handshakes, messages, limits, and closing."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import socket
import threading
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING, Any, Final

from tests.data.python.client_runtime import describe, run
from tests.data.python.fixture_websocket import Play, RawPeer, SocketServer, TunnelProxy, client_context

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from types import ModuleType

    from websockets.sync.server import ServerConnection

_JOINED: Final = json.dumps({"kind": "joined", "user": "ann"})
_UPGRADE: Final = (
    b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n"
)
_LARGE: Final = "x" * (32 * 1024 * 1024)
_PROBLEM: Final = (("Content-Type", "application/json"),)
_INVALID: Final = (("WWW-Authenticate", 'Bearer error="invalid_token"'),)


def _described(value: object) -> str:
    """Describe an outcome, naming a failure's cause by its class alone, whose text the platform and library choose."""
    text = describe(value)
    if isinstance(value, BaseException) and (cause := getattr(value, "cause", None)) is not None:
        text = text.replace(f"cause={cause!r}", f"cause={type(cause).__name__}")
    return text


def record(lines: list[str], label: str, call: Callable[[], object]) -> object:
    try:
        result = call()
    except Exception as error:  # noqa: BLE001
        lines.append(f"  {label} ! {_described(error)}")
        return None
    lines.append(f"  {label} = {_described(result)}")
    return result


async def arecord(lines: list[str], label: str, call: Callable[[], Any]) -> object:
    try:
        result = await call()
    except Exception as error:  # noqa: BLE001
        lines.append(f"  {label} ! {_described(error)}")
        return None
    lines.append(f"  {label} = {_described(result)}")
    return result


def _data(value: object) -> object:
    """Return a message's data as plain values, whatever its backend: a model, a dataclass, a Struct, or a dict."""
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if hasattr(value, "__struct_fields__"):
        return {name: _data(getattr(value, name)) for name in value.__struct_fields__}
    return value


def _message(message: Any) -> str:
    """Describe a message by its sequence, frame kind, data, and raw bytes."""
    return f"{message.sequence} {message.frame} {type(message.data).__name__}{_data(message.data)!r} raw={message.raw!r}"


def _chat(connection: ServerConnection) -> None:
    """Greet the client, echo each message's text in uppercase, and close normally after its goodbye."""
    connection.send(_JOINED)
    for message in connection:
        text = json.loads(message)["text"]
        connection.send(json.dumps({"kind": "said", "text": text.upper()}))
        if text == "bye":
            connection.close(1000, "done")
            return


def _echo(connection: ServerConnection) -> None:
    """Send a binary greeting, echo one text message as bytes, and close normally."""
    connection.send(b"\x00\x01")
    connection.send(connection.recv().encode())
    connection.close()


def _sending(*messages: str | bytes, code: int | None = None, reason: str = "") -> Callable[[ServerConnection], None]:
    """Return a talk that sends messages, then closes with a code when given or waits for the client."""

    def talk(connection: ServerConnection) -> None:
        for message in messages:
            connection.send(message)
        if code is not None:
            connection.close(code, reason)

    return talk


def _replying(connection: ServerConnection) -> None:
    """Answer the first message, then wait for the client."""
    connection.recv()
    connection.send("secret-reply")


def _closing(connection: ServerConnection) -> None:
    connection.close()


class _Harness:
    """A generated WebSocket package's public modules, its models, and the fixture server its clients reach."""

    def __init__(self, package: ModuleType, lines: list[str], server: SocketServer) -> None:
        self.package = package
        self.lines = lines
        self.server = server
        self.options, self.protocols, self.errors, self.auth = (
            importlib.import_module(f"{package.__name__}.{name}") for name in ("options", "protocols", "errors", "auth")
        )
        self.models = importlib.import_module(f"{package.__name__}_models")

    def transport(self, **settings: Any) -> Any:
        """Return transport settings that trust the fixture's certificate."""
        return self.protocols.WebSocketTransportOptions(ssl_context=client_context(), **settings)

    def client(self, url: str | None = None, *, transport: Any = None, **settings: Any) -> Any:
        """Return client options reaching a server without retry delays, with the fixture's TLS trust."""
        options = self.options
        return options.ClientOptions(
            base_url=url or self.server.url,
            retry=options.RetryOptions(initial_delay=0, jitter="none"),
            protocols=options.ProtocolClientOptions(websocket_transport=transport or self.transport()),
            **settings,
        )

    def room(self, value: str = "r1") -> object:
        """Return the room argument of the chat helper for a wire value."""
        types = importlib.import_module(f"{self.package.__name__}.types.rooms")
        return types.RoomSocketRequestCodecs.parameter(location="path", name="room").from_wire(value)

    def since(self, value: int) -> object:
        """Return the since argument of the chat helper for a wire value."""
        types = importlib.import_module(f"{self.package.__name__}.types.rooms")
        return types.RoomSocketRequestCodecs.parameter(location="query", name="since").from_wire(value)

    def trace(self, value: str) -> object:
        """Return the X-Trace argument of the secure helper for a wire value."""
        types = importlib.import_module(f"{self.package.__name__}.types.secure")
        return types.SecureSocketRequestCodecs.parameter(location="header", name="X-Trace").from_wire(value)

    def text(self, value: str) -> object:
        """Return a message the chat helper sends."""
        return self.models.ClientMessage(text=value)

    def ws(self, **limits: Any) -> Any:
        """Return WebSocket options."""
        return self.protocols.WSOptions(**limits)

    def report(self, *plays: Play) -> None:
        """Report what the server saw in each play, once it ended."""
        self.lines.extend(play.report() for play in plays)


def sockets(package: ModuleType, lines: list[str]) -> None:
    """Open WebSocket sessions to local servers through the synchronous and asyncio clients of a generated package."""
    server = SocketServer()
    harness = _Harness(package, lines, server)
    try:
        with package.Client(options=harness.client()) as api:
            _conversation(harness, api)
            _channels(harness, api)
            _refusals(harness, api)
            _decoding(harness, api)
            _limits(harness, api)
            _closing_sessions(harness, api)
        with harness.package.Client(options=harness.client(auth=harness.auth.AuthConfig({"bearer": _Tokens(harness.auth)}))) as api:
            _sends(harness, api)
        _hooked(harness)
        _authenticated(harness)
        _logged(harness)
        _client_close(harness)
        _handshakes(harness)
        _peers(harness)
        _proxies(harness)
        run(lambda: _async_sockets(harness))
        run(lambda: _async_hooked(harness))
    finally:
        server.stop()


def _conversation(harness: _Harness, api: Any) -> None:
    """Send and receive typed messages until the server closes normally, then refuse every further step."""
    lines = harness.lines
    (play,) = harness.server.play(Play(talk=_chat))
    with api.protocols.rooms.chat.connect(room=harness.room("r 1"), since=harness.since(5)) as session:
        lines.append(f"  connected {session.response.status_code} subprotocol={session.subprotocol!r} {session!r}")
        lines.append(f"    {_message(session.receive())}")
        session.send(harness.text("hi"))
        lines.append(f"    {_message(session.receive())}")
        session.send(harness.text("bye"))
        lines.extend(f"    iterated {_message(message)}" for message in session)
        lines.append(f"    progress {dict(session.progress)}")
        record(lines, "receive after the server closed", session.receive)
        record(lines, "send after the server closed", lambda: session.send(harness.text("late")))
        record(lines, "ping after the server closed", session.ping)
        lines.append(f"    iteration after the end {list(session)}")
    harness.report(play)


def _large(connection: ServerConnection) -> None:
    """Echo the size of one message and close normally."""
    connection.send(str(len(connection.recv())))
    connection.close()


def _slow(connection: ServerConnection) -> None:
    """Read the first fragment of a message, then nothing for a while, then the rest."""
    fragments = connection.recv_streaming()
    next(fragments)
    threading.Event().wait(1.5)
    for _ in fragments:
        pass


def _aborting(connection: ServerConnection) -> None:
    """Drop the connection once the first fragment of a message arrived."""
    fragments = connection.recv_streaming()
    next(fragments)
    connection.close_socket()
    for _ in fragments:
        pass


def _sends(harness: _Harness, api: Any) -> None:
    """Fragment a long message, refuse a send past its timeout, and give up on one cut off midway."""
    lines, server = harness.lines, harness.server
    (play,) = server.play(Play(talk=_large))
    with api.protocols.secure.chat.connect() as session:
        session.send(b"y" * 100000)
        lines.append(f"  fragmented {_message(session.receive())}")
    harness.report(play)
    for label, talk, limits in (
        ("send past its timeout", _closing, {"send_timeout": 1e-9}),
        ("send overdue between fragments", _slow, {"send_timeout": 0.3}),
        ("send cut off midway", _aborting, {}),
    ):
        server.play(Play(talk=talk))
        session = api.protocols.feed.text.connect(ws_options=harness.ws(close_timeout=0.1, **limits))
        record(lines, label, lambda session=session: session.send(_LARGE))
        session.close()


def _channels(harness: _Harness, api: Any) -> None:
    """Exchange text and bytes, ping, and compress where the helper permits it."""
    lines = harness.lines
    plays = harness.server.play(Play(talk=_echo), Play(talk=_chat))
    with api.protocols.feed.text.connect() as session:
        lines.append(f"  bytes channel {_message(session.receive())} subprotocol={session.subprotocol!r}")
        receipt = session.ping(b"probe")
        lines.append(f"    ping {type(receipt).__name__} {receipt.latency >= 0}")
        session.send("hello")
        lines.append(f"    {_message(session.receive())}")
        record(lines, "receive after a normal close", session.receive)
    with api.protocols.rooms.chat.connect(room=harness.room(), ws_options=harness.ws(compression="deflate")) as session:
        lines.append(f"  compressed {_message(session.receive())}")
        session.send(harness.text("bye"))
        lines.extend(f"    iterated {_message(message)}" for message in session)
    harness.report(*plays)
    record(
        lines,
        "compression the helper does not permit",
        lambda: api.protocols.feed.text.connect(ws_options=harness.ws(compression="deflate")),
    )


def _refusals(harness: _Harness, api: Any) -> None:
    """Refuse handshakes with statuses, retry them, follow a redirect, and refuse what fails before sending."""
    lines, server, options = harness.lines, harness.server, harness.options
    chat = api.protocols.rooms.chat
    cases: tuple[tuple[str, tuple[Play, ...], dict[str, Any]], ...] = (
        ("declared error", (Play(refuse=(404, _PROBLEM, b'{"detail":"no room"}')),), {}),
        ("undeclared success", (Play(refuse=(200, (), b"plain")),), {}),
        (
            "retried",
            (Play(refuse=(503, (), b"")), Play(refuse=(503, (), b"")), Play(talk=_closing)),
            {},
        ),
        ("not retried", (Play(refuse=(503, (), b"")),), {"options": options.RequestOptions(retry=options.RetryOptions(max_retries=0))}),
        ("retry after too long", (Play(refuse=(429, (("Retry-After", "120"),), b"")),), {}),
        (
            "redirected",
            (Play(refuse=(302, (("Location", "/rooms/r2/socket"),), b"")), Play(talk=_closing)),
            {"options": options.RequestOptions(redirects=options.RedirectOptions(enabled=True))},
        ),
        (
            "redirected to a wss URL",
            (Play(refuse=(307, (("Location", f"WSS://localhost:{server.port}/rooms/r3/socket"),), b"")), Play(talk=_closing)),
            {"options": options.RequestOptions(redirects=options.RedirectOptions(enabled=True))},
        ),
        (
            "redirected to a ws URL",
            (Play(refuse=(307, (("Location", f"ws://localhost:{server.port}/rooms/r3/socket"),), b"")),),
            {"options": options.RequestOptions(redirects=options.RedirectOptions(enabled=True))},
        ),
        ("redirect not followed", (Play(refuse=(302, (("Location", "/rooms/r2/socket"),), b"")),), {}),
        ("subprotocol not selected", (Play(subprotocol=None),), {}),
        (
            "session without room for a retry",
            (Play(refuse=(503, (), b"")),),
            {"session_options": options.SessionOptions(max_network_sends=1)},
        ),
    )
    for label, plays, arguments in cases:
        server.play(*plays)
        session = record(lines, label, lambda arguments=arguments: chat.connect(room=harness.room(), **arguments))
        if session is not None:
            lines.append(
                f"    attempts {session.response.resource_attempt_count} redirects {session.response.redirect_count}"
            )
            lines.extend(f"    iterated {_message(message)}" for message in session)
        harness.report(*plays)
    for label, call in (
        ("managed header", lambda: chat.connect(room=harness.room(), options=options.RequestOptions(headers=(("Upgrade", "h2c"),)))),
        ("no send slot", lambda: chat.connect(room=harness.room(), session_options=options.SessionOptions(max_network_sends=0))),
        ("reconnecting", lambda: chat.connect(room=harness.room(), ws_options=harness.ws(reconnect=True))),
        ("options of another type", lambda: chat.connect(room=harness.room(), ws_options=options.RequestOptions())),
        ("request options of another type", lambda: chat.connect(room=harness.room(), options=harness.ws())),
        ("session options of another type", lambda: chat.connect(room=harness.room(), session_options=harness.ws())),
    ):
        record(lines, label, call)


def _decoding(harness: _Harness, api: Any) -> None:
    """Refuse messages that do not decode as declared, closing with 1002, and values that do not encode."""
    lines, server = harness.lines, harness.server
    for label, talk, helper in (
        ("not JSON", _sending("{broken"), "chat"),
        ("refused by its schema", _sending('{"kind": "said"}'), "chat"),
        ("binary frame for text", _sending(_JOINED.encode()), "chat"),
        ("text frame for bytes", _sending("text"), "feed"),
        ("large undecodable", _sending('"' + "x" * 70000), "chat"),
    ):
        (play,) = server.play(Play(talk=talk))
        session = (
            api.protocols.rooms.chat.connect(room=harness.room())
            if helper == "chat"
            else api.protocols.feed.text.connect()
        )
        try:
            session.receive()
        except harness.errors.StreamDecodeError as failure:
            lines.append(
                f"  {label} ! {_described(failure)} sequence={failure.sequence} prefix={len(failure.raw_prefix)} "
                f"truncated={failure.truncated} cause={type(failure.cause).__name__}"
            )
        record(lines, "after the decode failure", session.receive)
        session.close()
        harness.report(play)
    plays = server.play(Play(), Play())
    chat = api.protocols.rooms.chat.connect(room=harness.room())
    record(lines, "message of another type", lambda: chat.send(42))
    chat.close()
    feed = api.protocols.feed.text.connect()
    record(lines, "bytes for text", lambda: feed.send(b"bytes"))
    feed.close()
    harness.report(*plays)


def _limits(harness: _Harness, api: Any) -> None:
    """Refuse a message over its limit with 1009, end an idle wait with 1001, and report an abnormal closure."""
    lines, server, options = harness.lines, harness.server, harness.options
    chat = api.protocols.rooms.chat
    for label, play, arguments in (
        ("message over the limit", Play(talk=_sending(json.dumps({"kind": "said", "text": "x" * 64}))), {"ws_options": harness.ws(max_message_bytes=32)}),
        ("idle", Play(), {"ws_options": harness.ws(idle_timeout=0.05)}),
        ("idle inherited", Play(), {"options": options.RequestOptions(stream_idle_timeout=0.05)}),
        ("session deadline", Play(), {"session_options": options.SessionOptions(total_timeout=1.0), "ws_options": harness.ws(idle_timeout=None)}),
        ("abnormal closure", Play(talk=_sending(code=1011, reason="boom")), {}),
        ("server's own message limit", Play(talk=_sending(code=1009, reason="peer limit")), {}),
    ):
        server.play(play)
        session = chat.connect(room=harness.room(), **arguments)
        record(lines, label, session.receive)
        record(lines, f"{label} again", session.receive)
        harness.report(play)
    (play,) = server.play(Play(talk=_sending(_JOINED, code=4001, reason="kicked")))
    session = chat.connect(room=harness.room())
    try:
        lines.extend(f"  iterated {_message(message)}" for message in session)
    except harness.errors.WebSocketClosedError as closed:
        lines.append(f"  abnormal end of iteration code={closed.code} reason={closed.reason!r} clean={closed.clean}")
    harness.report(play)


def _closing_sessions(harness: _Harness, api: Any) -> None:
    """Close with a code and reason, refuse invalid ones, and refuse every step after closing."""
    lines, server = harness.lines, harness.server
    (play,) = server.play(Play(talk=_sending(_JOINED)))
    session = api.protocols.rooms.chat.connect(room=harness.room())
    for label, call in (
        ("reserved close code", lambda: session.close(1005)),
        ("close code of another type", lambda: session.close("1000")),
        ("close reason over 123 bytes", lambda: session.close(4000, "x" * 124)),
        ("ping payload over 125 bytes", lambda: session.ping(b"x" * 126)),
        ("ping payload of another type", lambda: session.ping("probe")),
    ):
        record(lines, label, call)
    session.close(4000, "bye")
    session.close()
    for label, call in (
        ("receive after closing", session.receive),
        ("send after closing", lambda: session.send(harness.text("late"))),
        ("ping after closing", session.ping),
    ):
        record(lines, label, call)
    lines.append(f"  iteration after closing {list(session)}")
    harness.report(play)
    (play,) = server.play(Play(talk=_closing))
    session = api.protocols.feed.text.connect()
    harness.report(play)
    record(lines, "ping once the server closed unread", session.ping)


class _Ends:
    """A hook reporting each event name and outcome of the calls it observes."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    def on_event(self, event: Any) -> None:
        self.lines.append(f"    hook {event.name} outcome={event.outcome} status={event.status}")


class _AsyncEnds(_Ends):
    async def on_event(self, event: Any) -> None:
        super().on_event(event)


def _hooked(harness: _Harness) -> None:
    """Report a session's handshake events and its end: normal, closed early, or failed."""
    lines, server = harness.lines, harness.server
    with harness.package.Client(options=harness.client(hooks=(_Ends(lines),))) as api:
        chat = api.protocols.rooms.chat
        for label, play in (
            ("hooked normal end", Play(talk=_sending(code=1000))),
            ("hooked early close", None),
            ("hooked failure", Play(talk=_sending("{broken"))),
            ("hooked refusal", Play(refuse=(404, _PROBLEM, b'{"detail":"no room"}'))),
        ):
            (played,) = server.play(play or Play())
            lines.append(f"  {label}")
            session = record(lines, "connect", lambda: chat.connect(room=harness.room()))
            if session is not None:
                if play is not None:
                    record(lines, "receive", session.receive)
                session.close()
            harness.report(played)


async def _async_hooked(harness: _Harness) -> None:
    """Report an asyncio session's handshake events and its end, and a refused handshake's."""
    lines, server = harness.lines, harness.server
    async with harness.package.AsyncClient(options=harness.client(hooks=(_AsyncEnds(lines),))) as api:
        chat = api.protocols.rooms.chat
        for label, play in (
            ("async hooked normal end", Play(talk=_sending(code=1000))),
            ("async hooked refusal", Play(refuse=(404, _PROBLEM, b'{"detail":"no room"}'))),
        ):
            server.play(play)
            lines.append(f"  {label}")
            session = await arecord(lines, "connect", lambda: chat.connect(room=harness.room()))
            if session is not None:
                await arecord(lines, "receive", session.receive)
                await session.aclose()
            await asyncio.to_thread(play.done.wait, 10)
            harness.report(play)


class _Tokens:
    """A bearer provider that replaces its token once the server rejects the first."""

    def __init__(self, auth: ModuleType) -> None:
        self.auth = auth
        self.tokens = iter(("first", "second"))
        self.current = self.next()
        self.calls: list[str] = []

    def next(self) -> Any:
        return self.auth.BearerCredential(self.auth.AccessToken(f"{next(self.tokens)}-material"), self.auth.TokenVersion())

    def get(self, context: object) -> Any:
        del context
        self.calls.append("get")
        return self.current

    def invalidate(self, version: object) -> None:
        self.calls.append(f"invalidate current={version is self.current.version}")

    def refresh(self, context: object) -> Any:
        del context
        self.calls.append("refresh")
        self.current = self.next()
        return self.current


class _AsyncTokens(_Tokens):
    async def get(self, context: object) -> Any:  # ty: ignore[invalid-method-override]
        return super().get(context)

    async def invalidate(self, version: object) -> None:  # ty: ignore[invalid-method-override]
        super().invalidate(version)

    async def refresh(self, context: object) -> Any:  # ty: ignore[invalid-method-override]
        return super().refresh(context)


def _authenticated(harness: _Harness) -> None:
    """Authenticate each handshake, refreshing a rejected token once, and send a header parameter."""
    lines, server, options = harness.lines, harness.server, harness.options
    for label, retry in (("refreshed after invalid_token", None), ("no refresh without retries", 0)):
        tokens = _Tokens(harness.auth)
        settings = {} if retry is None else {"retry": options.RetryOptions(max_retries=retry)}
        client_options = harness.client(auth=harness.auth.AuthConfig({"bearer": tokens}))
        plays = server.play(Play(refuse=(401, _INVALID, b"")), *(() if retry == 0 else (Play(talk=_closing),)))
        with harness.package.Client(options=client_options) as api:
            session = record(
                lines,
                label,
                lambda settings=settings, api=api: api.protocols.secure.chat.connect(
                    x_trace=harness.trace("t1"), options=options.RequestOptions(**settings)
                ),
            )
            if session is not None:
                session.close()
        lines.append(f"    provider {tokens.calls}")
        harness.report(*plays)


class _Captured(logging.Handler):
    """Keep the messages of the records the WebSocket library's client loggers emit."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("websockets.client"):
            self.messages.append(record.getMessage())


def _logged(harness: _Harness) -> None:
    """Emit no client debug records, which would hold headers and messages, even with every logger at DEBUG."""
    lines, server = harness.lines, harness.server
    root, captured = logging.getLogger(), _Captured()
    level = root.level
    root.addHandler(captured)
    root.setLevel(logging.DEBUG)
    try:
        (play,) = server.play(Play(talk=_replying))
        client_options = harness.client(auth=harness.auth.AuthConfig({"bearer": _Tokens(harness.auth)}))
        with harness.package.Client(options=client_options) as api:
            session = api.protocols.secure.chat.connect()
            session.send(b"secret-payload")
            lines.append(f"  received with debug logging {_message(session.receive())}")
            session.close()
        harness.report(play)
    finally:
        root.removeHandler(captured)
        root.setLevel(level)
    secrets = [message for message in captured.messages if "secret" in message or "material" in message]
    lines.append(f"    client debug records {len(captured.messages)} with secrets {len(secrets)}")


def _client_close(harness: _Harness) -> None:
    """Close a client with a session open: the session closes with 1001 and refuses its next receive."""
    lines, server = harness.lines, harness.server
    (play,) = server.play(Play())
    api = harness.package.Client(options=harness.client(cleanup_timeout=0.05))
    session = api.protocols.rooms.chat.connect(room=harness.room())
    record(lines, "client close with a session open", api.close)
    record(lines, "receive after the client closed", session.receive)
    harness.report(play)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _handshakes(harness: _Harness) -> None:
    """Classify handshakes that break the protocol, time out, or never connect."""
    lines, options = harness.lines, harness.options
    once = options.RequestOptions(retry=options.RetryOptions(max_retries=0))
    accept = b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n"
    for label, reply, arguments in (
        ("open timeout", None, {"ws_options": harness.ws(open_timeout=1.0)}),
        ("deadline during the open", None, {"options": options.RequestOptions(total_timeout=1.0)}),
        ("closed before a response", b"", {}),
        ("malformed response", b"NOT-HTTP\r\n\r\n", {}),
        ("missing upgrade", b"HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n", {}),
        ("wrong accept", b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: wrong\r\n\r\n", {}),
        ("subprotocol not offered", accept + b"Sec-WebSocket-Protocol: other\r\n\r\n", {}),
        ("header line too long", accept + b"X-Long: " + b"x" * 9000 + b"\r\n\r\n", {}),
    ):
        peer = RawPeer(reply)
        try:
            with harness.package.Client(options=harness.client(peer.url)) as api:
                record(
                    lines,
                    label,
                    lambda api=api, arguments=arguments: api.protocols.feed.text.connect(
                        **{"options": once, **arguments}
                    ),
                )
        finally:
            peer.stop()
    with harness.package.Client(options=harness.client(f"https://localhost:{_free_port()}")) as api:
        record(lines, "refused connection", lambda: api.protocols.feed.text.connect(options=once))
    with harness.package.Client(options=harness.client(transport=harness.protocols.WebSocketTransportOptions())) as api:
        record(lines, "untrusted certificate", lambda: api.protocols.feed.text.connect(options=once))


def _peers(harness: _Harness) -> None:
    """Fail a session whose peer never answers a ping, explicit or kept alive, or hangs up while one waits."""
    lines = harness.lines
    peer = RawPeer(_UPGRADE)
    hangup = RawPeer(_UPGRADE, hangup=True)
    try:
        with harness.package.Client(options=harness.client(hangup.url)) as api:
            session = api.protocols.feed.text.connect(ws_options=harness.ws(close_timeout=0.1))
            record(lines, "peer hanging up during a ping", session.ping)
        with harness.package.Client(options=harness.client(peer.url)) as api:
            feed = api.protocols.feed.text
            session = feed.connect(ws_options=harness.ws(pong_timeout=0.1, ping_interval=None, close_timeout=0.1))
            record(lines, "unanswered ping", session.ping)
            record(lines, "after the unanswered ping", session.receive)
            session = feed.connect(
                ws_options=harness.ws(ping_interval=0.05, pong_timeout=0.05, close_timeout=0.1, idle_timeout=None)
            )
            record(lines, "unanswered keepalive", session.receive)
    finally:
        peer.stop()
        hangup.stop()
    _concurrent_pings(harness)


def _concurrent_pings(harness: _Harness) -> None:
    """Refuse a ping whose payload another ping waits for without failing the session, and give default pings unique
    payloads; every ping still waiting ends when the peer hangs up.
    """
    lines, peer = harness.lines, RawPeer(_UPGRADE)
    try:
        with harness.package.Client(options=harness.client(peer.url)) as api:
            session = api.protocols.feed.text.connect(ws_options=harness.ws(ping_interval=None, close_timeout=0.1))
            waiting: list[str] = []
            threads = []
            for count, (label, payload) in enumerate(
                (("first ping", b"same"), ("default ping", b""), ("another default ping", b"")), start=1
            ):
                thread = threading.Thread(
                    target=lambda label=label, payload=payload: record(waiting, label, lambda: session.ping(payload))
                )
                thread.start()
                threads.append(thread)
                peer.wait_records(count)
                if count == 1:
                    record(lines, "ping with a payload another ping waits for", lambda: session.ping(b"same"))
            lines.append(f"    pings waiting {peer.records} {session!r}")
            peer.release.set()
            for thread in threads:
                thread.join(10)
            lines.extend(sorted(waiting))
    finally:
        peer.stop()


@contextmanager
def _environment(**values: str) -> Iterator[None]:
    """Set environment variables for a block, restoring them afterwards."""
    saved = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                del os.environ[name]
            else:
                os.environ[name] = value


def _proxies(harness: _Harness) -> None:
    """Tunnel through an explicit or environment proxy, and report a proxy's refusal."""
    lines, server = harness.lines, harness.server
    proxy = TunnelProxy(server.port)
    refusing = TunnelProxy(server.port, refuse=b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")
    garbled = TunnelProxy(server.port, refuse=b"NOT-HTTP\r\n\r\n")
    try:
        for label, transport, environment in (
            ("explicit proxy", harness.transport(proxy=proxy.url), {}),
            ("environment proxy", harness.transport(trust_env=True), {"https_proxy": proxy.url, "no_proxy": ""}),
        ):
            plays = server.play(Play(talk=_echo))
            with _environment(**environment), harness.package.Client(options=harness.client(transport=transport)) as api:
                session = api.protocols.feed.text.connect()
                lines.append(f"  {label} {_message(session.receive())}")
                session.close()
            harness.report(*plays)
        port = str(server.port)
        lines.append(f"  proxy requests {[request.replace(port, '<port>') for request in proxy.requests]}")
        for label, broken in (("refusing proxy", refusing), ("garbled proxy", garbled)):
            with harness.package.Client(options=harness.client(transport=harness.transport(proxy=broken.url))) as api:
                record(lines, label, api.protocols.feed.text.connect)
            lines.append(f"  {label} requests {[request.replace(port, '<port>') for request in broken.requests]}")
        for label, environment_proxy in (
            ("environment proxy of another scheme", "ftp://proxy.test:21"),
            ("environment proxy user without a password", "http://user@proxy.test:3128"),
        ):
            with (
                _environment(https_proxy=environment_proxy, no_proxy=""),
                harness.package.Client(options=harness.client(transport=harness.transport(trust_env=True))) as api,
            ):
                record(lines, label, api.protocols.feed.text.connect)
    finally:
        proxy.stop()
        refusing.stop()
        garbled.stop()


async def _async_sockets(harness: _Harness) -> None:
    """Open, use, and close sessions with asyncio, including the waits its guard stops."""
    lines, server, options = harness.lines, harness.server, harness.options
    async with harness.package.AsyncClient(options=harness.client()) as api:
        chat = api.protocols.rooms.chat
        (play,) = server.play(Play(talk=_chat))
        async with await chat.connect(room=harness.room()) as session:
            lines.append(f"  async connected {session.response.status_code} {session.subprotocol!r}")
            lines.append(f"    {_message(await session.receive())}")
            await session.send(harness.text("bye"))
            async for message in session:
                lines.append(f"    async iterated {_message(message)}")
            await arecord(lines, "async receive after the end", session.receive)
            await arecord(lines, "async send after the end", lambda: session.send(harness.text("late")))
        harness.report(play)
        (play,) = server.play(Play(talk=_echo))
        session = await api.protocols.feed.text.connect()
        lines.append(f"  async bytes {_message(await session.receive())}")
        receipt = await session.ping()
        lines.append(f"    async ping {receipt.latency >= 0}")
        await session.send("hello")
        lines.append(f"    {_message(await session.receive())}")
        await session.aclose()
        harness.report(play)
        (play,) = server.play(Play())
        session = await chat.connect(room=harness.room())
        waiting = asyncio.create_task(session.receive())
        await asyncio.sleep(0)
        await arecord(lines, "async concurrent receive", session.receive)
        await session.aclose(4000, "bye")
        await arecord(lines, "async receive closed meanwhile", lambda: waiting)
        await arecord(lines, "async receive after closing", session.receive)
        lines.append(f"  async iteration after closing {[message async for message in session]}")
        await arecord(lines, "async invalid close", lambda: session.aclose(1015))
        harness.report(play)
        for label, play, arguments in (
            ("async refusal", Play(refuse=(404, _PROBLEM, b'{"detail":"no room"}')), {}),
            ("async decode failure", Play(talk=_sending("{broken")), {}),
            ("async idle", Play(), {"ws_options": harness.ws(idle_timeout=0.05)}),
            ("async session deadline", Play(), {"session_options": options.SessionOptions(total_timeout=1.0), "ws_options": harness.ws(idle_timeout=None)}),
            ("async abnormal closure", Play(talk=_sending(code=1011, reason="boom")), {}),
            ("async server's own message limit", Play(talk=_sending(code=1009, reason="peer limit")), {}),
        ):
            server.play(play)
            session = await arecord(lines, f"{label} connect", lambda arguments=arguments: chat.connect(room=harness.room(), **arguments))
            if session is not None:
                await arecord(lines, label, session.receive)
                await session.aclose()
            harness.report(play)
        for label, step in (
            ("async send once the server closed unread", lambda session: session.send("late")),
            ("async ping once the server closed unread", lambda session: session.ping()),
        ):
            (play,) = server.play(Play(talk=_closing))
            session = await api.protocols.feed.text.connect()
            await asyncio.to_thread(play.done.wait, 10)
            harness.report(play)
            await arecord(lines, label, lambda step=step, session=session: step(session))
        token = options.CancelToken()
        (play,) = server.play(Play())
        session = await chat.connect(room=harness.room(), options=options.RequestOptions(cancel_token=token))
        asyncio.get_running_loop().call_later(0.05, token.cancel)
        await arecord(lines, "async cancelled receive", session.receive)
        harness.report(play)
        (play,) = server.play(Play())
        session = await chat.connect(room=harness.room())
        waiting = asyncio.create_task(session.receive())
        await asyncio.sleep(0)
        waiting.cancel()
        cancelled = (await asyncio.gather(waiting, return_exceptions=True))[0]
        lines.append(f"  async receive cancelled by its task {type(cancelled).__name__}")
        await arecord(lines, "async receive after the cancellation", session.receive)
        harness.report(play)
    (play,) = server.play(Play())
    api = harness.package.AsyncClient(options=harness.client(cleanup_timeout=1.0))
    session = await api.protocols.rooms.chat.connect(room=harness.room())
    waiting = asyncio.create_task(session.receive())
    await asyncio.sleep(0)
    await api.aclose()
    await arecord(lines, "async receive stopped by the client closing", lambda: waiting)
    harness.report(play)
    peer = RawPeer(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n")
    try:
        async with harness.package.AsyncClient(options=harness.client(peer.url)) as api:
            session = await api.protocols.feed.text.connect(ws_options=harness.ws(pong_timeout=0.1, close_timeout=0.1))
            await arecord(lines, "async unanswered ping", session.ping)
    finally:
        peer.stop()
    waiting = RawPeer(_UPGRADE)
    try:
        async with harness.package.AsyncClient(options=harness.client(waiting.url)) as api:
            session = await api.protocols.feed.text.connect(ws_options=harness.ws(ping_interval=None, close_timeout=0.1))
            first = asyncio.create_task(session.ping(b"same"))
            await asyncio.to_thread(waiting.wait_records, 1)
            await arecord(lines, "async ping with a payload another ping waits for", lambda: session.ping(b"same"))
            second = asyncio.create_task(session.ping())
            await asyncio.to_thread(waiting.wait_records, 2)
            third = asyncio.create_task(session.ping())
            await asyncio.to_thread(waiting.wait_records, 3)
            lines.append(f"    async pings waiting {waiting.records} {session!r}")
            waiting.release.set()
            for label, task in (("async first ping", first), ("async default ping", second), ("async another default ping", third)):
                await arecord(lines, label, lambda task=task: task)
    finally:
        waiting.stop()
    silent = RawPeer(None)
    try:
        async with harness.package.AsyncClient(options=harness.client(silent.url)) as api:
            once = options.RequestOptions(retry=options.RetryOptions(max_retries=0))
            await arecord(lines, "async open timeout", lambda: api.protocols.feed.text.connect(options=once, ws_options=harness.ws(open_timeout=1.0)))
    finally:
        silent.stop()
