"""Open the channels of AsyncAPI documents through generated clients against a local WebSocket server."""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING, Any

import httpx2

from tests.api_generation.scenarios.client_sockets import _data, _described, _message, record
from tests.api_generation.support.client_runtime import run
from tests.api_generation.support.fixture_websocket import Play, SocketServer, client_context

if TYPE_CHECKING:
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
