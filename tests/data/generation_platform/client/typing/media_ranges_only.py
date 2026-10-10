"""Select concrete response types through the existing range-only upload operation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pets.responses import AsyncRawResponse, RawResponse, Response
from pets.types.pets.photos import UploadResponse
from typing_extensions import assert_type

if TYPE_CHECKING:
    from pets import AsyncClient, Client
    from pets_models import FieldPetsPetIdPhotoPutPathPetIdParameter


def selected(client: Client, pet: FieldPetsPetIdPhotoPutPathPetIdParameter, media: str) -> None:
    """Check range-only response arguments and results in synchronous views."""
    assert_type(client.pets.photos.upload(petId=pet, response_media_type="image/jpeg"), UploadResponse)
    assert_type(client.pets.photos.upload(petId=pet, response_media_type=media), UploadResponse)
    assert_type(client.pets.photos.upload(petId=pet, response_media_type=None), UploadResponse)
    assert_type(
        client.pets.photos.with_response.upload(petId=pet, response_media_type=media), Response[UploadResponse]
    )
    assert_type(client.pets.photos.with_raw_response.upload(petId=pet, response_media_type=media), RawResponse)
    with client.pets.photos.with_streaming_response.upload(petId=pet, response_media_type=media) as response:
        assert_type(response, RawResponse)


async def selected_async(client: AsyncClient, pet: FieldPetsPetIdPhotoPutPathPetIdParameter, media: str) -> None:
    """Check range-only response arguments and results in asynchronous views."""
    assert_type(await client.pets.photos.upload(petId=pet, response_media_type="image/jpeg"), UploadResponse)
    assert_type(await client.pets.photos.upload(petId=pet, response_media_type=media), UploadResponse)
    assert_type(await client.pets.photos.upload(petId=pet, response_media_type=None), UploadResponse)
    assert_type(
        await client.pets.photos.with_response.upload(petId=pet, response_media_type=media), Response[UploadResponse]
    )
    assert_type(
        await client.pets.photos.with_raw_response.upload(petId=pet, response_media_type=media), AsyncRawResponse
    )
    async with client.pets.photos.with_streaming_response.upload(petId=pet, response_media_type=media) as response:
        assert_type(response, AsyncRawResponse)
