"""Use a generated client as typed code: calls, media overloads, results, errors, and header accessors."""

from __future__ import annotations

from collections.abc import AsyncIterator, Set
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from pathlib import Path
from ssl import SSLContext, create_default_context
from typing import IO, BinaryIO, Literal

import httpx2
from typing_extensions import assert_type

from pets import AsyncClient, AsyncClientView, Client, ClientView
from pets.auth import OAuthProviderOptions
from pets.bodies import (
    AsyncBinaryBody,
    AsyncMultipartBody,
    DecodedPart,
    FieldPart,
    FilePart,
    MultipartBody,
    MultipartData,
    SyncBinaryBody,
)
from pets.model_codecs import JSONValue
from pets.errors import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthError,
    AuthReason,
    ConfigurationError,
    DecodeError,
    NotFoundError,
    SDKError,
)
from pets.options import (
    UNSET,
    ClientOptions,
    Clock,
    IdempotencyKey,
    RequestOptions,
    RetryOptions,
    TimeoutOptions,
    TransportOptions,
    Unset,
)
from pets.responses import AsyncRawResponse, HeadersView, RawResponse, Response, ResponseInfo
from pets.types.pets import (
    CreatePetResponse,
    ListPetsResponse,
    decode_list_pets_header,
)
from pets_models import (
    FieldPetsGetHeaderXTraceParameter,
    FieldPetsPetIdFilesGetPathPetIdParameter,
    FieldPetsPetIdFilesPostPathPetIdParameter,
    FieldPetsPetIdGetPathPetIdParameter,
    FieldPetsPetIdPhotoPutPathPetIdParameter,
    FieldPetsPostRequest,
    NewPet,
)


def call(
    client: Client,
    trace: FieldPetsGetHeaderXTraceParameter,
    pet: FieldPetsPetIdGetPathPetIdParameter,
    body: NewPet,
    text: FieldPetsPostRequest,
    photo: FieldPetsPetIdPhotoPutPathPetIdParameter,
) -> None:
    assert_type(client.pets.list_pets(x_trace=trace, limit=UNSET), ListPetsResponse)
    response = client.pets.with_response.list_pets(x_trace=trace, options=RequestOptions(max_response_bytes=None))
    assert_type(response, Response[ListPetsResponse])
    decode_list_pets_header(response.info, name="X-Next")
    decode_list_pets_header(response.info, name="X-Rate")
    assert_type(client.pets.create_pet(body=body, media_type="application/json"), CreatePetResponse)
    client.pets.create_pet(body=text, media_type="text/plain")
    client.pets.get_pet(pet_id=pet, response_media_type="text/plain")
    client.pets.photos.upload(pet_id=photo, body=b"\x00")
    try:
        client.pets.list_pets(x_trace=trace)
    except NotFoundError as error:
        assert_type(error.body, object)
        assert_type(error.status_code, int)
        assert_type(error.headers, HeadersView)
        assert_type(error.request_id, str | None)
    except APIStatusError as error:
        assert_type(error.body_bytes, bytes)


async def call_async(client: AsyncClient, trace: FieldPetsGetHeaderXTraceParameter) -> None:
    assert_type(await client.pets.list_pets(x_trace=trace), ListPetsResponse)
    response = await client.pets.with_response.list_pets(x_trace=trace)
    assert_type(response, Response[ListPetsResponse])
    await client.aclose()


def raw(
    client: Client,
    sink: BinaryIO,
    trace: FieldPetsGetHeaderXTraceParameter,
    pet: FieldPetsPetIdGetPathPetIdParameter,
    body: NewPet,
) -> None:
    saved = client.pets.with_raw_response.list_pets(x_trace=trace)
    assert_type(saved, RawResponse)
    assert_type(saved.info, ResponseInfo)
    assert_type(saved.body_bytes, bytes)
    assert_type(saved.read(), bytes)
    assert_type(saved.text(), str)
    assert_type(saved.json(), JSONValue)
    saved.raise_for_status()
    manager = client.pets.with_streaming_response.create_pet(body=body, media_type="application/json")
    assert_type(manager, AbstractContextManager[RawResponse])
    with client.pets.with_streaming_response.get_pet(pet_id=pet, response_media_type="text/plain") as streamed:
        for chunk in streamed.iter_bytes():
            assert_type(chunk, bytes)
        streamed.stream_to(sink)
    assert_type(client.request_raw("POST", "https://example.com/hooks", body=b"{}"), RawResponse)
    with client.with_streaming_response.request_raw("GET", "https://example.com/file") as download:
        download.stream_to("file.bin", overwrite=True)


