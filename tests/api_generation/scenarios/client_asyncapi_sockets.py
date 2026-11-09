"""Open the channels of AsyncAPI documents through generated clients against a local WebSocket server."""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING, Any, is_typeddict

import httpx2

from tests.api_generation.scenarios.client_sockets import _data, _described, _message, _sending, record
from tests.api_generation.support.client_runtime import run
from tests.api_generation.support.fixture_websocket import Play, SocketServer, client_context

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    from websockets.sync.server import ServerConnection


def _trade(connection: ServerConnection) -> None:
    """Answer a subscription with a trade, then send a message no model decodes."""
    subscribed = json.loads(connection.recv())
    connection.send(json.dumps({"event": "trade", "price": 1.5, "volume": len(subscribed["channels"])}))
    connection.send(json.dumps({"event": "trade"}))


def _ticks(connection: ServerConnection) -> None:
    """Send a text tick, then a binary frame the channel does not carry."""
    connection.send("tick 1")
    connection.send(b"\x00")


def _audio(connection: ServerConnection) -> None:
    """Report the size of a binary frame as a status, and close normally."""
    connection.send(json.dumps({"level": len(connection.recv())}))
    connection.close(1000, "done")


def _subscriber(connection: ServerConnection) -> None:
    """As the server of a client-perspective document, send a subscription and read the trade the client answers."""
    connection.send(json.dumps({"event": "subscribe", "channels": ["trades"]}))
    connection.recv()
    connection.close(1000, "done")


def _models(package: ModuleType) -> ModuleType:
    return importlib.import_module(f"{package.__name__}_models")


def asyncapi_sockets(package: ModuleType, lines: list[str]) -> None:
    """Open each channel of a server's document through the synchronous and asyncio clients of a generated package."""
    models = _models(package)
    server = SocketServer()
    try:
        market, ticker, audio = server.play(Play(talk=_trade), Play(talk=_ticks), Play(talk=_audio))
        with (
            httpx2.Client(verify=client_context()) as http,
            package.Client(base_url=server.url, http_client=http) as client,
        ):
            lines.append(f"  channels {client.market is client.protocols.market} {type(client.ticker).__name__}")
            with client.market.connect(pair="XBT-USD") as session:
                session.send(models.Subscribe(event="subscribe", channels=["trades", "book"]))
                lines.append(f"    market {_message(session.receive())}")
                record(lines, "market undecodable", session.receive)
            lines.append(market.report())
            with client.ticker.connect() as session:
                lines.append(f"    ticker {_message(session.receive())}")
                record(lines, "ticker send", lambda: session.send("never"))
                record(lines, "ticker binary", session.receive)
            lines.append(ticker.report())
            with client.audio.connect() as session:
                session.send(b"\x01\x02\x03")
                lines.extend(f"    audio {_message(message)}" for message in session)
            lines.append(audio.report())
        (opened,) = server.play(Play(talk=_trade))
        run(lambda: _async_market(package, models, server.url, lines))
        lines.append(opened.report())
    finally:
        server.stop()


async def _async_market(package: ModuleType, models: ModuleType, url: str, lines: list[str]) -> None:
    """Send a subscription and read the trade through the asyncio client."""
    async with (
        httpx2.AsyncClient(verify=client_context()) as http,
        package.AsyncClient(base_url=url, http_client=http) as client,
        client.market.connect(pair="A-B") as session,
    ):
        await session.send(models.Subscribe(event="subscribe", channels=["trades"]))
        message: Any = await session.receive()
        lines.append(f"    async market {type(message.data).__name__}{_data(message.data)!r}")


def asyncapi_perspective(package: ModuleType, lines: list[str]) -> None:
    """Receive what the document's client receives and send what it sends, under the client perspective."""
    models = _models(package)
    server = SocketServer()
    try:
        (played,) = server.play(Play(talk=_subscriber))
        with (
            httpx2.Client(verify=client_context()) as http,
            package.Client(base_url=server.url, http_client=http) as client,
            client.trades.connect(symbol="XBT-USD") as session,
        ):
            lines.append(f"    trades {_message(session.receive())}")
            session.send(models.Trade(event="trade", price=2.0))
            lines.extend(f"    trades {_message(message)}" for message in session)
        lines.extend((played.report(), f"  closed {_described(session)}"))
    finally:
        server.stop()


