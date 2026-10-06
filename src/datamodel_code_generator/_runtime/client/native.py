"""Native HTTP client construction, request cloning and conservative failure classification."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import httpx2

from .errors import APIConnectionError, APITimeoutError, ConfigurationError, DeliveryState, SDKError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from .errors import IOPhase
    from .options import ResolvedTransportOptions
    from .timing import ResolvedTimeoutOptions


def attempt_timeout(phases: ResolvedTimeoutOptions, remaining: float | None) -> httpx2.Timeout:
    """Clamp each native phase to the remaining total deadline before send."""

    def cap(value: float | None) -> float | None:
        return value if remaining is None else remaining if value is None else min(value, remaining)

    return httpx2.Timeout(
        connect=cap(phases.connect), read=cap(phases.read), write=cap(phases.write), pool=cap(phases.pool)
    )


def delivery(error: BaseException, *, send_started: bool, response_started: bool = False) -> DeliveryState:
    """Classify only by the public send boundary and native exception class."""
    if response_started:
        return DeliveryState.RESPONSE_STARTED
    if not send_started or isinstance(error, (httpx2.ConnectError, httpx2.ConnectTimeout, httpx2.PoolTimeout)):
        return DeliveryState.NOT_SENT
    return DeliveryState.MAYBE_SENT


def native_error(error: Exception, *, send_started: bool, response_started: bool = False) -> SDKError:
    """Convert an ordinary native failure; BaseException interruptions are never intercepted."""
    state = delivery(error, send_started=send_started, response_started=response_started)
    if isinstance(error, SDKError):
        error.delivery_state = state
        return error
    phase: IOPhase = (
        "connect"
        if isinstance(error, (httpx2.ConnectError, httpx2.ConnectTimeout))
        else "pool"
        if isinstance(error, httpx2.PoolTimeout)
        else "read"
        if isinstance(error, (httpx2.ReadError, httpx2.ReadTimeout))
        else "write"
        if isinstance(error, (httpx2.WriteError, httpx2.WriteTimeout))
        else "unknown"
    )
    cls = APITimeoutError if isinstance(error, httpx2.TimeoutException) else APIConnectionError
    return cls(phase=phase, delivery_state=state, cause=error)


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


def cloned(
    request: httpx2.Request, *, url: str | None = None, headers: list[tuple[str, str]] | None = None
) -> httpx2.Request:
    """Clone one native request while retaining its mode-correct stream and fixed timeout."""
    return httpx2.Request(
        request.method,
        request.url if url is None else url,
        headers=request.headers.multi_items() if headers is None else headers,
        stream=request.stream,
        extensions=dict(request.extensions),
    )


def response_bytes(response: httpx2.Response) -> Iterator[bytes]:
    """Read raw stream bytes once, leaving the first native close to the SDK's finally."""
    if response.is_stream_consumed:
        raise httpx2.StreamConsumed
    response.is_stream_consumed = True
    if not isinstance(response.stream, httpx2.SyncByteStream):
        msg = "Expected a synchronous response stream"
        raise TypeError(msg)
    yield from response.stream


async def async_response_bytes(response: httpx2.Response) -> AsyncIterator[bytes]:
    """Read native asynchronous bytes without awaited EOF auto-close or native decompression."""
    if response.is_stream_consumed:
        raise httpx2.StreamConsumed
    response.is_stream_consumed = True
    if not isinstance(response.stream, httpx2.AsyncByteStream):
        msg = "Expected an asynchronous response stream"
        raise TypeError(msg)
    async for chunk in response.stream:
        yield chunk


def native_client(transport: ResolvedTransportOptions) -> httpx2.Client:
    """Create an SDK-owned HTTPX2 client from resolved construction settings, refusing HTTP/2 without its extra."""
    try:
        return httpx2.Client(
            verify=transport.verify if transport.ssl_context is None else transport.ssl_context,
            proxy=transport.proxy,
            trust_env=transport.trust_env,
            http2=transport.http2,
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
            limits=httpx2.Limits(
                max_connections=transport.max_connections,
                max_keepalive_connections=transport.max_keepalive_connections,
                keepalive_expiry=transport.keepalive_expiry,
            ),
        )
    except ImportError as error:
        raise ConfigurationError(field_path=("transport", "http2"), reason="unavailable", cause=error) from None
