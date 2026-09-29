"""HTTPX2 adapters with public trace evidence for resource delivery and native failure provenance."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Final, NoReturn

import httpcore2
import httpx2
from typing_extensions import TypeIs, TypeVar

from .errors import (
    AdapterContractError,
    AdapterExecutionError,
    CleanupError,
    ConfigurationError,
    DeliveryState,
    IOPhase,
    PhaseTimeoutError,
    RedirectPolicyError,
    SDKError,
    TransportError,
)
from .evidence import cause_graph, connect_failure, transient_connect
from .responses import HeadersView
from .transports import PreparedRequest, TransportCapabilities, attempt_trace

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from .bodies import AsyncBodyAttempt, BodyAttempt
    from .hooks import RetryReason
    from .options import ResolvedTransportOptions
    from .transports import AttemptIOContext, AttemptTrace

AttemptT = TypeVar("AttemptT", bound="BodyAttempt | AsyncBodyAttempt")

_BORROWED: Final = TransportCapabilities(internal_retry_limit=None, delivery_evidence=False, http_versions=())
_NATIVE: Final = TransportCapabilities(internal_retry_limit=0, delivery_evidence=True, http_versions=("HTTP/1.1",))
_NATIVE_H2: Final = TransportCapabilities(
    internal_retry_limit=0, delivery_evidence=True, http_versions=("HTTP/1.1", "HTTP/2")
)
_PHASES: Final[tuple[tuple[type[httpx2.TransportError], IOPhase, RetryReason], ...]] = (
    (httpx2.ConnectTimeout, "connect", "connect_timeout"),
    (httpx2.PoolTimeout, "pool", "pool_timeout"),
    (httpx2.WriteTimeout, "write", "write_timeout"),
    (httpx2.WriteError, "write", "write_error"),
    (httpx2.ReadTimeout, "read", "read_timeout"),
    (httpx2.ReadError, "read", "read_error"),
)
_TIMEOUT_REASONS: Final[dict[str, RetryReason]] = {
    "connect": "connect_timeout",
    "pool": "pool_timeout",
    "read": "read_timeout",
    "write": "write_timeout",
}
_EXCLUDED: Final = (
    httpx2.InvalidURL,
    httpx2.UnsupportedProtocol,
    httpx2.LocalProtocolError,
    httpx2.ProxyError,
    httpx2.DecodingError,
    httpx2.SSEError,
    httpx2.CloseError,
)
_CORE: Final = (
    httpcore2.ConnectionNotAvailable,
    httpcore2.ProxyError,
    httpcore2.UnsupportedProtocol,
    httpcore2.ProtocolError,
    httpcore2.TimeoutException,
    httpcore2.NetworkError,
)
_CONNECT: Final = frozenset({
    "connection.connect_tcp",
    "connection.connect_unix_socket",
    "connection.start_tls",
    "proxy.start_tls",
    "socks.connect_tcp",
    "socks.start_tls",
})
_REDIRECT: Final = frozenset({301, 302, 303, 307, 308})
_PAIR_SIZE: Final = 2
_HTTP11_HEAD_SIZE: Final = 4
_HTTP2_HEAD_SIZE: Final = 2
_ZERO_LENGTH_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "QUERY"})


def _is_list(value: object) -> TypeIs[list[object]]:
    return isinstance(value, list)


def _is_tuple(value: object) -> TypeIs[tuple[object, ...]]:
    return isinstance(value, tuple)


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _native_phase(error: httpx2.HTTPError) -> IOPhase:
    for kind, phase, _ in _PHASES:
        if isinstance(error, kind):
            return phase
    return "unknown"


def transport_retry_reason(error: TransportError, trace: AttemptTrace) -> RetryReason | None:
    """Recognize classified I/O from native and explicit adapters without granting operation safety."""
    cause = error.cause
    if isinstance(cause, _EXCLUDED):
        return None
    if isinstance(error, PhaseTimeoutError):
        return _TIMEOUT_REASONS.get(error.phase)
    if isinstance(cause, httpx2.ConnectError):
        return "connect_error" if transient_connect(cause_graph(cause), trace.connect_failure) else None
    if isinstance(cause, httpx2.RemoteProtocolError):
        graph = cause_graph(cause)
        return (
            "remote_protocol"
            if graph is not None and any(isinstance(node, httpcore2.RemoteProtocolError) for node in graph.nodes)
            else None
        )
    if isinstance(cause, httpx2.HTTPError):
        return next((reason for kind, _, reason in _PHASES if isinstance(cause, kind)), None)
    reason: RetryReason | None
    if error.phase == "connect":
        reason = "connect_error" if cause is not None and transient_connect(cause_graph(cause)) else None
    elif error.phase == "read":
        reason = "read_error"
    else:
        reason = "write_error" if error.phase == "write" else None
    return reason


def _delivery(trace: AttemptTrace) -> DeliveryState:
    if trace.response_started:
        return DeliveryState.RESPONSE_STARTED
    return DeliveryState.NOT_SENT if trace.proven_not_sent else DeliveryState.MAYBE_SENT


def _processing_error(error: httpx2.RemoteProtocolError | httpx2.InvalidURL, trace: AttemptTrace) -> SDKError:
    graph = cause_graph(error)
    if graph is not None:
        if any(isinstance(node, httpcore2.RemoteProtocolError) for node in graph.nodes):
            return TransportError(delivery_state=_delivery(trace), phase="read", cause=error)
        head = trace.head
        if (
            head is not None
            and head.status_code in _REDIRECT
            and "location" in head.headers
            and not any(isinstance(node, _CORE) for node in graph.nodes)
        ):
            return RedirectPolicyError(delivery_state=DeliveryState.RESPONSE_STARTED, cause=error)
    return AdapterExecutionError(delivery_state=_delivery(trace), cause=error)


def transport_error(
    error: httpx2.HTTPError | httpx2.InvalidURL,
    context: AttemptIOContext,
    *,
    trusted_default: bool = False,
) -> SDKError:
    """Classify a native failure using this invocation's actual trace and constructor provenance."""
    trace = attempt_trace(context)
    if trace.broken:
        return AdapterContractError(delivery_state=_delivery(trace), cause=error)
    if isinstance(error, (httpx2.RemoteProtocolError, httpx2.InvalidURL)):
        return _processing_error(error, trace)
    if isinstance(error, httpx2.CloseError):
        return CleanupError(cause=error)
    if isinstance(error, httpx2.ProxyError):
        return TransportError(delivery_state=_delivery(trace), cause=error)
    phase: IOPhase
    if isinstance(error, httpx2.ConnectError):
        phase = "connect"
        unsent = trace.phase == "connect"
    else:
        phase = _native_phase(error)
        unsent = isinstance(error, (httpx2.ConnectTimeout, httpx2.PoolTimeout))
    trace.proven_not_sent = (
        trusted_default and unsent and not (trace.headers_started or trace.response_started or trace.wire_sent)
    )
    if phase == "unknown":
        return AdapterExecutionError(delivery_state=_delivery(trace), cause=error)
    return TransportError(delivery_state=_delivery(trace), phase=phase, cause=error)


