"""Talk to local WebSocket servers through generated WebSocket helpers: handshakes, messages, limits, and closing."""

from __future__ import annotations

import asyncio
import importlib
import itertools
import json
import logging
import socket
import subprocess
import sys
import threading
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import httpcore2
import httpx2

from tests.data.python.client_limiters import _AsyncSemaphoreLimiter, _SemaphoreLimiter
from tests.data.python.client_regressions import json_error_body, retained_body
from tests.data.python.client_runtime import argument, describe, run
from tests.data.python.fixture_websocket import Play, RawPeer, SocketServer, TunnelProxy, client_context

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
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


async def aconnected(
    lines: list[str], label: str, connecting: Callable[[], Any], use: Callable[[Any], Awaitable[object]] | None = None
) -> None:
    """Enter an asyncio connect's block, recording its session or its handshake's failure, and use the session there."""
    try:
        async with connecting() as session:
            lines.append(f"  {label} = {_described(session)}")
            if use is not None:
                await use(session)
    except Exception as error:  # noqa: BLE001
        lines.append(f"  {label} ! {_described(error)}")


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


def _answering(connection: ServerConnection) -> None:
    """Send the first message back as bytes, then wait for the client."""
    connection.send(connection.recv().encode())


def _closing(connection: ServerConnection) -> None:
    connection.close()


class _Harness:
    """A generated WebSocket package's public modules, its models, and the fixture server its clients reach."""

    def __init__(self, package: ModuleType, lines: list[str], server: SocketServer) -> None:
        self.package = package
        self.lines = lines
        self.server = server
        self.options, self.protocols, self.errors = (
            importlib.import_module(f"{package.__name__}.{name}") for name in ("options", "protocols", "errors")
        )
        self.models = importlib.import_module(f"{package.__name__}_models")

    def transport(self, **settings: Any) -> Any:
        """Return transport settings that trust the fixture's certificate."""
        return self.options.TransportOptions(ssl_context=client_context(), **settings)

    def client(self, url: str | None = None, *, transport: Any = None, **settings: Any) -> Any:
        """Return client options reaching a server without retry delays, with the fixture's TLS trust."""
        options = self.options
        return options.ClientOptions(
            base_url=url or self.server.url,
            retry=options.RetryOptions(initial_delay=0, jitter="none"),
            transport=transport or self.transport(),
            **settings,
        )

    def room(self, value: str = "r1") -> object:
        """Return the room argument of the chat helper for a wire value."""
        return argument(self.package, "roomSocket", "path", "room", value)

    def since(self, value: int) -> object:
        """Return the since argument of the chat helper for a wire value."""
        return argument(self.package, "roomSocket", "query", "since", value)

    def trace(self, value: str) -> object:
        """Return the X-Trace argument of the secure helper for a wire value."""
        return argument(self.package, "secureSocket", "header", "X-Trace", value)

    def text(self, value: str) -> object:
        """Return a message the chat helper sends."""
        return self.models.ClientMessage(text=value)

    def ws(self, **limits: Any) -> Any:
        """Return WebSocket options."""
        return self.protocols.WSOptions(**limits)

    def report(self, *plays: Play) -> None:
        """Report what the server saw in each play, once it ended."""
        self.lines.extend(play.report() for play in plays)

    async def areport(self, *plays: Play) -> None:
        """Report what the server saw in each play once it ended, waiting off the event loop so that the loop can
        finish closing a connection meanwhile."""
        for play in plays:
            await asyncio.to_thread(play.done.wait, 10)
        self.report(*plays)


def sockets(package: ModuleType, lines: list[str]) -> None:
    """Open WebSocket sessions to local servers through the synchronous and asyncio clients of a generated package."""
    _imports(package, lines)
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
        _clocked(harness)
        with harness.package.Client(options=harness.client(), bearer=_Tokens()) as api:
            _sends(harness, api)
        _hooked(harness)
        _authenticated(harness)
        _logged(harness)
        _client_close(harness)
        _handshakes(harness)
        _peers(harness)
        _proxies(harness)
        run(lambda: _async_sockets(harness))
        run(lambda: _async_refused(harness))
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


