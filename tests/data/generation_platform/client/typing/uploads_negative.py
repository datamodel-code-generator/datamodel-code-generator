"""Reject unawaited asyncio starts, sources of the other kind, other options, other result types, and misuses."""

from __future__ import annotations

from pets import AsyncClient, Client
from pets.options import SessionOptions
from pets.protocols import AsyncBytesUploadSource, AsyncUploadHandle, BytesUploadSource, UploadHandle, UploadIdentity
from pets.types.files import CompleteFileResponse
from pets_models import FieldFilesPostHeaderTusResumableParameter


async def wrong_uploads(
    client: Client, async_client: AsyncClient, version: FieldFilesPostHeaderTusResumableParameter
) -> None:
    """Reject each misuse of a helper, its handle, or a source."""
    source = BytesUploadSource.from_bytes(b"content")
    streamed = AsyncBytesUploadSource.from_bytes(b"content")
    async_helper = async_client.protocols.files.upload
    helper = client.protocols.files.upload
    unawaited: AsyncUploadHandle[None] = async_helper.start(streamed, tus_resumable=version)  # error
    client.protocols.files.upload.start(streamed, tus_resumable=version)  # error
    client.protocols.files.upload.start(b"content", tus_resumable=version)  # error
    client.protocols.files.upload.start(source)  # error
    client.protocols.files.upload.start(source, tus_resumable=version, upload_options=SessionOptions())  # error
    client.protocols.files.upload.start(source, tus_resumable=version, upload_length=7)  # error
    results: UploadHandle[CompleteFileResponse] = helper.start(source, tus_resumable=version)  # error
    finished: UploadHandle[None] = client.protocols.files.finish.start(source, tus_resumable=version)  # error
    client.protocols.files.upload.resume(source, b"state")  # error
    client.protocols.files.upload.start(source, tus_resumable=version).run(True)  # error
    client.protocols.files.upload.start(source, tus_resumable=version).advance().complete = True  # error
    UploadIdentity(size=1, sha256="digest")  # error
    client.protocols.files.parts.start(source, tus_resumable=version).abort_remote()  # pyright: ignore[reportUnknownMemberType]  # error
    client.protocols.files.upload.start(source, tus_resumable=version).abort_remote()  # pyright: ignore[reportUnknownMemberType]  # error
    client.protocols.files.parts_abort.start(source, tus_resumable=version).abort_remote(True)  # error
    no_result: None = client.protocols.files.parts_abort.start(source, tus_resumable=version).abort_remote()  # error
    await async_client.protocols.files.parts.start(source, tus_resumable=version)  # error
    async_parts = await async_client.protocols.files.parts.start(streamed, tus_resumable=version)
    await async_parts.abort_remote()  # pyright: ignore[reportUnknownMemberType]  # error
    del unawaited, results, finished, no_result