async def raw_async(
    client: AsyncClient, trace: FieldPetsGetHeaderXTraceParameter, pet: FieldPetsPetIdGetPathPetIdParameter
) -> None:
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



def bodies(
    client: Client, file: BinaryIO, spooled: IO[bytes], photo: FieldPetsPetIdPhotoPutPathPetIdParameter
) -> None:
    inputs: tuple[SyncBinaryBody, ...] = (b"\x00", file, spooled, Path("photo.png"), [b"a", b"b"], iter([b"a"]))
    for body in inputs:
        client.pets.photos.upload(pet_id=photo, body=body)
    client.request_raw("PUT", "https://example.com/file", body=Path("photo.png"))


async def bodies_async(client: AsyncClient, file: BinaryIO, photo: FieldPetsPetIdPhotoPutPathPetIdParameter) -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b"a"

    inputs: tuple[AsyncBinaryBody, ...] = (b"\x00", file, Path("photo.png"), iter([b"a"]), chunks())
    for body in inputs:
        await client.pets.photos.upload(pet_id=photo, body=body)
    await client.request_raw("PUT", "https://example.com/file", body=chunks())


def multipart(client: Client, file: BinaryIO) -> None:
    name: FieldPart[str] = FieldPart("name", "Ada")
    count = FieldPart("count", 3, content_type="text/plain", headers=(("X-Trace", "t"),))
    photo = FilePart("photo", file, filename="a.png", content_type="image/png")
    assert_type(photo.content, BinaryIO)
    parts: MultipartBody[str | int] = MultipartBody((name, count, photo))
    assert_type(parts.parts, tuple[FilePart[SyncBinaryBody] | FieldPart[str | int], ...])
    body = MultipartBody[JSONValue]((FieldPart("meta", {"k": 1}), FieldPart("skipped", UNSET), FilePart("f", b"x")))
    client.request_raw("POST", "https://example.com/forms", body=body)


def file_parts(
    client: Client,
    file: BinaryIO,
    pet: FieldPetsPetIdFilesPostPathPetIdParameter,
    read: FieldPetsPetIdFilesGetPathPetIdParameter,
) -> None:
    note = FieldPart("note", "hello")
    labels = FieldPart("labels", ["a", "b"])
    assert_type(labels, FieldPart[list[str]])
    client.pets.attach_files(pet_id=pet, body=MultipartBody((note, labels, FilePart("file", file))))
    files = client.pets.read_files(pet_id=read)
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


async def multipart_async(client: AsyncClient) -> None:
    body = AsyncMultipartBody[JSONValue]((FieldPart("a", "x"), FilePart("f", Path("a.bin"))))
    await client.request_raw("POST", "https://example.com/forms", body=body)


def transports() -> None:
    with httpx2.Client() as native:
        borrowed = Client(http_client=native)
        assert_type(borrowed.with_options(RequestOptions(total_timeout=1.0)), ClientView)
        borrowed.close()
    assert_type(AsyncClient(http_client=httpx2.AsyncClient()).with_options(RequestOptions()), AsyncClientView)


def trace(request: httpx2.Request) -> None:
    del request


def inspect(response: httpx2.Response) -> None:
    del response


async def atrace(request: httpx2.Request) -> None:
    del request


def event_hooks(pet: FieldPetsPetIdGetPathPetIdParameter) -> None:
    with httpx2.Client(event_hooks={"request": [trace], "response": [inspect]}) as native:
        try:
            Client(http_client=native).pets.get_pet(pet_id=pet)
        except SDKError as error:
            assert_type(error.reason, str | None)


async def async_event_hooks(pet: FieldPetsPetIdGetPathPetIdParameter) -> None:
    async with httpx2.AsyncClient(event_hooks={"request": [atrace]}) as native:
        await AsyncClient(http_client=native).pets.get_pet(pet_id=pet)


def timing_options(client: Client) -> None:
    clock = Clock(monotonic=lambda: 0.0, random=lambda: 0.5)
    assert_type(OAuthProviderOptions(clock=clock).clock, Clock)
    phases = TimeoutOptions(connect=1, read=None, write=UNSET, pool=0)
    assert_type(phases.read, float | Unset | None)
    configured = ClientOptions(
        timeout=phases,
        total_timeout=None,
        clock=clock,
    )
    assert_type(configured.clock, Clock | Unset)
    assert_type(configured.timeout, TimeoutOptions | Unset | None)
    assert_type(configured.total_timeout, float | Unset | None)
    Client(options=configured)
    assert_type(client.with_options(RequestOptions(timeout=None, total_timeout=0)), ClientView)


