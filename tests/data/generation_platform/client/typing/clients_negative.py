"""Misuse a generated client: each marked line holds exactly one type error."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import BinaryIO

from pets import AsyncClient, Client
from pets.errors import (
    APIStatusError,
    APITimeoutError,
    AuthError,
    ConfigurationError,
    DecodeError,
    SDKError,
)
from pets.options import (
    Clock,
    RequestOptions,
    RetryOptions,
    ServerSelection,
)
from pets.responses import (
    RawResponse,
    ResponseInfo,
)
from pets.types.pets import decode_list_pets_header
from pets_models import (
    FieldPetsGetHeaderXTraceParameter,
    FieldPetsPetIdFilesGetPathPetIdParameter,
    FieldPetsPetIdFilesPostPathPetIdParameter,
    FieldPetsPetIdGetPathPetIdParameter,
    FieldPetsPetIdPhotoPutPathPetIdParameter,
    NewPet,
)


def misuse(
    client: Client,
    trace: FieldPetsGetHeaderXTraceParameter,
    pet: FieldPetsPetIdGetPathPetIdParameter,
    body: NewPet,
    photo: FieldPetsPetIdPhotoPutPathPetIdParameter,
) -> None:
    client.pets.list_pets()  # error
    client.pets.list_pets(x_trace=trace, limit=None)  # error
    client.pets.create_pet(body=body, media_type="text/csv")  # error
    client.pets.get_pet(pet_id=pet, response_media_type="image/png")  # error
    response = client.pets.with_response.list_pets(x_trace=trace)
    decode_list_pets_header(response.info, name="X-Other")  # error
    client.pets.photos.upload(pet_id=photo, body="text")  # error
    client.pets.delete_pets_by_pet_id(pet_id=pet, options="fast")  # error


def misuse_transports(client: Client, adapter: object) -> None:
    import httpx2

    Client(http_client=httpx2.AsyncClient())  # error
    AsyncClient(http_client=httpx2.Client())  # error
    client.with_options(None)  # error
    del adapter


def misuse_raw(client: Client, pet: FieldPetsPetIdGetPathPetIdParameter) -> None:
    client.pets.with_raw_response.get_pet(pet_id=pet, response_media_type="image/png")  # error
    streamed: RawResponse = client.pets.with_streaming_response.get_pet(pet_id=pet)  # error
    del streamed
    client.request_raw("POST", "https://example.com", body="text")  # error
    client.with_streaming_response.request_raw("GET")  # error


async def misuse_raw_async(client: AsyncClient, pet: FieldPetsPetIdGetPathPetIdParameter) -> None:
    await client.pets.with_streaming_response.get_pet(pet_id=pet)  # error
    saved = await client.pets.with_raw_response.get_pet(pet_id=pet)
    chunks: Iterator[bytes] = saved.iter_bytes()  # error
    del chunks


async def misuse_bodies(
    client: Client, aclient: AsyncClient, file: BinaryIO, photo: FieldPetsPetIdPhotoPutPathPetIdParameter
) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b"a"

    client.pets.photos.upload(pet_id=photo, body=chunks())  # error
    client.pets.photos.upload(pet_id=photo, body="file.bin")  # error
    await aclient.pets.photos.upload(pet_id=photo, body=["text"])  # error


def misuse_multipart(
    client: Client,
    file: BinaryIO,
    stream: AsyncIterator[bytes],
    pet: FieldPetsPetIdFilesPostPathPetIdParameter,
    files: FieldPetsPetIdFilesGetPathPetIdParameter,
) -> None:
    from pets.bodies import AsyncMultipartBody, FieldPart, FilePart, MultipartBody, MultipartData
    from pets.model_codecs import JSONValue

    part = FilePart("f", stream)
    MultipartBody[str]((part,))  # error
    FilePart("f", "file.bin")  # error
    client.request_raw("POST", "https://example.com/forms", body=AsyncMultipartBody[JSONValue](()))  # error
    FieldPart[str]("a", 1)  # error
    parts: MultipartBody[str] = MultipartBody[int](())  # error
    del parts
    client.pets.attach_files(pet_id=pet, body=MultipartBody((FieldPart("note", 1), FilePart("file", b"x"))))  # error
    client.pets.attach_files(pet_id=pet, body=MultipartBody[str](()))  # error
    narrowed: MultipartData[str] = client.pets.read_files(pet_id=files)  # error
    del narrowed


def misuse_multipart_data(data: object) -> None:
    from pets.bodies import MultipartData

    narrowed: MultipartData[str] = MultipartData[bytes](())  # error
    del narrowed, data


def misuse_timing(client: Client, options: RequestOptions) -> None:
    RequestOptions(timeout="slow")  # error
    ServerSelection(index="first")  # error
    RequestOptions(total_timeout="soon")  # error
    RequestOptions(deadline=60)  # error
    RequestOptions(stream_idle_timeout="forever")  # error
    Client(timeout="slow")  # error
    Clock(monotonic=0.0)  # error
    RequestOptions(clock=Clock())  # error
    client.with_options(clock=Clock())  # error
    options.timeout = 1  # error


def misuse_deadline_errors(error: SDKError) -> None:
    APITimeoutError(reason=1)  # error
    AuthError(reason="expired")  # error
    DecodeError(direction="request")  # error
    APIStatusError(body=b"")  # error
    SDKError(attempt_count="one")  # error
    error.attempt_count = "zero"  # error


def misuse_retry_options(client: Client, info: ResponseInfo, error: ConfigurationError) -> None:
    RetryOptions(max_retries=1)  # error
    RetryOptions(jitter="equal")  # error
    RetryOptions(statuses=[429, 503])  # error
    RetryOptions(respect_retry_after=None)  # error
    RetryOptions(retry_after_ms_header=42)  # error
    Client(follow_redirects="yes")  # error
    Client(max_retries=None)  # error
    RequestOptions(idempotency_key=5)  # error
    Client(default_headers={"a": 1})  # error
    Client(options=RequestOptions())  # error
    Client(compression="gzip")  # error
    RetryOptions(retry_on_pool_timeout="yes")  # error
    RetryOptions(initial_delay="soon")  # error
    client.with_options(options=RequestOptions())  # error
    RequestOptions(default_headers={"X-Trace": "t"})  # error
    ResponseInfo(
        status_code=200,
        headers=info.headers,
        elapsed=0,
        content_type=None,
        attempt_count="one",  # error
    )
    info.attempt_count = 1  # error
    ConfigurationError(field_path=("redirects",), reason="redirect_refused", body_available=False)  # error
    error.field_path = "redirects"  # error


def unnarrowed_delay(retry: RetryOptions) -> float:
    return retry.max_delay  # error
