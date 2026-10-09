"""Keep event types through checkpoints, resumes, and reconnecting streams, with sync and asyncio clients."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pets.errors import SessionLimitError, StreamInterruptedError
from pets.options import RequestOptions
from pets.model_codecs import JSONValue
from pets.protocols import AsyncEventStream, EventStream, StreamOptions, UnknownEvent
from pets_models import Created, Mark, Message, Record
from typing_extensions import assert_type

if TYPE_CHECKING:
    from pets import AsyncClient, Client


def resumed(client: Client) -> None:
    """Checkpoint a stream as plain JSON and resume it with the open method's arguments and options."""
    stream = client.protocols.events.live.open(stream_options=StreamOptions(reconnect=True, max_reconnects=None))
    state = stream.checkpoint()
    assert_type(state, JSONValue)
    again = client.protocols.events.live.resume(
        state,
        topic="news",
        stream_options=StreamOptions(reconnect=True, max_reconnect_wait=5, total_timeout=30),
        options=RequestOptions(),
    )
    assert_type(again, EventStream[Message])
    assert_type(client.protocols.events.tracked.resume(state), EventStream[Created | UnknownEvent])
    assert_type(client.protocols.records.all.resume(state), EventStream[Record])
    assert_type(client.protocols.marks.scoped.resume(state), EventStream[Mark])
    assert_type(client.protocols.marks.named.resume(state), EventStream[Mark])
    assert_type(client.protocols.marks.deep.resume(state), EventStream[Mark])
    assert_type(client.protocols.marks.bound.resume(state), EventStream[Mark])
    try:
        next(again)
    except SessionLimitError as error:
        assert_type(error.limit, float)
    except StreamInterruptedError as error:
        assert_type(error.sequence, int)


async def async_resumed(client: AsyncClient) -> None:
    """Resume an asyncio stream with one await and checkpoint it without awaiting."""
    stream = await client.protocols.events.live.open()
    state = stream.checkpoint()
    assert_type(state, JSONValue)
    again = await client.protocols.events.live.resume(state)
    assert_type(again, AsyncEventStream[Message])
    assert_type(await client.protocols.marks.scoped.resume(state), AsyncEventStream[Mark])
    assert_type(await client.protocols.marks.named.resume(state), AsyncEventStream[Mark])
    assert_type(await client.protocols.marks.deep.resume(state), AsyncEventStream[Mark])
    assert_type(await client.protocols.marks.bound.resume(state), AsyncEventStream[Mark])
    await again.aclose()
