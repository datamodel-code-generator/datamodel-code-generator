"""The shared core of generated clients: build each call's request, send it through HTTPX2, and decode the response."""

from __future__ import annotations

import re
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Final, Literal, TypeVar
from uuid import uuid4

import httpx2

from ..model_codecs.parameters import FragmentContribution, QueryStringContribution, encode_parameter
from ..model_codecs.unset import UNSET, Unset
from .errors import ConfigurationError, DeliveryState, RequestEncodingError, ResponseTooLargeError, TransportError
from .operations import DATA_ERRORS, normalized
from .options import ClientOptions, RequestOptions, ServerSelection, checked_base_url
from .responses import HeadersView, Response, ResponseInfo

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from ..model_codecs.parameters import ParameterFragment
    from .errors import IOPhase
    from .operations import OperationPlan, ResponseDecoder, ServerPlan

T = TypeVar("T")

MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024
MAX_ERROR_BODY_BYTES: Final = 64 * 1024
_ACCEPT_ENCODING: Final = (b"Accept-Encoding", b"gzip, deflate")
_TIMEOUT: Final = {"connect": 5.0, "read": 30.0, "write": 30.0, "pool": 5.0}
_PLACEHOLDER: Final = re.compile(r"\{([^{}]*)\}")
_OWNERSHIPS: Final = frozenset({"borrowed", "owned"})
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


_DEFAULT_SERVER: Final = ServerSelection()
_DEFAULT_SETTINGS: Final = _Settings(None, _DEFAULT_SERVER, MAX_RESPONSE_BYTES, MAX_ERROR_BODY_BYTES)


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
    )


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
        self.headers: list[tuple[bytes, bytes]] = []
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
                self.headers.extend((fragment.name or b"", fragment.value) for fragment in fragments)
            case _:
                self.cookies.extend(_pairs(fragments))


def _pairs(fragments: tuple[ParameterFragment, ...]) -> Iterator[str]:
    return (f"{(fragment.name or b'').decode('ascii')}={fragment.value.decode('ascii')}" for fragment in fragments)


class _Body:
    __slots__ = ("chunks", "limit", "overflow", "size", "success", "truncated")

    def __init__(self, limit: int | None, *, success: bool) -> None:
        self.limit = limit
        self.success = success
        self.chunks: list[bytes] = []
        self.size = 0
        self.truncated = False
        self.overflow = False

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


def _phase(error: httpx2.TransportError) -> tuple[IOPhase, DeliveryState]:
    for kind, phase, state in _PHASES:
        if isinstance(error, kind):
            return phase, state
    return _UNKNOWN


def _transport_error(
    error: httpx2.TransportError, operation: OperationPlan[object, object], call_id: str, *, started: bool
) -> TransportError:
    phase, state = _phase(error)
    return TransportError(
        delivery_state=DeliveryState.RESPONSE_STARTED if started else state,
        phase=phase,
        operation_id=operation.operation_id,
        call_id=call_id,
        cause=error,
    )


def _info(
    response: httpx2.Response, operation: OperationPlan[object, object], call_id: str, started: float
) -> ResponseInfo:
    headers = HeadersView(response.headers.multi_items())
    content_type = headers.get("content-type")
    return ResponseInfo(
        status_code=response.status_code,
        headers=headers,
        call_id=call_id,
        elapsed=monotonic() - started,
        content_type=None if content_type is None else normalized(content_type),
        request_id=None if operation.request_id_header is None else headers.get(operation.request_id_header),
    )


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
    return Response(data=decoder.decode(info, body.content, truncated=body.truncated), info=info)


class _Core:
    __slots__ = ("_closed", "_fixed", "_owned", "_settings", "_urls")

    def __init__(self, defaults: ClientDefaults, options: object, ownership: object) -> None:
        if options is not None and not isinstance(options, ClientOptions):
            raise ConfigurationError(field_path=("options",), condition="invalid_type")
        if ownership not in _OWNERSHIPS:
            raise ConfigurationError(field_path=("http_client_ownership",), condition="invalid_value")
        self._settings = _DEFAULT_SETTINGS if options is None else _layered(_DEFAULT_SETTINGS, options)
        agent = defaults.user_agent
        self._fixed = (
            (_ACCEPT_ENCODING,) if agent is None else ((b"User-Agent", agent.encode("ascii")), _ACCEPT_ENCODING)
        )
        self._urls: dict[int, tuple[tuple[ServerPlan, ...], str]] = {}
        self._owned = ownership == "owned"
        self._closed = False

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
    ) -> tuple[httpx2.Request, _Settings]:
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
        headers = [*self._fixed]
        if accept is not None:
            headers.append((b"Accept", accept.encode("ascii")))
        headers.extend(request.headers)
        if request.cookies:
            headers.append((b"Cookie", "; ".join(request.cookies).encode("ascii")))
        if encoded is not None:
            headers.append((b"Content-Type", encoded.media_type.encode("ascii")))
        return httpx2.Request(
            operation.method,
            f"{base}{route}{'?' if query else ''}{query}",
            headers=headers,
            content=None if encoded is None else encoded.content,
            extensions={"timeout": dict(_TIMEOUT)},
        ), settings