def _timeouts(context: AttemptIOContext) -> dict[str, float | None]:
    timeout = context.timeout
    return {"connect": timeout.connect, "read": timeout.read, "write": timeout.write, "pool": timeout.pool}


def _headers(value: object) -> HeadersView | None:
    if not _is_list(value):
        return None
    pairs: list[tuple[bytes, bytes]] = []
    for pair in value:
        if not _is_tuple(pair) or len(pair) != _PAIR_SIZE:
            return None
        name, item = pair
        if not isinstance(name, bytes) or not isinstance(item, bytes):
            return None
        pairs.append((name, item))
    return HeadersView(httpx2.Headers(pairs).multi_items())


class _NativeTrace:
    """Translate checked public native events, excluding the proxy's CONNECT preparation."""

    __slots__ = ("_method", "_receiving", "_targets", "_trace", "_tunnel")

    def __init__(self, request: httpx2.Request, context: AttemptIOContext) -> None:
        self._trace = attempt_trace(context)
        self._method = request.method.encode("ascii")
        url = httpcore2.URL(
            scheme=request.url.raw_scheme,
            host=request.url.raw_host,
            port=request.url.port,
            target=request.url.raw_path,
        )
        self._targets = frozenset((url.target, bytes(url)))
        self._tunnel = url.host + b":" + str(url.origin.port).encode("ascii")
        self._receiving: bool | None = None

    def _broken(self) -> NoReturn:
        trace = self._trace
        trace.broken = True
        trace.proven_not_sent = False
        raise AdapterContractError(delivery_state=_delivery(trace))

    def _resource(self, info: Mapping[object, object]) -> bool:
        request = info.get("request")
        if not isinstance(request, httpcore2.Request):
            self._broken()
        return self._request_fields(request.method, request.url)

    def _request_fields(self, method: object, url: object) -> bool:
        if not isinstance(method, bytes) or not isinstance(url, httpcore2.URL):
            self._broken()
        return self._request_target(method, url.target)

    def _request_target(self, method: bytes, target: object) -> bool:
        if not isinstance(target, bytes):
            self._broken()
        if method == self._method and target in self._targets:
            return True
        if method == b"CONNECT" and target == self._tunnel:
            return False
        return self._broken()

    def _head(self, protocol: str, value: object) -> None:
        if not _is_tuple(value):
            self._broken()
        if (
            protocol == "http11"
            and len(value) == _HTTP11_HEAD_SIZE
            and isinstance(value[0], bytes)
            and isinstance(value[2], bytes)
        ):
            version, status, headers = value[0].decode("ascii", errors="replace"), value[1], _headers(value[3])
        elif protocol == "http2" and len(value) == _HTTP2_HEAD_SIZE:
            version, status, headers = "HTTP/2", value[0], _headers(value[1])
        else:
            self._broken()
        self._trace.response_headers_received(http_version=version, status_code=status, headers=headers)
        if self._trace.broken:
            self._broken()

    def _connect(self, state: str, info: Mapping[object, object]) -> None:
        trace = self._trace
        if state == "started":
            trace.phase_started("connect")
        elif state == "failed":
            error = info.get("exception")
            if not isinstance(error, BaseException):
                self._broken()
            elif isinstance(error, httpcore2.ConnectError):
                trace.connect_failure = connect_failure(error)

    def __call__(self, name: object, info: object) -> None:
        if not isinstance(name, str) or not _is_mapping(info):
            self._broken()
        event, _, state = name.rpartition(".")
        if event in _CONNECT:
            self._connect(state, info)
            return
        trace = self._trace
        protocol, _, operation = event.partition(".")
        if protocol not in {"http11", "http2"}:
            return
        if state == "started" and operation in {
            "send_request_headers",
            "send_request_body",
            "receive_response_headers",
            "receive_response_body",
        }:
            resource = self._resource(info)
            if operation == "receive_response_headers":
                self._receiving = resource
            if resource:
                trace.phase_started("write" if operation.startswith("send_") else "read")
                if operation == "send_request_headers":
                    trace.request_headers_started()
        elif state == "complete" and operation == "receive_response_headers":
            if self._receiving is None:
                self._broken()
            elif self._receiving:
                self._head(protocol, info.get("return_value"))
            self._receiving = None

    async def async_trace(self, name: object, info: object) -> None:
        """Use the awaitable callback required by native async trace dispatch."""
        self(name, info)