def _lobby(connection: ServerConnection) -> None:
    """Echo a line of a chat room, and close normally."""
    connection.send(f"echo {connection.recv()}")
    connection.close(1000, "done")


def _notices(connection: ServerConnection) -> None:
    """Read a notice, and send a frame on the channel the client only sends on."""
    connection.send(f"read {connection.recv()}")


def asyncapi_serverless(package: ModuleType, lines: list[str]) -> None:
    """Open a channel whose address has no leading slash, of a document without servers, under the given base URL.

    A frame on a channel the client only sends on is a DecodeError, which closes the connection with 1002.
    """
    server = SocketServer()
    try:
        lobby, notices = server.play(Play(talk=_lobby), Play(talk=_notices))
        with (
            httpx2.Client(verify=client_context()) as http,
            package.Client(base_url=f"{server.url}/ws", http_client=http) as client,
        ):
            with client.rooms.connect(room="lobby") as session:
                session.send("hello")
                lines.extend(f"    rooms {_message(message)}" for message in session)
            lines.append(lobby.report())
            with client.notices.connect() as session:
                session.send("closing")
                record(lines, "notices receive", session.receive)
            lines.append(notices.report())
    finally:
        server.stop()


def _collecting(seen: list[str], count: int, *replies: str | bytes) -> Callable[[ServerConnection], None]:
    """Return a talk that keeps the client's first messages as received, sends replies, and waits for the client."""

    def talk(connection: ServerConnection) -> None:
        seen.extend(repr(connection.recv()) for _ in range(count))
        for reply in replies:
            connection.send(reply)

    return talk


def _value(models: ModuleType, name: str, **fields: object) -> object:
    """Return a value of a model by its fields: a dict for a TypedDict, else an instance."""
    model = getattr(models, name)
    return dict(fields) if is_typeddict(model) else model(**fields)


def _feed(package: ModuleType, models: ModuleType, server: SocketServer, lines: list[str]) -> None:
    """Send each kind of message on the feed and receive its tagged, binary, unknown and malformed messages."""
    seen: list[str] = []
    trade = json.dumps({"event": "trade", "price": 2.5})
    beat = json.dumps({"event": "heartbeat", "at": 7})
    played = server.play(
        Play(talk=_collecting(seen, 4, trade, beat, b"\x09", json.dumps({"event": "quote"}))),
        *(Play(talk=_sending(item)) for item in ('{"price": 1}', '{"event": 1}', "not json")),
    )
    unsubscribe = models.Unsubscribe
    if hasattr(unsubscribe, "model_dump"):
        unsubscribe = type("Mine", (unsubscribe,), {})
    with (
        httpx2.Client(verify=client_context()) as http,
        package.Client(base_url=server.url, http_client=http) as client,
    ):
        with client.feed.connect() as session:
            session.send(_value(models, "Subscribe", op="subscribe", channels=["a"]))
            session.send(
                _value(models, "Unsubscribe", op="unsubscribe", channels=["b"])
                if unsubscribe is models.Unsubscribe
                else unsubscribe(op="unsubscribe", channels=["b"])
            )
            session.send("ping")
            session.send(bytearray(b"\x01"))
            record(lines, "feed int", lambda: session.send(5))
            record(lines, "feed unknown op", lambda: session.send({"op": "other"}))
            lines.extend(f"    feed {_message(session.receive())}" for _ in range(3))
            record(lines, "feed unknown event", session.receive)
        for label in ("missing", "number", "malformed"):
            with client.feed.connect() as session:
                record(lines, f"feed {label} event", session.receive)
    lines.append(f"    feed server saw {', '.join(seen)}")
    lines.extend(play.report() for play in played)


def _chat(package: ModuleType, server: SocketServer, lines: list[str]) -> None:
    """Receive the untagged chat messages as their union, and refuse what no member decodes or a binary frame."""
    played = server.play(
        Play(talk=_sending(json.dumps({"text": "hi"}), json.dumps({"level": 2}), b"\x00")),
        Play(talk=_sending(json.dumps({"bogus": 1}))),
    )
    with (
        httpx2.Client(verify=client_context()) as http,
        package.Client(base_url=server.url, http_client=http) as client,
    ):
        with client.chat.connect() as session:
            lines.extend(f"    chat {_message(session.receive())}" for _ in range(2))
            record(lines, "chat binary", session.receive)
        with client.chat.connect() as session:
            record(lines, "chat undecodable", session.receive)
    lines.extend(play.report() for play in played)


