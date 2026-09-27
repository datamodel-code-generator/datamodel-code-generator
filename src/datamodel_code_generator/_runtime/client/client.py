"""The shared core of generated clients: build each call's request, send it through a transport adapter, and decode it.

A client or a view of it runs calls while OPEN; closing stops new calls, stops the active ones at their next step,
waits up to the cleanup timeout for them, and closes the transport the client owns.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from contextlib import suppress
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Final, Generic, Literal, TypeVar
from uuid import uuid4

import httpx2
from typing_extensions import Self, TypeIs

from ..model_codecs.parameters import FragmentContribution, QueryStringContribution, encode_parameter
from ..model_codecs.unset import UNSET, Unset
from .bodies import EncodedAttempt
from .coding import ContentDecoder
from .errors import (
    AdapterContractError,
    AdapterExecutionError,
    CleanupError,
    ConfigurationError,
    DeliveryState,
    ProtocolError,
    RequestEncodingError,
    ResponseTooLargeError,
    SDKError,
    TransportError,
    UnsupportedAsyncBackendError,
)
from .lifecycle import Scope
from .native import AsyncHttpx2Transport, Httpx2Transport
from .operations import DATA_ERRORS, normalized
from .options import ClientOptions, RequestOptions, ServerSelection, checked_base_url
from .responses import HeadersView, Response, ResponseInfo
from .transports import (
    AttemptIOContext,
    AttemptTrace,
    OwnedTransportAdapter,
    PreparedRequest,
    ResolvedTimeoutOptions,
    TransportCapabilities,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterable, AsyncIterator, Iterable, Iterator

    from ..model_codecs.parameters import ParameterFragment
    from .bodies import AsyncBodyAttempt, BodyAttempt
    from .operations import OperationPlan, ResponseDecoder, ServerPlan
    from .transports import AsyncTransportAdapter, AsyncTransportResponse, TransportAdapter, TransportResponse

T = TypeVar("T")
AdapterT = TypeVar("AdapterT")

MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024
MAX_ERROR_BODY_BYTES: Final = 64 * 1024
CLEANUP_TIMEOUT: Final = 5.0
TIMEOUT: Final = ResolvedTimeoutOptions(connect=5.0, read=30.0, write=30.0, pool=5.0)
_ACCEPT_ENCODING: Final = ("Accept-Encoding", "gzip, deflate")
_PLACEHOLDER: Final = re.compile(r"\{([^{}]*)\}")
_OWNERSHIPS: Final = frozenset({"borrowed", "owned"})
_MIN_STATUS: Final = 200
_MAX_STATUS: Final = 599


@dataclass(frozen=True, slots=True, kw_only=True)
class ClientDefaults:
    """The generated defaults of one client package."""

    user_agent: str | None = None


@dataclass(frozen=True, slots=True)
class _Settings:
    base_url: str | None
    server: ServerSelection
    max_response_bytes: int | None
    max_error_body_bytes: int
    cleanup_timeout: float


_DEFAULT_SERVER: Final = ServerSelection()
_DEFAULT_SETTINGS: Final = _Settings(None, _DEFAULT_SERVER, MAX_RESPONSE_BYTES, MAX_ERROR_BODY_BYTES, CLEANUP_TIMEOUT)


def _layered(settings: _Settings, layer: ClientOptions | RequestOptions) -> _Settings:
    """Return the settings with one options layer applied: its set fields replace, UNSET ones inherit."""
    base_url, server = settings.base_url, settings.server
    if not isinstance(layer.base_url, Unset):
        base_url, server = layer.base_url.rstrip("/"), _DEFAULT_SERVER
    elif not isinstance(layer.server, Unset):
        base_url, server = None, layer.server
    return _Settings(
        base_url,
        server,
        settings.max_response_bytes if isinstance(layer.max_response_bytes, Unset) else layer.max_response_bytes,
        settings.max_error_body_bytes if isinstance(layer.max_error_body_bytes, Unset) else layer.max_error_body_bytes,
        settings.cleanup_timeout if isinstance(layer.cleanup_timeout, Unset) else layer.cleanup_timeout,
    )


def _client_settings(options: object) -> _Settings:
    match options:
        case None:
            return _DEFAULT_SETTINGS
        case ClientOptions():
            return _layered(_DEFAULT_SETTINGS, options)
        case _:
            pass
    raise ConfigurationError(field_path=("options",), condition="invalid_type")


def _server_url(operation: OperationPlan[object, object], selection: ServerSelection) -> str:
    operation_id = operation.operation_id
    if selection.index >= len(operation.servers):
        raise ConfigurationError(field_path=("server", "index"), condition="out_of_range", operation_id=operation_id)
    server = operation.servers[selection.index]
    declared = {variable.name: variable for variable in server.variables}
    if not selection.variables.keys() <= declared.keys():
        raise ConfigurationError(field_path=("server", "variables"), condition="undeclared", operation_id=operation_id)
    values: dict[str, str] = {}
    for name, variable in declared.items():
        values[name] = value = selection.variables.get(name, variable.default)
        if variable.enum and value not in variable.enum:
            raise ConfigurationError(
                field_path=("server", "variables", name), condition="not_allowed", operation_id=operation_id
            )
    return checked_base_url(_PLACEHOLDER.sub(lambda match: values[match[1]], server.url), ("server",)).rstrip("/")


def _encoding_error(
    operation: OperationPlan[object, object], location: tuple[str, str], error: BaseException | None = None
) -> RequestEncodingError:
    return RequestEncodingError(location=location, operation_id=operation.operation_id, cause=error)


class _Request:
    __slots__ = ("cookies", "headers", "path", "query")

    def __init__(self) -> None:
        self.path: dict[str, str] = {}
        self.query: list[str] = []
        self.headers: list[tuple[str, str]] = []
        self.cookies: list[str] = []

    def add(self, contribution: FragmentContribution | QueryStringContribution, name: str) -> None:
        if isinstance(contribution, QueryStringContribution):
            self.query.append(contribution.raw_query.decode("ascii"))
            return
        fragments = contribution.ordered_fragments
        match contribution.location:
            case "path":
                self.path[name] = "".join(fragment.value.decode("ascii") for fragment in fragments)
            case "query":
                self.query.extend(_pairs(fragments))
            case "header":
                self.headers.extend(
                    ((fragment.name or b"").decode("ascii"), fragment.value.decode("ascii")) for fragment in fragments
                )
            case _:
                self.cookies.extend(_pairs(fragments))


def _pairs(fragments: tuple[ParameterFragment, ...]) -> Iterator[str]:
    return (f"{(fragment.name or b'').decode('ascii')}={fragment.value.decode('ascii')}" for fragment in fragments)


class _Body:
    __slots__ = ("chunks", "limit", "overflow", "problem", "size", "success", "truncated")

    def __init__(self, limit: int | None, *, success: bool) -> None:
        self.limit = limit
        self.success = success
        self.chunks: list[bytes] = []
        self.size = 0
        self.truncated = False
        self.overflow = False
        self.problem: ProtocolError | None = None

    def add(self, chunk: bytes) -> bool:
        """Keep a chunk within the limit and return whether reading continues.

        A success body over its limit stops reading as an overflow; an error body keeps its bounded prefix.
        """
        self.size += len(chunk)
        if self.limit is None or self.size <= self.limit:
            self.chunks.append(chunk)
            return True
        if self.success:
            self.overflow = True
        else:
            self.chunks.append(chunk[: len(chunk) - (self.size - self.limit)])
            self.truncated = True
        return False

    @property
    def content(self) -> bytes:
        return b"".join(self.chunks)


def _info(
    status: int, headers: HeadersView, operation: OperationPlan[object, object], call_id: str, started: float
) -> ResponseInfo:
    content_type = headers.get("content-type")
    return ResponseInfo(
        status_code=status,
        headers=headers,
        call_id=call_id,
        elapsed=monotonic() - started,
        content_type=None if content_type is None else normalized(content_type),
        request_id=None if operation.request_id_header is None else headers.get(operation.request_id_header),
    )


def _attributed(error: SDKError, operation: OperationPlan[object, object], call_id: str) -> SDKError:
    """Name the call on an error that an adapter or the lifecycle raised without it."""
    error.operation_id = error.operation_id or operation.operation_id
    error.call_id = error.call_id or call_id
    return error


def _delivery(trace: AttemptTrace, capabilities: TransportCapabilities) -> DeliveryState:
    """Return how far an attempt provably got from the evidence its adapter reported."""
    if trace.response_started:
        return DeliveryState.RESPONSE_STARTED
    if capabilities.delivery_evidence and not (trace.headers_started or trace.wire_sent):
        return DeliveryState.NOT_SENT
    return DeliveryState.MAYBE_SENT


def _head(status: object, headers: object, trace: AttemptTrace) -> tuple[int, HeadersView]:
    """Return a response's final status and headers, or raise when the adapter broke its contract."""
    if type(status) is not int or not isinstance(headers, HeadersView) or trace.broken:
        raise AdapterContractError(delivery_state=DeliveryState.RESPONSE_STARTED)
    if not _MIN_STATUS <= status <= _MAX_STATUS:
        raise AdapterContractError(delivery_state=DeliveryState.RESPONSE_STARTED)
    return status, headers


