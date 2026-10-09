"""Reject resuming helpers without resume metadata, unawaited and wrongly awaited resumes, and other states."""

from __future__ import annotations

from pets import AsyncClient, Client
from pets.model_codecs import JSONValue
from pets.protocols import EventStream
from pets_models import Created


async def wrong_resumes(client: Client, async_client: AsyncClient, state: JSONValue) -> None:
    """Reject each misuse of a resume."""
    client.protocols.events.plain.resume(state)  # error
    await client.protocols.events.live.resume(state)  # error
    list(async_client.protocols.events.live.resume(state))  # error
    client.protocols.events.live.resume(b"state")  # error
    client.protocols.events.tracked.resume(state, topic="news")  # error
    typed: EventStream[Created] = client.protocols.events.live.resume(state)  # error
    await async_client.protocols.events.live.open().checkpoint()  # error
    del typed
