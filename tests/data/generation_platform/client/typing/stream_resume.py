"""Keep event types through checkpoints, resumes, and reconnecting streams, with sync and asyncio clients."""

from __future__ import annotations

from pets import AsyncClient, Client
from pets.errors import StreamInterruptedError, StreamResumeExhaustedError
from pets.options import RequestOptions, SessionOptions
from pets.protocols import AsyncEventStream, EventStream, ResumeState, StreamOptions, UnknownEvent, import_state
from pets_models import Created, Message, Record
from typing_extensions import assert_type


def resumed(client: Client) -> None:
    """Checkpoint a stream, export and import its state, and resume it with the open method's options."""
    stream = client.protocols.events.live.open(stream_options=StreamOptions(reconnect=True, max_reconnects=None))
    state = stream.checkpoint()
    assert_type(state, ResumeState)
    again = client.protocols.events.live.resume(
        import_state(state.export()),
        stream_options=StreamOptions(reconnect=True, max_reconnect_wait=5),
        options=RequestOptions(),
        session_options=SessionOptions(max_network_sends=4),
    )
    assert_type(again, EventStream[Message])
    assert_type(client.protocols.events.tracked.resume(state), EventStream[Created | UnknownEvent])
    assert_type(client.protocols.records.all.resume(state), EventStream[Record])
    try:
        next(again)
    except StreamResumeExhaustedError as error:
        exhausted: ResumeState | None = error.resume_state
        del exhausted
    except StreamInterruptedError as error:
        interrupted: ResumeState | None = error.resume_state
        del interrupted


async def async_resumed(client: AsyncClient) -> None:
    """Resume an asyncio stream with one await and checkpoint it without awaiting."""
    stream = await client.protocols.events.live.open()
    state = stream.checkpoint()
    assert_type(state, ResumeState)
    again = await client.protocols.events.live.resume(state)
    assert_type(again, AsyncEventStream[Message])
    await again.aclose()