def _raw(chunks: Iterable[object]) -> Iterator[bytes]:
    """Yield the chunks of an adapter's body, skipping empty ones and refusing anything but bytes."""
    for chunk in chunks:
        if type(chunk) is not bytes:
            raise AdapterContractError(delivery_state=DeliveryState.RESPONSE_STARTED)
        if chunk:
            yield chunk


async def _araw(chunks: AsyncIterable[object]) -> AsyncIterator[bytes]:
    """Yield the chunks of an async adapter's body, skipping empty ones and refusing anything but bytes."""
    async for chunk in chunks:
        if type(chunk) is not bytes:
            raise AdapterContractError(delivery_state=DeliveryState.RESPONSE_STARTED)
        if chunk:
            yield chunk


def _secondary(error: BaseException, failure: Exception) -> None:
    """Keep a cleanup failure beside the error that is already propagating, never in its place."""
    if isinstance(error, SDKError):
        error.secondary_errors = (*error.secondary_errors, failure)


def _received(decoder: ResponseDecoder[object, object], status: int, settings: _Settings) -> _Body:
    success = decoder.success(status)
    return _Body(settings.max_response_bytes if success else settings.max_error_body_bytes, success=success)


def _completed(
    decoder: ResponseDecoder[T, object], info: ResponseInfo, body: _Body, settings: _Settings
) -> Response[T]:
    if body.overflow:
        assert settings.max_response_bytes is not None
        raise ResponseTooLargeError(
            info=info,
            representation="decoded",
            limit=settings.max_response_bytes,
            observed_bytes=body.size,
            call_id=info.call_id,
        )
    if (problem := body.problem) is not None and body.success:
        raise problem
    truncated = body.truncated or problem is not None
    return Response(data=decoder.decode(info, body.content, truncated=truncated, problem=problem), info=info)