def _decode_failure(label: str, failure: Any) -> str:
    """Report a message failure's public prefix and whether its SDK traceback still holds a larger payload."""
    prefix = len(failure.body_bytes)
    return (
        f"  {label} ! {_described(failure)} location={failure.location} prefix={prefix} "
        f"truncated={failure.truncated} cause={type(failure.cause).__name__} "
        f"retained={retained_body(failure, prefix)}"
    )


def _large(connection: ServerConnection) -> None:
    """Echo the size of one message and close normally."""
    connection.send(str(len(connection.recv())))
    connection.close()


def _sends(harness: _Harness, api: Any) -> None:
    """Send a long message whole and refuse a send past its timeout."""
    lines, server = harness.lines, harness.server
    (play,) = server.play(Play(talk=_large))
    with api.protocols.secure.chat.connect() as session:
        session.send(b"y" * 100000)
        lines.append(f"  whole {_message(session.receive())}")
    harness.report(play)


def _channels(harness: _Harness, api: Any) -> None:
    """Exchange text and bytes, ping, and exchange typed messages."""
    lines = harness.lines
    plays = harness.server.play(Play(talk=_echo), Play(talk=_chat))
    with api.protocols.feed.text.connect() as session:
        lines.append(f"  bytes channel {_message(session.receive())} subprotocol={session.subprotocol!r}")
        receipt = session.ping(b"probe")
        lines.append(f"    ping {type(receipt).__name__} {receipt.latency >= 0}")
        session.send("hello")
        lines.append(f"    {_message(session.receive())}")
        record(lines, "receive after a normal close", session.receive)
    with api.protocols.rooms.chat.connect(room=harness.room()) as session:
        lines.append(f"  typed {_message(session.receive())}")
        session.send(harness.text("bye"))
        lines.extend(f"    iterated {_message(message)}" for message in session)
    harness.report(*plays)


def _refusals(harness: _Harness, api: Any) -> None:
    """Refuse handshakes with statuses, never retrying or redirecting them, and refuse what fails before sending."""
    lines, server, options = harness.lines, harness.server, harness.options
    chat = api.protocols.rooms.chat
    cases: tuple[tuple[str, tuple[Play, ...], dict[str, Any]], ...] = (
        ("declared error", (Play(refuse=(404, _PROBLEM, b'{"detail":"no room"}')),), {}),
        ("undeclared success", (Play(refuse=(200, (), b"plain")),), {}),
        ("not retried", (Play(refuse=(503, (), b"")),), {}),
        ("retry after not honored", (Play(refuse=(429, (("Retry-After", "0"),), b"")),), {}),
        (
            "redirect not followed",
            (Play(refuse=(302, (("Location", "/rooms/r2/socket"),), b"")),),
            {"options": options.RequestOptions(follow_redirects=True)},
        ),
        ("subprotocol not selected", (Play(subprotocol=None),), {}),
    )
    for label, plays, arguments in cases:
        server.play(*plays)
        session = record(lines, label, lambda arguments=arguments: chat.connect(room=harness.room(), **arguments))
        if session is not None:
            lines.append(f"    attempts {session.response.attempt_count}")
            lines.extend(f"    iterated {_message(message)}" for message in session)
        harness.report(*plays)
    for label, call in (
        ("managed header", lambda: chat.connect(room=harness.room(), options=options.RequestOptions(headers=(("Upgrade", "h2c"),)))),
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
        ("large undecodable", _sending(json_error_body("large syntax").decode()), "chat"),
    ):
        (play,) = server.play(Play(talk=talk))
        session = (
            api.protocols.rooms.chat.connect(room=harness.room())
            if helper == "chat"
            else api.protocols.feed.text.connect()
        )
        try:
            session.receive()
        except harness.errors.DecodeError as failure:
            lines.append(_decode_failure(label, failure))
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
        ("idle inherited", Play(), {"options": options.RequestOptions(timeout=options.TimeoutOptions(read=0.05))}),
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


