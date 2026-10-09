"""Native HTTP client construction and conservative failure classification."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast

import httpx2

from .errors import APIConnectionError, APITimeoutError, ConfigurationError, DecodeError
from .logical import Delivery

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Iterator

    from .options import ResolvedTransportOptions
    from .responses import ResponseInfo
    from .timing import ResolvedTimeoutOptions


def native_timeout(phases: ResolvedTimeoutOptions) -> dict[str, float | None]:
    """Return the per-request timeout extension of an attempt's resolved phases."""
    return {"connect": phases.connect, "read": phases.read, "write": phases.write, "pool": phases.pool}


def delivery(error: BaseException, *, send_started: bool, response_started: bool = False) -> Delivery:
    """Classify how far a failed send got, only by the public send boundary and native exception class."""
    if response_started:
        return Delivery.RESPONSE_STARTED
    if not send_started or isinstance(error, (httpx2.ConnectError, httpx2.ConnectTimeout, httpx2.PoolTimeout)):
        return Delivery.NOT_SENT
    return Delivery.MAYBE_SENT


def native_error(error: Exception) -> APIConnectionError:
    """Convert an ordinary native failure; BaseException interruptions are never intercepted."""
    if isinstance(error, httpx2.TimeoutException):
        return APITimeoutError(reason="phase_timeout", cause=error)
    return APIConnectionError(cause=error)


def transport_retry_reason(
    error: APIConnectionError,
) -> Literal["connect_error", "connect_timeout", "pool_timeout"] | None:
    """Only the native connection trio can be candidates for a transport retry."""
    cause = error.cause
    if isinstance(cause, httpx2.ConnectTimeout):
        return "connect_timeout"
    if isinstance(cause, httpx2.PoolTimeout):
        return "pool_timeout"
    return "connect_error" if isinstance(cause, httpx2.ConnectError) else None


def request_fields(request: httpx2.Request) -> list[tuple[str, str]]:
    """Return a native request's header fields in the case they are sent, decoded as the SDK encoded them."""
    return [(name.decode(), value.decode()) for name, value in request.headers.raw]


def wire_fields(fields: Iterable[tuple[str, str]]) -> list[tuple[bytes, bytes]]:
    """Encode header fields as UTF-8, so non-ASCII values are sent as they are given."""
    return [(name.encode(), value.encode()) for name, value in fields]


def _malformed(error: httpx2.DecodingError, info: ResponseInfo, operation_id: str | None) -> DecodeError:
    return DecodeError(reason="malformed_coding", operation_id=operation_id, info=info, cause=error)


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
    if "content-encoding" not in response.headers:
        yield from response_bytes(response)
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
    if "content-encoding" not in response.headers:
        async for chunk in async_response_bytes(response):
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


def response_bytes(response: httpx2.Response) -> Iterator[bytes]:
    """Read the raw stream bytes, content codings included, once, leaving the native close to the SDK's finally."""
    if response.is_stream_consumed:
        raise httpx2.StreamConsumed
    response.is_stream_consumed = True
    yield from cast("httpx2.SyncByteStream", response.stream)


async def async_response_bytes(response: httpx2.Response) -> AsyncIterator[bytes]:
    """Read the raw asynchronous bytes, content codings included, once, without awaited EOF auto-close."""
    if response.is_stream_consumed:
        raise httpx2.StreamConsumed
    response.is_stream_consumed = True
    async for chunk in cast("httpx2.AsyncByteStream", response.stream):
        yield chunk


def native_client(transport: ResolvedTransportOptions) -> httpx2.Client:
    """Create an SDK-owned HTTPX2 client from resolved construction settings, refusing HTTP/2 without its extra."""
    try:
        return httpx2.Client(
            verify=transport.verify if transport.ssl_context is None else transport.ssl_context,
            proxy=transport.proxy,
            trust_env=transport.trust_env,
            http2=transport.http2,
            timeout=httpx2.Timeout(600.0, connect=5.0),
            limits=httpx2.Limits(
                max_connections=transport.max_connections,
                max_keepalive_connections=transport.max_keepalive_connections,
                keepalive_expiry=transport.keepalive_expiry,
            ),
        )
    except ImportError as error:
        raise ConfigurationError(field_path=("transport", "http2"), reason="unavailable", cause=error) from None


def native_async_client(transport: ResolvedTransportOptions) -> httpx2.AsyncClient:
    """Create an SDK-owned asyncio HTTPX2 client from resolved construction settings."""
    try:
        return httpx2.AsyncClient(
            verify=transport.verify if transport.ssl_context is None else transport.ssl_context,
            proxy=transport.proxy,
            trust_env=transport.trust_env,
            http2=transport.http2,
            timeout=httpx2.Timeout(600.0, connect=5.0),
            limits=httpx2.Limits(
                max_connections=transport.max_connections,
                max_keepalive_connections=transport.max_keepalive_connections,
                keepalive_expiry=transport.keepalive_expiry,
            ),
        )
    except ImportError as error:
        raise ConfigurationError(field_path=("transport", "http2"), reason="unavailable", cause=error) from None
