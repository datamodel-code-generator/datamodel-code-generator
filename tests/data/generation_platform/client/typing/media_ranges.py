"""Keep concrete response selections and exact overload results in every client view."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pets.responses import AsyncRawResponse, RawResponse, Response
from pets.types.files import ReadFileResponse, StoreFileResponse
from pets_models import Address
from typing_extensions import assert_type

if TYPE_CHECKING:
    from pets import AsyncClient, Client


def selected(client: Client, media: str) -> None:
    """Check synchronous range selectors and exact return types."""
    assert_type(client.files.read_file(response_media_type="image/jpeg"), ReadFileResponse)
    assert_type(client.files.read_file(response_media_type=media), ReadFileResponse)
    assert_type(client.files.read_file(response_media_type=None), ReadFileResponse)
    assert_type(
        client.files.store_file(body=b"image", media_type="image/jpeg", response_media_type=media), StoreFileResponse
    )
    assert_type(
        client.files.store_file(body=b"image", media_type="image/jpeg", response_media_type="image/png"), bytes | None
    )
    assert_type(
        client.files.store_file(body=b"image", media_type="image/jpeg", response_media_type="application/json"),
        Address | None,
    )
    assert_type(client.files.with_response.read_file(response_media_type=media), Response[ReadFileResponse])
    assert_type(
        client.files.with_response.store_file(body=b"image", media_type="image/jpeg", response_media_type="image/png"),
        Response[bytes | None],
    )
    assert_type(client.files.with_raw_response.read_file(response_media_type=media), RawResponse)
    assert_type(
        client.files.with_raw_response.store_file(body=b"image", media_type="image/jpeg", response_media_type=media),
        RawResponse,
    )
    with client.files.with_streaming_response.read_file(response_media_type=media) as response:
        assert_type(response, RawResponse)
    with client.files.with_streaming_response.store_file(
        body=b"image", media_type="image/jpeg", response_media_type=media
    ) as mixed:
        assert_type(mixed, RawResponse)


async def selected_async(client: AsyncClient, media: str) -> None:
    """Check asynchronous range selectors and exact return types."""
    assert_type(await client.files.read_file(response_media_type="image/jpeg"), ReadFileResponse)
    assert_type(await client.files.read_file(response_media_type=media), ReadFileResponse)
    assert_type(await client.files.read_file(response_media_type=None), ReadFileResponse)
    assert_type(
        await client.files.store_file(body=b"image", media_type="image/jpeg", response_media_type=media),
        StoreFileResponse,
    )
    assert_type(
        await client.files.store_file(body=b"image", media_type="image/jpeg", response_media_type="image/png"),
        bytes | None,
    )
    assert_type(
        await client.files.store_file(body=b"image", media_type="image/jpeg", response_media_type="application/json"),
        Address | None,
    )
    assert_type(await client.files.with_response.read_file(response_media_type=media), Response[ReadFileResponse])
    assert_type(
        await client.files.with_response.store_file(
            body=b"image", media_type="image/jpeg", response_media_type="image/png"
        ),
        Response[bytes | None],
    )
    assert_type(await client.files.with_raw_response.read_file(response_media_type=media), AsyncRawResponse)
    assert_type(
        await client.files.with_raw_response.store_file(
            body=b"image", media_type="image/jpeg", response_media_type=media
        ),
        AsyncRawResponse,
    )
    async with client.files.with_streaming_response.read_file(response_media_type=media) as response:
        assert_type(response, AsyncRawResponse)
    async with client.files.with_streaming_response.store_file(
        body=b"image", media_type="image/jpeg", response_media_type=media
    ) as mixed:
        assert_type(mixed, AsyncRawResponse)
