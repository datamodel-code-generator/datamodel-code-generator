"""Use a generated client as typed code: calls, media overloads, results, errors, and header accessors."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from typing import BinaryIO

from typing_extensions import assert_type

from pets import AsyncClient, Client
from pets.bodies import (
    AsyncBinaryBody,
    AsyncBodyAttempt,
    AsyncBodyFactory,
    AsyncFileBody,
    AsyncMultipartBody,
    AsyncStreamBody,
    BodyAttempt,
    BodyAttemptContext,
    BodyFactory,
    DecodedPart,
    FieldPart,
    FileBody,
    FilePart,
    MultipartBody,
    MultipartData,
    StreamBody,
    SyncBinaryBody,
)
from pets.model_codecs import JSONValue, ModelValue, NativeOutboundCodec, RequestMedia, ResponseMedia, WireValue
from pets.options import UNSET, RequestOptions
from pets.responses import AsyncRawResponse, HeadersView, RawResponse, Response, ResponseInfo
from pets.transports import (
    AsyncTransportResponse,
    AttemptIOContext,
    OwnedTransportAdapter,
    PreparedRequest,
    TransportCapabilities,
    TransportResponse,
)
from pets.types.pets.photos import UploadRequestCodecs, UploadResponse
from pets.types.pets import (
    AttachFilesRequestCodecs,
    CreatePetRequestCodecs,
    CreatePetResponse,
    GetPetRequestCodecs,
    ListPetsErrorData,
    ListPetsHTTPError,
    ListPetsRequestCodecs,
    ListPetsResponse,
    ReadFilesRequestCodecs,
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


def raw(client: Client, sink: BinaryIO) -> None:
    trace = ListPetsRequestCodecs.parameter(location="header", name="X-Trace").from_wire("t")
    pet = GetPetRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    saved = client.pets.with_raw_response.list_pets(x_trace=trace)
    assert_type(saved, RawResponse)
    assert_type(saved.info, ResponseInfo)
    assert_type(saved.body_bytes, bytes)
    assert_type(saved.read(), bytes)
    assert_type(saved.text(), str)
    assert_type(saved.json(), JSONValue)
    saved.raise_for_status()
    body = CreatePetRequestCodecs.body(media_type="application/json").from_wire({"name": "dog"})
    manager = client.pets.with_streaming_response.create_pet(body=body, media_type="application/json")
    assert_type(manager, AbstractContextManager[RawResponse])
    with client.pets.with_streaming_response.get_pet(pet_id=pet, response_media_type="text/plain") as streamed:
        for chunk in streamed.iter_bytes():
            assert_type(chunk, bytes)
        streamed.stream_to(sink)
    assert_type(client.request_raw("POST", "https://example.com/hooks", body=b"{}"), RawResponse)
    with client.with_streaming_response.request_raw("GET", "https://example.com/file") as download:
        download.stream_to("file.bin", overwrite=True)


async def raw_async(client: AsyncClient) -> None:
    trace = ListPetsRequestCodecs.parameter(location="header", name="X-Trace").from_wire("t")
    pet = GetPetRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    saved = await client.pets.with_raw_response.list_pets(x_trace=trace)
    assert_type(saved, AsyncRawResponse)
    assert_type(await saved.read(), bytes)
    assert_type(await saved.json(), JSONValue)
    await saved.raise_for_status()
    manager = client.pets.with_streaming_response.get_pet(pet_id=pet)
    assert_type(manager, AbstractAsyncContextManager[AsyncRawResponse])
    async with manager as streamed:
        async for chunk in streamed.iter_raw_bytes():
            assert_type(chunk, bytes)
    assert_type(await client.request_raw("GET", "https://example.com"), AsyncRawResponse)
    async with client.with_streaming_response.request_raw("GET", "https://example.com") as download:
        await download.stream_to("file.bin")


def build(context: BodyAttemptContext) -> BodyAttempt:
    raise NotImplementedError(context.call_id)


async def abuild(context: BodyAttemptContext) -> AsyncBodyAttempt:
    raise NotImplementedError(context.attempt_index)


def bodies(client: Client, file: BinaryIO) -> None:
    photo = UploadRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    inputs: tuple[SyncBinaryBody, ...] = (
        b"\x00",
        FileBody(file),
        FileBody(file, ownership="owned"),
        FileBody.from_path("photo.png"),
        StreamBody([b"a", b"b"]),
        StreamBody(iter([b"a"]), ownership="owned"),
        BodyFactory(build, content_length=1, content_type="image/png", fingerprint=b"f"),
    )
    for body in inputs:
        client.pets.photos.upload(pet_id=photo, body=body)
    assert_type(BodyFactory(build).content_type, str | None)
    client.request_raw("PUT", "https://example.com/file", body=FileBody.from_path("photo.png"))


async def bodies_async(client: AsyncClient, file: BinaryIO) -> None:
    photo = UploadRequestCodecs.parameter(location="path", name="petId").from_wire(1)

    async def chunks() -> AsyncIterator[bytes]:
        yield b"a"

    body = AsyncFileBody.from_path("photo.png")
    inputs: tuple[AsyncBinaryBody, ...] = (
        b"\x00",
        AsyncFileBody(file),
        body,
        AsyncStreamBody(chunks()),
        AsyncBodyFactory(abuild),
    )
    for each in inputs:
        await client.pets.photos.upload(pet_id=photo, body=each)
    await client.request_raw("PUT", "https://example.com/file", body=AsyncStreamBody(chunks(), ownership="owned"))
    await body.aclose()
    body.close()


def multipart(client: Client, file: BinaryIO) -> None:
    name: FieldPart[str] = FieldPart("name", "Ada")
    count = FieldPart("count", 3, content_type="text/plain", headers=(("X-Trace", "t"),))
    photo = FilePart("photo", FileBody(file), filename="a.png", content_type="image/png")
    assert_type(photo.content, FileBody)
    parts: MultipartBody[str | int] = MultipartBody((name, count, photo))
    assert_type(parts.parts, tuple[FilePart[SyncBinaryBody] | FieldPart[str | int], ...])
    body = MultipartBody[WireValue]((FieldPart("meta", {"k": 1}), FieldPart("skipped", UNSET), FilePart("f", b"x")))
    client.request_raw("POST", "https://example.com/forms", body=body)


def file_parts(client: Client, file: BinaryIO) -> None:
    note = FieldPart("note", "hello")
    labels = FieldPart("labels", AttachFilesRequestCodecs.part(name="labels").from_wire(["a", "b"]))
    assert_type(labels, FieldPart[ModelValue[list[str]]])
    pet = AttachFilesRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    client.pets.attach_files(pet_id=pet, body=MultipartBody((note, labels, FilePart("file", FileBody(file)))))
    codec = AttachFilesRequestCodecs.part(name="note", media_type="multipart/form-data")
    assert_type(codec, NativeOutboundCodec[str])
    assert_type(AttachFilesRequestCodecs.part(name="file"), NativeOutboundCodec[str] | NativeOutboundCodec[list[str]])
    files = client.pets.read_files(pet_id=ReadFilesRequestCodecs.parameter(location="path", name="petId").from_wire(1))
    assert_type(files, MultipartData[str | bytes])
    for part in files.parts:
        assert_type(part.value, str | bytes)


def multipart_data(data: MultipartData[bytes]) -> None:
    for part in data.parts:
        assert_type(part, DecodedPart[bytes])
        assert_type(part.value, bytes)
        assert_type(part.name, str | None)
        assert_type(part.filename, str | None)
        assert_type(part.content_type, str | None)
        assert_type(part.headers, HeadersView)
    widened: MultipartData[bytes | str] = data
    del widened


def media_selectors(client: Client, file: BinaryIO) -> None:
    photo = UploadRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    sent = UploadRequestCodecs.select_request_media(
        declared_media="application/octet-stream", concrete_media="application/octet-stream"
    )
    assert_type(sent, RequestMedia[SyncBinaryBody, AsyncBinaryBody])
    accepted = UploadRequestCodecs.select_response_media(declared_media="image/*", concrete_media="image/png")
    assert_type(accepted, ResponseMedia[UploadResponse])
    assert_type(accepted.concrete_media, str)
    stored = client.pets.photos.upload(pet_id=photo, body=FileBody(file), media_type=sent, response_media_type=accepted)
    assert_type(stored, UploadResponse)


async def media_selectors_async(client: AsyncClient) -> None:
    photo = UploadRequestCodecs.parameter(location="path", name="petId").from_wire(1)
    sent = UploadRequestCodecs.select_request_media(
        declared_media="application/octet-stream", concrete_media="application/octet-stream"
    )
    await client.pets.photos.upload(pet_id=photo, body=AsyncFileBody.from_path("a.png"), media_type=sent)


async def multipart_async(client: AsyncClient) -> None:
    body = AsyncMultipartBody[WireValue]((FieldPart("a", "x"), FilePart("f", AsyncFileBody.from_path("a.bin"))))
    await client.request_raw("POST", "https://example.com/forms", body=body)


class Adapter:
    """A synchronous transport adapter, structurally."""

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(internal_retry_limit=0, delivery_evidence=False, http_versions=("HTTP/1.1",))

    def send(self, request: PreparedRequest[BodyAttempt], context: AttemptIOContext) -> TransportResponse:
        raise NotImplementedError

    def close(self) -> None:
        pass


class AsyncAdapter:
    """An async transport adapter, structurally."""

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(internal_retry_limit=None, delivery_evidence=True, http_versions=("HTTP/2",))

    async def send(
        self, request: PreparedRequest[AsyncBodyAttempt], context: AttemptIOContext
    ) -> AsyncTransportResponse:
        raise NotImplementedError

    async def aclose(self) -> None:
        pass


def transports() -> None:
    borrowed = Client(transport_adapter=Adapter())
    assert_type(borrowed.with_options(RequestOptions(cleanup_timeout=1.0)), Client)
    owned = OwnedTransportAdapter(Adapter())
    assert_type(owned, OwnedTransportAdapter[Adapter])
    Client(transport_adapter=owned)
    assert_type(
        AsyncClient(transport_adapter=OwnedTransportAdapter(AsyncAdapter())).with_options(RequestOptions()), AsyncClient
    )
