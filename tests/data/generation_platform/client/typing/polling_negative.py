"""Reject unawaited starts, awaited resumes, missing bodies, other options and handle types, and undeclared cancels."""

from __future__ import annotations

from pets import AsyncClient, Client
from pets.options import SessionOptions
from pets.protocols import AsyncLroHandle, CancelReceipt, LroHandle, ResumeState
from pets.types.jobs import GetJobResponse, GetReportResponse
from pets_models import JobRequest


async def wrong_handles(client: Client, async_client: AsyncClient, job: JobRequest, state: ResumeState) -> None:
    """Reject each misuse of a helper or its handle."""
    run = async_client.protocols.jobs.run
    unawaited: AsyncLroHandle[GetReportResponse, GetJobResponse] = run.start(body=job)  # error
    client.protocols.jobs.run.start()  # error
    client.protocols.jobs.run.start(body=job, poll_options=SessionOptions())  # error
    await client.protocols.jobs.run.start(body=job)  # error
    results: LroHandle[None, GetJobResponse] = client.protocols.jobs.run.start(body=job)  # error
    polls: LroHandle[object, object] = client.protocols.jobs.run.start(body=job)  # error
    client.protocols.jobs.run.start(body=job).status().terminal = True  # error
    client.protocols.jobs.run.start(body=job, response_media_type="application/json")  # error
    client.protocols.jobs.run.start(body=job).cancel_remote()  # error
    await async_client.protocols.jobs.run.resume(state)  # error
    client.protocols.jobs.run.resume(b"state")  # error
    client.protocols.jobs.run.resume(state, body=job)  # error
    receipt: CancelReceipt[GetJobResponse] = client.protocols.jobs.tracked.start(body=job).cancel_remote()  # error
    del unawaited, results, polls, receipt
