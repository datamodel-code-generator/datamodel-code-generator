"""Keep result and poll types through polling helpers and their handles, with sync and asyncio clients."""

from __future__ import annotations

from pets import AsyncClient, Client
from pets.options import RequestOptions, SessionOptions
from pets.protocols import AsyncLroHandle, LroHandle, PollOptions, PollSnapshot, ProtocolProgress
from pets.responses import ResponseInfo
from pets.types.jobs import GetJobResponse, GetReportResponse
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
