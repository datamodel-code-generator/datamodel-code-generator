"""The shared core of generated clients: build each call's request, send it through a transport adapter, and decode it.

A client or a view of it runs calls while OPEN; closing stops new calls, stops the active ones at their next step,
waits up to the cleanup timeout for them, and closes the transport the client owns.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from contextlib import (
    AbstractAsyncContextManager,
    AbstractContextManager,
    asynccontextmanager,
    contextmanager,
    suppress,
)
from dataclasses import dataclass
from functools import partial
from time import monotonic
from typing import TYPE_CHECKING, Final, Generic, Literal, TypeVar
from uuid import uuid4

import httpx2
from typing_extensions import Self, TypeIs

from ..model_codecs.parameters import FragmentContribution, QueryStringContribution, encode_parameter
from ..model_codecs.selectors import MediaSelector, ResponseMedia
from ..model_codecs.unset import UNSET, Unset
from .bodies import (
    AsyncBodyFactory,
    AsyncFileBody,
    AsyncStreamBody,
    BodyAttemptContext,
    BodyFactory,
    EncodedAttempt,
    FileBody,
    StreamBody,
)
from .coding import ContentDecoder
from .errors import (
    AdapterContractError,
    AdapterExecutionError,
    CleanupError,
    ConfigurationError,
    DeliveryState,
    HTTPStatusError,
    ProtocolError,
    RequestEncodingError,
    ResponseTooLargeError,
    SDKError,
    TransportError,
    UnsupportedAsyncBackendError,
    add_secondary,
)
from .lifecycle import Scope
from .media import normalized
from .multipart import MultipartSource, is_multipart, new_boundary
from .native import AsyncHttpx2Transport, Httpx2Transport
from .operations import DATA_ERRORS, ResponseDecoder
from .options import ClientOptions, RequestOptions, ServerSelection, Settings, checked_base_url, is_base_url
from .raw import AsyncRawResponse, RawResponse
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
    from collections.abc import (
        AsyncGenerator,
        AsyncIterable,
        AsyncIterator,
        Awaitable,
        Callable,
        Generator,
        Iterable,
        Iterator,
    )

    from ..model_codecs.parameters import ParameterFragment
    from ..model_codecs.wire import WireValue
    from .bodies import AsyncBodyAttempt, BodyAttempt
    from .multipart import AsyncBodyInput, BodyInput
    from .operations import OperationPlan, ServerPlan
    from .transports import AsyncTransportAdapter, AsyncTransportResponse, TransportAdapter, TransportResponse

T = TypeVar("T")
AdapterT = TypeVar("AdapterT")
HandleT = TypeVar("HandleT")

MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024
MAX_ERROR_BODY_BYTES: Final = 64 * 1024
CLEANUP_TIMEOUT: Final = 5.0
TIMEOUT: Final = ResolvedTimeoutOptions(connect=5.0, read=30.0, write=30.0, pool=5.0)
_ACCEPT_ENCODING: Final = ("Accept-Encoding", "gzip, deflate")
_PLACEHOLDER: Final = re.compile(r"\{([^{}]*)\}")
_TOKEN: Final = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_OWNERSHIPS: Final = frozenset({"borrowed", "owned"})
_BINARY: Final = "A body must be bytes or a file, stream, factory, or multipart body of the client's mode"
_MIN_STATUS: Final = 200
_MAX_STATUS: Final = 599


@dataclass(frozen=True, slots=True, kw_only=True)
class ClientDefaults:
    """The generated defaults of one client package."""

    user_agent: str | None = None


_DEFAULT_SERVER: Final = ServerSelection()
_DEFAULT_SETTINGS: Final = Settings(
    None, _DEFAULT_SERVER, MAX_RESPONSE_BYTES, MAX_ERROR_BODY_BYTES, CLEANUP_TIMEOUT, None
)


def _layered(settings: Settings, layer: ClientOptions | RequestOptions) -> Settings:
    """Return the settings with one options layer applied: its set fields replace, UNSET ones inherit."""
    base_url, server = settings.base_url, settings.server
    if not isinstance(layer.base_url, Unset):
        base_url, server = layer.base_url.rstrip("/"), _DEFAULT_SERVER
    elif not isinstance(layer.server, Unset):
        base_url, server = None, layer.server
    return Settings(
        base_url,
        server,
        settings.max_response_bytes if isinstance(layer.max_response_bytes, Unset) else layer.max_response_bytes,
        settings.max_error_body_bytes if isinstance(layer.max_error_body_bytes, Unset) else layer.max_error_body_bytes,
        settings.cleanup_timeout if isinstance(layer.cleanup_timeout, Unset) else layer.cleanup_timeout,
        settings.max_stream_bytes if isinstance(layer.max_stream_bytes, Unset) else layer.max_stream_bytes,
    )


def _client_settings(options: object) -> Settings:
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
                    ((fragment.name or b"").decode("ascii"), fragment.value.decode()) for fragment in fragments
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
    status: int, headers: HeadersView, request_id_header: str | None, call_id: str, started: float
) -> ResponseInfo:
    content_type = headers.get("content-type")
    return ResponseInfo(
        status_code=status,
        headers=headers,
        call_id=call_id,
        elapsed=monotonic() - started,
        content_type=None if content_type is None else normalized(content_type),
        request_id=None if request_id_header is None else headers.get(request_id_header),
    )


def _attributed(error: SDKError, operation_id: str | None, call_id: str) -> SDKError:
    """Name the call on an error that an adapter or the lifecycle raised without it."""
    error.operation_id = error.operation_id or operation_id
    error.call_id = error.call_id or call_id
    return error


def _encoded(content: object, media_type: str | None) -> tuple[EncodedAttempt | None, object]:
    """Split a body into the attempt of bytes, sent as they are, and an input that builds its own attempts, or UNSET."""
    if type(content) is bytes:
        return EncodedAttempt(content, media_type), UNSET
    return None, content


def _context(call_id: str) -> BodyAttemptContext:
    return BodyAttemptContext(call_id=call_id, attempt_index=0, hop_index=0, remaining_timeout=None)


def _attempt(content: object, call_id: str) -> BodyAttempt:
    """Return what a synchronous file, stream, factory, or multipart body builds for an attempt; refuse the rest."""
    match content:
        case FileBody() | StreamBody() | BodyFactory():
            return content(_context(call_id))
        case MultipartSource():
            return content.attempt(_context(call_id))
        case _:
            pass
    raise RequestEncodingError(location=("body",), cause=TypeError(_BINARY))


async def _aattempt(content: object, call_id: str) -> AsyncBodyAttempt:
    """Return what an async file, stream, factory, or multipart body builds for an attempt; refuse the rest."""
    match content:
        case AsyncFileBody() | AsyncStreamBody() | AsyncBodyFactory():
            return await content(_context(call_id))
        case MultipartSource():
            return await content.aattempt(_context(call_id))
        case _:
            pass
    raise RequestEncodingError(location=("body",), cause=TypeError(_BINARY))


def _checked_raw(method: object, url: object) -> tuple[str, str]:
    """Check the method and absolute URL of a raw request."""
    if not isinstance(method, str) or not _TOKEN.fullmatch(method):
        raise ConfigurationError(field_path=("method",), condition="invalid_value")
    if not isinstance(url, str) or not _absolute(url):
        raise ConfigurationError(field_path=("url",), condition="invalid_url")
    return method, url


def _absolute(url: str) -> bool:
    """Return whether a raw request's URL is a base URL that generated clients accept, plus an optional query."""
    base, _, query = url.partition("?")
    try:
        return "#" not in query and is_base_url(base)
    except ValueError:
        return False


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


