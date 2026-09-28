"""The native transport adapters: HTTPX2 clients sending with redirects and HTTPX2 authentication off."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import httpx2

from .errors import AdapterContractError, DeliveryState, IOPhase, TransportError
from .responses import HeadersView
from .transports import TransportCapabilities

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from .bodies import AsyncBodyAttempt, BodyAttempt
    from .transports import AttemptIOContext, PreparedRequest

CAPABILITIES: Final = TransportCapabilities(
    internal_retry_limit=0, delivery_evidence=False, http_versions=("HTTP/1.1",)
)
_UNKNOWN: Final[tuple[IOPhase, DeliveryState]] = ("unknown", DeliveryState.MAYBE_SENT)
_PHASES: Final[tuple[tuple[type[httpx2.TransportError], IOPhase, DeliveryState], ...]] = (
    (httpx2.ConnectTimeout, "connect", DeliveryState.NOT_SENT),
    (httpx2.ConnectError, "connect", DeliveryState.NOT_SENT),
    (httpx2.PoolTimeout, "pool", DeliveryState.NOT_SENT),
    (httpx2.WriteTimeout, "write", DeliveryState.MAYBE_SENT),
    (httpx2.WriteError, "write", DeliveryState.MAYBE_SENT),
    (httpx2.ReadTimeout, "read", DeliveryState.MAYBE_SENT),
    (httpx2.ReadError, "read", DeliveryState.MAYBE_SENT),
)


def _phase(error: httpx2.TransportError) -> tuple[IOPhase, DeliveryState]:
    for kind, phase, state in _PHASES:
        if isinstance(error, kind):
            return phase, state
    return _UNKNOWN


def transport_error(error: httpx2.TransportError, *, started: bool) -> TransportError:
    """Classify an HTTPX2 failure by phase and by how far the request got."""
    phase, state = _phase(error)
    return TransportError(delivery_state=DeliveryState.RESPONSE_STARTED if started else state, phase=phase, cause=error)


def _timeouts(context: AttemptIOContext) -> dict[str, float | None]:
    timeout = context.timeout
    return {"connect": timeout.connect, "read": timeout.read, "write": timeout.write, "pool": timeout.pool}


def _request(
    prepared: PreparedRequest[BodyAttempt] | PreparedRequest[AsyncBodyAttempt],
    context: AttemptIOContext,
    content: Iterator[bytes] | AsyncIterator[bytes] | None,
) -> httpx2.Request:
    """Build the HTTPX2 request of an attempt, framing a body of known length with its Content-Length.

    Header values go out as UTF-8, as the parameter codecs encode them.
    """
    headers = [(name.encode(), value.encode()) for name, value in prepared.headers]
    if (body := prepared.body) is not None and (length := body.content_length) is not None:
        headers.append((b"Content-Length", str(length).encode()))
    extensions = {"timeout": _timeouts(context)}
    return httpx2.Request(prepared.method, prepared.url, headers=headers, content=content, extensions=extensions)


class Httpx2Response:
    """A streaming HTTPX2 response whose raw body is read at most once."""

    __slots__ = ("_context", "_response", "_timeout", "headers")

    def __init__(self, response: httpx2.Response, context: AttemptIOContext) -> None:
        """Wrap a response whose headers arrived."""
        self._response = response
        self._context = context
        self._timeout = context.timeout
        self.headers = HeadersView(response.headers.multi_items())

    @property
    def status_code(self) -> int:
        """Return the final status code."""
        return self._response.status_code

    def iter_raw_bytes(self) -> Iterator[bytes]:
        """Yield the body as it arrived, classifying a failure while it streams."""
        if self._context.timeout is not self._timeout:
            self._response.request.extensions["timeout"] = _timeouts(self._context)
        try:
            yield from self._response.iter_raw()
        except httpx2.TransportError as error:
            raise transport_error(error, started=True) from None

    def close(self) -> None:
        """Release the response."""
        self._response.close()


class AsyncHttpx2Response:
    """A streaming HTTPX2 async response whose raw body is read at most once."""

    __slots__ = ("_context", "_response", "_timeout", "headers")

    def __init__(self, response: httpx2.Response, context: AttemptIOContext) -> None:
        """Wrap a response whose headers arrived."""
        self._response = response
        self._context = context
        self._timeout = context.timeout
        self.headers = HeadersView(response.headers.multi_items())

    @property
    def status_code(self) -> int:
        """Return the final status code."""
        return self._response.status_code

    async def iter_raw_bytes(self) -> AsyncIterator[bytes]:
        """Yield the body as it arrived, classifying a failure while it streams."""
        if self._context.timeout is not self._timeout:
            self._response.request.extensions["timeout"] = _timeouts(self._context)
        try:
            async for chunk in self._response.aiter_raw():
                yield chunk
        except httpx2.TransportError as error:
            raise transport_error(error, started=True) from None

    async def aclose(self) -> None:
        """Release the response."""
        await self._response.aclose()


class Httpx2Transport:
    """Send through one HTTPX2 client, refusing a response whose body something already read.

    An HTTPX2 event hook that reads the body, or a mock response built from `content=` or `json=`, leaves nothing to
    stream, so its response is an adapter contract failure.
    """

    __slots__ = ("_client",)

    def __init__(self, client: httpx2.Client) -> None:
        """Send through the given HTTPX2 client."""
        self._client = client

    @property
    def capabilities(self) -> TransportCapabilities:
        """Return that HTTPX2 retries nothing itself and reports no delivery evidence."""
        return CAPABILITIES

    def send(self, request: PreparedRequest[BodyAttempt], context: AttemptIOContext) -> Httpx2Response:
        """Send one request and return the streaming response once its headers arrived."""
        try:
            native = _request(request, context, None if request.body is None else request.body.iter_bytes())
            response = self._client.send(native, stream=True, auth=None, follow_redirects=False)
        except httpx2.TransportError as error:
            raise transport_error(error, started=False) from None
        if response.is_stream_consumed:
            response.close()
            raise AdapterContractError(delivery_state=DeliveryState.RESPONSE_STARTED)
        return Httpx2Response(response, context)

    def close(self) -> None:
        """Close the HTTPX2 client."""
        self._client.close()


class AsyncHttpx2Transport:
    """Send through one HTTPX2 async client, refusing a response whose body something already read."""

    __slots__ = ("_client",)

    def __init__(self, client: httpx2.AsyncClient) -> None:
        """Send through the given HTTPX2 async client."""
        self._client = client

    @property
    def capabilities(self) -> TransportCapabilities:
        """Return that HTTPX2 retries nothing itself and reports no delivery evidence."""
        return CAPABILITIES

    async def send(self, request: PreparedRequest[AsyncBodyAttempt], context: AttemptIOContext) -> AsyncHttpx2Response:
        """Send one request and return the streaming response once its headers arrived."""
        try:
            native = _request(request, context, None if request.body is None else request.body.aiter_bytes())
            response = await self._client.send(native, stream=True, auth=None, follow_redirects=False)
        except httpx2.TransportError as error:
            raise transport_error(error, started=False) from None
        if response.is_stream_consumed:
            await response.aclose()
            raise AdapterContractError(delivery_state=DeliveryState.RESPONSE_STARTED)
        return AsyncHttpx2Response(response, context)

    async def aclose(self) -> None:
        """Close the HTTPX2 async client."""
        await self._client.aclose()
