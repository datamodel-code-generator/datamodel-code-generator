"""Use a generated client as typed code: calls, media overloads, results, errors, and header accessors."""

from __future__ import annotations

from typing_extensions import assert_type

from pets import AsyncClient, Client
from pets.model_codecs import ModelValue
from pets.options import UNSET, RequestOptions
from pets.responses import Response
from pets.types.pets.photos import UploadRequestCodecs
from pets.types.pets import (
    CreatePetRequestCodecs,
    CreatePetResponse,
    GetPetRequestCodecs,
    ListPetsErrorData,
    ListPetsHTTPError,
    ListPetsRequestCodecs,
    ListPetsResponse,
    decode_list_pets_header,
)


def call(client: Client) -> None:
    trace = ListPetsRequestCodecs.parameter(location="header", name="X-Trace").from_wire("t")
    pet = GetPetRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    assert_type(client.pets.list_pets(x_trace=trace, limit=UNSET), ListPetsResponse)
    response = client.pets.with_response.list_pets(x_trace=trace, options=RequestOptions(max_response_bytes=None))
    assert_type(response, Response[ListPetsResponse])
    decode_list_pets_header(response.info, name="X-Next")
    decode_list_pets_header(response.info, name="X-Rate")
    body = CreatePetRequestCodecs.body(media_type="application/json").from_wire({"name": "dog"})
    assert_type(client.pets.create_pet(body=body, media_type="application/json"), CreatePetResponse)
    text = CreatePetRequestCodecs.body(media_type="text/plain").from_wire("dog")
    client.pets.create_pet(body=text, media_type="text/plain")
    client.pets.get_pet(pet_id=pet, response_media_type="text/plain")
    photo = UploadRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    client.pets.photos.upload(pet_id=photo, body=b"\x00")
    try:
        client.pets.list_pets(x_trace=trace)
    except ListPetsHTTPError as error:
        assert_type(error.error_data, ListPetsErrorData | None)


async def call_async(client: AsyncClient) -> None:
    trace = ListPetsRequestCodecs.parameter(location="header", name="X-Trace").from_wire("t")
    assert_type(await client.pets.list_pets(x_trace=trace), ListPetsResponse)
    response = await client.pets.with_response.list_pets(x_trace=trace)
    assert_type(response, Response[ListPetsResponse])
    value: ModelValue[object] | None = None
    del value
    await client.aclose()
