"""Keep message types through WebSocket helpers and their sessions, messages, and receipts, sync and asyncio."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractAsyncContextManager
from typing import Literal

from pets import AsyncClient, Client
from pets.errors import APIConnectionError, ConfigurationError, WebSocketClosedError
from pets.options import ProtocolClientOptions, RequestOptions, SessionOptions
from pets.protocols import (
    AsyncWebSocketSession,
    Message,
    PingReceipt,
    ProtocolDefaults,
    WebSocketSession,
    WSOptions,
)
from pets_models import (
    ClientMessage,
    FieldRoomsRoomSocketGetPathRoomParameter,
    FieldRoomsRoomSocketGetQuerySinceParameter,
    FieldSecureSocketGetHeaderXTraceParameter,
    ServerMessage,
)
from typing_extensions import assert_type


def sockets(
    client: Client,
    message: ClientMessage,
    room: FieldRoomsRoomSocketGetPathRoomParameter,
    since: FieldRoomsRoomSocketGetQuerySinceParameter,
    trace: FieldSecureSocketGetHeaderXTraceParameter,
) -> None:
    """Send and receive typed messages, text, and bytes, ping, and close."""
    session = client.protocols.rooms.chat.connect(
        room=room,
        since=since,
        ws_options=WSOptions(open_timeout=1, idle_timeout=None, max_message_bytes=65536),
        options=RequestOptions(),
        session_options=SessionOptions(total_timeout=30),
    )
    assert_type(session, WebSocketSession[ClientMessage, ServerMessage])
    session.send(message)
    received = session.receive()
    assert_type(received, Message[ServerMessage])
    assert_type(received.data, ServerMessage)
    assert_type(received.frame, Literal["text", "binary"])
    assert_type(received.sequence, int)
    assert_type(received.raw, bytes)
    assert_type(session.subprotocol, str | None)
    assert_type(session.ping(b"a"), PingReceipt)
    assert_type(session.ping().latency, float)
    messages: Iterator[Message[ServerMessage]] = session
    widened: Message[object] = received
    session.close(4000, "bye")
    with client.protocols.feed.text.connect() as feed:
        assert_type(feed, WebSocketSession[str, bytes])
        feed.send("a")
        for item in feed:
            assert_type(item.data, bytes)
    with client.protocols.secure.chat.connect(x_trace=trace) as secure:
        secure.send(b"a")
        assert_type(secure.receive().data, str)
    del messages, widened


def failures(error: WebSocketClosedError, unknown: APIConnectionError, busy: ConfigurationError) -> None:
    """Read the close code, reason, and cleanliness, a message's unknown delivery, and the receive state."""
    assert_type(error.code, int | None)
    assert_type(error.reason, str)
    assert_type(error.clean, bool)
    assert_type(unknown.reason, str | None)
    assert_type(busy.field_path, tuple[str | int, ...])


def options() -> Client:
    """Give a client WebSocket helper defaults; transport settings are the HTTP client's."""
    return Client(
        protocols=ProtocolClientOptions(
            defaults={"rooms.chat": ProtocolDefaults(options=WSOptions(ping_interval=None))}
        ),
    )


async def async_sockets(
    client: AsyncClient, message: ClientMessage, room: FieldRoomsRoomSocketGetPathRoomParameter
) -> None:
    """Enter a connect's async with block, then await sends, receives, pings, and closes, and iterate asynchronously."""
    connecting = client.protocols.rooms.chat.connect(room=room)
    assert_type(connecting, AbstractAsyncContextManager[AsyncWebSocketSession[ClientMessage, ServerMessage]])
    async with connecting as session:
        assert_type(session, AsyncWebSocketSession[ClientMessage, ServerMessage])
        await session.send(message)
        assert_type(await session.receive(), Message[ServerMessage])
        assert_type(await session.ping(), PingReceipt)
        messages: AsyncIterator[Message[ServerMessage]] = session
        async for item in session:
            assert_type(item.data, ServerMessage)
        await session.aclose(1001)
        del messages
    async with client.protocols.feed.text.connect() as feed:
        assert_type(feed, AsyncWebSocketSession[str, bytes])
