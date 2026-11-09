"""Native HTTP client construction and conservative failure classification."""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Literal, TypeAlias, cast

import httpx2

from .errors import APIConnectionError, APITimeoutError, ConfigurationError, response_failure
from .options import DEFAULT_TIMEOUT
from .retry import Delivery

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Collection, Iterator

    from .errors import DecodeError
    from .responses import ResponseInfo
    from .timing import ResolvedTimeoutOptions

_UnsentReason: TypeAlias = Literal["connect_error", "connect_timeout", "pool_timeout"]


def native_timeout(phases: ResolvedTimeoutOptions) -> dict[str, float | None]:
    """Return the per-request timeout extension of an attempt's resolved phases."""
    return {"connect": phases.connect, "read": phases.read, "write": phases.write, "pool": phases.pool}


def native_phases(phases: ResolvedTimeoutOptions) -> httpx2.Timeout:
    """Return resolved phases as an HTTPX2 timeout, which a native client or a call's options take."""
    return httpx2.Timeout(**native_timeout(phases))


def _unsent(error: BaseException | None) -> _UnsentReason | None:
    """Return the retry reason of a native failure that leaves its request unsent, or None for any other failure.

    Only a failure to connect, or to take a connection from the pool, proves that nothing was sent.
    """
    if isinstance(error, httpx2.ConnectTimeout):
        return "connect_timeout"
    if isinstance(error, httpx2.PoolTimeout):
        return "pool_timeout"
    return "connect_error" if isinstance(error, httpx2.ConnectError) else None


def delivery(error: BaseException, *, response_started: bool = False) -> Delivery:
    """Classify how far a failed send got, only by the public send boundary and native exception class."""
    if response_started:
        return Delivery.RESPONSE_STARTED
    return Delivery.MAYBE_SENT if _unsent(error) is None else Delivery.NOT_SENT


def native_error(error: Exception) -> APIConnectionError:
    """Convert an ordinary native failure; BaseException interruptions are never intercepted."""
    if isinstance(error, httpx2.TimeoutException):
        return APITimeoutError(reason="phase_timeout", cause=error)
    return APIConnectionError(cause=error)


def transport_retry_reason(error: APIConnectionError) -> _UnsentReason | None:
    """Only the native connection trio can be candidates for a transport retry."""
    return _unsent(error.cause)


def request_fields(request: httpx2.Request) -> list[tuple[str, str]]:
    """Return a native request's header fields in the case they are sent, decoded as the SDK encoded them.

    The SDK writes ASCII only; a byte an Auth or an event hook added reads as its Latin-1 character.
    """
    return [(name.decode("latin-1"), value.decode("latin-1")) for name, value in request.headers.raw]


def wire_fields(fields: Collection[tuple[str, str]]) -> list[tuple[bytes, bytes]]:
    """Encode header fields as ASCII, refusing any other text before it is sent, with the header as the field path.

    The error keeps no value.
    """
    try:
        return [(name.encode("ascii"), value.encode("ascii")) for name, value in fields]
    except UnicodeEncodeError as error:
        unsent = next(name for name, value in fields if error.object in {name, value})
        raise ConfigurationError(field_path=("headers", unsent), reason="invalid_value") from None


def _malformed(error: httpx2.DecodingError, info: ResponseInfo, operation_id: str | None) -> DecodeError:
    return response_failure(info, "malformed_coding", cause=error, operation_id=operation_id)


class _Held(httpx2.SyncByteStream):
    """Raw chunks of a response that HTTPX2 decodes, leaving the response's close to the SDK."""

    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = chunks

    def __iter__(self) -> Iterator[bytes]:
        return self._chunks


class _AsyncHeld(httpx2.AsyncByteStream):
    """Raw asynchronous chunks of a response that HTTPX2 decodes, leaving the response's close to the SDK."""

    def __init__(self, chunks: AsyncIterator[bytes]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._chunks


def decoded_bytes(response: httpx2.Response, info: ResponseInfo, operation_id: str | None) -> Iterator[bytes]:
    """Read the body once with HTTPX2 removing its content codings; a coding that does not decode is malformed.

    The raw chunks pass through a response of their own, so decoding never closes the one the SDK releases.
    """
    if response.is_stream_consumed or "content-encoding" not in response.headers:
        yield from response_bytes(response, decoded=True)
        return
    decoding = httpx2.Response(response.status_code, headers=response.headers, stream=_Held(response_bytes(response)))
    try:
        yield from decoding.iter_bytes()
    except httpx2.DecodingError as error:
        raise _malformed(error, info, operation_id) from None


async def async_decoded_bytes(
    response: httpx2.Response, info: ResponseInfo, operation_id: str | None
) -> AsyncIterator[bytes]:
    """Read the asynchronous body once with HTTPX2 removing its content codings, as `decoded_bytes` does."""
    if response.is_stream_consumed or "content-encoding" not in response.headers:
        async for chunk in async_response_bytes(response, decoded=True):
            yield chunk
        return
    decoding = httpx2.Response(
        response.status_code, headers=response.headers, stream=_AsyncHeld(async_response_bytes(response))
    )
    try:
        async for chunk in decoding.aiter_bytes():
            yield chunk
    except httpx2.DecodingError as error:
        raise _malformed(error, info, operation_id) from None


def _read_content(response: httpx2.Response, *, decoded: bool) -> bytes:
    """Return the body HTTPX2 already read, as `Response(content=...)` and `Response(json=...)` are read on creation.

    HTTPX2 keeps it decoded, so the raw bytes of a read body with a coding other than identity are gone, as are those
    of a stream consumed without being read. Codings are named as HTTPX2 reads them to choose its decoder.
    """
    codings = response.headers.get_list("content-encoding", split_commas=True)
    if decoded or all(coding.strip().lower() == "identity" for coding in codings):
        with suppress(httpx2.ResponseNotRead):
            return response.content
    raise httpx2.StreamConsumed


def response_bytes(response: httpx2.Response, *, decoded: bool = False) -> Iterator[bytes]:
    """Read the raw stream bytes, content codings included, once, leaving the native close to the SDK's finally.

    A body HTTPX2 already read is its content; `decoded` accepts it without its content codings.
    """
    if response.is_stream_consumed:
        yield _read_content(response, decoded=decoded)
        return
    response.is_stream_consumed = True
    yield from cast("httpx2.SyncByteStream", response.stream)


async def async_response_bytes(response: httpx2.Response, *, decoded: bool = False) -> AsyncIterator[bytes]:
    """Read the raw asynchronous bytes, content codings included, once, without awaited EOF auto-close."""
    if response.is_stream_consumed:
        yield _read_content(response, decoded=decoded)
        return
    response.is_stream_consumed = True
    async for chunk in cast("httpx2.AsyncByteStream", response.stream):
        yield chunk


def native_client() -> httpx2.Client:
    """Create an SDK-owned HTTPX2 client: HTTPX2's defaults, with the phase timeouts a client's settings start from."""
    return httpx2.Client(timeout=native_phases(DEFAULT_TIMEOUT))


def native_async_client() -> httpx2.AsyncClient:
    """Create an SDK-owned asyncio HTTPX2 client as `native_client` creates a synchronous one."""
    return httpx2.AsyncClient(timeout=native_phases(DEFAULT_TIMEOUT))
