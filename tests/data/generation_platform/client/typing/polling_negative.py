"""Reject unawaited asyncio starts, missing bodies, other options, other handle types, and undeclared arguments."""

from __future__ import annotations

from pets import AsyncClient, Client
from pets.options import SessionOptions
from pets.protocols import AsyncLroHandle, LroHandle
from pets.types.jobs import GetJobResponse, GetReportResponse
from pets_models import JobRequest


async def wrong_handles(client: Client, async_client: AsyncClient, job: JobRequest) -> None:
    """Reject each misuse of a helper or its handle."""
    unawaited: AsyncLroHandle[GetReportResponse, GetJobResponse] = async_client.protocols.jobs.run.start(body=job)  # error
    client.protocols.jobs.run.start()  # error
    client.protocols.jobs.run.start(body=job, poll_options=SessionOptions())  # error
    await client.protocols.jobs.run.start(body=job)  # error
    results: LroHandle[None, GetJobResponse] = client.protocols.jobs.run.start(body=job)  # error
    polls: LroHandle[object, object] = client.protocols.jobs.run.start(body=job)  # error
    client.protocols.jobs.run.start(body=job).status().terminal = True  # error
    client.protocols.jobs.run.start(body=job, response_media_type="application/json")  # error
    del unawaited, results, polls
