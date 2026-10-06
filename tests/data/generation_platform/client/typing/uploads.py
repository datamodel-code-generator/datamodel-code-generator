"""Keep result types through upload helpers and their handles, from bytes and files, with sync and asyncio clients."""

from __future__ import annotations

from pathlib import Path

from pets import AsyncClient, Client
from pets.options import RequestOptions, SessionOptions
from pets.protocols import (
    AsyncUploadHandle,
    ResumeState,
    UploadHandle,
    UploadOptions,
    UploadProgress,
    UploadSource,
)
from pets.types.files import CompleteFileResponse
from pets_models import FieldFilesPostHeaderTusResumableParameter
from typing_extensions import assert_type


def uploads(client: Client, version: FieldFilesPostHeaderTusResumableParameter, path: Path) -> None:
    """Start, advance, run, checkpoint, and resume uploads of bytes and files."""
    source: UploadSource = b"content"
    handle = client.protocols.files.upload.start(
        source,
        tus_resumable=version,
        upload_options=UploadOptions(chunk_bytes=4, max_parts=None),
        options=RequestOptions(),
        session_options=SessionOptions(total_timeout=30),
    )
    assert_type(handle, UploadHandle[None])
    progress = handle.advance()
    assert_type(progress, UploadProgress)
    assert_type(progress.confirmed_bytes, int)
    assert_type(progress.complete, bool)
    assert_type(handle.run(), None)
    state = handle.checkpoint()
    assert_type(state, ResumeState)
    handle.close()
    with path.open("rb") as file, client.protocols.files.finish.start(file, tus_resumable=version) as finished:
        assert_type(finished.run(), CompleteFileResponse)
    resumed = client.protocols.files.finish.resume(source, state, upload_options=UploadOptions())
    assert_type(resumed, UploadHandle[CompleteFileResponse])


async def async_uploads(client: AsyncClient, version: FieldFilesPostHeaderTusResumableParameter, path: Path) -> None:
    """Start, advance, run, and resume uploads with asyncio, from a bytearray and from a file."""
    source = bytearray(b"content")
    handle = await client.protocols.files.upload.start(source, tus_resumable=version)
    assert_type(handle, AsyncUploadHandle[None])
    assert_type(await handle.advance(), UploadProgress)
    assert_type(await handle.run(), None)
    state = handle.checkpoint()
    await handle.aclose()
    with path.open("rb") as file:
        async with await client.protocols.files.finish.start(file, tus_resumable=version) as finished:
            assert_type(await finished.run(), CompleteFileResponse)
    resumed = await client.protocols.files.upload.resume(memoryview(source), state)
    assert_type(resumed, AsyncUploadHandle[None])