def _received(decoder: ResponseDecoder[object, object], status: int, settings: Settings) -> _Body:
    success = decoder.success(status)
    return _Body(settings.max_response_bytes if success else settings.max_error_body_bytes, success=success)


def _completed(
    decoder: ResponseDecoder[T, object], info: ResponseInfo, body: _Body, settings: Settings, operation_id: str | None
) -> Response[T]:
    if body.overflow:
        assert settings.max_response_bytes is not None
        raise ResponseTooLargeError(
            info=info,
            representation="decoded",
            limit=settings.max_response_bytes,
            observed_bytes=body.size,
            operation_id=operation_id,
            call_id=info.call_id,
        )
    if (problem := body.problem) is not None and body.success:
        raise problem
    truncated = body.truncated or problem is not None
    try:
        data = decoder.decode(info, body.content, truncated=truncated, problem=problem)
    except SDKError as error:
        _attributed(error, operation_id, info.call_id)
        raise
    return Response(data=data, info=info)


RAW_DECODER: Final[ResponseDecoder[object, object]] = ResponseDecoder((), (), HTTPStatusError)


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


class _Core(Generic[AdapterT, HandleT]):
    __slots__ = ("_owned", "_scope", "_settings", "_shared", "_urls")

    def __init__(self, shared: _Shared[AdapterT], settings: Settings, scope: Scope[HandleT], *, owned: bool) -> None:
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

    def _admitted(self, operation_id: str | None, call_id: str) -> None:
        try:
            self._scope.admit()
        except SDKError as error:
            raise _attributed(error, operation_id, call_id) from None

    def _failure(self, error: Exception, operation_id: str | None, call_id: str, delivery: DeliveryState) -> SDKError:
        """Return the error a call raises for a failure while sending or reading.

        A transport failure while the client is closing is the closing's doing, so the closing error carries it.
        """
        match error:
            case TransportError() if (closed := self._scope.closing()) is not None:
                closed.cause = error
                return _attributed(closed, operation_id, call_id)
            case SDKError():
                return _attributed(error, operation_id, call_id)
            case _:
                pass
        return AdapterExecutionError(delivery_state=delivery, operation_id=operation_id, call_id=call_id, cause=error)

    def _checked(self, info: ResponseInfo) -> None:
        """Stop a call at this step when its client or view started closing."""
        if (closed := self._scope.closing()) is not None:
            closed.info = info
            raise closed

    def _response_info(
        self,
        response: TransportResponse | AsyncTransportResponse,
        trace: AttemptTrace,
        request_id_header: str | None,
        call_id: str,
        started: float,
    ) -> ResponseInfo:
        """Return the metadata of a response whose headers arrived, stopping here when the client is closing."""
        status, headers = (
            (response.status_code, response.headers)
            if self._shared.trusted
            else _head(response.status_code, response.headers, trace)
        )
        info = _info(status, headers, request_id_header, call_id, started)
        self._checked(info)
        return info

    def _raw_prepared(
        self, method: object, url: object, body: object, options: object
    ) -> tuple[PreparedRequest[EncodedAttempt], object, Settings]:
        """Return a raw call's request to any URL, with the client's fixed headers and a factory's media type.

        Bytes are the request's attempt; any other body is returned beside it, to build its own attempt.
        """
        if options is not None and not isinstance(options, RequestOptions):
            raise ConfigurationError(field_path=("options",), condition="invalid_type")
        settings = self._settings if options is None else _layered(self._settings, options)
        verb, target = _checked_raw(method, url)
        media_type = body.content_type if isinstance(body, (BodyFactory, AsyncBodyFactory)) else None
        if is_multipart(body):
            body = MultipartSource(body, boundary := new_boundary())
            media_type = f"multipart/form-data; boundary={boundary}"
        fixed = self._shared.fixed
        headers = HeadersView(fixed if media_type is None else (*fixed, ("Content-Type", media_type)))
        attempt, deferred = _encoded(body, None)
        return PreparedRequest(method=verb, url=target, headers=headers, body=attempt), deferred, settings

    def _base(self, operation: OperationPlan[object, object], settings: Settings) -> str:
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
    def _decoder(
        operation: OperationPlan[T, object], response_media_type: str | MediaSelector | None
    ) -> ResponseDecoder[T, object]:
        """Return the operation's decoder, narrowed to the call's response media or else the operation's.

        A response media selector of the operation narrows it to the selector's concrete media type.
        """
        decoder = operation.responses
        media_type: str | None
        match response_media_type:
            case None:
                media_type = operation.response_media_type
            case MediaSelector():
                _, media_type = ResponseMedia.chosen(response_media_type, operation.codecs)
            case _:
                media_type = response_media_type or operation.response_media_type
        return decoder if media_type is None else decoder.narrowed(operation.operation_id, media_type)

    def _prepare(  # noqa: PLR0913
        self,
        operation: OperationPlan[object, object],
        arguments: tuple[object, ...],
        *,
        body: object,
        media_type: str | MediaSelector | None,
        options: object,
        accept: str | None,
    ) -> tuple[PreparedRequest[EncodedAttempt], object, Settings]:
        """Return a call's request, its body input when that builds its own attempts or else UNSET, and its settings."""
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
        encoded = (
            None
            if operation.body is None
            else operation.body.encode(operation.operation_id, body, media_type, operation.codecs)
        )
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
        url = f"{base}{route}{'?' if query else ''}{query}"
        attempt: EncodedAttempt | None = None
        deferred: object = UNSET
        if encoded is not None:
            headers.append(("Content-Type", encoded.media_type))
            attempt, deferred = _encoded(encoded.content, encoded.media_type)
        return (
            PreparedRequest(method=operation.method, url=url, headers=HeadersView(headers), body=attempt),
            deferred,
            settings,
        )


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