def _clocked(harness: _Harness) -> None:
    """Time a session's waits on the client's clock: a stepped clock expires an idle wait at once, and a frozen one
    ends it, or the session's deadline, once as much real time passed.
    """
    lines, server, options = harness.lines, harness.server, harness.options
    ticks = itertools.count(step=30.0)
    frozen = options.Clock(monotonic=lambda: 100.0)
    for label, clock, idle, session in (
        ("idle on a stepped clock", options.Clock(monotonic=lambda: float(next(ticks))), 60.0, None),
        ("idle on a frozen clock", frozen, 0.05, None),
        ("session deadline on a frozen clock", frozen, None, options.SessionOptions(total_timeout=0.05)),
    ):
        (play,) = server.play(Play())
        with harness.package.Client(options=harness.client(clock=clock)) as api:
            session = api.protocols.rooms.chat.connect(
                room=harness.room(),
                options=options.RequestOptions(total_timeout=None),
                ws_options=harness.ws(idle_timeout=idle),
                session_options=session,
            )
            record(lines, label, session.receive)
        harness.report(play)
    run(lambda: _async_clocked(harness, frozen))


async def _async_clocked(harness: _Harness, frozen: Any) -> None:
    """End an asyncio session's wait at its deadline once as much real time passed on a frozen client clock."""
    options = harness.options
    (play,) = harness.server.play(Play())
    async with (
        harness.package.AsyncClient(options=harness.client(clock=frozen)) as api,
        api.protocols.rooms.chat.connect(
            room=harness.room(),
            options=options.RequestOptions(total_timeout=None),
            ws_options=harness.ws(idle_timeout=None),
            session_options=options.SessionOptions(total_timeout=0.05),
        ) as session,
    ):
        await arecord(harness.lines, "async session deadline on a frozen clock", session.receive)
    await harness.areport(play)


