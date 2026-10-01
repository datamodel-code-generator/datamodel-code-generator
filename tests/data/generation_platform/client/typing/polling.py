"""Keep result, poll, and cancel types through polling helpers, their handles, and checkpoints, sync and asyncio."""

from __future__ import annotations

from pets import AsyncClient, Client
from pets.options import RequestOptions, SessionOptions
from pets.protocols import (
    AsyncLroHandle,
    CancelReceipt,
    LroHandle,
    PollOptions,
    PollSnapshot,
    ProtocolProgress,
    ResumeState,
)
from pets.responses import ResponseInfo
from pets.types.exports import CancelExportsResponse
from pets.types.jobs import CancelJobResponse, GetJobResponse, GetReportResponse
from pets.types.reports import FindReportResponse, LatestReportResponse
from pets_models import JobRequest, Report
from typing_extensions import assert_type


def operations(client: Client, job: JobRequest) -> None:
    """Start operations, poll them once, and wait for their typed results."""
    helper = client.protocols.jobs.run
    handle = helper.start(
        body=job,
        poll_options=PollOptions(max_polls=3, interval=0.5),
        options=RequestOptions(),
        session_options=SessionOptions(max_network_sends=5),
    )
    assert_type(handle, LroHandle[GetReportResponse, GetJobResponse])
    snapshot = handle.status()
    assert_type(snapshot, PollSnapshot[GetJobResponse])
    assert_type(snapshot.data, GetJobResponse)
    assert_type(snapshot.terminal, bool)
    assert_type(snapshot.response, ResponseInfo)
    assert_type(handle.wait(), GetReportResponse)
    progress: ProtocolProgress = handle.progress
    with helper.start(body=job) as managed:
        assert_type(managed, LroHandle[GetReportResponse, GetJobResponse])
    handle.close()
    assert_type(client.protocols.jobs.inline.start(body=job).wait(), Report)
    assert_type(client.protocols.jobs.report.start(body=job), LroHandle[FindReportResponse, GetJobResponse])
    assert_type(client.protocols.exports.run.start().wait(), None)
    assert_type(client.protocols.exports.latest.start().wait(), LatestReportResponse)
    wider: PollSnapshot[object] = snapshot
    del progress, wider


async def async_operations(client: AsyncClient, job: JobRequest) -> None:
    """Start operations with asyncio and await their polls and results."""
    helper = client.protocols.jobs.run
    handle = await helper.start(body=job)
    assert_type(handle, AsyncLroHandle[GetReportResponse, GetJobResponse])
    assert_type(await handle.status(), PollSnapshot[GetJobResponse])
    assert_type(await handle.wait(), GetReportResponse)
    async with await client.protocols.jobs.inline.start(body=job) as managed:
        assert_type(await managed.wait(), Report)
    await handle.aclose()


def checkpoints(client: Client, job: JobRequest, state: ResumeState) -> None:
    """Checkpoint handles, resume them with their helper's handle type, and cancel declared operations remotely."""
    helper = client.protocols.jobs.run
    saved = helper.start(body=job).checkpoint()
    assert_type(saved, ResumeState)
    assert_type(helper.resume(saved), LroHandle[GetReportResponse, GetJobResponse])
    resumed = helper.resume(
        state,
        poll_options=PollOptions(max_polls=2),
        options=RequestOptions(),
        session_options=SessionOptions(max_network_sends=5),
    )
    assert_type(resumed.wait(), GetReportResponse)
    tracked = client.protocols.jobs.tracked.start(body=job)
    receipt = tracked.cancel_remote()
    assert_type(receipt, CancelReceipt[CancelJobResponse])
    assert_type(receipt.data, CancelJobResponse)
    assert_type(receipt.response, ResponseInfo)
    assert_type(tracked.wait(), Report)
    base: LroHandle[Report, GetJobResponse] = tracked
    assert_type(client.protocols.jobs.tracked.resume(state).cancel_remote(), CancelReceipt[CancelJobResponse])
    assert_type(client.protocols.exports.run.start().cancel_remote(), CancelReceipt[CancelExportsResponse])
    wider: CancelReceipt[object] = receipt
    del base, wider


async def async_checkpoints(client: AsyncClient, job: JobRequest, state: ResumeState) -> None:
    """Resume asyncio handles without awaiting, and await their remote cancellation."""
    resumed = client.protocols.jobs.run.resume(state)
    assert_type(resumed, AsyncLroHandle[GetReportResponse, GetJobResponse])
    assert_type(resumed.checkpoint(), ResumeState)
    tracked = await client.protocols.jobs.tracked.start(body=job)
    assert_type(await tracked.cancel_remote(), CancelReceipt[CancelJobResponse])
    base: AsyncLroHandle[Report, GetJobResponse] = client.protocols.jobs.tracked.resume(state)
    assert_type(await base.wait(), Report)
