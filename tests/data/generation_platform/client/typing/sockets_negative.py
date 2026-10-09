"""Reject awaited asyncio connects, awaited sync connects, wrong message types, other options, and frozen messages."""

from __future__ import annotations

from pets import AsyncClient, Client
from pets.options import SessionOptions
from pets.protocols import Message, WebSocketSession
from pets_models import ClientMessage, FieldRoomsRoomSocketGetPathRoomParameter, ServerMessage


async def wrong_sockets(
    client: Client,
    async_client: AsyncClient,
    message: Message[ServerMessage],
    room: FieldRoomsRoomSocketGetPathRoomParameter,
) -> None:
    """Reject each misuse of a helper, its session, or its messages."""
    await async_client.protocols.rooms.chat.connect(room=room)  # error
    await client.protocols.rooms.chat.connect(room=room)  # error
    client.protocols.rooms.chat.connect()  # error
    client.protocols.rooms.chat.connect(room=room, ws_options=SessionOptions())  # error
    client.protocols.feed.text.connect().send(b"a")  # error
    client.protocols.secure.chat.connect().send("a")  # error
    client.protocols.rooms.chat.connect(room=room).send(1)  # error
    typed: WebSocketSession[ClientMessage, str] = client.protocols.rooms.chat.connect(room=room)  # error
    message.data = message.data  # error
    del typed