def _body_framing(body: BodyAttempt | AsyncBodyAttempt) -> tuple[str, str]:
    """Describe one opened body's framing from its cached length in either execution mode."""
    length = body.content_length
    return ("Transfer-Encoding", "chunked") if length is None else ("Content-Length", str(length))


def finalize_unsigned(prepared: PreparedRequest[AttemptT]) -> PreparedRequest[AttemptT]:
    """Finalize canonical resource headers before signing, retaining the already opened body unchanged."""
    url = httpx2.URL(prepared.url)
    headers = [("Host", url.netloc.decode("ascii"))]
    body = prepared.body
    if body is None and prepared.method in _ZERO_LENGTH_METHODS:
        headers.append(("Content-Length", "0"))
    headers.extend(prepared.headers)
    if body is not None:
        headers.append(_body_framing(body))
    return PreparedRequest(method=prepared.method, url=str(url), headers=HeadersView(headers), body=prepared.body)


def _request(
    prepared: PreparedRequest[BodyAttempt] | PreparedRequest[AsyncBodyAttempt],
    context: AttemptIOContext,
    content: Iterator[bytes] | AsyncIterator[bytes] | None,
    *,
    asynchronous: bool = False,
) -> httpx2.Request:
    """Build one request with live phase caps and a public, mode-correct trace callback."""
    headers = [(name.encode(), value.encode()) for name, value in prepared.headers]
    if (
        (body := prepared.body) is not None
        and not any(name.lower() in {b"content-length", b"transfer-encoding"} for name, _ in headers)
        and (length := body.content_length) is not None
    ):
        headers.append((b"Content-Length", str(length).encode()))
    request = httpx2.Request(
        prepared.method, prepared.url, headers=headers, content=content, extensions={"timeout": _timeouts(context)}
    )
    trace = _NativeTrace(request, context)
    request.extensions["trace"] = trace.async_trace if asynchronous else trace
    return request