class ClientCore(_Core):
    """Send the calls of a synchronous client through one HTTPX2 client."""

    __slots__ = ("_http",)

    def __init__(
        self,
        defaults: ClientDefaults,
        *,
        options: ClientOptions | None = None,
        http_client: httpx2.Client | Unset = UNSET,
        http_client_ownership: Literal["borrowed", "owned"] = "borrowed",
    ) -> None:
        """Borrow the given HTTPX2 client unless ownership is transferred, or create and own one."""
        super().__init__(defaults, options, http_client_ownership)
        match http_client:
            case httpx2.Client():
                self._http = http_client
            case Unset():
                self._http = httpx2.Client(trust_env=False)
                self._owned = True
            case _:
                raise ConfigurationError(field_path=("http_client",), condition="invalid_type")

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
        decoder = self._decoder(operation, response_media_type)
        request, settings = self._prepare(
            operation, arguments, body=body, media_type=media_type, options=options, accept=decoder.accept
        )
        call_id = str(uuid4())
        started = monotonic()
        try:
            response = self._http.send(request, stream=True, auth=None, follow_redirects=False)
        except httpx2.TransportError as error:
            raise _transport_error(error, operation, call_id, started=False) from None
        try:
            received = _received(decoder, response.status_code, settings)
            for chunk in _chunks(response.iter_bytes(), operation, call_id):
                if not received.add(chunk):
                    break
            info = _info(response, operation, call_id, started)
        finally:
            response.close()
        return _completed(decoder, info, received, settings)

    def close(self) -> None:
        """Close the HTTPX2 client when this client owns it."""
        if not self._closed:
            self._closed = True
            if self._owned:
                self._http.close()


def _chunks(chunks: Iterator[bytes], operation: OperationPlan[object, object], call_id: str) -> Iterator[bytes]:
    try:
        yield from chunks
    except httpx2.TransportError as error:
        raise _transport_error(error, operation, call_id, started=True) from None


class AsyncClientCore(_Core):
    """Send the calls of an asyncio client through one HTTPX2 async client."""

    __slots__ = ("_http",)

    def __init__(
        self,
        defaults: ClientDefaults,
        *,
        options: ClientOptions | None = None,
        http_client: httpx2.AsyncClient | Unset = UNSET,
        http_client_ownership: Literal["borrowed", "owned"] = "borrowed",
    ) -> None:
        """Borrow the given HTTPX2 async client unless ownership is transferred, or create and own one."""
        super().__init__(defaults, options, http_client_ownership)
        match http_client:
            case httpx2.AsyncClient():
                self._http = http_client
            case Unset():
                self._http = httpx2.AsyncClient(trust_env=False)
                self._owned = True
            case _:
                raise ConfigurationError(field_path=("http_client",), condition="invalid_type")

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
        decoder = self._decoder(operation, response_media_type)
        request, settings = self._prepare(
            operation, arguments, body=body, media_type=media_type, options=options, accept=decoder.accept
        )
        call_id = str(uuid4())
        started = monotonic()
        try:
            response = await self._http.send(request, stream=True, auth=None, follow_redirects=False)
        except httpx2.TransportError as error:
            raise _transport_error(error, operation, call_id, started=False) from None
        try:
            received = _received(decoder, response.status_code, settings)
            async for chunk in _async_chunks(response.aiter_bytes(), operation, call_id):
                if not received.add(chunk):
                    break
            info = _info(response, operation, call_id, started)
        finally:
            await response.aclose()
        return _completed(decoder, info, received, settings)

    async def aclose(self) -> None:
        """Close the HTTPX2 async client when this client owns it."""
        if not self._closed:
            self._closed = True
            if self._owned:
                await self._http.aclose()


async def _async_chunks(
    chunks: AsyncIterator[bytes], operation: OperationPlan[object, object], call_id: str
) -> AsyncIterator[bytes]:
    try:
        async for chunk in chunks:
            yield chunk
    except httpx2.TransportError as error:
        raise _transport_error(error, operation, call_id, started=True) from None
