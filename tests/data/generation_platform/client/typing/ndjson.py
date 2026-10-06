"""Keep record types through NDJSON helpers, their streams, records, and error records, with sync and asyncio clients."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

from pets import AsyncClient, Client
from pets.errors import StreamRemoteError
from pets.options import RequestOptions, SessionOptions
from pets.protocols import AsyncEventStream, EventStream, StreamEvent, StreamOptions, UnknownEvent
from pets_models import Created, Deleted, Record, SearchQuery
from typing_extensions import assert_type


def records(client: Client, query: SearchQuery) -> None:
    """Yield typed records and their data, keep unknown records in the union, and read error records as object."""
    stream = client.protocols.records.all.open(
        stream_options=StreamOptions(max_line_bytes=1024, max_event_bytes=512),
        options=RequestOptions(),
        session_options=SessionOptions(total_timeout=30),
    )
    assert_type(stream, EventStream[Record])
    events: Iterator[StreamEvent[Record]] = stream
    for event in stream:
        assert_type(event.data, Record)
        assert_type(event.raw_data, str)
        assert_type(event.event_id, str | None)
    for value in stream.data():
        assert_type(value, Record)
    with client.protocols.records.tagged.open() as tagged:
        assert_type(tagged, EventStream[Created | Deleted | UnknownEvent])
        for item in tagged.data():
            if isinstance(item, UnknownEvent):
                assert_type(item.discriminator, str)
    assert_type(client.protocols.records.lenient.open(), EventStream[Record])
    assert_type(client.protocols.search.all.open(body=query), EventStream[Record])
    try:
        next(client.protocols.records.tagged.open())
    except StreamRemoteError as error:
        remote(error)
    stream.close()
    del events


def remote(error: StreamRemoteError) -> None:
    """Read an error record's data as object, never Any, and its absent SSE type."""
    assert_type(error.data, object)
    assert_type(error.event_type, str | None)


async def async_records(client: AsyncClient, query: SearchQuery) -> None:
    """Open asyncio NDJSON streams with one await and iterate them, and their data, without awaiting the iterators."""
    stream = await client.protocols.records.all.open()
    assert_type(stream, AsyncEventStream[Record])
    events: AsyncIterator[StreamEvent[Record]] = stream
    async for event in stream:
        assert_type(event.data, Record)
    data = stream.data()
    assert_type(data, AsyncIterator[Record])
    async with await client.protocols.search.all.open(body=query) as search:
        assert_type(search, AsyncEventStream[Record])
    async with await client.protocols.records.tagged.open() as tagged:
        assert_type(tagged, AsyncEventStream[Created | Deleted | UnknownEvent])
    await stream.aclose()
    del events