class _Shared(Generic[AdapterT]):
    """What a client shares with its views: the transport, the fixed headers, and whether the transport was closed."""

    __slots__ = ("adapter", "adapter_closed", "fixed", "loop", "trusted")

    def __init__(self, defaults: ClientDefaults, adapter: AdapterT, *, trusted: bool) -> None:
        agent = defaults.user_agent
        self.adapter = adapter
        self.trusted = trusted
        self.fixed = (_ACCEPT_ENCODING,) if agent is None else (("User-Agent", agent), _ACCEPT_ENCODING)
        self.adapter_closed = False
        self.loop: asyncio.AbstractEventLoop | None = None


class _Core(Generic[AdapterT]):
    __slots__ = ("_owned", "_scope", "_settings", "_shared", "_urls")

    def __init__(self, shared: _Shared[AdapterT], settings: _Settings, scope: Scope, *, owned: bool) -> None:
        self._shared = shared
        self._settings = settings
        self._scope = scope
        self._owned = owned
        self._urls: dict[int, tuple[tuple[ServerPlan, ...], str]] = {}

    def view(self, options: object) -> Self:
        """Return a view with the options layered on these, sharing the transport and counting its calls here too."""
        if not isinstance(options, RequestOptions):
            raise ConfigurationError(field_path=("options",), condition="invalid_type")
        return type(self)(self._shared, _layered(self._settings, options), self._scope.view(), owned=False)

    def _admitted(self, operation: OperationPlan[object, object], call_id: str) -> None:
        try:
            self._scope.admit()
        except SDKError as error:
            raise _attributed(error, operation, call_id) from None

    def _failure(
        self, error: Exception, operation: OperationPlan[object, object], call_id: str, delivery: DeliveryState
    ) -> SDKError:
        """Return the error a call raises for a failure while sending or reading.

        A transport failure while the client is closing is the closing's doing, so the closing error carries it.
        """
        match error:
            case TransportError() if (closed := self._scope.closing()) is not None:
                closed.cause = error
                return _attributed(closed, operation, call_id)
            case SDKError():
                return _attributed(error, operation, call_id)
            case _:
                pass
        return AdapterExecutionError(
            delivery_state=delivery, operation_id=operation.operation_id, call_id=call_id, cause=error
        )

    def _checked(self, info: ResponseInfo) -> None:
        """Stop a call at this step when its client or view started closing."""
        if (closed := self._scope.closing()) is not None:
            closed.info = info
            raise closed

    def _base(self, operation: OperationPlan[object, object], settings: _Settings) -> str:
        """Return a call's base URL, resolving the client's server selection once per server list."""
        if settings.base_url is not None:
            return settings.base_url
        if settings.server is not self._settings.server:
            return _server_url(operation, settings.server)
        servers = operation.servers
        if (cached := self._urls.get(id(servers))) is not None and cached[0] is servers:
            return cached[1]
        url = _server_url(operation, settings.server)
        self._urls[id(servers)] = (servers, url)
        return url

    @staticmethod
    def _decoder(operation: OperationPlan[T, object], response_media_type: str | None) -> ResponseDecoder[T, object]:
        """Return the operation's decoder, narrowed to the call's response media or else the operation's."""
        decoder = operation.responses
        if (media_type := response_media_type or operation.response_media_type) is None:
            return decoder
        return decoder.narrowed(operation.operation_id, media_type)

    def _prepare(  # noqa: PLR0913
        self,
        operation: OperationPlan[object, object],
        arguments: tuple[object, ...],
        *,
        body: object,
        media_type: str | None,
        options: object,
        accept: str | None,
    ) -> tuple[PreparedRequest[EncodedAttempt], _Settings]:
        if options is not None and not isinstance(options, RequestOptions):
            raise ConfigurationError(
                field_path=("options",), condition="invalid_type", operation_id=operation.operation_id
            )
        settings = self._settings if options is None else _layered(self._settings, options)
        request = _Request()
        for spec, value in zip(operation.parameters, arguments, strict=True):
            plan = spec.plan
            if isinstance(value, Unset):
                if plan.required:
                    raise _encoding_error(operation, (plan.location, plan.name))
                continue
            try:
                contribution = encode_parameter(plan, spec.encode(value))
            except (*DATA_ERRORS, ValueError, TypeError) as error:
                raise _encoding_error(operation, (plan.location, plan.name), error) from None
            request.add(contribution, plan.name)
        encoded = None if operation.body is None else operation.body.encode(operation.operation_id, body, media_type)
        base = self._base(operation, settings)
        path = request.path
        route = _PLACEHOLDER.sub(lambda match: path[match[1]], operation.path) if path else operation.path
        query = "&".join(request.query)
        headers = [*self._shared.fixed]
        if accept is not None:
            headers.append(("Accept", accept))
        headers.extend(request.headers)
        if request.cookies:
            headers.append(("Cookie", "; ".join(request.cookies)))
        if encoded is not None:
            headers.append(("Content-Type", encoded.media_type))
        return PreparedRequest(
            method=operation.method,
            url=f"{base}{route}{'?' if query else ''}{query}",
            headers=HeadersView(headers),
            body=None if encoded is None else EncodedAttempt(encoded.content, encoded.media_type),
        ), settings


