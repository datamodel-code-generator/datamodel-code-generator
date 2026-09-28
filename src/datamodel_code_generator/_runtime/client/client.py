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
from urllib.parse import quote, unquote_plus, urlsplit

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
    LimiterExecutionError,
    ProtocolError,
    RequestEncodingError,
    ResponseTooLargeError,
    SDKError,
    TransportError,
    UnsupportedAsyncBackendError,
    add_secondary,
)
from .events import CallEvents, call_events
from .hooks import LimiterContext
from .lifecycle import Scope, TaskInterruptionError, cleanup_secondary, task_result
from .logical import LogicalCallContext
from .media import normalized
from .multipart import MultipartSource, is_multipart, new_boundary
from .native import AsyncHttpx2Transport, Httpx2Transport
from .operations import DATA_ERRORS, ResponseDecoder
from .options import (
    DEFAULT_VALIDATION,
    ClientOptions,
    HeaderPatch,
    QueryPatch,
    RequestOptions,
    ServerSelection,
    Settings,
    TimeoutOptions,
    ValidationModes,
    awaited,
    checked_base_url,
    context,
    is_base_url,
)
from .raw import AsyncRawResponse, RawResponse
from .responses import HeadersView, Response, ResponseInfo
from .transports import (
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
        Sequence,
    )

    from ..model_codecs.parameters import ParameterFragment
    from ..model_codecs.wire import WireValue
    from .bodies import AsyncBodyAttempt, BodyAttempt
    from .hooks import AsyncLimiter, AsyncPermit, Limiter, Permit
    from .multipart import AsyncBodyInput, BodyInput
    from .operations import OperationPlan, ServerPlan
    from .options import RequestValidation
    from .transports import AsyncTransportAdapter, AsyncTransportResponse, TransportAdapter, TransportResponse

T = TypeVar("T")
AdapterT = TypeVar("AdapterT")
HandleT = TypeVar("HandleT")

MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024
MAX_ERROR_BODY_BYTES: Final = 64 * 1024
CLEANUP_TIMEOUT: Final = 5.0
_ACCEPT_ENCODING: Final = ("Accept-Encoding", "gzip, deflate")
_PLACEHOLDER: Final = re.compile(r"\{([^{}]*)\}")
_TOKEN: Final = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_OWNERSHIPS: Final = frozenset({"borrowed", "owned"})
_BINARY: Final = "A body must be bytes or a file, stream, factory, or multipart body of the client's mode"
_MIN_STATUS: Final = 200
_MAX_STATUS: Final = 599


@dataclass(frozen=True, slots=True, kw_only=True)
class ClientDefaults:
    """The generated defaults of one client package, and the validation modes it allows."""

    user_agent: str | None = None
    validation: ValidationModes = DEFAULT_VALIDATION


_DEFAULT_SERVER: Final = ServerSelection()


def _layered(
    settings: Settings,
    layer: ClientOptions | RequestOptions,
    modes: ValidationModes,
    operation_id: str | None = None,
) -> Settings:
    """Return the settings with one options layer applied: its set fields replace, UNSET ones inherit.

    A validation mode the package does not allow is refused.
    """
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
        (*settings.headers, layer.headers) if layer.headers else settings.headers,
        (*settings.query, layer.query) if layer.query else settings.query,
        settings.hooks if isinstance(layer.hooks, Unset) else layer.hooks,
        settings.context if isinstance(layer.context, Unset) else context({**settings.context, **layer.context}),
        settings.async_hooks if isinstance(layer.hooks, Unset) else awaited(layer.hooks),
        settings.validation
        if isinstance(layer.validation, Unset)
        else modes.layered(settings.validation, layer.validation, operation_id),
        timeout=_timeouts(settings.timeout, layer.timeout),
        stream_read_timeout=(
            None
            if layer.timeout is None
            else layer.timeout.read
            if isinstance(layer.timeout, TimeoutOptions) and not isinstance(layer.timeout.read, Unset)
            else settings.stream_read_timeout
        ),
        total_timeout=settings.total_timeout if isinstance(layer.total_timeout, Unset) else layer.total_timeout,
        deadline=settings.deadline if isinstance(layer.deadline, Unset) else layer.deadline,
        cancel_token=settings.cancel_token if isinstance(layer.cancel_token, Unset) else layer.cancel_token,
        limiter=settings.limiter if isinstance(layer.limiter, Unset) else layer.limiter,
        max_network_sends=(
            settings.max_network_sends if isinstance(layer.max_network_sends, Unset) else layer.max_network_sends
        ),
        stream_idle_timeout=(
            settings.stream_idle_timeout if isinstance(layer.stream_idle_timeout, Unset) else layer.stream_idle_timeout
        ),
        stream_total_timeout=(
            settings.stream_total_timeout
            if isinstance(layer.stream_total_timeout, Unset)
            else layer.stream_total_timeout
        ),
    )


def _timeouts(current: ResolvedTimeoutOptions, layer: TimeoutOptions | Unset | None) -> ResolvedTimeoutOptions:
    """Resolve nested phase fields while preserving the difference between omission and an explicit None."""
    if isinstance(layer, Unset):
        return current
    if layer is None:
        return ResolvedTimeoutOptions(connect=None, read=None, write=None, pool=None)
    return ResolvedTimeoutOptions(
        connect=current.connect if isinstance(layer.connect, Unset) else layer.connect,
        read=current.read if isinstance(layer.read, Unset) else layer.read,
        write=current.write if isinstance(layer.write, Unset) else layer.write,
        pool=current.pool if isinstance(layer.pool, Unset) else layer.pool,
    )


def _client_settings(options: object, defaults: ClientDefaults) -> Settings:
    modes = defaults.validation
    settings = Settings(
        None,
        _DEFAULT_SERVER,
        MAX_RESPONSE_BYTES,
        MAX_ERROR_BODY_BYTES,
        CLEANUP_TIMEOUT,
        None,
        validation=modes.default(),
    )
    match options:
        case None:
            return settings
        case ClientOptions():
            return _layered(settings, options, modes)
        case _:
            pass
    raise ConfigurationError(field_path=("options",), condition="invalid_type")


