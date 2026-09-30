"""Misuse a generated client: each marked line holds exactly one type error."""

from __future__ import annotations

from collections.abc import Iterator
from typing import BinaryIO

from pets import AsyncClient, Client
from pets.errors import (
    BudgetExceededError,
    DeadlineExceededError,
    DeliveryState,
    LimiterExecutionError,
    PhaseTimeoutError,
    RedirectPolicyError,
    RequestCancelledError,
    SDKError,
)
from pets.hooks import AsyncLimiter, AsyncPermit, Limiter, LimiterContext, Permit
from pets.options import (
    CancelToken,
    ClientOptions,
    Deadline,
    IdempotencyKey,
    RedirectOptions,
    RequestOptions,
    RetryOptions,
    TimeoutOptions,
    TransportOptions,
    ValidationOptions,
)
from pets.responses import RawResponse, ResponseInfo
from pets.transports import OwnedTransportAdapter
from pets.types.pets import (
    AttachFilesRequestCodecs,
    CreatePetRequestCodecs,
    DeletePetsByPetIdRequestCodecs,
    GetPetRequestCodecs,
    ListPetsRequestCodecs,
    ReadFilesRequestCodecs,
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


def misuse_selectors(client: Client) -> None:
    body = CreatePetRequestCodecs.body(media_type="application/json").from_wire({"name": "dog"})
    sent = UploadRequestCodecs.select_request_media(
        declared_media="application/octet-stream", concrete_media="application/octet-stream"
    )
    UploadRequestCodecs.select_request_media(declared_media="image/png", concrete_media="image/png")  # error
    client.pets.create_pet(body=body, media_type=sent)  # error


def misuse_multipart(client: Client, file: BinaryIO) -> None:
    from pets.bodies import AsyncFileBody, AsyncMultipartBody, FieldPart, FilePart, MultipartBody, MultipartData
    from pets.model_codecs import WireValue

    MultipartBody[str]((FilePart("f", AsyncFileBody(file)),))  # error
    client.request_raw("POST", "https://example.com/forms", body=AsyncMultipartBody[WireValue](()))  # error
    FieldPart[str]("a", 1)  # error
    parts: MultipartBody[str] = MultipartBody[int](())  # error
    del parts
    pet = AttachFilesRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    client.pets.attach_files(pet_id=pet, body=MultipartBody((FieldPart("note", 1), FilePart("file", b"x"))))  # error
    client.pets.attach_files(pet_id=pet, body=MultipartBody[str](()))  # error
    files = ReadFilesRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    narrowed: MultipartData[str] = client.pets.read_files(pet_id=files)  # error
    del narrowed


def misuse_multipart_data(data: object) -> None:
    from pets.bodies import MultipartData

    narrowed: MultipartData[str] = MultipartData[bytes](())  # error
    del narrowed, data


def misuse_hooks() -> None:
    RequestOptions(hooks=("trace",))  # error
    RequestOptions(context={"tags": ["a"]})  # error


def misuse_validation() -> None:
    ValidationOptions(request=None)  # error
    ValidationOptions(response="none")  # error
    ValidationOptions(arguments="strict")  # error
    RequestOptions(validation="schema")  # error


def misuse_timing(deadline: Deadline, token: CancelToken, phase: TimeoutOptions, context: LimiterContext) -> None:
    Deadline.after("soon")  # error
    TimeoutOptions(connect="slow")  # error
    ClientOptions(timeout=30)  # error
    RequestOptions(total_timeout="soon")  # error
    RequestOptions(deadline=60)  # error
    RequestOptions(cancel_token=True)  # error
    RequestOptions(max_network_sends=0.5)  # error
    RequestOptions(stream_idle_timeout="forever")  # error
    RequestOptions(stream_total_timeout="forever")  # error
    deadline.at = 0  # error
    token.cancelled = True  # error
    phase.read = 1  # error
    context.remaining_timeout = 0  # error


def misuse_limiters(sync: Limiter, asynchronous: AsyncLimiter, permit: Permit, async_permit: AsyncPermit) -> None:
    wrong_sync: Limiter = asynchronous  # error
    wrong_async: AsyncLimiter = sync  # error
    wrong_permit: Permit = async_permit  # error
    wrong_async_permit: AsyncPermit = permit  # error
    RequestOptions(limiter="queue")  # error
    del wrong_sync, wrong_async, wrong_permit, wrong_async_permit


def misuse_deadline_errors(error: SDKError) -> None:
    state = DeliveryState.NOT_SENT
    PhaseTimeoutError(effective_timeout=1, phase="unknown", delivery_state=state)  # error
    DeadlineExceededError(deadline_at=0, elapsed=1, phase="connect", delivery_state=state)  # error
    RequestCancelledError(source="asyncio", delivery_state=state)  # error
    BudgetExceededError(budget_kind="auth", limit=1, used=0)  # error
    LimiterExecutionError(action="wait")  # error
    SDKError(network_send_count="one")  # error
    error.network_send_count = 0  # error
    error.wire_send_count = 0  # error


def misuse_retry_options(key: IdempotencyKey, info: ResponseInfo, error: RedirectPolicyError) -> None:
    RetryOptions(max_retries=None)  # error
    RetryOptions(jitter="equal")  # error
    RetryOptions(statuses=[429, 503])  # error
    RetryOptions(respect_retry_after=None)  # error
    RetryOptions(retry_after_ms_header=42)  # error
    RedirectOptions(enabled=None)  # error
    RedirectOptions(allowed_origins=["https://example.com"])  # error
    RequestOptions(retry=None)  # error
    RequestOptions(redirects=None)  # error
    RequestOptions(idempotency_key="opaque")  # error
    RequestOptions(transport=TransportOptions())  # error
    ClientOptions(transport=None)  # error
    TransportOptions(verify="strict")  # error
    TransportOptions(retry_owner="native")  # error
    IdempotencyKey()  # error
    IdempotencyKey("opaque", None)  # error
    IdempotencyKey("opaque", first_used_at="2026-09-28")  # error
    key.value = "changed"  # error
    key.first_used_at = None  # error
    ResponseInfo(
        status_code=200,
        headers=info.headers,
        call_id=info.call_id,
        elapsed=0,
        content_type=None,
        wire_send_count="one",  # error
    )
    info.wire_send_count = 1  # error
    RedirectPolicyError()  # error
    RedirectPolicyError(delivery_state=DeliveryState.RESPONSE_STARTED, body_available=True)  # error
    error.body_available = False  # error
    error.delivery_state = DeliveryState.NOT_SENT  # error