def _ownership(http_client: object, ownership: object, transport_adapter: object) -> None:
    if ownership not in _OWNERSHIPS:
        raise ConfigurationError(field_path=("http_client_ownership",), condition="invalid_value")
    if isinstance(transport_adapter, Unset):
        return
    if not isinstance(http_client, Unset):
        raise ConfigurationError(field_path=("transport_adapter",), condition="conflicts_with_http_client")
    if ownership == "owned":
        raise ConfigurationError(field_path=("http_client_ownership",), condition="conflicts_with_transport_adapter")


def _declared(adapter: object) -> None:
    if not isinstance(getattr(adapter, "capabilities", None), TransportCapabilities):
        raise AdapterContractError(delivery_state=DeliveryState.NOT_SENT)


def _is_adapter(value: object) -> TypeIs[TransportAdapter]:
    send, close = getattr(value, "send", None), getattr(value, "close", None)
    return callable(send) and callable(close) and not inspect.iscoroutinefunction(send)


def _is_async_adapter(value: object) -> TypeIs[AsyncTransportAdapter]:
    return inspect.iscoroutinefunction(getattr(value, "send", None)) and inspect.iscoroutinefunction(
        getattr(value, "aclose", None)
    )


def _adapter(
    http_client: httpx2.Client | Unset,
    ownership: str,
    transport_adapter: TransportAdapter | OwnedTransportAdapter[TransportAdapter] | Unset,
) -> tuple[TransportAdapter, bool]:
    """Return the synchronous client's adapter and whether the client owns it."""
    _ownership(http_client, ownership, transport_adapter)
    adapter, owned = (
        (transport_adapter.adapter, True)
        if isinstance(transport_adapter, OwnedTransportAdapter)
        else (transport_adapter, False)
    )
    match adapter:
        case Unset():
            pass
        case _ if _is_adapter(adapter):
            _declared(adapter)
            return adapter, owned
        case _:
            raise ConfigurationError(field_path=("transport_adapter",), condition="invalid_type")
    match http_client:
        case httpx2.Client():
            return Httpx2Transport(http_client), ownership == "owned"
        case Unset():
            return Httpx2Transport(httpx2.Client(trust_env=False)), True
        case _:
            pass
    raise ConfigurationError(field_path=("http_client",), condition="invalid_type")