def _conforming(response: httpx2.Response, context: AttemptIOContext, *, trusted_default: bool) -> bool:
    trace = attempt_trace(context)
    return (
        not response.is_stream_consumed
        and not trace.broken
        and (not trusted_default or (trace.headers_started and trace.head is not None))
    )


def _received_headers(response: httpx2.Response, context: AttemptIOContext, *, trusted_default: bool) -> HeadersView:
    trace = attempt_trace(context)
    if trusted_default:
        assert trace.head is not None
        return trace.head.headers
    headers = HeadersView(response.headers.multi_items())
    if trace.head is None:
        trace.response_headers_received(
            http_version=response.http_version, status_code=response.status_code, headers=headers
        )
    return headers


class Httpx2Response:
    """A streaming HTTPX2 response whose raw body is read at most once."""

    __slots__ = ("_context", "_response", "_timeout", "headers")

    def __init__(self, response: httpx2.Response, context: AttemptIOContext, *, trusted_default: bool) -> None:
        """Wrap a response whose headers arrived."""
        self._response = response
        self._context = context
        self._timeout = context.timeout
        self.headers = _received_headers(response, context, trusted_default=trusted_default)

    @property
    def status_code(self) -> int:
        """Return the final status code."""
        return self._response.status_code

    def iter_raw_bytes(self) -> Iterator[bytes]:
        """Yield raw bytes, installing the handed-off stream timeout before its first native read."""
        if self._context.timeout is not self._timeout:
            self._response.request.extensions["timeout"] = _timeouts(self._context)
        try:
            yield from self._response.iter_raw()
        except (httpx2.HTTPError, httpx2.InvalidURL) as error:
            raise transport_error(error, self._context) from None

    def close(self) -> None:
        """Release the response."""
        self._response.close()


class AsyncHttpx2Response:
    """A streaming HTTPX2 async response whose raw body is read at most once."""

    __slots__ = ("_context", "_response", "_timeout", "headers")

    def __init__(self, response: httpx2.Response, context: AttemptIOContext, *, trusted_default: bool) -> None:
        """Wrap a response whose headers arrived."""
        self._response = response
        self._context = context
        self._timeout = context.timeout
        self.headers = _received_headers(response, context, trusted_default=trusted_default)

    @property
    def status_code(self) -> int:
        """Return the final status code."""
        return self._response.status_code

    @property
    def closed(self) -> bool:
        """Return whether HTTPX2 already released the response, as it does once the body is read to its end."""
        return self._response.is_closed

    async def iter_raw_bytes(self) -> AsyncIterator[bytes]:
        """Yield raw bytes with the stream's live timeout and preserved delivery evidence."""
        if self._context.timeout is not self._timeout:
            self._response.request.extensions["timeout"] = _timeouts(self._context)
        try:
            async for chunk in self._response.aiter_raw():
                yield chunk
        except (httpx2.HTTPError, httpx2.InvalidURL) as error:
            raise transport_error(error, self._context) from None

    async def aclose(self) -> None:
        """Release the response."""
        await self._response.aclose()


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
        raise ConfigurationError(field_path=("transport", "http2"), condition="unavailable", cause=error) from None


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
        raise ConfigurationError(field_path=("transport", "http2"), condition="unavailable", cause=error) from None


