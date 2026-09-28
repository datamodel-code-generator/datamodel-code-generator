"""Misuse a generated client: each marked line holds exactly one type error."""

from __future__ import annotations

from collections.abc import Iterator
from typing import BinaryIO

from pets import AsyncClient, Client
from pets.responses import RawResponse
from pets.transports import OwnedTransportAdapter
from pets.types.pets import (
    CreatePetRequestCodecs,
    DeletePetsByPetIdRequestCodecs,
    GetPetRequestCodecs,
    ListPetsRequestCodecs,
    decode_list_pets_header,
)
from pets.types.pets.photos import UploadRequestCodecs


def misuse(client: Client) -> None:
    trace = ListPetsRequestCodecs.parameter(location="header", name="X-Trace").from_wire("t")
    pet = GetPetRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    body = CreatePetRequestCodecs.body(media_type="application/json").from_wire({"name": "dog"})
    client.pets.list_pets()  # error
    client.pets.list_pets(x_trace=trace, limit=None)  # error
    client.pets.create_pet(body=body, media_type="text/csv")  # error
    client.pets.get_pet(pet_id=pet, response_media_type="image/png")  # error
    response = client.pets.with_response.list_pets(x_trace=trace)
    decode_list_pets_header(response.info, name="X-Other")  # error
    photo = UploadRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    client.pets.photos.upload(pet_id=photo, body="text")  # error
    deleted = DeletePetsByPetIdRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    client.pets.delete_pets_by_pet_id(pet_id=deleted, options="fast")  # error


def misuse_transports(client: Client, adapter: object) -> None:
    from clients import Adapter, AsyncAdapter

    Client(transport_adapter=OwnedTransportAdapter(AsyncAdapter()))  # error
    AsyncClient(transport_adapter=Adapter())  # error
    client.with_options(None)  # error
    del adapter


def misuse_raw(client: Client) -> None:
    pet = GetPetRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    client.pets.with_raw_response.get_pet(pet_id=pet, response_media_type="image/png")  # error
    streamed: RawResponse = client.pets.with_streaming_response.get_pet(pet_id=pet)  # error
    del streamed
    client.request_raw("POST", "https://example.com", body="text")  # error
    client.with_streaming_response.request_raw("GET")  # error


async def misuse_raw_async(client: AsyncClient) -> None:
    pet = GetPetRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    await client.pets.with_streaming_response.get_pet(pet_id=pet)  # error
    saved = await client.pets.with_raw_response.get_pet(pet_id=pet)
    chunks: Iterator[bytes] = saved.iter_bytes()  # error
    del chunks


async def misuse_bodies(client: Client, aclient: AsyncClient, file: BinaryIO) -> None:
    from pets.bodies import AsyncFileBody, BodyFactory, FileBody, StreamBody

    photo = UploadRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    client.pets.photos.upload(pet_id=photo, body=AsyncFileBody(file))  # error
    await aclient.pets.photos.upload(pet_id=photo, body=FileBody(file))  # error
    StreamBody(["text"])  # error
    FileBody(file, ownership="shared")  # error
    BodyFactory(lambda: b"")  # error


def misuse_multipart(client: Client, file: BinaryIO) -> None:
    from pets.bodies import AsyncFileBody, AsyncMultipartBody, FieldPart, FilePart, MultipartBody
    from pets.model_codecs import WireValue

    MultipartBody[str]((FilePart("f", AsyncFileBody(file)),))  # error
    client.request_raw("POST", "https://example.com/forms", body=AsyncMultipartBody[WireValue](()))  # error
    FieldPart[str]("a", 1)  # error
    parts: MultipartBody[str] = MultipartBody[int](())  # error
    del parts