def _async_adapter(
    http_client: httpx2.AsyncClient | Unset,
    ownership: str,
    transport_adapter: AsyncTransportAdapter | OwnedTransportAdapter[AsyncTransportAdapter] | Unset,
) -> tuple[AsyncTransportAdapter, bool]:
    """Return the async client's adapter and whether the client owns it."""
    _ownership(http_client, ownership, transport_adapter)
    adapter, owned = (
        (transport_adapter.adapter, True)
        if isinstance(transport_adapter, OwnedTransportAdapter)
        else (transport_adapter, False)
    )
    match adapter:
        case Unset():
            pass
        case _ if _is_async_adapter(adapter):
            _declared(adapter)
            return adapter, owned
        case _:
            raise ConfigurationError(field_path=("transport_adapter",), condition="invalid_type")
    match http_client:
        case httpx2.AsyncClient():
            return AsyncHttpx2Transport(http_client), ownership == "owned"
        case Unset():
            return AsyncHttpx2Transport(httpx2.AsyncClient(trust_env=False)), True
        case _:
            pass
    raise ConfigurationError(field_path=("http_client",), condition="invalid_type")


def _released(response: TransportResponse, operation: OperationPlan[object, object], call_id: str) -> None:
    try:
        response.close()
    except Exception as failure:  # noqa: BLE001
        raise AdapterExecutionError(
            delivery_state=DeliveryState.RESPONSE_STARTED,
            operation_id=operation.operation_id,
            call_id=call_id,
            cause=failure,
        ) from None


def _discarded(response: TransportResponse, error: BaseException) -> None:
    try:
        response.close()
    except Exception as failure:  # noqa: BLE001
        _secondary(error, failure)


async def _areleased(response: AsyncTransportResponse, operation: OperationPlan[object, object], call_id: str) -> None:
    try:
        await response.aclose()
    except Exception as failure:  # noqa: BLE001
        raise AdapterExecutionError(
            delivery_state=DeliveryState.RESPONSE_STARTED,
            operation_id=operation.operation_id,
            call_id=call_id,
            cause=failure,
        ) from None


async def _adiscarded(response: AsyncTransportResponse, error: BaseException) -> None:
    try:
        await response.aclose()
    except Exception as failure:  # noqa: BLE001
        _secondary(error, failure)


