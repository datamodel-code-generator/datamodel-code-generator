"""Native HTTP client construction, request cloning and conservative failure classification."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal, cast

import httpx2

from .errors import APIConnectionError, APITimeoutError, ConfigurationError, DeliveryState, SDKError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Iterator

    from .errors import IOPhase
    from .options import ResolvedTransportOptions
    from .timing import ResolvedTimeoutOptions

_PHASES: Final[tuple[tuple[tuple[type[httpx2.TransportError], ...], IOPhase], ...]] = (
    ((httpx2.ConnectError, httpx2.ConnectTimeout), "connect"),
    ((httpx2.PoolTimeout,), "pool"),
    ((httpx2.ReadError, httpx2.ReadTimeout, httpx2.RemoteProtocolError), "read"),
    ((httpx2.WriteError, httpx2.WriteTimeout), "write"),
)


def native_timeout(phases: ResolvedTimeoutOptions) -> dict[str, float | None]:
    """Return the per-request timeout extension of an attempt's resolved phases."""
    return {"connect": phases.connect, "read": phases.read, "write": phases.write, "pool": phases.pool}


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
    phase = _phase(error)
    if isinstance(error, httpx2.TimeoutException):
        return APITimeoutError(
            phase=phase,
            reason="phase_timeout",
            effective_timeout=_expired_cap(error, phase),
            delivery_state=state,
            cause=error,
        )
    return APIConnectionError(phase=phase, delivery_state=state, cause=error)


def _phase(error: Exception) -> IOPhase:
    for kinds, phase in _PHASES:
        if isinstance(error, kinds):
            return phase
    return "unknown"


def _expired_cap(error: httpx2.TimeoutException, phase: IOPhase) -> float | None:
    """Return the phase timeout the failed request carried, when the native error kept its request."""
    try:
        caps: object = error.request.extensions.get("timeout")
    except RuntimeError:
        return None
    cap = cast("dict[str, object]", caps).get(phase) if isinstance(caps, dict) else None
    return float(cap) if isinstance(cap, (int, float)) else None


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


def cloned(
    request: httpx2.Request, *, url: str | None = None, headers: Iterable[tuple[str, str]] | None = None
) -> httpx2.Request:
    """Clone one native request while retaining its mode-correct stream and fixed timeout."""
    return httpx2.Request(
        request.method,
        request.url if url is None else url,
        headers=request.headers.raw if headers is None else wire_fields(headers),
        stream=request.stream,
        extensions=dict(request.extensions),
    )


def response_bytes(response: httpx2.Response) -> Iterator[bytes]:
    """Read raw stream bytes once, leaving the first native close to the SDK's finally."""
    if response.is_stream_consumed:
        raise httpx2.StreamConsumed
    response.is_stream_consumed = True
    yield from cast("httpx2.SyncByteStream", response.stream)


async def async_response_bytes(response: httpx2.Response) -> AsyncIterator[bytes]:
    """Read native asynchronous bytes without awaited EOF auto-close or native decompression."""
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