def _patched(
    pairs: list[tuple[str, str]], patch: Sequence[tuple[str, str | None]], fold: Callable[[str], str] = str.lower
) -> list[tuple[str, str]]:
    """Return named values with one layer applied: the values it gives a name replace that name's at their first place.

    None removes a name's values, and a name the values lack comes last, in the layer's order. Header names fold their
    case; query names do not.
    """
    if not patch:
        return pairs
    groups: dict[str, list[tuple[str, str]]] = {}
    for name, value in patch:
        group = groups.setdefault(fold(name), [])
        if value is not None:
            group.append((name, value))
    pending = dict(groups)
    patched: list[tuple[str, str]] = []
    for name, value in pairs:
        if (key := fold(name)) not in groups:
            patched.append((name, value))
        elif key in pending:
            patched.extend(pending.pop(key))
    patched.extend(pair for group in pending.values() for pair in group)
    return patched


def _headers(
    generated: list[tuple[str, str]],
    patches: tuple[Sequence[tuple[str, str | None]], ...],
    media_type: str | None,
) -> HeadersView:
    """Return the headers of a call: the generated ones with each layer applied in order, then the body's media type."""
    for patch in patches:
        generated = _patched(generated, patch)
    if media_type is None:
        return HeadersView(generated)
    return HeadersView([
        *(pair for pair in generated if pair[0].lower() != "content-type"),
        ("Content-Type", media_type),
    ])


def _query(lower: tuple[QueryPatch, ...], explicit: list[str], call: QueryPatch) -> str:
    """Return a query with its layers applied: the client's and views' patches, the explicit pairs, and the call's.

    A patch's names and values are percent-encoded once, and explicit pairs compare by their names decoded as forms
    decode them, a plus sign being a space.
    """
    named: list[tuple[str, str | None]] = [(unquote_plus(pair.partition("=")[0]), pair) for pair in explicit]
    pairs: list[tuple[str, str]] = []
    for layer in (*map(_encoded_query, lower), named, _encoded_query(call)):
        pairs = _patched(pairs, layer, str)
    return "&".join(pair for _, pair in pairs)


def _encoded_query(patch: QueryPatch) -> list[tuple[str, str | None]]:
    return [
        (name, None if value is None else f"{quote(name, safe='')}={quote(value, safe='')}") for name, value in patch
    ]


def _unframed(
    patches: tuple[HeaderPatch, ...], media_type: str | None, accept: str | None, operation_id: str | None
) -> None:
    """Refuse header patches that relabel a call's body or its narrowed response media.

    A Content-Type patch must name the body's media type, or remove it from a call without a body, and an Accept patch
    must name the media type a call narrowed its response to; media types compare normalized.
    """
    for patch in patches:
        for name, value in patch:
            if (condition := _conflict(name, value, media_type, accept)) is not None:
                raise ConfigurationError(field_path=("headers", name), condition=condition, operation_id=operation_id)


def _conflict(name: str, value: str | None, media_type: str | None, accept: str | None) -> str | None:
    match name.lower():
        case "content-type" if _relabels(value, media_type):
            return "conflicts_with_body_media"
        case "accept" if accept is not None and _relabels(value, accept):
            return "conflicts_with_response_media"
        case _:
            return None


def _relabels(value: str | None, media_type: str | None) -> bool:
    match value, media_type:
        case None, None:
            return False
        case str(), str():
            return normalized(value) != normalized(media_type)
        case _:
            return True


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


def _parameters(
    operation: OperationPlan[object, object], arguments: tuple[object, ...], mode: RequestValidation
) -> _Request:
    """Return a call's request with its parameters encoded as its request validation mode selects."""
    request = _Request()
    for spec, value in zip(operation.parameters, arguments, strict=True):
        plan = spec.plan
        if isinstance(value, Unset):
            if plan.required:
                raise _encoding_error(operation, (plan.location, plan.name))
            continue
        try:
            contribution = encode_parameter(plan, spec.encode(value, mode))
        except (*DATA_ERRORS, ValueError, TypeError) as error:
            raise _encoding_error(operation, (plan.location, plan.name), error) from None
        request.add(contribution, plan.name)
    return request


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


def _info(status: int, headers: HeadersView, request_id_header: str | None, call: LogicalCallContext) -> ResponseInfo:
    content_type = headers.get("content-type")
    return ResponseInfo(
        status_code=status,
        headers=headers,
        call_id=call.call_id,
        resource_attempt_count=call.resource_attempt_count,
        redirect_count=call.redirect_count,
        auth_exchange_count=call.auth_exchange_count,
        network_send_count=call.network_send_count,
        network_send_budget_used=call.network_send_budget_used,
        auth_exchange_budget_used=call.auth_exchange_budget_used,
        auth_refresh_ids=call.auth_refresh_ids,
        auth_refresh_pending=call.auth_refresh_pending,
        elapsed=monotonic() - call.started,
        content_type=None if content_type is None else normalized(content_type),
        request_id=None if request_id_header is None else headers.get(request_id_header),
    )


def _encoded(content: object, media_type: str | None) -> tuple[EncodedAttempt | None, object]:
    """Split a body into the attempt of bytes, sent as they are, and an input that builds its own attempts, or UNSET."""
    if type(content) is bytes:
        return EncodedAttempt(content, media_type), UNSET
    return None, content


def _context(call: LogicalCallContext) -> BodyAttemptContext:
    return BodyAttemptContext(call_id=call.call_id, attempt_index=0, hop_index=0, remaining_timeout=call.remaining())


def _attempt(content: object, call: LogicalCallContext) -> BodyAttempt:
    """Return what a synchronous file, stream, factory, or multipart body builds for an attempt; refuse the rest."""
    match content:
        case FileBody() | StreamBody() | BodyFactory():
            return content(_context(call))
        case MultipartSource():
            return content.attempt(_context(call))
        case _:
            pass
    raise RequestEncodingError(location=("body",), cause=TypeError(_BINARY))


async def _aattempt(content: object, call: LogicalCallContext) -> AsyncBodyAttempt:
    """Return what an async file, stream, factory, or multipart body builds for an attempt; refuse the rest."""
    match content:
        case AsyncFileBody() | AsyncStreamBody() | AsyncBodyFactory():
            return await content(_context(call))
        case MultipartSource():
            return await content.aattempt(_context(call))
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
        data = decoder.decode(
            info,
            body.content,
            truncated=truncated,
            problem=problem,
            native=settings.validation.response == "native",
        )
    except SDKError as error:
        error.operation_id = error.operation_id or operation_id
        error.call_id = error.call_id or info.call_id
        raise
    return Response(data=data, info=info)


