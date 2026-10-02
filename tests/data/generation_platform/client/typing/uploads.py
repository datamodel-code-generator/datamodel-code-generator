"""Keep result types through upload helpers, their handles, and builtin sources, with sync and asyncio clients."""

from __future__ import annotations

from typing import TYPE_CHECKING

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
from pets.types.files import AbortFileResponse, AssembleFileResponse, CompleteFileResponse
from typing_extensions import assert_type

if TYPE_CHECKING:
    from pathlib import Path

    from pets import AsyncClient, Client
    from pets_models import FieldFilesPostHeaderTusResumableParameter


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
    async with (
        await AsyncFileUploadSource.from_path(path) as file,
        await client.protocols.files.finish.start(file, tus_resumable=version) as finished,
    ):
        assert_type(await finished.run(), CompleteFileResponse)
    resumed = await client.protocols.files.upload.resume(source, state)
    assert_type(resumed, AsyncUploadHandle[None])
    async with source.open_range(0, 1) as reader:
        read: AsyncRangeReader = reader
        assert_type(await read.read(1), bytes)


def parts_and_abort(client: Client, version: FieldFilesPostHeaderTusResumableParameter) -> None:
    """Keep parts completion and remote abort responses concrete."""
    source = BytesUploadSource.from_bytes(b"content")
    with client.protocols.files.parts.start(source, tus_resumable=version) as parts:
        assert_type(parts.run(), AssembleFileResponse)
        assert_type(parts.advance().confirmed_parts, tuple[PartReceipt, ...])
    with client.protocols.files.parts_abort.start(source, tus_resumable=version) as abortable:
        assert_type(abortable.abort_remote(), AbortFileResponse)
        assert_type(abortable.run(), AssembleFileResponse)
        assert_type(
            client.protocols.files.parts_abort.resume(source, abortable.checkpoint()).abort_remote(), AbortFileResponse
        )
    with client.protocols.files.abort.start(source, tus_resumable=version) as offset:
        assert_type(offset.abort_remote(), AbortFileResponse)
        assert_type(offset.run(), None)


async def async_parts_and_abort(client: AsyncClient, version: FieldFilesPostHeaderTusResumableParameter) -> None:
    """Keep native async parts and abort result types."""
    source = AsyncBytesUploadSource.from_bytes(b"content")
    async with await client.protocols.files.parts.start(source, tus_resumable=version) as parts:
        assert_type(await parts.run(), AssembleFileResponse)
    async with await client.protocols.files.parts_abort.start(source, tus_resumable=version) as abortable:
        assert_type(await abortable.abort_remote(), AbortFileResponse)
        assert_type(await abortable.run(), AssembleFileResponse)
        resumed = await client.protocols.files.parts_abort.resume(source, abortable.checkpoint())
        assert_type(await resumed.abort_remote(), AbortFileResponse)
    async with await client.protocols.files.abort.start(source, tus_resumable=version) as offset:
        assert_type(await offset.abort_remote(), AbortFileResponse)