class ClientCore(_Core["TransportAdapter"]):
    """Run the calls of a synchronous client and its views through one transport adapter."""

    __slots__ = ()

    @classmethod
    def create(
        cls,
        defaults: ClientDefaults,
        *,
        options: ClientOptions | None = None,
        http_client: httpx2.Client | Unset = UNSET,
        http_client_ownership: Literal["borrowed", "owned"] = "borrowed",
        transport_adapter: TransportAdapter | OwnedTransportAdapter[TransportAdapter] | Unset = UNSET,
    ) -> Self:
        """Send through the adapter or HTTPX2 client given, borrowing it unless ownership moved, or create one."""
        settings = _client_settings(options)
        adapter, owned = _adapter(http_client, http_client_ownership, transport_adapter)
        return cls(
            _Shared(defaults, adapter, trusted=isinstance(adapter, Httpx2Transport)), settings, Scope(), owned=owned
        )

    def execute(  # noqa: PLR0913
        self,
        operation: OperationPlan[T, object],
        arguments: tuple[object, ...],
        *,
        body: object = UNSET,
        media_type: str | None = None,
        options: RequestOptions | None = None,
        response_media_type: str | None = None,
    ) -> Response[T]:
        """Send one call and return its decoded success, or raise its typed failure."""
        call_id = str(uuid4())
        self._admitted(operation, call_id)
        try:
            decoder = self._decoder(operation, response_media_type)
            request, settings = self._prepare(
                operation, arguments, body=body, media_type=media_type, options=options, accept=decoder.accept
            )
            started, trace = monotonic(), AttemptTrace()
            response = self._send(request, trace, operation, call_id)
            try:
                info, received = self._read(response, trace, operation, call_id, started, decoder, settings)
            except Exception as error:  # noqa: BLE001
                failure = self._failure(error, operation, call_id, DeliveryState.RESPONSE_STARTED)
                _discarded(response, failure)
                raise failure from None
            except BaseException as error:
                _discarded(response, error)
                raise
            _released(response, operation, call_id)
        finally:
            self._scope.release()
        return _completed(decoder, info, received, settings)

    def _send(
        self,
        request: PreparedRequest[BodyAttempt],
        trace: AttemptTrace,
        operation: OperationPlan[object, object],
        call_id: str,
    ) -> TransportResponse:
        try:
            return self._shared.adapter.send(request, AttemptIOContext(TIMEOUT, trace))
        except Exception as error:  # noqa: BLE001
            raise self._failure(
                error, operation, call_id, _delivery(trace, self._shared.adapter.capabilities)
            ) from None
        finally:
            if request.body is not None:
                request.body.close()

    def _read(  # noqa: PLR0913, PLR0917
        self,
        response: TransportResponse,
        trace: AttemptTrace,
        operation: OperationPlan[object, object],
        call_id: str,
        started: float,
        decoder: ResponseDecoder[object, object],
        settings: _Settings,
    ) -> tuple[ResponseInfo, _Body]:
        shared = self._shared
        status, headers = (
            (response.status_code, response.headers)
            if shared.trusted
            else _head(response.status_code, response.headers, trace)
        )
        info = _info(status, headers, operation, call_id, started)
        self._checked(info)
        received = _received(decoder, status, settings)
        raw = response.iter_raw_bytes()
        chunks = ContentDecoder(info, operation.operation_id).decoded(raw if shared.trusted else _raw(raw))
        try:
            for chunk in chunks:
                self._checked(info)
                if not received.add(chunk):
                    break
        except ProtocolError as error:
            received.problem = error
        return info, received

    def close(self) -> None:
        """Stop new calls, wait for the active ones, and close the transport this client owns."""
        scope = self._scope
        if not scope.begin_close():
            return
        timeout = self._settings.cleanup_timeout
        pending = scope.drain(timeout)
        failure: Exception | None = None
        shared = self._shared
        if self._owned and not shared.adapter_closed:
            shared.adapter_closed = True
            try:
                shared.adapter.close()
            except Exception as error:  # noqa: BLE001
                failure = error
        if pending or failure is not None:
            raise CleanupError(pending_calls=pending, timeout=timeout if pending else None, cause=failure)
        scope.finish()