RAW_DECODER: Final[ResponseDecoder[object, object]] = ResponseDecoder((), (), HTTPStatusError)


class _Shared(Generic[AdapterT]):
    """What a client shares with its views: the transport, the fixed headers, and whether the transport was closed."""

    __slots__ = ("adapter", "adapter_closed", "closing_tasks", "fixed", "loop", "modes", "trusted")

    def __init__(self, defaults: ClientDefaults, adapter: AdapterT, *, trusted: bool) -> None:
        agent = defaults.user_agent
        self.adapter = adapter
        self.trusted = trusted
        self.modes = defaults.validation
        self.fixed = (_ACCEPT_ENCODING,) if agent is None else (("User-Agent", agent), _ACCEPT_ENCODING)
        self.adapter_closed = False
        self.closing_tasks: dict[Scope[AsyncRawResponse], asyncio.Task[list[Exception]]] | None = None
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
        return type(self)(
            self._shared, _layered(self._settings, options, self._shared.modes), self._scope.view(), owned=False
        )

    def _admitted(self, call: LogicalCallContext) -> None:
        call.check()
        try:
            self._scope.admit()
        except SDKError as error:
            raise call.failure(error) from None

    @staticmethod
    def _failure(error: BaseException, call: LogicalCallContext, delivery: DeliveryState) -> BaseException:
        """Classify a transport failure, honoring cancellation, closing and deadline precedence."""
        selected = call.failure(error, delivery_state=delivery)
        if not isinstance(selected, Exception):
            return selected
        if isinstance(selected, TransportError) and isinstance(selected.cause, httpx2.TimeoutException):
            return call.timeout_failure(selected.cause, selected.phase, selected.delivery_state)
        if isinstance(selected, SDKError):
            return selected
        return call.snapshot_error(AdapterExecutionError(delivery_state=delivery, cause=selected))

    def _response_info(
        self,
        response: TransportResponse | AsyncTransportResponse,
        trace: AttemptTrace,
        request_id_header: str | None,
        call: LogicalCallContext,
    ) -> ResponseInfo:
        """Return received metadata only after checking the response and the call's termination signals."""
        call.delivery_state = DeliveryState.RESPONSE_STARTED
        status, headers = (
            (response.status_code, response.headers)
            if self._shared.trusted
            else _head(response.status_code, response.headers, trace)
        )
        info = _info(status, headers, request_id_header, call)
        try:
            call.check("send")
        except SDKError as error:
            error.info = info
            raise
        return info

    def _raw_prepared(
        self, method: object, url: object, body: object, options: RequestOptions | None
    ) -> tuple[PreparedRequest[EncodedAttempt], object]:
        """Return a raw call's request to any URL, with the client's fixed headers and a factory's media type.

        Bytes are the request's attempt; any other body is returned beside it, to build its own attempt.
        """
        verb, target = _checked_raw(method, url)
        if self._settings.query or (options is not None and options.query):
            base, _, explicit = target.partition("?")
            call = () if options is None else options.query
            query = _query(self._settings.query, [pair for pair in explicit.split("&") if pair], call)
            target = f"{base}?{query}" if query else base
        media_type = body.content_type if isinstance(body, (BodyFactory, AsyncBodyFactory)) else None
        if is_multipart(body):
            body = MultipartSource(body, boundary := new_boundary())
            media_type = f"multipart/form-data; boundary={boundary}"
        fixed = self._shared.fixed
        call = () if options is None else options.headers
        if self._settings.headers or call:
            if media_type is not None:
                _unframed((*self._settings.headers, call), media_type, None, None)
            headers = _headers([*fixed], (*self._settings.headers, call), media_type)
        else:
            headers = HeadersView(fixed if media_type is None else (*fixed, ("Content-Type", media_type)))
        attempt, deferred = _encoded(body, None)
        return PreparedRequest(method=verb, url=target, headers=headers, body=attempt), deferred

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

    def _call_settings(self, options: object, operation_id: str | None) -> Settings:
        """Return the settings a call runs with: this client's or view's, with the call's options layered on them."""
        if options is None:
            return self._settings
        if not isinstance(options, RequestOptions):
            if (closed := self._scope.closing()) is not None:
                raise closed
            raise ConfigurationError(field_path=("options",), condition="invalid_type", operation_id=operation_id)
        return _layered(self._settings, options, self._shared.modes, operation_id)

    def _prepare(  # noqa: PLR0913
        self,
        operation: OperationPlan[object, object],
        arguments: tuple[object, ...],
        settings: Settings,
        *,
        body: object,
        media_type: str | MediaSelector | None,
        options: RequestOptions | None,
        accept: str | None,
        narrowed: bool,
    ) -> tuple[PreparedRequest[EncodedAttempt], object]:
        """Return a call's request, and its body input when that builds its own attempts or else UNSET.

        Header patches apply in layers: the client's and views' over the generated headers, the parameters' over those,
        and the call's last; the body's media type and a narrowed Accept stay as the call chose them.
        """
        validation = settings.validation
        if validation.arguments == "pydantic":
            arguments, body = operation.checked(arguments, body, media_type)
        request = _parameters(operation, arguments, validation.request)
        encoded = (
            None
            if operation.body is None
            else operation.body.encode(
                operation.operation_id, body, media_type, operation.codecs, mode=validation.request
            )
        )
        base = self._base(operation, settings)
        path = request.path
        route = _PLACEHOLDER.sub(lambda match: path[match[1]], operation.path) if path else operation.path
        query = self._call_query(operation, request.query, options)
        headers = [*self._shared.fixed]
        if accept is not None:
            headers.append(("Accept", accept))
        if request.cookies:
            request.headers.append(("Cookie", "; ".join(request.cookies)))
        url = f"{base}{route}{'?' if query else ''}{query}"
        attempt: EncodedAttempt | None = None
        deferred: object = UNSET
        sent = None if encoded is None else encoded.media_type
        prepared = self._call_headers(
            headers,
            request.headers,
            options,
            media_type=sent,
            accept=accept if narrowed else None,
            operation_id=operation.operation_id,
        )
        if encoded is not None:
            attempt, deferred = _encoded(encoded.content, encoded.media_type)
        return PreparedRequest(method=operation.method, url=url, headers=prepared, body=attempt), deferred

    def _call_query(
        self, operation: OperationPlan[object, object], pairs: list[str], options: RequestOptions | None
    ) -> str:
        """Return a typed call's query: its parameters' pairs, patched when a layer patches them.

        An operation whose querystring parameter carries its whole query takes no query patch.
        """
        call = () if options is None else options.query
        if not self._settings.query and not call:
            return "&".join(pairs)
        if any(spec.plan.location == "querystring" for spec in operation.parameters):
            raise ConfigurationError(
                field_path=("query",), condition="conflicts_with_querystring", operation_id=operation.operation_id
            )
        return _query(self._settings.query, pairs, call)

    def _call_headers(  # noqa: PLR0913
        self,
        generated: list[tuple[str, str]],
        params: list[tuple[str, str]],
        options: RequestOptions | None,
        *,
        media_type: str | None,
        accept: str | None,
        operation_id: str | None,
    ) -> HeadersView:
        """Return a typed call's headers: the parameters' over the generated ones, patched when a layer patches them."""
        call = () if options is None else options.headers
        if not self._settings.headers and not call:
            generated.extend(params)
            if media_type is not None:
                generated.append(("Content-Type", media_type))
            return HeadersView(generated)
        _unframed((*self._settings.headers, call), media_type, accept, operation_id)
        return _headers(generated, (*self._settings.headers, params, call), media_type)


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


