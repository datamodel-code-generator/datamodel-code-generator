"""Reject sync iteration of asyncio NDJSON streams, unawaited opens, narrowed records, a missing body, and other options."""

from __future__ import annotations

from pets import AsyncClient, Client
from pets.options import RequestOptions
from pets.protocols import EventStream
from pets_models import Created, Record


async def wrong_records(client: Client, async_client: AsyncClient) -> None:
    """Reject each misuse of an NDJSON helper or its stream."""
    list(await async_client.protocols.records.all.open())  # error
    await client.protocols.records.all.open()  # error
    typed: EventStream[Created] = client.protocols.records.tagged.open()  # error
    lenient: EventStream[Created] = client.protocols.records.lenient.open()  # error
    client.protocols.records.all.open(stream_options=RequestOptions())  # error
    client.protocols.search.all.open()  # error
    client.protocols.records.all.open(body=Record(text="a"))  # error
    del typed, lenient
