"""Reject sync iteration of asyncio streams, unawaited opens, narrowed events, other options, and immutable events."""

from __future__ import annotations

from pets import AsyncClient, Client
from pets.options import RequestOptions
from pets.protocols import EventStream, StreamEvent
from pets_models import Created, Message


async def wrong_streams(client: Client, async_client: AsyncClient, event: StreamEvent[Message]) -> None:
    """Reject each misuse of a helper, its stream, or its events."""
    list(await async_client.protocols.events.messages.open())  # error
    await client.protocols.events.messages.open()  # error
    typed: EventStream[Created] = client.protocols.events.typed.open()  # error
    client.protocols.events.messages.open(stream_options=RequestOptions())  # error
    client.protocols.events.messages.open(response_media_type="text/event-stream")  # error
    client.protocols.feed.all.open()  # error
    event.data = Message(text="a")  # error
    client.protocols.events.everyone = client.protocols.events.messages  # error
    del typed