def _released(close: Callable[[], None], operation_id: str | None, call_id: str) -> None:
    try:
        close()
    except CleanupError:
        raise
    except Exception as failure:  # noqa: BLE001
        raise CleanupError(operation_id=operation_id, call_id=call_id, cause=failure) from None


def _discarded(close: Callable[[], object], error: BaseException) -> None:
    """Run a close while an error propagates, keeping its failure beside that error."""
    if (failure := _quietly(close)) is not None:
        cleanup_secondary(error, failure)


async def _adiscarded(close: Callable[[], Awaitable[None]], error: BaseException) -> None:
    """Await a close while an error propagates, keeping its failure beside that error."""
    if (failure := await _aquietly(close)) is not None:
        cleanup_secondary(error, failure)


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


def _close_observed(task: asyncio.Task[list[Exception]]) -> None:
    """Retrieve a retained finalizer's exception even after its last waiting caller leaves."""
    if not task.cancelled():
        task.exception()


async def _aclose_scope(
    scope: Scope[AsyncRawResponse], shared: _Shared[AsyncTransportAdapter], timeout: float, *, owned: bool
) -> list[Exception]:
    """Own draining and resource release until completion independently of close callers."""
    try:
        remaining = await scope.adrain(timeout)
        closes: list[Callable[[], Awaitable[None]]] = [handle.aclose for handle in remaining]
        if owned and not shared.adapter_closed:
            shared.adapter_closed = True
            closes.append(shared.adapter.aclose)
        return [failure for close in closes if (failure := await _aquietly(close)) is not None]
    except BaseException as interrupted:  # noqa: BLE001
        raise TaskInterruptionError(interrupted) from None


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


def _is_limiter(value: object) -> TypeIs[Limiter]:
    acquire = getattr(value, "acquire", None)
    return callable(acquire) and not inspect.iscoroutinefunction(acquire)


def _is_async_limiter(value: object) -> TypeIs[AsyncLimiter]:
    return inspect.iscoroutinefunction(getattr(value, "acquire", None))


def _is_permit(value: object) -> TypeIs[Permit]:
    release = getattr(value, "release", None)
    return callable(release) and not inspect.iscoroutinefunction(release)


def _is_async_permit(value: object) -> TypeIs[AsyncPermit]:
    return inspect.iscoroutinefunction(getattr(value, "release", None))


def _limiter_context(call: LogicalCallContext, url: str) -> LimiterContext:
    parts = urlsplit(url)
    return LimiterContext(
        operation_id=call.operation_id,
        origin=f"{parts.scheme}://{parts.netloc}",
        call_id=call.call_id,
        parent_session_id=None,
        remaining_timeout=call.remaining(),
        cancel_token=call.settings.cancel_token,
    )


def _release_permit(permit: Permit) -> None:
    try:
        permit.release()
    except Exception as error:  # noqa: BLE001
        raise LimiterExecutionError(action="release", cause=error) from None


async def _arelease_permit(permit: AsyncPermit) -> None:
    try:
        await permit.release()
    except Exception as error:  # noqa: BLE001
        raise LimiterExecutionError(action="release", cause=error) from None


def _acquire(limiter: Limiter | AsyncLimiter, call: LogicalCallContext, url: str, events: CallEvents | None) -> Permit:
    """Acquire a mode-correct permit, leaving all post-acquisition work to its owner."""
    call.check("limiter")
    if not _is_limiter(limiter):
        raise ConfigurationError(field_path=("limiter",), condition="async_limiter")
    if events is not None:
        events.origin = _limiter_context(call, url).origin
        events.emit(events.event("limiter_wait"))
    try:
        permit = limiter.acquire(_limiter_context(call, url))
    except Exception as error:  # noqa: BLE001
        raise LimiterExecutionError(action="acquire", cause=error) from None
    if not _is_permit(permit):
        raise LimiterExecutionError(action="acquire", cause=TypeError("Expected a synchronous Permit"))
    return permit


async def _aacquire(
    limiter: Limiter | AsyncLimiter, call: LogicalCallContext, url: str, events: CallEvents | None
) -> AsyncPermit:
    """Acquire an async permit in the monitored task that will own every late grant."""
    call.check("limiter")
    if not _is_async_limiter(limiter):
        raise ConfigurationError(field_path=("limiter",), condition="sync_limiter")
    if events is not None:
        events.origin = _limiter_context(call, url).origin
        await events.aemit(events.event("limiter_wait"))
    try:
        permit = await limiter.acquire(_limiter_context(call, url))
    except Exception as error:  # noqa: BLE001
        raise LimiterExecutionError(action="acquire", cause=error) from None
    if not _is_async_permit(permit):
        raise LimiterExecutionError(action="acquire", cause=TypeError("Expected an asynchronous Permit"))
    return permit


