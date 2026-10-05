"""Keep event types through SSE helpers, their streams, events, and error events, with sync and asyncio clients."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

from pets import AsyncClient, Client
from pets.errors import StreamRemoteError
from pets.options import RequestOptions, SessionOptions
from pets.protocols import (
    AsyncEventStream,
    EventStream,
    ProtocolProgress,
    StreamEvent,
    StreamOptions,
    UnknownEvent,
)
from pets.responses import ResponseInfo
from pets_models import Created, Deleted, FeedQuery, Message
from typing_extensions import assert_type


def events(client: Client, query: FeedQuery) -> None:
    """Yield typed events and their data, keep unknown events in the union, and read error events as object."""
    stream = client.protocols.events.messages.open(
        stream_options=StreamOptions(idle_timeout=5, max_event_bytes=1024),
        options=RequestOptions(),
        session_options=SessionOptions(total_timeout=30),
    )
    assert_type(stream, EventStream[Message])
    events: Iterator[StreamEvent[Message]] = stream
    for event in stream:
        assert_type(event, StreamEvent[Message])
        assert_type(event.data, Message)
        assert_type(event.event_type, str)
        assert_type(event.event_id, str | None)
        assert_type(event.retry_ms, int | None)
        assert_type(event.sequence, int)
        assert_type(event.raw_data, str)
    for message in stream.data():
        assert_type(message, Message)
    assert_type(stream.response, ResponseInfo)
    progress: ProtocolProgress = stream.progress
    with client.protocols.events.typed.open() as typed:
        assert_type(typed, EventStream[Created | Deleted | UnknownEvent])
        for value in typed.data():
            if isinstance(value, UnknownEvent):
                assert_type(value.discriminator, str)
                assert_type(value.raw_data, str)
    assert_type(client.protocols.events.tagged.open(), EventStream[Created | Deleted])
    assert_type(client.protocols.feed.all.open(body=query), EventStream[Message])
    wider: StreamEvent[object] = next(stream)
    try:
        next(client.protocols.events.typed.open())
    except StreamRemoteError as error:
        remote(error)
    stream.close()
    del events, progress, wider


def remote(error: StreamRemoteError) -> None:
    """Read an error event's data as object, never Any, and its SSE type."""
    assert_type(error.data, object)
    assert_type(error.event_type, str | None)


async def async_events(client: AsyncClient) -> None:
    """Open asyncio streams with one await and iterate them, and their data, without awaiting the iterators."""
    stream = await client.protocols.events.messages.open()
    assert_type(stream, AsyncEventStream[Message])
    events: AsyncIterator[StreamEvent[Message]] = stream
    async for event in stream:
        assert_type(event.data, Message)
    data = stream.data()
    assert_type(data, AsyncIterator[Message])
    async for message in data:
        assert_type(message, Message)
    async with await client.protocols.events.typed.open() as typed:
        assert_type(typed, AsyncEventStream[Created | Deleted | UnknownEvent])
    await stream.aclose()
    del events