def _orders(package: ModuleType, models: ModuleType, server: SocketServer, lines: list[str]) -> None:
    """Send an order and its cancellation, each by its class, and read the acknowledgement."""
    seen: list[str] = []
    (played,) = server.play(Play(talk=_collecting(seen, 2, json.dumps({"id": "o-1"}))))
    with (
        httpx2.Client(verify=client_context()) as http,
        package.Client(base_url=server.url, http_client=http) as client,
        client.orders.connect() as session,
    ):
        session.send(_value(models, "Place", symbol="XBT", size=1.0))
        session.send(_value(models, "Cancel", id="o-1"))
        lines.append(f"    orders {_message(session.receive())}")
    lines.extend((f"    orders server saw {', '.join(seen)}", played.report()))


def _alerts(package: ModuleType, server: SocketServer, lines: list[str]) -> None:
    """Receive a null alert and a raised one, which admit null and so are decoded as their union, not by their tag."""
    (played,) = server.play(Play(talk=_sending("null", json.dumps({"kind": "raised"}))))
    with (
        httpx2.Client(verify=client_context()) as http,
        package.Client(base_url=server.url, http_client=http) as client,
        client.alerts.connect() as session,
    ):
        lines.extend(f"    alerts {_message(session.receive())}" for _ in range(2))
    lines.append(played.report())


def asyncapi_messages(package: ModuleType, lines: list[str]) -> None:
    """Open each channel of several messages per direction that the package's backend can tell apart."""
    models = _models(package)
    server = SocketServer()
    try:
        _feed(package, models, server, lines)
        if hasattr(package.Client, "chat"):
            _chat(package, server, lines)
        if hasattr(package.Client, "orders"):
            _orders(package, models, server, lines)
        if hasattr(package.Client, "alerts"):
            _alerts(package, server, lines)
    finally:
        server.stop()


def asyncapi_union_sends(package: ModuleType, lines: list[str]) -> None:
    """Send items and a note on a channel whose JSON messages the Pydantic models encode as one union."""
    models = _models(package)
    server = SocketServer()
    seen: list[str] = []
    try:
        (played,) = server.play(Play(talk=_collecting(seen, 3)))
        with (
            httpx2.Client(verify=client_context()) as http,
            package.Client(base_url=server.url, http_client=http) as client,
            client.batch.connect() as session,
        ):
            session.send(models.Item(name="a"))
            session.send([models.Item(name="b")])
            session.send("note")
        lines.extend((f"    batch server saw {', '.join(seen)}", played.report()))
    finally:
        server.stop()


def asyncapi_subclass_sends(package: ModuleType, lines: list[str]) -> None:
    """Send an edit, a revision and a value of a subclass of the revision, each with its nearest class's encoder."""
    models = _models(package)
    server = SocketServer()
    seen: list[str] = []
    mine = type("Mine", (models.Revision,), {})
    try:
        (played,) = server.play(Play(talk=_collecting(seen, 3)))
        with (
            httpx2.Client(verify=client_context()) as http,
            package.Client(base_url=server.url, http_client=http) as client,
            client.edits.connect() as session,
        ):
            session.send(models.Edit(text="a"))
            session.send(models.Revision(text="b", number=2))
            session.send(mine(text="c", number=3))
        lines.extend((f"    edits server saw {', '.join(seen)}", played.report()))
    finally:
        server.stop()


def asyncapi_enum_tag_sends(package: ModuleType, lines: list[str]) -> None:
    """Send dicts on the feed whose tag is a plain enum member, each told apart by the member's value."""
    models = _models(package)
    server = SocketServer()
    seen: list[str] = []
    try:
        (played,) = server.play(Play(talk=_collecting(seen, 2)))
        with (
            httpx2.Client(verify=client_context()) as http,
            package.Client(base_url=server.url, http_client=http) as client,
            client.feed.connect() as session,
        ):
            session.send({"op": "subscribe", "channels": ["a"]})
            session.send({"op": models.Op.unsubscribe, "channels": ["b"]})
        lines.extend((f"    feed server saw {', '.join(seen)}", played.report()))
    finally:
        server.stop()