def _released(response: TransportResponse, operation_id: str | None, call_id: str) -> None:
    try:
        response.close()
    except Exception as failure:  # noqa: BLE001
        raise AdapterExecutionError(
            delivery_state=DeliveryState.RESPONSE_STARTED, operation_id=operation_id, call_id=call_id, cause=failure
        ) from None


def _discarded(close: Callable[[], object], error: BaseException) -> None:
    """Run a close while an error propagates, keeping its failure beside that error."""
    if (failure := _quietly(close)) is not None:
        add_secondary(error, failure)


async def _areleased(response: AsyncTransportResponse, operation_id: str | None, call_id: str) -> None:
    try:
        await response.aclose()
    except Exception as failure:  # noqa: BLE001
        raise AdapterExecutionError(
            delivery_state=DeliveryState.RESPONSE_STARTED, operation_id=operation_id, call_id=call_id, cause=failure
        ) from None


async def _adiscarded(close: Callable[[], Awaitable[None]], error: BaseException) -> None:
    """Await a close while an error propagates, keeping its failure beside that error."""
    if (failure := await _aquietly(close)) is not None:
        add_secondary(error, failure)


@contextmanager
def _streamed(opened: Callable[[], RawResponse]) -> Generator[RawResponse, None, None]:
    """Send on entering the block and yield the streaming response, which leaving the block closes."""
    handle = opened()
    try:
        yield handle
    except BaseException as error:
        handle.discard(error)
        raise
    handle.close()


