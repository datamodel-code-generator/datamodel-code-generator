"""Reject unawaited asyncio starts, text and one-shot sources, other options, other result types, and misuses."""

from __future__ import annotations

from collections.abc import Iterator
from io import StringIO

from pets import AsyncClient, Client
from pets.options import RequestOptions
from pets.protocols import AsyncUploadHandle, UploadHandle
from pets.types.files import CompleteFileResponse
from pets_models import FieldFilesPostHeaderTusResumableParameter


async def wrong_uploads(
    client: Client, async_client: AsyncClient, version: FieldFilesPostHeaderTusResumableParameter, chunks: Iterator[bytes]
) -> None:
    """Reject each misuse of a helper, its handle, or a source."""
    source = b"content"
    unawaited: AsyncUploadHandle[None] = async_client.protocols.files.upload.start(source, Tus_Resumable=version)  # error
    client.protocols.files.upload.start("content", Tus_Resumable=version)  # error
    client.protocols.files.upload.start(StringIO("content"), Tus_Resumable=version)  # error
    client.protocols.files.upload.start(chunks, Tus_Resumable=version)  # error
    client.protocols.files.upload.start(source)  # error
    client.protocols.files.upload.start(source, Tus_Resumable=version, upload_options=RequestOptions())  # error
    client.protocols.files.upload.start(source, Tus_Resumable=version, Upload_Length=7)  # error
    results: UploadHandle[CompleteFileResponse] = client.protocols.files.upload.start(source, Tus_Resumable=version)  # error
    finished: UploadHandle[None] = client.protocols.files.finish.start(source, Tus_Resumable=version)  # error
    client.protocols.files.upload.resume(source, b"state")  # error
    client.protocols.files.upload.start(source, Tus_Resumable=version).run(True)  # error
    client.protocols.files.upload.start(source, Tus_Resumable=version).advance().complete = True  # error
    del unawaited, results, finished
