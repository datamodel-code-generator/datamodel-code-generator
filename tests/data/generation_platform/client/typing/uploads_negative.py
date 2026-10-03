"""Reject unawaited asyncio starts, text and one-shot sources, other options, other result types, and misuses."""

from __future__ import annotations

from collections.abc import Iterator
from io import StringIO

from pets import AsyncClient, Client
from pets.options import SessionOptions
from pets.protocols import AsyncUploadHandle, UploadHandle
from pets.types.files import CompleteFileResponse
from pets_models import FieldFilesPostHeaderTusResumableParameter


async def wrong_uploads(
    client: Client, async_client: AsyncClient, version: FieldFilesPostHeaderTusResumableParameter, chunks: Iterator[bytes]
) -> None:
    """Reject each misuse of a helper, its handle, or a source."""
    source = b"content"
    unawaited: AsyncUploadHandle[None] = async_client.protocols.files.upload.start(source, tus_resumable=version)  # error
    client.protocols.files.upload.start("content", tus_resumable=version)  # error
    client.protocols.files.upload.start(StringIO("content"), tus_resumable=version)  # error
    client.protocols.files.upload.start(chunks, tus_resumable=version)  # error
    client.protocols.files.upload.start(source)  # error
    client.protocols.files.upload.start(source, tus_resumable=version, upload_options=SessionOptions())  # error
    client.protocols.files.upload.start(source, tus_resumable=version, upload_length=7)  # error
    results: UploadHandle[CompleteFileResponse] = client.protocols.files.upload.start(source, tus_resumable=version)  # error
    finished: UploadHandle[None] = client.protocols.files.finish.start(source, tus_resumable=version)  # error
    client.protocols.files.upload.resume(source, b"state")  # error
    client.protocols.files.upload.start(source, tus_resumable=version).run(True)  # error
    client.protocols.files.upload.start(source, tus_resumable=version).advance().complete = True  # error
    del unawaited, results, finished