class Httpx2Transport:
    """Send through one native client, preserving whether the SDK constructed its default transport."""

    __slots__ = ("_capabilities", "_client", "_trusted_default")

    def __init__(self, client: httpx2.Client, *, trusted_default: bool = False, http2: bool = False) -> None:
        """Wrap a client; ownership transfer alone never establishes internal retry or delivery guarantees."""
        self._client = client
        self._trusted_default = trusted_default
        self._capabilities = (_NATIVE_H2 if http2 else _NATIVE) if trusted_default else _BORROWED

    @property
    def capabilities(self) -> TransportCapabilities:
        """Return only the guarantees established by native construction provenance."""
        return self._capabilities

    def send(self, request: PreparedRequest[BodyAttempt], context: AttemptIOContext) -> Httpx2Response:
        """Send one invocation and return an unread, conforming resource response."""
        try:
            native = _request(request, context, None if request.body is None else request.body.iter_bytes())
            response = self._client.send(native, stream=True, auth=None, follow_redirects=False)
        except (httpx2.HTTPError, httpx2.InvalidURL) as error:
            raise transport_error(error, context, trusted_default=self._trusted_default) from None
        if not _conforming(response, context, trusted_default=self._trusted_default):
            failure = AdapterContractError(delivery_state=DeliveryState.RESPONSE_STARTED)
            try:
                response.close()
            except Exception as error:  # noqa: BLE001
                failure.secondary_errors = (error,)
            raise failure from None
        return Httpx2Response(response, context, trusted_default=self._trusted_default)

    def close(self) -> None:
        """Close the HTTPX2 client."""
        self._client.close()


class AsyncHttpx2Transport:
    """Send through one native async client with the same evidence contracts as synchronous sends."""

    __slots__ = ("_capabilities", "_client", "_trusted_default")

    def __init__(self, client: httpx2.AsyncClient, *, trusted_default: bool = False, http2: bool = False) -> None:
        """Wrap a client, keeping injected native clients' internal retry limits unknown."""
        self._client = client
        self._trusted_default = trusted_default
        self._capabilities = (_NATIVE_H2 if http2 else _NATIVE) if trusted_default else _BORROWED

    @property
    def capabilities(self) -> TransportCapabilities:
        """Return only the guarantees established by native construction provenance."""
        return self._capabilities

    async def send(self, request: PreparedRequest[AsyncBodyAttempt], context: AttemptIOContext) -> AsyncHttpx2Response:
        """Send one invocation and return an unread, conforming resource response."""
        try:
            native = _request(
                request, context, None if request.body is None else request.body.aiter_bytes(), asynchronous=True
            )
            response = await self._client.send(native, stream=True, auth=None, follow_redirects=False)
        except (httpx2.HTTPError, httpx2.InvalidURL) as error:
            raise transport_error(error, context, trusted_default=self._trusted_default) from None
        if not _conforming(response, context, trusted_default=self._trusted_default):
            failure = AdapterContractError(delivery_state=DeliveryState.RESPONSE_STARTED)
            try:
                await response.aclose()
            except Exception as error:  # noqa: BLE001
                failure.secondary_errors = (error,)
            raise failure from None
        return AsyncHttpx2Response(response, context, trusted_default=self._trusted_default)

    async def aclose(self) -> None:
        """Close the HTTPX2 client."""
        await self._client.aclose()
