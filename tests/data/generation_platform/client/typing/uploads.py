"""Keep result types through upload helpers, their handles, and builtin sources, with sync and asyncio clients."""

from __future__ import annotations

from pathlib import Path

from pets import AsyncClient, Client
from pets.options import RequestOptions, SessionOptions
from pets.protocols import (
    AsyncBytesUploadSource,
    AsyncFileUploadSource,
    AsyncRangeReader,
    AsyncUploadHandle,
    AsyncUploadSource,
    BytesUploadSource,
    FileUploadSource,
    PartReceipt,
    RangeReader,
    ResumeState,
    UploadHandle,
    UploadIdentity,
    UploadOptions,
    UploadProgress,
    UploadSource,
)
from pets.types.files import CompleteFileResponse
from pets_models import FieldFilesPostHeaderTusResumableParameter
from typing_extensions import assert_type


def uploads(client: Client, version: FieldFilesPostHeaderTusResumableParameter, path: Path) -> None:
    """Start, advance, run, checkpoint, and resume uploads of builtin sources."""
    source: UploadSource = BytesUploadSource.from_bytes(b"content")
    handle = client.protocols.files.upload.start(
        source,
        tus_resumable=version,
        upload_options=UploadOptions(chunk_bytes=4, max_parts=None),
        options=RequestOptions(),
        session_options=SessionOptions(max_network_sends=5),
    )
    assert_type(handle, UploadHandle[None])
    progress = handle.advance()
    assert_type(progress, UploadProgress)
    assert_type(progress.confirmed_bytes, int)
    assert_type(progress.confirmed_parts, tuple[PartReceipt, ...])
    assert_type(handle.run(), None)
    state = handle.checkpoint()
    assert_type(state, ResumeState)
    handle.close()
    with client.protocols.files.finish.start(FileUploadSource.from_path(path), tus_resumable=version) as finished:
        assert_type(finished.run(), CompleteFileResponse)
    resumed = client.protocols.files.finish.resume(source, state, upload_options=UploadOptions())
    assert_type(resumed, UploadHandle[CompleteFileResponse])
    identity: UploadIdentity = source.identity
    with source.open_range(0, identity.size) as reader:
        read: RangeReader = reader
        assert_type(read.read(4), bytes)


async def async_uploads(client: AsyncClient, version: FieldFilesPostHeaderTusResumableParameter, path: Path) -> None:
    """Start, advance, run, and resume uploads with asyncio, from bytes and from a file read on its own worker."""
    source: AsyncUploadSource = AsyncBytesUploadSource.from_bytes(bytearray(b"content"))
    handle = await client.protocols.files.upload.start(source, tus_resumable=version)
    assert_type(handle, AsyncUploadHandle[None])
    assert_type(await handle.advance(), UploadProgress)
    assert_type(await handle.run(), None)
    state = handle.checkpoint()
    await handle.aclose()
    async with await AsyncFileUploadSource.from_path(path) as file:
        async with await client.protocols.files.finish.start(file, tus_resumable=version) as finished:
            assert_type(await finished.run(), CompleteFileResponse)
    resumed = await client.protocols.files.upload.resume(source, state)
    assert_type(resumed, AsyncUploadHandle[None])
    async with source.open_range(0, 1) as reader:
        read: AsyncRangeReader = reader
        assert_type(await read.read(1), bytes)