@asynccontextmanager
async def _astreamed(opened: Callable[[], Awaitable[AsyncRawResponse]]) -> AsyncGenerator[AsyncRawResponse, None]:
    """Send on entering the block and yield the streaming response, which leaving the block closes."""
    handle = await opened()
    try:
        yield handle
    except BaseException as error:
        await handle.discard(error)
        raise
    await handle.aclose()


def _quietly(close: Callable[[], object]) -> Exception | None:
    """Run a close and return its failure instead of raising it."""
    try:
        close()
    except Exception as error:  # noqa: BLE001
        return error
    return None


async def _aquietly(close: Callable[[], Awaitable[None]]) -> Exception | None:
    """Await a close and return its failure instead of raising it."""
    try:
        await close()
    except Exception as error:  # noqa: BLE001
        return error
    return None


def _cleanup(pending: int, remaining: int, timeout: float, failures: list[Exception]) -> CleanupError | None:
    """Return the error of a close that left calls or handles past its cleanup time or failed to release something."""
    if not (pending or remaining or failures):
        return None
    return CleanupError(
        pending_calls=pending,
        pending_leases=remaining,
        timeout=timeout if pending or remaining else None,
        cause=failures[0] if failures else None,
        secondary_errors=tuple(failures[1:]),
    )


class ClientCore(_Core["TransportAdapter", "RawResponse"]):
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
        media_type: str | MediaSelector | None = None,
        options: RequestOptions | None = None,
        response_media_type: str | MediaSelector | None = None,
    ) -> Response[T]:
        """Send one call and return its decoded success, or raise its typed failure."""
        operation_id, call_id = operation.operation_id, str(uuid4())
        self._admitted(operation_id, call_id)
        try:
            decoder = self._decoder(operation, response_media_type)
            request, deferred, settings = self._prepare(
                operation, arguments, body=body, media_type=media_type, options=options, accept=decoder.accept
            )
            started, trace = monotonic(), AttemptTrace()
            response = self._send(request, deferred, trace, operation_id, call_id)
            try:
                info = self._response_info(response, trace, operation.request_id_header, call_id, started)
                received = self._read(response, info, decoder, settings, operation_id=operation_id)
            except Exception as error:  # noqa: BLE001
                failure = self._failure(error, operation_id, call_id, DeliveryState.RESPONSE_STARTED)
                _discarded(response.close, failure)
                raise failure from None
            except BaseException as error:
                _discarded(response.close, error)
                raise
            _released(response, operation_id, call_id)
        finally:
            self._scope.release()
        return _completed(decoder, info, received, settings, operation_id)

    def execute_raw(  # noqa: PLR0913
        self,
        operation: OperationPlan[object, object],
        arguments: tuple[object, ...],
        *,
        body: object = UNSET,
        media_type: str | MediaSelector | None = None,
        options: RequestOptions | None = None,
        response_media_type: str | MediaSelector | None = None,
        stream: bool = False,
    ) -> RawResponse:
        """Send one call and return its raw response: buffered, or a streaming handle when asked."""
        operation_id, call_id = operation.operation_id, str(uuid4())
        self._admitted(operation_id, call_id)
        try:
            decoder = self._decoder(operation, response_media_type)
            request, deferred, settings = self._prepare(
                operation, arguments, body=body, media_type=media_type, options=options, accept=decoder.accept
            )
        except BaseException:
            self._scope.release()
            raise
        return self._raw(
            request,
            deferred,
            decoder,
            settings,
            operation_id=operation_id,
            request_id_header=operation.request_id_header,
            call_id=call_id,
            stream=stream,
        )

    def stream(  # noqa: PLR0913
        self,
        operation: OperationPlan[object, object],
        arguments: tuple[object, ...],
        *,
        body: object = UNSET,
        media_type: str | MediaSelector | None = None,
        options: RequestOptions | None = None,
        response_media_type: str | MediaSelector | None = None,
    ) -> AbstractContextManager[RawResponse]:
        """Return a block that sends one call on entry and yields its streaming response until exit."""
        return _streamed(
            lambda: self.execute_raw(
                operation,
                arguments,
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
                stream=True,
            )
        )

    def request_raw(
        self,
        method: str,
        url: str,
        *,
        body: BodyInput[WireValue] | Unset = UNSET,
        options: RequestOptions | None = None,
        stream: bool = False,
    ) -> RawResponse:
        """Send a request to any absolute URL, outside the operations, and return its raw response."""
        call_id = str(uuid4())
        self._admitted(None, call_id)
        try:
            request, deferred, settings = self._raw_prepared(method, url, body, options)
        except BaseException:
            self._scope.release()
            raise
        return self._raw(
            request,
            deferred,
            RAW_DECODER,
            settings,
            operation_id=None,
            request_id_header=None,
            call_id=call_id,
            stream=stream,
        )

    def stream_raw(
        self,
        method: str,
        url: str,
        *,
        body: BodyInput[WireValue] | Unset = UNSET,
        options: RequestOptions | None = None,
    ) -> AbstractContextManager[RawResponse]:
        """Return a block that sends a raw request on entry and yields its streaming response until exit."""
        return _streamed(lambda: self.request_raw(method, url, body=body, options=options, stream=True))

    def _raw(  # noqa: PLR0913
        self,
        request: PreparedRequest[BodyAttempt],
        deferred: object,
        decoder: ResponseDecoder[object, object],
        settings: Settings,
        *,
        operation_id: str | None,
        request_id_header: str | None,
        call_id: str,
        stream: bool,
    ) -> RawResponse:
        """Send an admitted call and hand its response over to a handle, which buffers it unless streaming."""
        scope = self._scope
        try:
            started, trace = monotonic(), AttemptTrace()
            response = self._send(request, deferred, trace, operation_id, call_id)
            try:
                info = self._response_info(response, trace, request_id_header, call_id, started)
            except Exception as error:  # noqa: BLE001
                failure = self._failure(error, operation_id, call_id, DeliveryState.RESPONSE_STARTED)
                _discarded(response.close, failure)
                raise failure from None
            except BaseException as error:
                _discarded(response.close, error)
                raise
            source = response.iter_raw_bytes if self._shared.trusted else partial(_checked_chunks, response)
            handle = RawResponse(
                info,
                decoder,
                settings,
                operation_id,
                lambda error: self._failure(error, operation_id, call_id, DeliveryState.RESPONSE_STARTED),
                source=source,
                close=response.close,
                scope=scope,
            )
            scope.handoff(handle)
        except BaseException:
            scope.release()
            raise
        if not stream:
            try:
                handle.read()
            except BaseException as error:
                handle.discard(error)
                raise
        return handle

    def _send(
        self,
        request: PreparedRequest[BodyAttempt],
        deferred: object,
        trace: AttemptTrace,
        operation_id: str | None,
        call_id: str,
    ) -> TransportResponse:
        """Send one attempt, building its body from a deferred file, stream, or factory, then close the body."""
        attempt = request.body
        try:
            if not isinstance(deferred, Unset):
                attempt = _attempt(deferred, call_id)
                request = PreparedRequest(method=request.method, url=request.url, headers=request.headers, body=attempt)
            response = self._shared.adapter.send(request, AttemptIOContext(TIMEOUT, trace))
        except Exception as error:  # noqa: BLE001
            failure = self._failure(error, operation_id, call_id, _delivery(trace, self._shared.adapter.capabilities))
            if attempt is not None:
                _discarded(attempt.close, failure)
            raise failure from None
        except BaseException as error:
            if attempt is not None:
                _discarded(attempt.close, error)
            raise
        if attempt is not None:
            try:
                attempt.close()
            except Exception as problem:  # noqa: BLE001
                closed = self._failure(problem, operation_id, call_id, DeliveryState.RESPONSE_STARTED)
                _discarded(response.close, closed)
                raise closed from None
            except BaseException as error:
                _discarded(response.close, error)
                raise
        return response

    def _read(
        self,
        response: TransportResponse,
        info: ResponseInfo,
        decoder: ResponseDecoder[object, object],
        settings: Settings,
        *,
        operation_id: str | None,
    ) -> _Body:
        received = _received(decoder, info.status_code, settings)
        raw = response.iter_raw_bytes()
        chunks = ContentDecoder(info, operation_id).decoded(raw if self._shared.trusted else _raw(raw))
        try:
            for chunk in chunks:
                self._checked(info)
                if not received.add(chunk):
                    break
        except ProtocolError as error:
            received.problem = error
        return received

    def close(self) -> None:
        """Stop new calls, wait for the active ones and open handles, and close the transport this client owns."""
        scope = self._scope
        if not scope.begin_close():
            return
        timeout = self._settings.cleanup_timeout
        remaining = scope.drain(timeout)
        closes: list[Callable[[], object]] = [handle.close for handle in remaining]
        shared = self._shared
        if self._owned and not shared.adapter_closed:
            shared.adapter_closed = True
            closes.append(shared.adapter.close)
        failures = [failure for close in closes if (failure := _quietly(close)) is not None]
        pending, _ = scope.pending()
        if (error := _cleanup(pending, len(remaining), timeout, failures)) is not None:
            raise error
        scope.finish()