def _closing_sessions(harness: _Harness, api: Any) -> None:
    """Close with a code and reason, refuse invalid ones, and refuse every step after closing."""
    lines, server = harness.lines, harness.server
    (play,) = server.play(Play(talk=_sending(_JOINED)))
    session = api.protocols.rooms.chat.connect(room=harness.room())
    for label, call in (
        ("reserved close code", lambda: session.close(1005)),
        ("close code of another type", lambda: session.close("1000")),
        ("close reason over 123 bytes", lambda: session.close(4000, "x" * 124)),
        ("close reason with a lone surrogate", lambda: session.close(4000, "\ud800")),
        ("close reason of another type", lambda: session.close(4000, 5)),
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
    _closing_connection(harness, api)


def _closing_connection(harness: _Harness, api: Any) -> None:
    """Refuse a send and a ping on a connection HTTPX2 already closes over a message the session has not read yet.

    The server's play ends once the client's close reached it, so the connection is closing before the steps run.
    """
    lines, server = harness.lines, harness.server
    (play,) = server.play(Play(talk=_sending(b"x" * 64)))
    session = api.protocols.feed.text.connect(ws_options=harness.ws(max_message_bytes=32))
    harness.report(play)
    record(lines, "send on a closing connection", lambda: session.send("late"))
    record(lines, "ping on a closing connection", session.ping)
    record(lines, "receive on a closing connection", session.receive)
    record(lines, "iteration after the failure", lambda: list(session))
    _closed_meanwhile(harness, api)


def _closed_meanwhile(harness: _Harness, api: Any) -> None:
    """End an iteration waiting in another thread when the session closes: its wait ends at its idle timeout.

    The server learns that the thread sent its message before the thread waits; a close that comes first ends the
    iteration the same way.
    """
    lines, server = harness.lines, harness.server
    sent = threading.Event()

    def talk(connection: ServerConnection) -> None:
        connection.recv()
        sent.set()

    (play,) = server.play(Play(talk=talk))
    session = api.protocols.feed.text.connect(ws_options=harness.ws(idle_timeout=0.3))
    iterated: list[object] = []

    def iterate() -> None:
        session.send("go")
        iterated.extend(session)

    thread = threading.Thread(target=iterate)
    thread.start()
    sent.wait(10)
    session.close(4000, "bye")
    thread.join(10)
    lines.append(f"  iteration closed meanwhile {iterated} {session!r}")
    harness.report(play)


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
            await aconnected(lines, "connect", lambda: chat.connect(room=harness.room()), lambda session: arecord(lines, "receive", session.receive))
            await harness.areport(play)


class _Tokens:
    """A bearer credential callable that gives a new token each time it is called."""

    def __init__(self) -> None:
        self.tokens = iter(("first", "second", "third"))
        self.calls: list[str] = []

    def __call__(self) -> str:
        self.calls.append("get")
        return f"{next(self.tokens)}-material"


def _authenticated(harness: _Harness) -> None:
    """Authenticate each handshake, never refreshing a rejected token, and send a header parameter."""
    lines, server, options = harness.lines, harness.server, harness.options
    for label, retry in (("rejected token not refreshed", None), ("no refresh without retries", 0)):
        tokens = _Tokens()
        settings = {} if retry is None else {"retry": options.RetryOptions(max_retries=retry)}
        plays = server.play(Play(refuse=(401, _INVALID, b"")))
        with harness.package.Client(options=harness.client(), bearer=tokens) as api:
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


_IMPORT_PROBE: Final = """
import importlib
import sys
sys.path.insert(0, sys.argv[1])
package = importlib.import_module(sys.argv[2])
plans = importlib.import_module(sys.argv[2] + '.protocols._plans')
with package.Client(options=None) as client:
    client.protocols.rooms.chat
    print('WebSocket library loaded by the plans=' + repr('wsproto' in sys.modules) + ' ' + type(plans.SOCKET_0).__name__)
    print('websockets library loaded=' + repr('websockets' in sys.modules))
"""


def _imports(package: ModuleType, lines: list[str]) -> None:
    """Import a WebSocket package, its plans, and a client's helpers in a fresh process, which never loads websockets."""
    location = Path(str(package.__file__)).parent.parent
    completed = subprocess.run(
        [sys.executable, "-I", "-c", _IMPORT_PROBE, str(location), package.__name__],
        check=True,
        capture_output=True,
        text=True,
    )
    lines.extend(f"  {line}" for line in completed.stdout.splitlines())


class _Captured(logging.Handler):
    """Keep the messages of the records the HTTP and WebSocket libraries' loggers emit."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(("httpx2", "httpcore2", "wsproto")):
            self.messages.append(record.getMessage())


def _logged(harness: _Harness) -> None:
    """Emit no client debug records holding credentials or messages, even with every logger at DEBUG."""
    lines, server = harness.lines, harness.server
    root, captured = logging.getLogger(), _Captured()
    level = root.level
    root.addHandler(captured)
    root.setLevel(logging.DEBUG)
    try:
        (play,) = server.play(Play(talk=_replying))
        with harness.package.Client(options=harness.client(), bearer=_Tokens()) as api:
            session = api.protocols.secure.chat.connect()
            session.send(b"secret-payload")
            lines.append(f"  received with debug logging {_message(session.receive())}")
            session.close()
        harness.report(play)
    finally:
        root.removeHandler(captured)
        root.setLevel(level)
    secrets = [message for message in captured.messages if "secret" in message or "material" in message]
    lines.append(f"    client debug records {bool(captured.messages)} with secrets {len(secrets)}")


def _client_close(harness: _Harness) -> None:
    """Close a client with sessions open: each session closes first, as its own close does, then the HTTP client."""
    lines, server = harness.lines, harness.server
    plays = server.play(Play(talk=_sending(_JOINED)), Play())
    api = harness.package.Client(options=harness.client())
    session = api.protocols.rooms.chat.connect(room=harness.room())
    other = api.protocols.feed.text.connect()
    lines.append(f"  received before the client closed {_message(session.receive())}")
    record(lines, "client close with two sessions open", api.close)
    record(lines, "client close again", api.close)
    record(lines, "send after the client closed", lambda: session.send(harness.text("late")))
    lines.append(f"  other session after the client closed {other!r}")
    harness.report(*plays)
    _borrowed(harness)


class _Socketless(httpx2.HTTPTransport):
    """An HTTP transport whose one connection replays a 101 and a binary message from memory, without a socket."""

    def __init__(self) -> None:
        super().__init__()
        self._pool = httpcore2.ConnectionPool(
            network_backend=httpcore2.MockBackend([_UPGRADE.replace(b"{accept}", b"unchecked"), b"\x82\x02hi"])
        )


def _borrowed(harness: _Harness) -> None:
    """Leave a session open past closing a client that borrowed its HTTP client, over a connection without a socket.

    The caller closes the session before the HTTP client; the connection's end after its message fails the session.
    """
    lines, options = harness.lines, harness.options
    with httpx2.Client(transport=_Socketless()) as native:
        api = harness.package.Client(http_client=native, options=options.ClientOptions(base_url=harness.server.url))
        session = api.protocols.feed.text.connect()
        api.close()
        lines.append(f"  borrowed client closed {session!r}")
        lines.append(f"    socketless {_message(session.receive())}")
        record(lines, "socketless end", session.receive)
        session.close()


def _handshakes(harness: _Harness) -> None:
    """Classify handshakes that time out, break HTTP, select another subprotocol, or never connect.

    HTTPX2 checks only the 101 status of an upgrade, as its own WebSocket client does.
    """
    lines, options = harness.lines, harness.options
    once = options.RequestOptions(retry=options.RetryOptions(max_retries=0))
    accept = b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n"
    for label, reply, arguments in (
        ("open timeout", None, {"ws_options": harness.ws(open_timeout=1.0)}),
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
                session = record(
                    lines,
                    label,
                    lambda api=api, arguments=arguments: api.protocols.feed.text.connect(
                        **{"options": once, **arguments}
                    ),
                )
                if session is not None:
                    session.close()
        finally:
            peer.stop()
    _deadline_open(harness, once)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        url = f"https://127.0.0.1:{probe.getsockname()[1]}"
        for retry in (1, 0):
            with harness.package.Client(options=harness.client(url)) as api:
                record(
                    lines,
                    f"refused connection retry={retry}",
                    lambda retry=retry, api=api: api.protocols.feed.text.connect(
                        options=options.RequestOptions(retry=options.RetryOptions(max_retries=retry))
                    ),
                )
        limiter = _SemaphoreLimiter(release_failure=RuntimeError("Permit close failed"))
        for label, settings, arguments in (
            ("refused retry with failed permit release", {"limiter": limiter}, {}),
        ):
            with harness.package.Client(options=harness.client(url, **settings)) as api:
                record(lines, label, lambda api=api, arguments=arguments: api.protocols.feed.text.connect(**arguments))
        lines.append(f"    refused permit cleanup {limiter.usage.report}")
    with harness.package.Client(options=harness.client(transport=harness.options.TransportOptions())) as api:
        record(lines, "untrusted certificate", lambda: api.protocols.feed.text.connect(options=once))


def _deadline_open(harness: _Harness, once: Any) -> None:
    """Bound an open by the call's deadline, reporting whether its timeout was the remaining time, not its value."""
    lines, peer = harness.lines, RawPeer(None)
    try:
        with harness.package.Client(options=harness.client(peer.url)) as api:
            options = harness.options.RequestOptions(total_timeout=1.0, retry=once.retry)
            try:
                api.protocols.feed.text.connect(options=options)
            except harness.errors.APITimeoutError as error:
                lines.append(
                    f"  deadline during the open ! {type(error).__name__} reason={error.reason}"
                )
    finally:
        peer.stop()


def _peers(harness: _Harness) -> None:
    """Fail a session whose peer never answers a ping, explicit or kept alive, or hangs up while one waits."""
    lines = harness.lines
    peer = RawPeer(_UPGRADE)
    hangup = RawPeer(_UPGRADE, hangup=True)
    try:
        with harness.package.Client(options=harness.client(hangup.url)) as api:
            session = api.protocols.feed.text.connect(ws_options=harness.ws(ping_interval=None, pong_timeout=0.1))
            record(lines, "peer hanging up during a ping", session.ping)
            session = api.protocols.feed.text.connect(ws_options=harness.ws(ping_interval=None))
            record(lines, "peer hanging up during a whole send", lambda: session.send(_LARGE))
        with harness.package.Client(options=harness.client(peer.url)) as api:
            feed = api.protocols.feed.text
            session = feed.connect(ws_options=harness.ws(pong_timeout=0.1, ping_interval=None))
            record(lines, "unanswered ping", session.ping)
            record(lines, "after the unanswered ping", session.receive)
            session = feed.connect(
                ws_options=harness.ws(ping_interval=0.05, pong_timeout=0.05, idle_timeout=None)
            )
            record(lines, "unanswered keepalive", session.receive)
    finally:
        peer.stop()
        hangup.stop()


def _proxies(harness: _Harness) -> None:
    """Tunnel through the HTTP client's proxy, and report a proxy's refusal."""
    lines, server = harness.lines, harness.server
    proxy = TunnelProxy(server.port)
    refusing = TunnelProxy(server.port, refuse=b"HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n")
    garbled = TunnelProxy(server.port, refuse=b"NOT-HTTP\r\n\r\n")
    try:
        plays = server.play(Play(talk=_echo))
        with harness.package.Client(options=harness.client(transport=harness.transport(proxy=proxy.url))) as api:
            session = api.protocols.feed.text.connect()
            lines.append(f"  proxy {_message(session.receive())}")
            session.close()
        harness.report(*plays)
        port = str(server.port)
        lines.append(f"  proxy requests {[request.replace(port, '<port>') for request in proxy.requests]}")
        once = harness.options.RequestOptions(retry=harness.options.RetryOptions(max_retries=0))
        for label, broken in (("refusing proxy", refusing), ("garbled proxy", garbled)):
            with harness.package.Client(options=harness.client(transport=harness.transport(proxy=broken.url))) as api:
                record(lines, label, lambda api=api: api.protocols.feed.text.connect(options=once))
            lines.append(f"  {label} requests {[request.replace(port, '<port>') for request in broken.requests]}")
    finally:
        proxy.stop()
        refusing.stop()
        garbled.stop()


async def _messages(session: Any) -> list[object]:
    """Return the messages an asyncio iteration yields."""
    return [message async for message in session]


async def _async_refused(harness: _Harness) -> None:
    """Retry an asyncio handshake refused before sending, within its session budget and permit cleanup."""
    lines = harness.lines
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        url = f"https://127.0.0.1:{probe.getsockname()[1]}"
        limiter = _AsyncSemaphoreLimiter(release_failure=RuntimeError("Permit close failed"))
        for label, settings, arguments in (
            ("async refused connection", {}, {}),
            ("async refused retry with failed permit release", {"limiter": limiter}, {}),
        ):
            async with harness.package.AsyncClient(options=harness.client(url, **settings)) as api:
                await aconnected(lines, label, lambda api=api, arguments=arguments: api.protocols.feed.text.connect(**arguments))
        lines.append(f"    async refused permit cleanup {limiter.usage.report}")


async def _async_sockets(harness: _Harness) -> None:
    """Open, use, and close sessions with asyncio, including the waits its guard stops."""
    lines, server = harness.lines, harness.server
    async with harness.package.AsyncClient(options=harness.client()) as api:
        chat = api.protocols.rooms.chat
        (play,) = server.play(Play(talk=_chat))
        async with chat.connect(room=harness.room()) as session:
            lines.append(f"  async connected {session.response.status_code} {session.subprotocol!r}")
            lines.append(f"    {_message(await session.receive())}")
            await session.send(harness.text("bye"))
            async for message in session:
                lines.append(f"    async iterated {_message(message)}")
            await arecord(lines, "async receive after the end", session.receive)
            await arecord(lines, "async send after the end", lambda: session.send(harness.text("late")))
        lines.append(f"    async after the block {session!r}")
        await harness.areport(play)
        (play,) = server.play(Play(talk=_echo))
        async with api.protocols.feed.text.connect() as session:
            lines.append(f"  async bytes {_message(await session.receive())}")
            receipt = await session.ping()
            lines.append(f"    async ping {receipt.latency >= 0}")
            await session.send("hello")
            lines.append(f"    {_message(await session.receive())}")
            await session.aclose()
            await harness.areport(play)
        (play,) = server.play(Play())
        async with chat.connect(room=harness.room()) as session:
            waiting = asyncio.create_task(session.receive())
            await asyncio.sleep(0)
            await arecord(lines, "async concurrent receive", session.receive)
            await session.aclose(4000, "bye")
            await arecord(lines, "async receive closed meanwhile", lambda: waiting)
            await arecord(lines, "async receive after closing", session.receive)
            lines.append(f"  async iteration after closing {[message async for message in session]}")
            await arecord(lines, "async invalid close", lambda: session.aclose(1015))
            await harness.areport(play)
        await _async_failures(harness, api)
        await _async_steps(harness, api)
    await _async_peers(harness)


async def _async_failures(harness: _Harness, api: Any) -> None:
    """Classify asyncio refusals and session failures; a failure leaving a connect's block leaves it as raised."""
    lines, server, options = harness.lines, harness.server, harness.options
    chat = api.protocols.rooms.chat
    for label, play, arguments in (
        ("async refusal", Play(refuse=(404, _PROBLEM, b'{"detail":"no room"}')), {}),
        ("async idle", Play(), {"ws_options": harness.ws(idle_timeout=0.05)}),
        ("async session deadline", Play(), {"session_options": options.SessionOptions(total_timeout=1.0), "ws_options": harness.ws(idle_timeout=None)}),
        ("async abnormal closure", Play(talk=_sending(code=1011, reason="boom")), {}),
        ("async server's own message limit", Play(talk=_sending(code=1009, reason="peer limit")), {}),
    ):
        server.play(play)
        await aconnected(
            lines,
            f"{label} connect",
            lambda arguments=arguments: chat.connect(room=harness.room(), **arguments),
            lambda session, label=label: arecord(lines, label, session.receive),
        )
        await harness.areport(play)
    for label, text in (("async decode failure", "syntax"), ("async large decode failure", "large syntax")):
        (play,) = server.play(Play(talk=_sending(json_error_body(text).decode())))
        try:
            async with chat.connect(room=harness.room()) as session:
                await session.receive()
        except harness.errors.DecodeError as failure:
            lines.append(_decode_failure(f"{label} left the block", failure))
        lines.append(f"    {label} after the block {session!r}")
        await harness.areport(play)
    for raised in (LookupError, SystemExit):
        (play,) = server.play(Play())
        try:
            async with chat.connect(room=harness.room()) as session:
                raise raised
        except raised as failure:
            lines.append(f"  async block's own failure left the block {type(failure).__name__} {session!r}")
        await harness.areport(play)
    (play,) = server.play(Play())
    entered = asyncio.Event()

    async def block() -> None:
        async with chat.connect(room=harness.room()) as session:
            entered.set()
            await asyncio.Event().wait()

    blocked = asyncio.create_task(block())
    await entered.wait()
    blocked.cancel()
    cancelled = (await asyncio.gather(blocked, return_exceptions=True))[0]
    lines.append(f"  async block cancelled by its task {type(cancelled).__name__}")
    await harness.areport(play)


async def _async_steps(harness: _Harness, api: Any) -> None:
    """Refuse steps on a closing connection, and keep a session usable past a receive its task cancelled."""
    lines, server = harness.lines, harness.server
    (play,) = server.play(Play(talk=_sending(b"x" * 64)))
    async with api.protocols.feed.text.connect(ws_options=harness.ws(max_message_bytes=32)) as session:
        await harness.areport(play)
        await arecord(lines, "async send on a closing connection", lambda: session.send("late"))
        await arecord(lines, "async ping on a closing connection", session.ping)
        await arecord(lines, "async receive on a closing connection", session.receive)
        await arecord(lines, "async iteration after the failure", lambda: _messages(session))
    (play,) = server.play(Play())
    async with api.protocols.feed.text.connect() as session:
        iterating = asyncio.create_task(_messages(session))
        await asyncio.sleep(0)
        await session.aclose(4000, "bye")
        await arecord(lines, "async iteration closed meanwhile", lambda: iterating)
        await harness.areport(play)
    (play,) = server.play(Play(talk=_answering))
    async with api.protocols.feed.text.connect() as session:
        waiting = asyncio.create_task(session.receive())
        await asyncio.sleep(0)
        waiting.cancel()
        cancelled = (await asyncio.gather(waiting, return_exceptions=True))[0]
        lines.append(f"  async receive cancelled by its task {type(cancelled).__name__} {session!r}")
        await arecord(lines, "async receive past wait_for", lambda: asyncio.wait_for(session.receive(), 0.05))
        await arecord(lines, "async send after the cancelled receives", lambda: session.send("after"))
        lines.append(f"    async received {_message(await session.receive())}")
    await harness.areport(play)


async def _async_peers(harness: _Harness) -> None:
    """Close a client under an open asyncio session, fail a session at a record that fails HTTPX2's reader, and fail
    pings and sends a raw peer never answers.
    """
    lines, options = harness.lines, harness.options
    harness.server.play(Play(talk=_sending(_JOINED)))
    api = harness.package.AsyncClient(options=harness.client())
    async with api.protocols.rooms.chat.connect(room=harness.room()) as session:
        lines.append(f"  async received before the client closed {_message(await session.receive())}")
        await arecord(lines, "async client close with a session open", api.aclose)
        await arecord(lines, "async receive after the client closed", session.receive)
    peer = RawPeer(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n")
    try:
        async with (
            harness.package.AsyncClient(options=harness.client(peer.url)) as api,
            api.protocols.feed.text.connect(ws_options=harness.ws(pong_timeout=0.1)) as session,
        ):
            await arecord(lines, "async unanswered ping", session.ping)
    finally:
        peer.stop()
    garbled = RawPeer(_UPGRADE, garbled=b"\x17\x03\x03\x00\x10" + bytes(16))
    try:
        async with harness.package.AsyncClient(options=harness.client(garbled.url)) as api:
            await aconnected(
                lines,
                "async record failing HTTPX2's reader",
                lambda: api.protocols.feed.text.connect(ws_options=harness.ws(ping_interval=None)),
                lambda session: session.receive(),
            )
    finally:
        garbled.stop()
    silent_pongs = RawPeer(_UPGRADE)
    try:
        async with harness.package.AsyncClient(options=harness.client(silent_pongs.url)) as api:
            async with api.protocols.feed.text.connect(ws_options=harness.ws(ping_interval=None)) as session:
                pinging = asyncio.create_task(session.ping())
                await asyncio.to_thread(silent_pongs.wait_records, 1)
                pinging.cancel()
                cancelled = (await asyncio.gather(pinging, return_exceptions=True))[0]
                lines.append(f"  async ping cancelled by its task {type(cancelled).__name__} {session!r}")
            async with api.protocols.feed.text.connect(ws_options=harness.ws(ping_interval=None)) as session:
                sending = asyncio.create_task(session.send(_LARGE))
                await asyncio.sleep(0)
                sending.cancel()
                cancelled = (await asyncio.gather(sending, return_exceptions=True))[0]
                lines.append(f"  async send cancelled by its task {type(cancelled).__name__} {session!r}")
                await arecord(lines, "async send after the cancelled send", lambda: session.send("late"))
    finally:
        silent_pongs.stop()
    silent = RawPeer(None)
    try:
        async with harness.package.AsyncClient(options=harness.client(silent.url)) as api:
            once = options.RequestOptions(retry=options.RetryOptions(max_retries=0))
            await aconnected(lines, "async open timeout", lambda: api.protocols.feed.text.connect(options=once, ws_options=harness.ws(open_timeout=1.0)))
    finally:
        silent.stop()