def _read_chunks(chunks: Iterable[bytes], call: LogicalCallContext, *, trusted: bool) -> Iterator[bytes]:
    source = iter(chunks if trusted else _raw(chunks))
    while True:
        call.check("send")
        try:
            chunk = next(source)
        except StopIteration:
            call.check("send")
            break
        call.check("send")
        yield chunk


class _CheckedBody:
    """Check a call at both boundaries of each synchronous request-body chunk."""

    __slots__ = ("body", "call")

    def __init__(self, body: BodyAttempt, call: LogicalCallContext) -> None:
        self.body = body
        self.call = call

    @property
    def content_length(self) -> int | None:
        return self.body.content_length

    @property
    def content_type(self) -> str | None:
        return self.body.content_type

    def iter_bytes(self) -> Iterator[bytes]:
        yield from _read_chunks(self.body.iter_bytes(), self.call, trusted=True)

    def close(self) -> None:
        self.body.close()


class _AsyncCheckedBody:
    """Check a call at both boundaries of each asynchronous request-body chunk."""

    __slots__ = ("body", "call")

    def __init__(self, body: AsyncBodyAttempt, call: LogicalCallContext) -> None:
        self.body = body
        self.call = call

    @property
    def content_length(self) -> int | None:
        return self.body.content_length

    @property
    def content_type(self) -> str | None:
        return self.body.content_type

    async def aiter_bytes(self) -> AsyncIterator[bytes]:
        source = self.body.aiter_bytes()
        while True:
            self.call.check("send")
            try:
                chunk = await anext(source)
            except StopAsyncIteration:
                self.call.check("send")
                break
            self.call.check("send")
            yield chunk

    async def aclose(self) -> None:
        await self.body.aclose()


class _LimitedResponse:
    """Keep a permit with a response whose call or raw handle owns its single release."""

    __slots__ = ("permit", "response")

    def __init__(self, response: TransportResponse, permit: Permit) -> None:
        self.response, self.permit = response, permit

    @property
    def status_code(self) -> int:
        return self.response.status_code

    @property
    def headers(self) -> HeadersView:
        return self.response.headers

    def iter_raw_bytes(self) -> Iterator[bytes]:
        return self.response.iter_raw_bytes()

    def close(self) -> None:
        try:
            self.response.close()
        except BaseException as error:
            _discarded(partial(_release_permit, self.permit), error)
            raise
        try:
            _release_permit(self.permit)
        except Exception as error:  # noqa: BLE001
            raise CleanupError(cause=error) from None


class _AsyncLimitedResponse:
    """Keep an asynchronous permit with a response whose owner releases it once."""

    __slots__ = ("permit", "response")

    def __init__(self, response: AsyncTransportResponse, permit: AsyncPermit) -> None:
        self.response, self.permit = response, permit

    @property
    def status_code(self) -> int:
        return self.response.status_code

    @property
    def headers(self) -> HeadersView:
        return self.response.headers

    def iter_raw_bytes(self) -> AsyncIterator[bytes]:
        return self.response.iter_raw_bytes()

    async def aclose(self) -> None:
        try:
            await self.response.aclose()
        except BaseException as error:
            await _adiscarded(partial(_arelease_permit, self.permit), error)
            raise
        try:
            await _arelease_permit(self.permit)
        except Exception as error:  # noqa: BLE001
            raise CleanupError(cause=error) from None