def _checked_chunks(response: TransportResponse) -> Iterator[bytes]:
    return _raw(response.iter_raw_bytes())


def _achecked_chunks(response: AsyncTransportResponse) -> AsyncIterator[bytes]:
    return _araw(response.iter_raw_bytes())


class AsyncClientCore(_Core["AsyncTransportAdapter", "AsyncRawResponse"]):
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

    def _running(self, operation_id: str | None = None, call_id: str | None = None) -> None:
        """Raise unless the caller runs on asyncio and on the loop this client belongs to."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            raise UnsupportedAsyncBackendError(operation_id=operation_id, call_id=call_id) from None
        shared = self._shared
        if shared.loop is None:
            shared.loop = loop
        elif shared.loop is not loop:
            raise UnsupportedAsyncBackendError(loop_mismatch=True, operation_id=operation_id, call_id=call_id)

    async def execute(  # noqa: PLR0913
        self,
        operation: OperationPlan[T, object],
        arguments: tuple[object, ...],
        *,
        body: object = UNSET,
        media_type: str | MediaSelector | None = None,
        options: RequestOptions | None = None,
        response_media_type: str | MediaSelector | None = None,
    ) -> Response[T]:
        """Send one call and return its decoded success, or raise its typed failure."""
        operation_id, call_id = operation.operation_id, str(uuid4())
        self._running(operation_id, call_id)
        self._admitted(operation_id, call_id)
        try:
            decoder = self._decoder(operation, response_media_type)
            request, deferred, settings = self._prepare(
                operation, arguments, body=body, media_type=media_type, options=options, accept=decoder.accept
            )
            started, trace = monotonic(), AttemptTrace()
            response = await self._send(request, deferred, trace, operation_id, call_id)
            try:
                info = self._response_info(response, trace, operation.request_id_header, call_id, started)
                received = await self._read(response, info, decoder, settings, operation_id=operation_id)
            except Exception as error:  # noqa: BLE001
                failure = self._failure(error, operation_id, call_id, DeliveryState.RESPONSE_STARTED)
                await _adiscarded(response.aclose, failure)
                raise failure from None
            except BaseException as error:
                await _adiscarded(response.aclose, error)
                raise
            await _areleased(response, operation_id, call_id)
        finally:
            self._scope.release()
        return _completed(decoder, info, received, settings, operation_id)

    async def execute_raw(  # noqa: PLR0913
        self,
        operation: OperationPlan[object, object],
        arguments: tuple[object, ...],
        *,
        body: object = UNSET,
        media_type: str | MediaSelector | None = None,
        options: RequestOptions | None = None,
        response_media_type: str | MediaSelector | None = None,
        stream: bool = False,
    ) -> AsyncRawResponse:
        """Send one call and return its raw response: buffered, or a streaming handle when asked."""
        operation_id, call_id = operation.operation_id, str(uuid4())
        self._running(operation_id, call_id)
        self._admitted(operation_id, call_id)
        try:
            decoder = self._decoder(operation, response_media_type)
            request, deferred, settings = self._prepare(
                operation, arguments, body=body, media_type=media_type, options=options, accept=decoder.accept
            )
        except BaseException:
            self._scope.release()
            raise
        return await self._raw(
            request,
            deferred,
            decoder,
            settings,
            operation_id=operation_id,
            request_id_header=operation.request_id_header,
            call_id=call_id,
            stream=stream,
        )

    def stream(  # noqa: PLR0913
        self,
        operation: OperationPlan[object, object],
        arguments: tuple[object, ...],
        *,
        body: object = UNSET,
        media_type: str | MediaSelector | None = None,
        options: RequestOptions | None = None,
        response_media_type: str | MediaSelector | None = None,
    ) -> AbstractAsyncContextManager[AsyncRawResponse]:
        """Return a block that sends one call on entry and yields its streaming response until exit."""
        return _astreamed(
            lambda: self.execute_raw(
                operation,
                arguments,
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
                stream=True,
            )
        )

    async def request_raw(
        self,
        method: str,
        url: str,
        *,
        body: AsyncBodyInput[WireValue] | Unset = UNSET,
        options: RequestOptions | None = None,
        stream: bool = False,
    ) -> AsyncRawResponse:
        """Send a request to any absolute URL, outside the operations, and return its raw response."""
        call_id = str(uuid4())
        self._running(None, call_id)
        self._admitted(None, call_id)
        try:
            request, deferred, settings = self._raw_prepared(method, url, body, options)
        except BaseException:
            self._scope.release()
            raise
        return await self._raw(
            request,
            deferred,
            RAW_DECODER,
            settings,
            operation_id=None,
            request_id_header=None,
            call_id=call_id,
            stream=stream,
        )

    def stream_raw(
        self,
        method: str,
        url: str,
        *,
        body: AsyncBodyInput[WireValue] | Unset = UNSET,
        options: RequestOptions | None = None,
    ) -> AbstractAsyncContextManager[AsyncRawResponse]:
        """Return a block that sends a raw request on entry and yields its streaming response until exit."""
        return _astreamed(lambda: self.request_raw(method, url, body=body, options=options, stream=True))

    async def _raw(  # noqa: PLR0913
        self,
        request: PreparedRequest[AsyncBodyAttempt],
        deferred: object,
        decoder: ResponseDecoder[object, object],
        settings: Settings,
        *,
        operation_id: str | None,
        request_id_header: str | None,
        call_id: str,
        stream: bool,
    ) -> AsyncRawResponse:
        """Send an admitted call and hand its response over to a handle, which buffers it unless streaming."""
        scope = self._scope
        try:
            started, trace = monotonic(), AttemptTrace()
            response = await self._send(request, deferred, trace, operation_id, call_id)
            try:
                info = self._response_info(response, trace, request_id_header, call_id, started)
            except Exception as error:  # noqa: BLE001
                failure = self._failure(error, operation_id, call_id, DeliveryState.RESPONSE_STARTED)
                await _adiscarded(response.aclose, failure)
                raise failure from None
            except BaseException as error:
                await _adiscarded(response.aclose, error)
                raise
            source = response.iter_raw_bytes if self._shared.trusted else partial(_achecked_chunks, response)
            handle = AsyncRawResponse(
                info,
                decoder,
                settings,
                operation_id,
                lambda error: self._failure(error, operation_id, call_id, DeliveryState.RESPONSE_STARTED),
                source=source,
                close=response.aclose,
                scope=scope,
            )
            scope.handoff(handle)
        except BaseException:
            scope.release()
            raise
        if not stream:
            try:
                await handle.read()
            except BaseException as error:
                await handle.discard(error)
                raise
        return handle

    async def _send(
        self,
        request: PreparedRequest[AsyncBodyAttempt],
        deferred: object,
        trace: AttemptTrace,
        operation_id: str | None,
        call_id: str,
    ) -> AsyncTransportResponse:
        """Send one attempt, building its body from a deferred file, stream, or factory, then close the body."""
        attempt = request.body
        try:
            if not isinstance(deferred, Unset):
                attempt = await _aattempt(deferred, call_id)
                request = PreparedRequest(method=request.method, url=request.url, headers=request.headers, body=attempt)
            response = await self._shared.adapter.send(request, AttemptIOContext(TIMEOUT, trace))
        except Exception as error:  # noqa: BLE001
            failure = self._failure(error, operation_id, call_id, _delivery(trace, self._shared.adapter.capabilities))
            if attempt is not None:
                await _adiscarded(attempt.aclose, failure)
            raise failure from None
        except BaseException as error:
            if attempt is not None:
                await _adiscarded(attempt.aclose, error)
            raise
        if attempt is not None:
            try:
                await attempt.aclose()
            except Exception as problem:  # noqa: BLE001
                closed = self._failure(problem, operation_id, call_id, DeliveryState.RESPONSE_STARTED)
                await _adiscarded(response.aclose, closed)
                raise closed from None
            except BaseException as error:
                await _adiscarded(response.aclose, error)
                raise
        return response

    async def _read(
        self,
        response: AsyncTransportResponse,
        info: ResponseInfo,
        decoder: ResponseDecoder[object, object],
        settings: Settings,
        *,
        operation_id: str | None,
    ) -> _Body:
        received = _received(decoder, info.status_code, settings)
        raw = response.iter_raw_bytes()
        chunks = ContentDecoder(info, operation_id).adecoded(raw if self._shared.trusted else _araw(raw))
        try:
            async for chunk in chunks:
                self._checked(info)
                if not received.add(chunk):
                    break
        except ProtocolError as error:
            received.problem = error
        return received

    async def aclose(self) -> None:
        """Stop new calls, wait for the active ones and open handles, and close the transport this client owns."""
        self._running()
        scope = self._scope
        if not scope.begin_close():
            return
        timeout = self._settings.cleanup_timeout
        remaining = await scope.adrain(timeout)
        closes: list[Callable[[], Awaitable[None]]] = [handle.aclose for handle in remaining]
        shared = self._shared
        if self._owned and not shared.adapter_closed:
            shared.adapter_closed = True
            closes.append(shared.adapter.aclose)
        failures = [failure for close in closes if (failure := await _aquietly(close)) is not None]
        pending, _ = scope.pending()
        if (error := _cleanup(pending, len(remaining), timeout, failures)) is not None:
            raise error
        scope.finish()