class AsyncClientCore(_Core["AsyncTransportAdapter"]):
    """Run the calls of an asyncio client and its views through one async transport adapter on one event loop."""

    __slots__ = ()

    @classmethod
    def create(
        cls,
        defaults: ClientDefaults,
        *,
        options: ClientOptions | None = None,
        http_client: httpx2.AsyncClient | Unset = UNSET,
        http_client_ownership: Literal["borrowed", "owned"] = "borrowed",
        transport_adapter: AsyncTransportAdapter | OwnedTransportAdapter[AsyncTransportAdapter] | Unset = UNSET,
    ) -> Self:
        """Send through the adapter or HTTPX2 client given, borrowing it unless ownership moved, or create one.

        A client created inside an event loop belongs to it; one created outside belongs to the loop of its first call.
        """
        settings = _client_settings(options)
        adapter, owned = _async_adapter(http_client, http_client_ownership, transport_adapter)
        shared = _Shared(defaults, adapter, trusted=isinstance(adapter, AsyncHttpx2Transport))
        with suppress(RuntimeError):
            shared.loop = asyncio.get_running_loop()
        return cls(shared, settings, Scope(), owned=owned)

    def _running(self) -> None:
        """Raise unless the caller runs on asyncio and on the loop this client belongs to."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            raise UnsupportedAsyncBackendError from None
        shared = self._shared
        if shared.loop is None:
            shared.loop = loop
        elif shared.loop is not loop:
            raise UnsupportedAsyncBackendError(loop_mismatch=True)

    async def execute(  # noqa: PLR0913
        self,
        operation: OperationPlan[T, object],
        arguments: tuple[object, ...],
        *,
        body: object = UNSET,
        media_type: str | None = None,
        options: RequestOptions | None = None,
        response_media_type: str | None = None,
    ) -> Response[T]:
        """Send one call and return its decoded success, or raise its typed failure."""
        call_id = str(uuid4())
        try:
            self._running()
        except SDKError as error:
            raise _attributed(error, operation, call_id) from None
        self._admitted(operation, call_id)
        try:
            decoder = self._decoder(operation, response_media_type)
            request, settings = self._prepare(
                operation, arguments, body=body, media_type=media_type, options=options, accept=decoder.accept
            )
            started, trace = monotonic(), AttemptTrace()
            response = await self._send(request, trace, operation, call_id)
            try:
                info, received = await self._read(response, trace, operation, call_id, started, decoder, settings)
            except Exception as error:  # noqa: BLE001
                failure = self._failure(error, operation, call_id, DeliveryState.RESPONSE_STARTED)
                await _adiscarded(response, failure)
                raise failure from None
            except BaseException as error:
                await _adiscarded(response, error)
                raise
            await _areleased(response, operation, call_id)
        finally:
            self._scope.release()
        return _completed(decoder, info, received, settings)

    async def _send(
        self,
        request: PreparedRequest[AsyncBodyAttempt],
        trace: AttemptTrace,
        operation: OperationPlan[object, object],
        call_id: str,
    ) -> AsyncTransportResponse:
        try:
            return await self._shared.adapter.send(request, AttemptIOContext(TIMEOUT, trace))
        except Exception as error:  # noqa: BLE001
            raise self._failure(
                error, operation, call_id, _delivery(trace, self._shared.adapter.capabilities)
            ) from None
        finally:
            if request.body is not None:
                await request.body.aclose()

    async def _read(  # noqa: PLR0913, PLR0917
        self,
        response: AsyncTransportResponse,
        trace: AttemptTrace,
        operation: OperationPlan[object, object],
        call_id: str,
        started: float,
        decoder: ResponseDecoder[object, object],
        settings: _Settings,
    ) -> tuple[ResponseInfo, _Body]:
        shared = self._shared
        status, headers = (
            (response.status_code, response.headers)
            if shared.trusted
            else _head(response.status_code, response.headers, trace)
        )
        info = _info(status, headers, operation, call_id, started)
        self._checked(info)
        received = _received(decoder, status, settings)
        raw = response.iter_raw_bytes()
        chunks = ContentDecoder(info, operation.operation_id).adecoded(raw if shared.trusted else _araw(raw))
        try:
            async for chunk in chunks:
                self._checked(info)
                if not received.add(chunk):
                    break
        except ProtocolError as error:
            received.problem = error
        return info, received

    async def aclose(self) -> None:
        """Stop new calls, wait for the active ones, and close the transport this client owns."""
        self._running()
        scope = self._scope
        if not scope.begin_close():
            return
        timeout = self._settings.cleanup_timeout
        pending = await scope.adrain(timeout)
        failure: Exception | None = None
        shared = self._shared
        if self._owned and not shared.adapter_closed:
            shared.adapter_closed = True
            try:
                await shared.adapter.aclose()
            except Exception as error:  # noqa: BLE001
                failure = error
        if pending or failure is not None:
            raise CleanupError(pending_calls=pending, timeout=timeout if pending else None, cause=failure)
        scope.finish()