def read_with_budget(client: Client, url: str) -> bytes:
    options = RequestOptions(total_timeout=10, timeout=TimeoutOptions(read=3))
    view = client.with_options(options)
    response = view.request_raw("GET", url, options=RequestOptions(timeout=TimeoutOptions(connect=2)))
    return response.read()


class SteppedClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def instant_retries(url: str) -> Client:
    stepped = SteppedClock()
    clock = Clock(monotonic=stepped, random=lambda: 0.5)
    return Client(options=ClientOptions(base_url=url, retry=RetryOptions(), clock=clock))


def download(client: Client, url: str, destination: BinaryIO) -> None:
    options = RequestOptions(total_timeout=10, timeout=TimeoutOptions(read=60))
    with client.with_streaming_response.request_raw("GET", url, options=options) as response:
        response.stream_to(destination)


def error_measurements(error: SDKError, info: ResponseInfo) -> None:
    assert_type(error.attempt_count, int)
    assert_type(error.elapsed, float)
    assert_type(error.request_id, str | None)
    assert_type(error.operation_id, str | None)
    assert_type(info.attempt_count, int)
    assert_type(info.elapsed, float)
    deadline = APITimeoutError(reason="deadline_exceeded")
    connection: APIConnectionError = deadline
    assert_type(connection.reason, str | None)
    assert_type(connection.cause, BaseException | None)
    auth = AuthError(reason="oauth_error", status_code=400, oauth_error="invalid_grant")
    assert_type(auth.reason, AuthReason)
    assert_type(auth.status_code, int | None)
    decoded = DecodeError(reason="unencodable", direction="request", location=("body", 0))
    assert_type(decoded.location, tuple[str | int, ...])
    redirect = ConfigurationError(field_path=("redirects",), reason="redirect_refused", info=info)
    assert_type(redirect.reason, str)
    assert_type(redirect.info, ResponseInfo | None)
    status = NotFoundError(info=info, body=b"", body_bytes=b"")
    assert_type(status.status_code, int)
    assert_type(status.body, object)


def retry_options(client: Client) -> None:
    retry = RetryOptions(
        max_retries=3,
        initial_delay=0.25,
        max_delay=2,
        jitter="none",
        statuses={429, 503},
        max_retry_after=None,
        respect_retry_after=True,
        retry_after_ms_header=None,
        should_retry_header=UNSET,
        retry_on_pool_timeout=False,
    )
    assert_type(retry.statuses, Set[int] | Unset)
    assert_type(retry.jitter, Literal["full", "none"] | Unset)
    assert_type(retry.max_retry_after, float | Unset | None)
    client.with_options(RequestOptions(retry=retry, follow_redirects=True))
    follows = ClientOptions(retry=RetryOptions(max_retries=0), follow_redirects=False)
    Client(options=follows)
    assert_type(follows.follow_redirects, bool | Unset)


def fetch_with_retries(client: Client, url: str) -> bytes:
    options = RequestOptions(
        retry=RetryOptions(max_retries=2, max_retry_after=20),
        follow_redirects=True,
        total_timeout=30,
    )
    return client.request_raw("GET", url, options=options).read()


def keyed_view(client: Client, value: str) -> ClientView:
    key = IdempotencyKey(value)
    return client.with_options(RequestOptions(idempotency_key=key))


def upload_file(client: Client, url: str, path: Path) -> bytes:
    response = client.request_raw(
        "PUT", url, body=path, options=RequestOptions(retry=RetryOptions(max_retries=2))
    )
    return response.read()


def configured_client(ca_file: str) -> Client:
    context = create_default_context(cafile=ca_file)
    transport = TransportOptions(ssl_context=context, max_connections=50, max_keepalive_connections=10)
    return Client(options=ClientOptions(transport=transport))


def idempotency_input(client: Client) -> None:
    key = IdempotencyKey("stored-key")
    assert_type(key.value, str)
    assert_type(IdempotencyKey.new(), IdempotencyKey)
    client.with_options(RequestOptions(idempotency_key=key))
    Client(options=ClientOptions(idempotency_key=IdempotencyKey.new()))
    client.with_options(RequestOptions(idempotency_key=None))


def transport_options(context: SSLContext) -> None:
    transport = TransportOptions(
        ssl_context=context,
        proxy="http://proxy.example.com:8080",
        trust_env=False,
        http2=False,
        max_connections=50,
        max_keepalive_connections=10,
        keepalive_expiry=5,
    )
    assert_type(transport.verify, bool | Unset)
    assert_type(transport.ssl_context, SSLContext | None)
    Client(options=ClientOptions(transport=transport))
    AsyncClient(options=ClientOptions(transport=TransportOptions(verify=True)))