class ClientCore(_Core["TransportAdapter", "RawResponse"]):
    """Run the calls of a synchronous client and its views through one transport adapter."""

    __slots__ = ()

    def _started(self, call: LogicalCallContext, path: str | None) -> CallEvents | None:
        """Admit a call, reporting both boundary events when it is already stopped."""
        events = call_events(call.settings, call=call, path=path, asynchronous=False)
        try:
            self._admitted(call)
        except BaseException as error:  # noqa: BLE001
            failure = call.failure(error)
            try:
                if events is not None:
                    for secondary in events.notify(events.starting(call.settings), terminal=True):
                        add_secondary(failure, secondary)
                    events.ended(failure)
                raise failure from None
            finally:
                call.finish()
        return events

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
        settings = _client_settings(options, defaults)
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
        fields: tuple[object, ...] = (),
        media_type: str | MediaSelector | None = None,
        options: RequestOptions | None = None,
        response_media_type: str | MediaSelector | None = None,
    ) -> Response[T]:
        """Send one logical call, keeping its deadline active through decoding and terminal hooks."""
        call = LogicalCallContext(
            self._call_settings(options, operation.operation_id), self._scope, operation.operation_id
        )
        events = self._started(call, operation.path)
        try:
            settings = call.settings
            if events is not None:
                events.emit(events.starting(settings))
            call.check("encode")
            if fields:
                body = operation.bound(body, fields, media_type)
            decoder = self._decoder(operation, response_media_type)
            request, deferred = self._prepare(
                operation,
                arguments,
                settings,
                body=body,
                media_type=media_type,
                options=options,
                accept=decoder.accept,
                narrowed=response_media_type is not None,
            )
            call.check("encode")
            trace = AttemptTrace()
            response = self._send(request, deferred, trace, call, events=events)
            try:
                info = self._response_info(response, trace, operation.request_id_header, call)
                if events is not None:
                    events.emit(events.responding(info))
                received = self._read(response, info, decoder, call)
                call.check("decode")
                result = _completed(decoder, info, received, settings, call.operation_id)
                call.check("decode")
            except BaseException as error:  # noqa: BLE001
                failure = (
                    self._failure(error, call, DeliveryState.RESPONSE_STARTED)
                    if isinstance(error, Exception)
                    else error
                )
                _discarded(response.close, failure)
                raise failure from None
            _released(response.close, call.operation_id, call.call_id)
            call.check("decode")
            if events is not None:
                events.finish(result)
            call.check("decode")
        except BaseException as error:  # noqa: BLE001
            failure = call.failure(error)
            if events is not None:
                events.ended(failure)
            raise failure from None
        else:
            return result
        finally:
            self._scope.release()
            call.finish()

    def execute_raw(  # noqa: PLR0913
        self,
        operation: OperationPlan[object, object],
        arguments: tuple[object, ...],
        *,
        body: object = UNSET,
        fields: tuple[object, ...] = (),
        media_type: str | MediaSelector | None = None,
        options: RequestOptions | None = None,
        response_media_type: str | MediaSelector | None = None,
        stream: bool = False,
    ) -> RawResponse:
        """Prepare a logical call and return its buffered or streaming raw response."""
        call = LogicalCallContext(
            self._call_settings(options, operation.operation_id), self._scope, operation.operation_id
        )
        events = self._started(call, operation.path)
        try:
            settings = call.settings
            if events is not None:
                events.emit(events.starting(settings))
            call.check("encode")
            if fields:
                body = operation.bound(body, fields, media_type)
            decoder = self._decoder(operation, response_media_type)
            request, deferred = self._prepare(
                operation,
                arguments,
                settings,
                body=body,
                media_type=media_type,
                options=options,
                accept=decoder.accept,
                narrowed=response_media_type is not None,
            )
            call.check("encode")
        except BaseException as error:  # noqa: BLE001
            self._scope.release()
            try:
                failure = call.failure(error)
                if events is not None:
                    events.ended(failure)
                raise failure from None
            finally:
                call.finish()
        return self._raw(
            request,
            deferred,
            decoder,
            call,
            request_id_header=operation.request_id_header,
            stream=stream,
            events=events,
        )

    def stream(  # noqa: PLR0913
        self,
        operation: OperationPlan[object, object],
        arguments: tuple[object, ...],
        *,
        body: object = UNSET,
        fields: tuple[object, ...] = (),
        media_type: str | MediaSelector | None = None,
        options: RequestOptions | None = None,
        response_media_type: str | MediaSelector | None = None,
    ) -> AbstractContextManager[RawResponse]:
        """Return a block that sends one call on entry and yields its streaming response until exit.

        The call's arguments bind when the block is made, before it sends anything.
        """
        if fields:
            body = operation.bound(body, fields, media_type)
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
        """Send an arbitrary URL under the same deadlines and send budget as a typed call."""
        call = LogicalCallContext(self._call_settings(options, None), self._scope)
        events = self._started(call, None)
        try:
            if events is not None:
                events.emit(events.starting(call.settings))
            call.check("encode")
            request, deferred = self._raw_prepared(method, url, body, options)
            call.check("encode")
        except BaseException as error:  # noqa: BLE001
            self._scope.release()
            try:
                failure = call.failure(error)
                if events is not None:
                    events.ended(failure)
                raise failure from None
            finally:
                call.finish()
        return self._raw(
            request,
            deferred,
            RAW_DECODER,
            call,
            request_id_header=None,
            stream=stream,
            events=events,
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
        call: LogicalCallContext,
        *,
        request_id_header: str | None,
        stream: bool,
        events: CallEvents | None,
    ) -> RawResponse:
        """Transfer a response and any permit only after successful terminal hooks."""
        scope = self._scope
        handle: RawResponse | None = None
        handed = False
        try:
            trace = AttemptTrace()
            response = self._send(request, deferred, trace, call, events=events)
            try:
                info = self._response_info(response, trace, request_id_header, call)
                if events is not None:
                    events.emit(events.responding(info))
            except BaseException as error:  # noqa: BLE001
                failure = (
                    self._failure(error, call, DeliveryState.RESPONSE_STARTED)
                    if isinstance(error, Exception)
                    else error
                )
                _discarded(response.close, failure)
                raise failure from None
            source = response.iter_raw_bytes if self._shared.trusted else partial(_checked_chunks, response)
            handle = RawResponse(
                info,
                decoder,
                call.settings,
                call.operation_id,
                lambda error: self._failure(error, call, DeliveryState.RESPONSE_STARTED),
                source=source,
                close=response.close,
                scope=scope,
                events=events if stream else None,
                call=call,
            )
            scope.handoff(handle)
            handed = True
            if not stream:
                handle.read()
            call.check("send")
            if events is not None:
                events.finish(UNSET, handed_off=stream)
            call.check("send")
            if stream:
                call.handoff()
        except BaseException as error:  # noqa: BLE001
            failure = call.failure(error)
            if handle is not None:
                handle.discard(failure)
            if events is not None:
                events.ended(failure)
            raise failure from None
        else:
            return handle
        finally:
            if not handed:
                scope.release()
            if not call.streaming:
                call.finish()

    def _send(
        self,
        request: PreparedRequest[BodyAttempt],
        deferred: object,
        trace: AttemptTrace,
        call: LogicalCallContext,
        *,
        events: CallEvents | None,
    ) -> TransportResponse:
        """Acquire a permit, open the body, send once, and transfer permit ownership with the response."""
        attempt = request.body
        permit: Permit | None = None
        try:
            if (limiter := call.settings.limiter) is not None:
                permit = _acquire(limiter, call, request.url, events)
                call.check("limiter")
                if events is not None:
                    events.emit(events.event("limiter_acquired"))
            call.check("encode")
            if not isinstance(deferred, Unset):
                attempt = _attempt(deferred, call)
            call.check("encode")
            if attempt is not None:
                request = PreparedRequest(
                    method=request.method,
                    url=request.url,
                    headers=request.headers,
                    body=_CheckedBody(attempt, call),
                )
            if events is not None:
                events.emit(events.attempting(request.url))
            io = call.io_context(trace)
            call.admit_send()
            if events is not None:
                events.sending()
            response = self._shared.adapter.send(request, io)
        except BaseException as error:  # noqa: BLE001
            failure = (
                self._failure(error, call, _delivery(trace, self._shared.adapter.capabilities))
                if isinstance(error, Exception)
                else error
            )
            if attempt is not None:
                _discarded(attempt.close, failure)
            if permit is not None:
                _discarded(partial(_release_permit, permit), failure)
            raise failure from None
        if permit is not None:
            response = _LimitedResponse(response, permit)
        if attempt is not None:
            try:
                _released(attempt.close, call.operation_id, call.call_id)
            except BaseException as error:  # noqa: BLE001
                failure = (
                    self._failure(error, call, DeliveryState.RESPONSE_STARTED)
                    if isinstance(error, Exception)
                    else error
                )
                _discarded(response.close, failure)
                raise failure from None
        return response

    def _read(
        self,
        response: TransportResponse,
        info: ResponseInfo,
        decoder: ResponseDecoder[object, object],
        call: LogicalCallContext,
    ) -> _Body:
        """Read a bounded response body while checking the logical deadline at each chunk."""
        received = _received(decoder, info.status_code, call.settings)
        raw = response.iter_raw_bytes()
        chunks = ContentDecoder(info, call.operation_id).decoded(_read_chunks(raw, call, trusted=self._shared.trusted))
        try:
            for chunk in chunks:
                call.check("send")
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

    async def _started(self, call: LogicalCallContext, path: str | None) -> CallEvents | None:
        """Admit a call, reporting both boundary events when it is already stopped."""
        events = call_events(call.settings, call=call, path=path, asynchronous=True)
        try:
            self._admitted(call)
        except BaseException as error:  # noqa: BLE001
            failure = call.failure(error)
            try:
                if events is not None:
                    await events.aended(failure, starting=True)
                raise failure from None
            finally:
                call.finish()
        return events

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
        settings = _client_settings(options, defaults)
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
        fields: tuple[object, ...] = (),
        media_type: str | MediaSelector | None = None,
        options: RequestOptions | None = None,
        response_media_type: str | MediaSelector | None = None,
    ) -> Response[T]:
        """Send one logical call, keeping its deadline active through decoding and terminal hooks."""
        call = LogicalCallContext(
            self._call_settings(options, operation.operation_id), self._scope, operation.operation_id
        )
        self._running(call.operation_id, call.call_id)
        events = await self._started(call, operation.path)
        try:
            settings = call.settings
            if events is not None:
                await events.aemit(events.starting(settings))
            call.check("encode")
            if fields:
                body = operation.bound(body, fields, media_type)
            decoder = self._decoder(operation, response_media_type)
            request, deferred = self._prepare(
                operation,
                arguments,
                settings,
                body=body,
                media_type=media_type,
                options=options,
                accept=decoder.accept,
                narrowed=response_media_type is not None,
            )
            call.check("encode")
            result = await call.bounded(
                lambda: self._complete_response(
                    request, deferred, decoder, call, request_id_header=operation.request_id_header, events=events
                )
            )
            call.check("decode")
            if events is not None:
                await events.afinish(result)
            call.check("decode")
        except BaseException as error:  # noqa: BLE001
            failure = call.failure(error)
            if events is not None:
                await events.aended(failure)
            raise failure from None
        else:
            return result
        finally:
            self._scope.release()
            call.finish()

    async def execute_raw(  # noqa: PLR0913
        self,
        operation: OperationPlan[object, object],
        arguments: tuple[object, ...],
        *,
        body: object = UNSET,
        fields: tuple[object, ...] = (),
        media_type: str | MediaSelector | None = None,
        options: RequestOptions | None = None,
        response_media_type: str | MediaSelector | None = None,
        stream: bool = False,
    ) -> AsyncRawResponse:
        """Prepare a logical call and return its buffered or streaming raw response."""
        call = LogicalCallContext(
            self._call_settings(options, operation.operation_id), self._scope, operation.operation_id
        )
        self._running(call.operation_id, call.call_id)
        events = await self._started(call, operation.path)
        try:
            settings = call.settings
            if events is not None:
                await events.aemit(events.starting(settings))
            call.check("encode")
            if fields:
                body = operation.bound(body, fields, media_type)
            decoder = self._decoder(operation, response_media_type)
            request, deferred = self._prepare(
                operation,
                arguments,
                settings,
                body=body,
                media_type=media_type,
                options=options,
                accept=decoder.accept,
                narrowed=response_media_type is not None,
            )
            call.check("encode")
        except BaseException as error:  # noqa: BLE001
            self._scope.release()
            try:
                failure = call.failure(error)
                if events is not None:
                    await events.aended(failure)
                raise failure from None
            finally:
                call.finish()
        return await self._raw(
            request,
            deferred,
            decoder,
            call,
            request_id_header=operation.request_id_header,
            stream=stream,
            events=events,
        )

    def stream(  # noqa: PLR0913
        self,
        operation: OperationPlan[object, object],
        arguments: tuple[object, ...],
        *,
        body: object = UNSET,
        fields: tuple[object, ...] = (),
        media_type: str | MediaSelector | None = None,
        options: RequestOptions | None = None,
        response_media_type: str | MediaSelector | None = None,
    ) -> AbstractAsyncContextManager[AsyncRawResponse]:
        """Return a block that sends one call on entry and yields its streaming response until exit.

        The call's arguments bind when the block is made, before it sends anything.
        """
        if fields:
            body = operation.bound(body, fields, media_type)
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
        """Send an arbitrary URL under the same deadlines and send budget as a typed call."""
        call = LogicalCallContext(self._call_settings(options, None), self._scope)
        self._running(call.operation_id, call.call_id)
        events = await self._started(call, None)
        try:
            if events is not None:
                await events.aemit(events.starting(call.settings))
            call.check("encode")
            request, deferred = self._raw_prepared(method, url, body, options)
            call.check("encode")
        except BaseException as error:  # noqa: BLE001
            self._scope.release()
            try:
                failure = call.failure(error)
                if events is not None:
                    await events.aended(failure)
                raise failure from None
            finally:
                call.finish()
        return await self._raw(
            request,
            deferred,
            RAW_DECODER,
            call,
            request_id_header=None,
            stream=stream,
            events=events,
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
        call: LogicalCallContext,
        *,
        request_id_header: str | None,
        stream: bool,
        events: CallEvents | None,
    ) -> AsyncRawResponse:
        """Transfer a response and any permit only after successful terminal hooks."""
        scope = self._scope
        handle: AsyncRawResponse | None = None
        handed = False

        async def receive() -> AsyncRawResponse:
            nonlocal handle, handed
            trace = AttemptTrace()
            response = await self._send(request, deferred, trace, call, events=events)
            try:
                info = self._response_info(response, trace, request_id_header, call)
                if events is not None:
                    await events.aemit(events.responding(info))
            except BaseException as error:  # noqa: BLE001
                failure = (
                    self._failure(error, call, DeliveryState.RESPONSE_STARTED)
                    if isinstance(error, Exception)
                    else call.failure(error)
                )
                await call.cleanup(response.aclose, error=failure)
                raise failure from None
            source = response.iter_raw_bytes if self._shared.trusted else partial(_achecked_chunks, response)
            handle = AsyncRawResponse(
                info,
                decoder,
                call.settings,
                call.operation_id,
                lambda error: self._failure(error, call, DeliveryState.RESPONSE_STARTED),
                source=source,
                close=response.aclose,
                scope=scope,
                events=events if stream else None,
                call=call,
            )
            scope.handoff(handle)
            handed = True
            if not stream:
                await handle.read()
            return handle

        try:
            result = await call.bounded(receive, cleanup=lambda response: response.aclose())
            call.check("send")
            if events is not None:
                await events.afinish(UNSET, handed_off=stream)
            call.check("send")
            if stream:
                call.handoff()
        except BaseException as error:  # noqa: BLE001
            failure = call.failure(error)
            if handle is not None:
                await handle.discard(failure)
            if events is not None:
                await events.aended(failure)
            raise failure from None
        else:
            return result
        finally:
            if not handed:
                scope.release()
            if not call.streaming:
                call.finish()

    async def _send(
        self,
        request: PreparedRequest[AsyncBodyAttempt],
        deferred: object,
        trace: AttemptTrace,
        call: LogicalCallContext,
        *,
        events: CallEvents | None,
    ) -> AsyncTransportResponse:
        """Run one attempt under the call's deadline, retaining late responses for cleanup."""
        try:
            return await call.bounded(
                lambda: self._send_attempt(request, deferred, trace, call, events=events),
                phase="send",
                cleanup=lambda response: response.aclose(),
            )
        except Exception as error:  # noqa: BLE001
            raise self._failure(error, call, _delivery(trace, self._shared.adapter.capabilities)) from None

    async def _send_attempt(
        self,
        request: PreparedRequest[AsyncBodyAttempt],
        deferred: object,
        trace: AttemptTrace,
        call: LogicalCallContext,
        *,
        events: CallEvents | None,
    ) -> AsyncTransportResponse:
        """Keep acquired resources in the owned task even when a callback suppresses cancellation."""
        attempt = request.body
        permit: AsyncPermit | None = None
        try:
            if (limiter := call.settings.limiter) is not None:
                permit = await _aacquire(limiter, call, request.url, events)
                call.check("limiter")
                if events is not None:
                    await events.aemit(events.event("limiter_acquired"))
            call.check("encode")
            if not isinstance(deferred, Unset):
                attempt = await _aattempt(deferred, call)
            call.check("encode")
            if attempt is not None:
                request = PreparedRequest(
                    method=request.method,
                    url=request.url,
                    headers=request.headers,
                    body=_AsyncCheckedBody(attempt, call),
                )
            if events is not None:
                await events.aemit(events.attempting(request.url))
            io = call.io_context(trace)
            call.admit_send()
            if events is not None:
                events.sending()
            response = await self._shared.adapter.send(request, io)
        except BaseException as error:  # noqa: BLE001
            failure = (
                self._failure(error, call, _delivery(trace, self._shared.adapter.capabilities))
                if isinstance(error, Exception)
                else error
            )
            if attempt is not None:
                await call.cleanup(attempt.aclose, error=failure)
            if permit is not None:
                await call.cleanup(partial(_arelease_permit, permit), error=failure)
            raise failure from None
        if permit is not None:
            response = _AsyncLimitedResponse(response, permit)
        if attempt is not None:
            try:
                await call.cleanup(attempt.aclose)
            except BaseException as error:
                await call.cleanup(response.aclose, error=error)
                raise
        return response

    async def _complete_response(  # noqa: PLR0913
        self,
        request: PreparedRequest[AsyncBodyAttempt],
        deferred: object,
        decoder: ResponseDecoder[T, object],
        call: LogicalCallContext,
        *,
        request_id_header: str | None,
        events: CallEvents | None,
    ) -> Response[T]:
        """Own sending, reading and decoding in one monitored task, releasing its response on every path."""
        trace = AttemptTrace()
        response = await self._send(request, deferred, trace, call, events=events)
        try:
            info = self._response_info(response, trace, request_id_header, call)
            if events is not None:
                await events.aemit(events.responding(info))
            received = await self._read(response, info, decoder, call)
            call.check("decode")
            result = _completed(decoder, info, received, call.settings, call.operation_id)
            call.check("decode")
        except BaseException as error:  # noqa: BLE001
            failure = (
                self._failure(error, call, DeliveryState.RESPONSE_STARTED)
                if isinstance(error, Exception)
                else call.failure(error)
            )
            await call.cleanup(response.aclose, error=failure)
            raise failure from None
        await call.cleanup(response.aclose)
        return result

    async def _read(
        self,
        response: AsyncTransportResponse,
        info: ResponseInfo,
        decoder: ResponseDecoder[object, object],
        call: LogicalCallContext,
    ) -> _Body:
        """Read a bounded response body while checking the logical deadline at each chunk."""
        received = _received(decoder, info.status_code, call.settings)
        raw = response.iter_raw_bytes()
        chunks = ContentDecoder(info, call.operation_id).adecoded(raw if self._shared.trusted else _araw(raw))
        try:
            while True:
                try:
                    chunk = await call.bounded(
                        chunks.__anext__, phase="send", delivery_state=DeliveryState.RESPONSE_STARTED
                    )
                except StopAsyncIteration:
                    break
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
        shared = self._shared
        if (tasks := shared.closing_tasks) is None:
            tasks = shared.closing_tasks = {}
        if (task := tasks.get(scope)) is None:
            task = tasks[scope] = asyncio.create_task(_aclose_scope(scope, shared, timeout, owned=self._owned))
            task.add_done_callback(_close_observed)
        deadline = monotonic() + timeout
        await asyncio.wait({task}, timeout=timeout)
        if not task.done():
            pending, remaining = scope.pending()
            raise CleanupError(
                pending_calls=pending if pending or remaining else 1, pending_leases=remaining, timeout=timeout
            )
        await scope.adrain(max(0.0, deadline - monotonic()))
        pending, remaining = scope.pending()
        if not (pending or remaining):
            scope.finish()
            tasks.pop(scope, None)
        failures = task_result(task)
        if (error := _cleanup(pending, remaining, timeout, failures)) is not None:
            raise error
