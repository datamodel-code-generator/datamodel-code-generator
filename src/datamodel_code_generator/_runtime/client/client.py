"""The shared core of generated clients: build each call's request, send it through a transport adapter, and decode it.

A client or a view of it runs calls while OPEN; closing stops new calls, stops the active ones at their next step,
waits up to the cleanup timeout for them, and closes the transport the client owns.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Mapping
from contextlib import (
    AbstractAsyncContextManager,
    AbstractContextManager,
    asynccontextmanager,
    contextmanager,
    suppress,
)
from dataclasses import dataclass, replace
from functools import partial
from time import monotonic
from typing import TYPE_CHECKING, ClassVar, Final, Generic, Literal, TypeVar
from urllib.parse import quote, unquote_plus, urlsplit

import httpx2
from typing_extensions import Self, TypeIs, cast  # noqa: UP035 - Preserve the existing Generic import.

from ..model_codecs.errors import ParameterEncodingError
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
from .body_sources import bind_async_body, bind_body, capture_async_body, capture_body
from .coding import ContentDecoder
from .errors import (
    AdapterContractError,
    AdapterExecutionError,
    CleanupError,
    ConfigurationError,
    DeliveryState,
    HookExecutionError,
    HTTPStatusError,
    LimiterExecutionError,
    PhaseTimeoutError,
    ProtocolConfigurationError,
    ProtocolError,
    ProtocolSizeError,
    RedirectPolicyError,
    RequestEncodingError,
    ResponseTooLargeError,
    SDKError,
    TransportError,
    UnsupportedAsyncBackendError,
    add_secondary,
)
from .events import CallEvents, aauth_ended, auth_ended, call_events
from .hooks import LimiterContext
from .lifecycle import AsyncOwnedProviders, OwnedProviders, Scope, cleanup_secondary
from .logical import LogicalCallContext, joined_cap
from .media import normalized
from .multipart import MultipartSource, is_multipart, new_boundary, quiet_aclose, quiet_close
from .native import (
    AsyncHttpx2Response,
    AsyncHttpx2Transport,
    Httpx2Transport,
    native_async_client,
    native_client,
    transport_retry_reason,
)
from .operations import DATA_ERRORS, ResponseDecoder
from .options import (
    DEFAULT_TRANSPORT,
    ClientOptions,
    CompressionOrigin,
    HeaderPatch,
    IdempotencyKey,
    QueryPatch,
    RequestOptions,
    ResolvedCompression,
    ServerSelection,
    Settings,
    TimeoutOptions,
    awaited,
    checked_base_url,
    context,
    layered_redirects,
    layered_retry,
    new_key,
    resolve_transport_options,
)
from .paths import PLACEHOLDER, dot_segment, dotted_route, path_segments
from .raw import AsyncRawResponse, RawResponse, arefused, refused
from .redirects import RedirectState, redirect_target
from .responses import HeadersView, Response, ResponseInfo
from .retry import (
    EMPTY_RETRY_HEADERS,
    RetryState,
    RetryTiming,
    bind_retry_headers,
    retry_after,
    retry_delay,
    retry_stop,
    should_retry,
    status_retry_reason,
)
from .tasks import TaskInterruptionError, task_result
from .timing import on_clock
from .transports import (
    AttemptTrace,
    OwnedTransportAdapter,
    PreparedRequest,
    ResolvedTimeoutOptions,
    TransportCapabilities,
    is_adapter,
    is_async_adapter,
    response_head,
)
from .urls import URLValidationError, absolute_target, canonical_origin, request_origin, strip_query

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
    from typing import Protocol

    from ..model_codecs.parameters import ParameterFragment, ParameterPlan
    from ..model_codecs.wire import WireValue
    from ..protocols.circuit_records import CircuitKey, CircuitPermit
    from ..protocols.circuits import Breaker
    from ..protocols.options import (
        ProtocolClientOptions,
        ProtocolDefaults,
        ProtocolSecurityContext,
    )
    from ..protocols.references import OperationRef
    from .auth import (
        AsyncCloseableCredentialProvider,
        AuthConfig,
        CloseableCredentialProvider,
        CredentialContext,
        TokenVersion,
    )
    from .auth_policy import (
        AcquiredCredential,
        AsyncBoundAuth,
        AsyncBoundCredential,
        AsyncHopCredentials,
        BoundAuth,
        BoundCredential,
        HopCredentials,
    )
    from .bodies import AsyncBodyAttempt, BodyAttempt
    from .body_sources import AsyncBodyBindings, AsyncBodySource, BodyBindings, BodySource
    from .errors import RetryStopReason
    from .hooks import AsyncLimiter, AsyncPermit, Limiter, Permit
    from .logical import OperationSession
    from .multipart import AsyncBodyInput, BodyInput
    from .operations import OperationPlan, ParameterSpec, ServerPlan
    from .options import ResolvedTransportOptions
    from .retry import RetryDelay
    from .security import SecuritySchemeEntry
    from .timing import Clock, Deadline
    from .transports import AsyncTransportAdapter, AsyncTransportResponse, TransportAdapter, TransportResponse
    from .urls import Origin

T = TypeVar("T")
R = TypeVar("R")
AdapterT = TypeVar("AdapterT")
HandleT = TypeVar("HandleT")

if TYPE_CHECKING:

    class _PagePlan(Protocol):
        """The identity of a protocol helper and of the operation its pages call."""

        @property
        def helper_id(self) -> str:
            """Return the helper's dotted name."""
            raise NotImplementedError

        @property
        def operation(self) -> OperationRef:
            """Return the reference of the operation the helper calls."""
            raise NotImplementedError


MAX_ERROR_BODY_BYTES: Final = 64 * 1024
CLEANUP_TIMEOUT: Final = 5.0
_ACCEPT_ENCODING: Final = ("Accept-Encoding", "gzip, deflate")
_TOKEN: Final = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_DOT_CAUSE: Final = "A path value cannot make its segment '.' or '..', which URL normalization removes"
_OWNERSHIPS: Final = frozenset({"borrowed", "owned"})
_BINARY: Final = "A body must be bytes or a file, stream, factory, or multipart body of the client's mode"
_MIN_STATUS: Final = 200
_NOT_MODIFIED: Final = 304
_MAX_STATUS: Final = 599
_ERROR_STATUS: Final = 400
_SWITCHING: Final = 101
_SOCKET_SCHEMES: Final = (("wss:", "https:"), ("ws:", "http:"))
_UNAUTHORIZED: Final = 401
_EMPTY_VISITED: Final[frozenset[tuple[str, str]]] = frozenset()


@dataclass(frozen=True, slots=True, kw_only=True)
class ClientDefaults:
    """The generated defaults of one client package and its helpers' kinds by name."""

    user_agent: str | None = None
    security_schemes: tuple[SecuritySchemeEntry, ...] = ()
    helpers: tuple[tuple[str, str], ...] = ()
    circuit_groups: frozenset[str] = frozenset()


_DEFAULT_SERVER: Final = ServerSelection()


def _layered(
    settings: Settings,
    layer: ClientOptions | RequestOptions,
    operation_id: str | None = None,
    origin: CompressionOrigin = "call",
) -> Settings:
    """Return the settings with one options layer applied: its set fields replace, UNSET ones inherit.

    A request coding the layer selects keeps the layer as its origin: the client, a view, or the call.
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
        retry=layered_retry(settings.retry, layer.retry, operation_id),
        redirects=layered_redirects(settings.redirects, layer.redirects),
        idempotency_key=settings.idempotency_key if isinstance(layer.idempotency_key, Unset) else layer.idempotency_key,
        auth=settings.auth if isinstance(layer.auth, Unset) else layer.auth,
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
        compression=(
            settings.compression
            if isinstance(coding := layer.compression, Unset)
            else None
            if coding is None
            else ResolvedCompression(coding, origin)
        ),
        clock=settings.clock,
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


def _protocol_options(
    options: ClientOptions | None, defaults: ClientDefaults, *, asynchronous: bool
) -> ProtocolClientOptions | None:
    """Return the client's protocol settings, refusing defaults or stores for a helper the package lacks.

    Defaults of another kind's helper, a cache or queue store the client's mode cannot call, and a WebSocket connector
    of the other execution mode are refused too.
    """
    if options is None or (protocols := options.protocols) is None or isinstance(protocols, Unset):
        return None
    if not isinstance(helpers := protocols.defaults, Unset) and helpers:
        from ..protocols.options import checked_defaults  # noqa: PLC0415 - Only helper defaults load the helper settings.

        checked_defaults(helpers, defaults.helpers)
    if not isinstance(queues := protocols.queue_stores, Unset) and queues:
        from ..protocols.options import checked_stores  # noqa: PLC0415 - Only queue stores load the helper settings.

        checked_stores(queues, defaults.helpers, asynchronous=asynchronous, kind="queue")
    if not isinstance(stores := protocols.cache_stores, Unset) and stores:
        from ..protocols.options import checked_stores  # noqa: PLC0415 - Only cache stores load the helper settings.

        checked_stores(stores, defaults.helpers, asynchronous=asynchronous)
    if (connector := protocols.websocket_connector) is not None and not isinstance(connector, Unset):
        from ..protocols.options import checked_connector  # noqa: PLC0415 - Only a connector loads the helper settings.

        checked_connector(connector, asynchronous=asynchronous)
    return protocols


def _client_settings(options: object) -> Settings:
    settings = Settings(None, _DEFAULT_SERVER, None, MAX_ERROR_BODY_BYTES, CLEANUP_TIMEOUT, None)
    match options:
        case None:
            return settings
        case ClientOptions():
            if not isinstance(clock := options.clock, Unset):
                settings = replace(settings, clock=clock)
            return _layered(settings, options, origin="client")
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
    return checked_base_url(PLACEHOLDER.sub(lambda match: values[match[1]], server.url), ("server",)).rstrip("/")


def _encoding_error(
    operation: OperationPlan[object, object], location: tuple[str, str], error: BaseException | None = None
) -> RequestEncodingError:
    return RequestEncodingError(location=location, operation_id=operation.operation_id, cause=error)


def _auth_identity(auth: AuthConfig) -> WireValue:
    """Return what identifies an auth configuration without its secrets or its providers' classes.

    It is the schemes it gives credentials for, with the audience and requested scopes of an OAuth grant, the
    selection, the anonymous settings, the origins credentials may go to, and what each signer declares it manages.
    """
    from .auth import OwnedCredentialProvider  # noqa: PLC0415 - Only a checkpoint identifies the auth.
    from .grants import grant_identity  # noqa: PLC0415 - Only a checkpoint identifies the auth.

    capabilities = (signer.capabilities for signer in auth.signers)
    return {
        "credentials": tuple(
            (name, grant_identity(provider.provider if isinstance(provider, OwnedCredentialProvider) else provider))
            for name, provider in sorted(auth.credentials.items())
        ),
        "selection": None if isinstance(auth.selection, Unset) else auth.selection,
        "anonymous": (auth.send_on_anonymous, tuple(sorted(auth.anonymous_schemes))),
        "origins": tuple(sorted(auth.allowed_origins)),
        "signers": tuple(
            (
                tuple(sorted(item.allowed_origins)),
                tuple(sorted(item.managed_headers)),
                tuple(sorted(item.managed_query)),
                item.requires_body_digest,
            )
            for item in capabilities
        ),
    }


def _secret(spec: ParameterSpec, value: WireValue, headers: frozenset[str], queries: frozenset[str]) -> bool:
    """Return whether an argument carries credentials: a cookie, a credential header, or a scheme's query field.

    Exploded form and deepObject query parameters send only their property names or bracketed names, including
    additional properties. Other query serializers retain the declaration name as their emitted field.
    """
    plan = spec.plan
    name = plan.name
    secret = False
    match plan.location:
        case "cookie":
            secret = True
        case "header":
            secret = name.lower() in headers
        case "query":
            if (
                plan.shape == "object"
                and plan.explode
                and plan.style in {"form", "deepObject"}
                and isinstance(value, Mapping)
            ):
                if plan.style == "form":
                    secret = not queries.isdisjoint(value)
                else:
                    secret = any(f"{name}[{member}]" in queries for member in value)
            else:
                secret = name in queries
        case "querystring":
            secret = isinstance(value, Mapping) and not queries.isdisjoint(value)
        case _:
            pass
    return secret


def _exploded(plan: ParameterPlan) -> bool:
    """Return whether a parameter sends each property of its object value as a field of its own."""
    return plan.shape == "object" and plan.explode and plan.style in {"form", "cookie"}


def _coded(operation: OperationPlan[object, object], spec: ParameterSpec, code: Callable[[], R]) -> R:
    """Return what coding an argument gives, raising a codec's refusal as the argument's encoding error."""
    try:
        return code()
    except (*DATA_ERRORS, ValueError, TypeError) as error:
        raise _encoding_error(operation, (spec.plan.location, spec.plan.name), error) from None


def _parameter(spec: ParameterSpec, value: object) -> object:
    """Return the contribution of one argument to its request, encoded as a call encodes it."""
    return encode_parameter(spec.plan, spec.encode(value))


def _unsaved(plan: _PagePlan, path: tuple[str, ...]) -> ProtocolConfigurationError:
    return ProtocolConfigurationError(
        field_path=path, condition="wrong_capability", helper_id=plan.helper_id, operation=plan.operation
    )


def _dot_parameter(template: str, path: dict[str, str]) -> str | None:
    """Return the path parameter that makes its segment a dot segment, which URL normalization would remove.

    The segment's first parameter with a value is named; a dot segment the template itself spells names none.
    """
    return next(
        (
            next((name for name in names if path[name]), names[0])
            for segment, names in path_segments(template)
            if dot_segment(segment, path)
        ),
        None,
    )


def _parameters(operation: OperationPlan[object, object], arguments: tuple[object, ...]) -> _Request:
    """Return a call's request with its parameters encoded."""
    request = _Request()
    for spec, value in zip(operation.parameters, arguments, strict=True):
        plan = spec.plan
        if isinstance(value, Unset):
            if plan.required:
                raise _encoding_error(operation, (plan.location, plan.name))
            continue
        try:
            request.add(encode_parameter(plan, spec.encode(value)), plan.name)
        except (*DATA_ERRORS, ValueError, TypeError) as error:
            raise _encoding_error(operation, (plan.location, plan.name), error) from None
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
        wire_send_count=call.wire_send_count,
        auth_exchange_budget_used=call.auth_exchange_budget_used,
        auth_refresh_ids=call.auth_refresh_ids,
        auth_refresh_pending=call.auth_refresh_pending,
        elapsed=call.monotonic() - call.started,
        content_type=None if content_type is None else normalized(content_type),
        request_id=None if request_id_header is None else headers.get(request_id_header),
    )


def _encoded(content: object, media_type: str | None) -> tuple[EncodedAttempt | None, object]:
    """Split a body into the attempt of bytes, sent as they are, and an input that builds its own attempts, or UNSET."""
    if type(content) is bytes:
        return EncodedAttempt(content, media_type), UNSET
    return None, content


def _context(call: _Call) -> BodyAttemptContext:
    return BodyAttemptContext(
        call_id=call.call_id,
        attempt_index=call.attempt_index,
        hop_index=call.hop_index,
        remaining_timeout=call.remaining(),
    )


def _checked_raw(method: object, url: object) -> tuple[str, str]:
    """Normalize the raw method and validate its native URL before sending."""
    if not isinstance(method, str) or not _TOKEN.fullmatch(method):
        raise ConfigurationError(field_path=("method",), condition="invalid_value")
    if not isinstance(url, str):
        raise ConfigurationError(field_path=("url",), condition="invalid_url")
    try:
        target = absolute_target(url)
    except URLValidationError as error:
        raise ConfigurationError(field_path=("url",), condition="invalid_url", cause=error) from None
    return method.upper(), target.url


def _delivery(call: _Call) -> DeliveryState:
    """Return how far a call provably got: a started response, else whether a send was admitted."""
    return DeliveryState.RESPONSE_STARTED if call.trace.response_started else call.delivery_state


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
        error.operation_id = error.operation_id or operation_id
        error.call_id = error.call_id or info.call_id
        raise
    return Response(data=data, info=info)


def _page(  # noqa: PLR0913
    decoder: ResponseDecoder[T, object],
    info: ResponseInfo,
    body: _Body,
    call: _Call,
    plan: _PagePlan,
    *,
    page_limited: bool,
) -> tuple[T, WireValue, bytes]:
    """Return a page's value, wire value, and body, or raise the error of a page or response over its size limit."""
    settings = call.settings
    if body.overflow:
        limit = settings.max_response_bytes
        assert limit is not None
        if page_limited:
            raise ProtocolSizeError(
                kind="page",
                limit=limit,
                observed=body.size,
                unit="bytes",
                helper_id=plan.helper_id,
                operation=plan.operation,
                info=info,
            )
        raise ResponseTooLargeError(info=info, representation="decoded", limit=limit, observed_bytes=body.size)
    if (problem := body.problem) is not None and body.success:
        raise problem
    content = body.content
    data, wire = decoder.decode_page(info, content, truncated=body.truncated or problem is not None, problem=problem)
    return data, wire, content


RAW_DECODER: Final[ResponseDecoder[object, object]] = ResponseDecoder((), (), HTTPStatusError)


def stored_value(operation: OperationPlan[T, object], info: ResponseInfo, body: bytes, settings: Settings) -> T:
    """Decode a stored success body as a call's decoder decodes a received one, under the call's settings."""
    received = _Body(settings.max_response_bytes, success=True)
    received.add(body)
    return _completed(operation.responses, info, received, settings, operation.operation_id).data


@dataclass(frozen=True, slots=True)
class CacheRequest:
    """What a cache fetch keys and sends, prepared once before its call.

    `credentials` identifies the credentials the request carries, None for none; `foreign_auth` tells that a view or
    the call replaced the client's own auth; `credential_headers` are the lowercase names of the headers credentials
    travel in, which the auth may add after the cache looked the request up.
    """

    settings: Settings
    request: PreparedRequest[EncodedAttempt]
    url: str
    credentials: WireValue
    partition: str | None
    foreign_auth: bool
    credential_headers: frozenset[str]


def _retry_error(error: BaseException) -> TypeIs[HTTPStatusError[object] | TransportError]:
    return isinstance(error, (HTTPStatusError, TransportError))


_EMPTY_ORIGINS: Final[frozenset[Origin]] = frozenset()


def _compressed(
    coding: ResolvedCompression, call: _Call, request: PreparedRequest[EncodedAttempt], deferred: object
) -> tuple[PreparedRequest[EncodedAttempt], bool]:
    """Apply a selected coding to a request with a body whose operation accepts it, adding its Content-Encoding.

    Elsewhere a coding the client or a view selected turns off, as one a helper's child call inherits does; one the
    call selected is refused before sending. A Content-Encoding header the call already sends conflicts with it.
    """
    from .compression import applies, gzipped_attempt  # noqa: PLC0415 - Only a selected coding loads the encoder.

    if not applies(coding, call.operation, body=request.body is not None or not isinstance(deferred, Unset)):
        if coding.origin == "call" and not isinstance(call, _SessionCall):
            raise ConfigurationError(field_path=("compression",), condition="not_applicable")
        return request, False
    if request.headers.get_all("content-encoding"):
        raise ConfigurationError(field_path=("headers", "Content-Encoding"), condition="managed")
    body = None if request.body is None else gzipped_attempt(request.body, partial(call.check, "encode"))
    headers = HeadersView((
        *(pair for pair in request.headers if pair[0].lower() != "content-length"),
        ("Content-Encoding", coding.token),
    ))
    return PreparedRequest(method=request.method, url=request.url, headers=headers, body=body), True


def _gzip_source(source: BodySource) -> BodySource:
    from .compression import GzipSource  # noqa: PLC0415 - Only a compressed body loads the encoder.

    return GzipSource(source)


def _agzip_source(source: AsyncBodySource) -> AsyncBodySource:
    from .compression import AsyncGzipSource  # noqa: PLC0415 - Only a compressed body loads the encoder.

    return AsyncGzipSource(source)


def _circuit(breaker: Breaker, call: _Call, url: str, root_auth: AuthConfig | None) -> CircuitKey | None:
    """Return the key of a call's circuit, or None for a call of an operation outside every circuit group.

    A call authenticating with other auth than the client's would share the client's partition, so it is refused.
    """
    if (operation := call.operation) is None or (group := operation.circuit_group) is None:
        return None
    if call.auth is not None and call.settings.auth is not root_auth:
        raise ProtocolConfigurationError(field_path=("options", "auth"), condition="security_partition")
    return breaker.call_key(url, group, authenticated=call.auth is not None)


def _admission(
    shared: _Shared[TransportAdapter], breaker: Breaker, call: _Call, url: str
) -> tuple[Breaker, CircuitPermit] | None:
    """Pass a call through its circuit, returning the breaker and permit to record its outcome with."""
    key = _circuit(breaker, call, url, shared.root_auth)
    return None if key is None else (breaker, breaker.admit(key))


async def _aadmission(
    shared: _Shared[AsyncTransportAdapter], breaker: Breaker, call: _Call, url: str
) -> tuple[Breaker, CircuitPermit] | None:
    """Pass an asyncio call through its circuit, returning the breaker and permit to record its outcome with."""
    key = _circuit(breaker, call, url, shared.root_auth)
    return None if key is None else (breaker, await breaker.aadmit(key))


def _abandoned(
    call: _Call,
    owned: tuple[BodySource | None, BodyBindings | None],
    admission: tuple[Breaker, CircuitPermit] | None,
    failure: BaseException,
) -> None:
    """Release a failed call's body source and captured input, then record its outcome if it passed a circuit."""
    try:
        for resource in owned:
            if resource is not None:
                call.retry_blocked |= not _discarded(resource.close, failure)
    finally:
        if admission is not None:
            admission[0].record(admission[1], failure, None)


async def _aabandoned(
    call: _Call,
    owned: tuple[AsyncBodySource | None, AsyncBodyBindings | None],
    admission: tuple[Breaker, CircuitPermit] | None,
    failure: BaseException,
) -> None:
    """Release a failed asyncio call's body, then record its outcome, finishing the record even if cancelled."""
    try:
        for resource in owned:
            if resource is not None:
                await call.cleanup(resource.aclose, error=failure)
    finally:
        if admission is not None:
            await asyncio.shield(admission[0].arecord(admission[1], failure, None))


def _circuit_recorded(admission: tuple[Breaker, CircuitPermit], result: object, call: _Call) -> None:
    """Record a returned call's final status, discarding its raw response when the store fails."""
    info = call.last_info
    assert info is not None
    try:
        admission[0].record(admission[1], None, info.status_code)
    except BaseException as error:
        if isinstance(result, RawResponse):
            result.discard(error)
        raise


async def _acircuit_recorded(admission: tuple[Breaker, CircuitPermit], result: object, call: _Call) -> None:
    """Record a returned asyncio call's final status, discarding its raw response when the store fails."""
    info = call.last_info
    assert info is not None
    try:
        await admission[0].arecord(admission[1], None, info.status_code)
    except BaseException as error:
        if isinstance(result, AsyncRawResponse):
            await result.discard(error)
        raise


def _uncredentialed(
    headers: Iterable[tuple[str, str]], url: str, schemes: tuple[SecuritySchemeEntry, ...]
) -> tuple[tuple[tuple[str, str], ...], str]:
    """Return the headers and URL of a request to another origin without the credentials of the original origin's.

    The credential and cookie headers and every header and query field a declared security scheme names are removed,
    whether the auth placed them or a patch, a parameter, or the server's URL carried them.
    """
    from .security import secret_names  # noqa: PLC0415 - Only a request to another origin needs the schemes.

    names, query = secret_names(schemes)
    kept = tuple((name, value) for name, value in headers if name.lower() not in names)
    return kept, strip_query(url, query)


class _Authentication:
    """The active call's fixed selection, current material, and single admitted recovery."""

    __slots__ = ("bound", "credentials", "pending", "recovery_used", "rejected", "secondary_errors")

    def __init__(self, bound: BoundAuth | AsyncBoundAuth) -> None:
        self.bound = bound
        self.credentials: HopCredentials | AsyncHopCredentials | None = None
        self.pending: tuple[int, TokenVersion] | None = None
        self.rejected: tuple[int, TokenVersion] | None = None
        self.recovery_used = False
        self.secondary_errors: tuple[Exception, ...] = ()

    def candidate(self, headers: HeadersView, *, challenge_less: bool) -> bool:
        """Retain the refreshable provider/version actually used by a qualifying resource rejection."""
        from .auth import BearerCredential  # noqa: PLC0415
        from .auth_policy import invalid_token  # noqa: PLC0415

        credentials = self.credentials
        if credentials is not None:
            for index, acquired in enumerate(credentials.values):
                if isinstance(acquired.material, BearerCredential) and invalid_token(
                    headers, challenge_less=challenge_less
                ):
                    if self.bound.credentials[index].refreshable is not None:
                        self.rejected = index, acquired.material.version
                    return True
        return False


def _parameter_names(operation: OperationPlan[object, object], location: str) -> Iterator[str]:
    for parameter in operation.parameters:
        plan = parameter.plan
        if plan.location == location:
            if _exploded(plan):
                yield from (field.name for field in plan.fields)
            else:
                yield plan.name


@contextmanager
def _auth_work(call: _Call) -> Generator[None, None, None]:
    call.check("auth")
    events = call.events
    started = call.monotonic() if events is not None else 0.0
    try:
        if events is not None:
            events.emit(events.event("auth_start", sent=events.sent))
        yield
        call.check("auth")
    except BaseException as error:  # noqa: BLE001
        failure = call.failure(error)
        if events is not None:
            auth_ended(events, started, failure)
        raise failure from None
    if events is not None:
        auth_ended(events, started)


@asynccontextmanager
async def _aauth_work(call: _Call) -> AsyncGenerator[None, None]:
    call.check("auth")
    events = call.events
    started = call.monotonic() if events is not None else 0.0
    try:
        if events is not None:
            await events.aemit(events.event("auth_start", sent=events.sent))
        yield
        call.check("auth")
    except BaseException as error:  # noqa: BLE001
        failure = call.failure(error)
        if events is not None:
            await aauth_ended(events, started, failure)
        raise failure from None
    if events is not None:
        await aauth_ended(events, started)


def _credential_context(binding: BoundCredential | AsyncBoundCredential, call: _Call) -> CredentialContext:
    from .auth import CredentialContext  # noqa: PLC0415
    from .urls import origin_text  # noqa: PLC0415

    assert call.current_origin is not None
    return CredentialContext(
        scheme=binding.scheme.name,
        required_scopes=binding.required_scopes,
        audience=None,
        origin=origin_text(call.current_origin),
        deadline=call.deadline,
        cancel_token=call.settings.cancel_token,
    )


def _expired_credentials(call: _Call) -> bool:
    from .auth_policy import credentials_expired  # noqa: PLC0415

    assert call.auth is not None
    credentials = call.auth.credentials
    return credentials is not None and credentials_expired(credentials, now=call.monotonic())


def _reauthorizing(call: _Call) -> bool:
    """Return whether the call's credentials expired while it waited for a permit."""
    return call.auth is not None and _expired_credentials(call)


def _auth_failed(call: _Call) -> bool:
    """Return whether a local invalidation failed, which a raw call raises instead of returning its response."""
    return call.auth is not None and bool(call.auth.secondary_errors)


def _usable_credentials(call: _Call) -> None:
    if call.auth is not None and _expired_credentials(call):
        from .errors import TokenExpiredError  # noqa: PLC0415

        raise TokenExpiredError(condition="expired", delivery_state=_delivery(call))


class _Call(LogicalCallContext):
    """Bind operation policy once while retaining the logical call's single ownership record."""

    __slots__ = (
        "allowed_origins",
        "attempt_index",
        "auth",
        "body_enabled",
        "current_origin",
        "decoder",
        "events",
        "hop_index",
        "idempotency",
        "initial_origin",
        "key",
        "key_expires_at",
        "last_failure",
        "last_info",
        "method",
        "operation",
        "previous_cap",
        "received_at",
        "received_wall_time",
        "request_id_header",
        "response_transferred",
        "retry_headers",
        "retry_safety",
        "server_origin",
        "stop_reason",
        "trace",
    )

    def __init__(
        self, settings: Settings, scope: Scope[HandleT], operation: OperationPlan[object, object] | None = None
    ) -> None:
        super().__init__(settings, scope, None if operation is None else operation.operation_id)
        self.auth: _Authentication | None = None
        self.operation = operation
        self.decoder: ResponseDecoder[object, object] = RAW_DECODER
        self.events: CallEvents | None = None
        self.request_id_header = None if operation is None else operation.request_id_header
        self.retry_safety: Literal["method_default", "idempotent", "never"] = (
            "method_default" if operation is None else operation.retry_safety
        )
        self.idempotency = None if operation is None else operation.idempotency
        key = settings.idempotency_key
        self.key = new_key(settings.clock) if self.idempotency is not None and isinstance(key, Unset) else key
        self.key_expires_at: float | None = None
        self.last_failure: BaseException | None = None
        self.last_info: ResponseInfo | None = None
        if self.idempotency is not None and isinstance(self.key, IdempotencyKey) and self.key.first_used_at is not None:
            self.key_expires_at = (
                self.started
                + self.idempotency.retention_seconds
                - (settings.clock.time() - self.key.first_used_at.timestamp())
            )
        self.retry_headers = EMPTY_RETRY_HEADERS
        self.allowed_origins = _EMPTY_ORIGINS
        self.initial_origin: Origin | None = None
        self.current_origin: Origin | None = None
        self.server_origin: Origin | None = None
        self.attempt_index = 0
        self.body_enabled = True
        self.hop_index = 0
        self.previous_cap: float | None = None
        self.stop_reason: RetryStopReason | None = None
        self.trace = AttemptTrace(clock=self.settings.clock)
        self.method = ""
        self.received_at = self.started
        self.received_wall_time = 0.0
        self.response_transferred = False

    def bind(self, capabilities: TransportCapabilities, options: RequestOptions | None) -> None:
        """Validate operation-bound controls before hooks, encoding, and any send."""
        operation = self.operation
        if self.idempotency is None and isinstance(self.key, IdempotencyKey):
            if options is not None and isinstance(options.idempotency_key, IdempotencyKey):
                raise ConfigurationError(field_path=("idempotency_key",), condition="not_declared")
            self.key = None
        retry = self.settings.retry
        if (
            retry.retry_after_ms_header is not UNSET
            or retry.should_retry_header is not UNSET
            or (
                operation is not None
                and (operation.retry_after_ms_header is not None or operation.should_retry_header is not None)
            )
        ):
            self.retry_headers = bind_retry_headers(
                retry,
                retry_after_ms_header=None if operation is None else operation.retry_after_ms_header,
                should_retry_header=None if operation is None else operation.should_retry_header,
            )
        if self.settings.redirects.allowed_origins:
            try:
                self.allowed_origins = frozenset(
                    canonical_origin(value) for value in self.settings.redirects.allowed_origins
                )
            except URLValidationError as error:
                raise ConfigurationError(
                    field_path=("redirects", "allowed_origins"), condition="invalid_value", cause=error
                ) from None
        if capabilities.delivery_evidence and capabilities.internal_retry_limit == 0:
            self.wire_send_count = 0

    def prepared(self, request: PreparedRequest[EncodedAttempt]) -> PreparedRequest[EncodedAttempt]:
        """Retain the original method and attach the call's sole declared idempotency key."""
        self.method = request.method
        if self.settings.redirects.enabled:
            target = absolute_target(request.url)
            self.initial_origin = self.current_origin = target.origin
            request = PreparedRequest(method=request.method, url=target.url, headers=request.headers, body=request.body)
        elif self.auth is not None:
            self.initial_origin = self.current_origin = request_origin(request.url)
        if self.idempotency is None:
            return request
        name = self.idempotency.header_name
        if request.headers.get_all(name):
            raise ConfigurationError(field_path=("headers", name), condition="managed")
        if not isinstance(self.key, IdempotencyKey):
            return request
        return PreparedRequest(
            method=request.method,
            url=request.url,
            headers=HeadersView((*request.headers.items(), (name, self.key.value))),
            body=request.body,
        )

    def received(self, info: ResponseInfo) -> None:
        """Save retry timing at header receipt before user hooks can consume the wait."""
        self.last_info = info
        if (head := response_head(self.trace)) is not None:
            self.received_at, self.received_wall_time = head.received_at, head.received_wall_time
        elif info.status_code >= _ERROR_STATUS and self.settings.retry.respect_retry_after:
            clock = self.settings.clock
            self.received_at, self.received_wall_time = clock.monotonic(), clock.time()

    def retry(
        self,
        info: ResponseInfo | None,
        error: TransportError | None,
        *,
        replayable: bool,
        retry_owner: Literal["sdk", "transport"],
    ) -> RetryDelay | None:
        """Apply the ordered pure gates and retain one absolute delay before response disposal."""
        self.check("send")
        if self.retry_blocked:
            self.stop_reason = "callback_failure"
            return None
        retry = self.settings.retry
        headers = None if info is None else info.headers
        hint = should_retry(headers, self.retry_headers.should_retry_header)
        reason = (
            transport_retry_reason(error, self.trace)
            if error is not None
            else status_retry_reason(info.status_code, retry, hint=hint)
            if info is not None
            else None
        )
        auth = self.auth
        auth_candidate = (
            error is None
            and info is not None
            and info.status_code == _UNAUTHORIZED
            and auth is not None
            and auth.candidate(
                info.headers,
                challenge_less=self.operation is not None and self.operation.auth_challenge_less_401,
            )
        )
        if auth_candidate:
            assert auth is not None
            reason = "auth_invalid_token" if auth.rejected is not None else None
        self.trace.connect_failure = None
        now = self.monotonic()
        self.stop_reason = retry_stop(
            RetryState(
                failure_kind="auth" if auth_candidate else "transport" if error is not None else "status",
                reason=reason,
                method=self.method,
                retry_safety=self.retry_safety,
                idempotency=self.idempotency,
                key_expires_at=self.key_expires_at,
                delivery_state=self.delivery_state,
                resource_attempt_count=self.resource_attempt_count,
                body_replayable=replayable,
                network_available=self.send_limit is None or self.network_send_budget_used < self.send_limit,
                server_hint=hint,
                proven_not_sent=self.trace.proven_not_sent,
            ),
            retry,
            retry_owner=retry_owner,
            now=now,
            auth_recovery_used=auth is not None and auth.recovery_used,
        )
        if self.stop_reason is not None:
            return None
        assert reason is not None
        server = (
            retry_after(
                headers,
                milliseconds_header=self.retry_headers.retry_after_ms_header,
                received_at=self.received_at,
                received_wall_time=self.received_wall_time,
            )
            if headers is not None and retry.respect_retry_after
            else None
        )
        planned = retry_delay(
            retry,
            reason=reason,
            server=server,
            timing=RetryTiming(
                self.previous_cap, now, None if self.deadline is None else self.deadline.at, self.settings.clock.random
            ),
        )
        if isinstance(planned, str):
            self.stop_reason = planned
            return None
        self.previous_cap = planned.backoff_cap
        return planned

    def stopped(self, error: BaseException) -> BaseException:
        """Attach the last policy decision without changing termination precedence."""
        failure = self.failure(error)
        if _retry_error(failure):
            failure.retry_stop_reason = self.stop_reason
            if self.auth is not None:
                for secondary in self.auth.secondary_errors:
                    if all(existing is not secondary for existing in failure.secondary_errors):
                        add_secondary(failure, secondary)
        return failure

    def resending(self, error: BaseException) -> None:
        """Recheck termination and key retention before waiting or opening another body."""
        self.check("sleep")
        if self.retry_blocked:
            self.stop_reason = "callback_failure"
            raise self.stopped(error)
        if self.key_expires_at is not None and self.monotonic() >= self.key_expires_at:
            self.stop_reason = "unsafe_operation"
            raise self.stopped(error)

    def retained(self) -> None:
        """Do not admit a resend after its stable idempotency key has expired during preparation."""
        if self.key_expires_at is None or self.monotonic() < self.key_expires_at:
            return
        if self.hop_index:
            raise self.snapshot_error(
                RedirectPolicyError(delivery_state=DeliveryState.RESPONSE_STARTED, info=self.last_info)
            )
        if self.attempt_index:
            assert self.last_failure is not None
            self.stop_reason = "unsafe_operation"
            raise self.stopped(self.last_failure)

    def restart(self, original: PreparedRequest[EncodedAttempt]) -> frozenset[tuple[str, str]]:
        """Begin the next resource candidate from the once-encoded original request."""
        self.attempt_index += 1
        self.hop_index = 0
        self.body_enabled = True
        self.current_origin = self.initial_origin
        self.trace = AttemptTrace(clock=self.settings.clock)
        self.phase_caps = ()
        self.stop_reason = None
        if self.events is not None:
            self.events.prepare(original.url, self.attempt_index)
        return frozenset({(original.method, original.url)}) if self.settings.redirects.enabled else _EMPTY_VISITED

    @staticmethod
    def redirect_headers(headers: HeadersView) -> HeadersView:
        """Return the response headers whose Location a redirect follows."""
        return headers

    def redirected(
        self,
        request: PreparedRequest[EncodedAttempt],
        info: ResponseInfo,
        visited: frozenset[tuple[str, str]],
        *,
        replayable: bool,
        schemes: tuple[SecuritySchemeEntry, ...],
    ) -> PreparedRequest[EncodedAttempt] | None:
        """Resolve an allowed redirect without mutating the original retry request.

        A hop to another origin carries none of the credentials the package's security schemes name.
        """
        redirects = self.settings.redirects
        if not redirects.enabled or info.status_code not in {301, 302, 303, 307, 308}:
            return None
        assert self.initial_origin is not None
        assert self.current_origin is not None
        target = redirect_target(
            info.status_code,
            self.redirect_headers(info.headers),
            RedirectState(
                method=request.method,
                url=request.url,
                current_origin=self.current_origin,
                initial_origin=self.initial_origin,
                allowed_origins=self.allowed_origins,
                redirect_count=self.redirect_count,
                visited=visited,
                retry_safety=self.retry_safety,
                idempotency=self.idempotency,
                key_expires_at=self.key_expires_at,
                body_replayable=replayable,
            ),
            redirects,
            now=self.monotonic(),
        )
        if self.send_limit is not None and self.network_send_budget_used >= self.send_limit:
            raise RedirectPolicyError(delivery_state=DeliveryState.RESPONSE_STARTED, info=info)
        url = target.url
        if self.auth is not None:
            from .auth_policy import strip_managed_query  # noqa: PLC0415

            url = strip_managed_query(url, self.auth.bound)
        headers = request.headers.items()
        if target.cross_origin:
            headers, url = _uncredentialed(headers, url, schemes)
        if url != target.url and (target.method, url) in visited:
            raise RedirectPolicyError(delivery_state=DeliveryState.RESPONSE_STARTED, info=info)
        if target.drop_body:
            headers = tuple(
                (name, value)
                for name, value in headers
                if not name.lower().startswith("content-") and name.lower() != "transfer-encoding"
            )
        self.current_origin = target.origin
        if target.drop_body:
            self.body_enabled = False
        return PreparedRequest(
            method=target.method,
            url=url,
            headers=HeadersView(headers),
            body=None if target.drop_body else request.body,
        )


class _SessionCall(_Call):
    """A child call of a protocol helper session, which also bounds the call's deadline and send admissions.

    It keeps the URL of the hop it sends, credentials excluded, against which a page's relative URLs resolve.
    """

    __slots__ = ("admission", "parent", "session", "url")

    def __init__(
        self,
        settings: Settings,
        scope: Scope[HandleT],
        operation: OperationPlan[object, object],
        session: OperationSession,
        bound: Deadline | None = None,
    ) -> None:
        """Bind the call to its session, ending it no later than the session or a helper's own bound does."""
        super().__init__(settings, scope, operation)
        self.session = self.parent = session
        self.admission: Callable[[], object] | None = None
        self.url = ""
        for limit in (session.deadline, None if bound is None else on_clock(bound, settings.clock)):
            if limit is not None and ((deadline := self.deadline) is None or limit.at < deadline.at):
                self.deadline = limit

    def prepared(self, request: PreparedRequest[EncodedAttempt]) -> PreparedRequest[EncodedAttempt]:
        """Prepare the request as an ordinary call does, keeping its URL."""
        request = super().prepared(request)
        self.url = request.url
        return request

    def followed_query(self, schemes: tuple[SecuritySchemeEntry, ...]) -> frozenset[str]:
        """Return the query fields a followed URL is sent and saved without, whatever its origin.

        They are the positions of the package's declared security schemes and the fields the call's auth places, which
        the auth adds again itself.
        """
        from .security import secret_names  # noqa: PLC0415 - Only a followed URL needs the schemes.

        query = secret_names(schemes)[1]
        return query if (auth := self.auth) is None else query | auth.bound.managed_query

    def restart(self, original: PreparedRequest[EncodedAttempt]) -> frozenset[tuple[str, str]]:
        """Begin the next resource candidate at the original URL."""
        self.url = original.url
        return super().restart(original)

    def redirected(
        self,
        request: PreparedRequest[EncodedAttempt],
        info: ResponseInfo,
        visited: frozenset[tuple[str, str]],
        *,
        replayable: bool,
        schemes: tuple[SecuritySchemeEntry, ...],
    ) -> PreparedRequest[EncodedAttempt] | None:
        """Resolve a redirect as an ordinary call does, keeping the URL of the hop it allows."""
        hop = super().redirected(request, info, visited, replayable=replayable, schemes=schemes)
        if hop is not None:
            self.url = hop.url
        return hop

    def retry(
        self,
        info: ResponseInfo | None,
        error: TransportError | None,
        *,
        replayable: bool,
        retry_owner: Literal["sdk", "transport"],
    ) -> RetryDelay | None:
        """Plan a retry as an ordinary call does, refusing it when the session cannot pay for its sends."""
        planned = super().retry(info, error, replayable=replayable, retry_owner=retry_owner)
        if planned is None:
            return None
        if not self.parent.room():
            self.stop_reason = "parent_budget_exhausted"
            return None
        return planned


def _http_location(location: str) -> str:
    """Return a ws or wss URL as the http or https URL of its handshake, and any other location unchanged."""
    lowered = location[:4].lower()
    for socket_scheme, http_scheme in _SOCKET_SCHEMES:
        if lowered.startswith(socket_scheme):
            return http_scheme + location[len(socket_scheme) :]
    return location


class _SocketCall(_SessionCall):
    """The handshake of a WebSocket helper: a session child call whose open has one cap for all of its phases.

    The cap is the least of the open timeout and the connect, read, and write timeouts, bounded by the deadline, so a
    cap the deadline binds ends the call with DeadlineExceededError. WebSockets have no pool.
    """

    __slots__ = ("open_timeout",)

    def __init__(
        self,
        settings: Settings,
        scope: Scope[HandleT],
        operation: OperationPlan[object, object],
        session: OperationSession,
        open_timeout: float | None,
    ) -> None:
        """Bind the call to its session and keep the open timeout."""
        super().__init__(settings, scope, operation, session)
        self.open_timeout = open_timeout

    def timeout(self) -> ResolvedTimeoutOptions:
        """Resolve the open's one cap for the connect, read, and write phases; a handed-over socket keeps the rest."""
        if self.streaming:
            return super().timeout()
        configured = self.settings.timeout
        cap = joined_cap(
            (self.open_timeout, configured.connect, configured.read, configured.write), self.remaining(), self.deadline
        )
        self.phase_caps = (cap, cap, cap, cap)
        return ResolvedTimeoutOptions(connect=cap.effective, read=cap.effective, write=cap.effective, pool=None)

    @staticmethod
    def redirect_headers(headers: HeadersView) -> HeadersView:
        """Read a ws or wss Location as the http or https URL a handshake requests, so the shared policy applies."""
        return HeadersView(
            (name, _http_location(value) if name.lower() == "location" else value) for name, value in headers.items()
        )


class _SessionWait(LogicalCallContext):
    """A wait of a protocol helper session between its child calls, ending no later than the session does."""

    __slots__ = ("session",)

    def __init__(
        self, settings: Settings, scope: Scope[HandleT], session: OperationSession, operation_id: str | None
    ) -> None:
        """Bind the wait to its session and the operation it comes before."""
        super().__init__(settings, scope, operation_id)
        self.session = session
        if (limit := session.deadline) is not None and ((deadline := self.deadline) is None or limit.at < deadline.at):
            self.deadline = limit


class _Shared(Generic[AdapterT]):
    """What a client shares with its views: the transport, the fixed headers, and whether the transport was closed.

    It also keeps the client's protocol helper settings and the breaker of its grouped operations' circuits.
    """

    __slots__ = (
        "adapter",
        "adapter_closed",
        "breaker",
        "circuit_groups",
        "closing_tasks",
        "fixed",
        "loop",
        "protocols",
        "providers",
        "root_auth",
        "security_schemes",
        "socket_connector",
        "transport",
        "trusted",
    )

    def __init__(
        self, defaults: ClientDefaults, adapter: AdapterT, transport: ResolvedTransportOptions, *, trusted: bool
    ) -> None:
        agent = defaults.user_agent
        self.transport = transport
        self.adapter = adapter
        self.trusted = trusted
        self.security_schemes = defaults.security_schemes
        self.providers: OwnedProviders | AsyncOwnedProviders | None = None
        self.fixed = (_ACCEPT_ENCODING,) if agent is None else (("User-Agent", agent), _ACCEPT_ENCODING)
        self.adapter_closed = False
        self.closing_tasks: dict[Scope[AsyncRawResponse], asyncio.Task[list[Exception]]] | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.protocols: ProtocolClientOptions | None = None
        self.root_auth: AuthConfig | None = None
        self.socket_connector: object = None
        self.breaker: Breaker | None = None
        self.circuit_groups = defaults.circuit_groups

    def protect(self, protocols: ProtocolClientOptions | None, clock: Clock, *, asynchronous: bool) -> None:
        """Keep the client's protocol settings and create its breaker, on the client's clock, if they enable one."""
        self.protocols = protocols
        if protocols is not None and self.circuit_groups and not isinstance(protocols.circuit, Unset):
            from ..protocols.circuits import breaker  # noqa: PLC0415 - Only circuit settings load the breaker.

            self.breaker = breaker(protocols, clock, asynchronous=asynchronous)


class _Core(Generic[AdapterT, HandleT]):  # noqa: PLR0904 - It serves every call and helper kind.
    __slots__ = ("_owned", "_scope", "_settings", "_shared", "_urls")
    _asynchronous: ClassVar[bool] = False

    def __init__(self, shared: _Shared[AdapterT], settings: Settings, scope: Scope[HandleT], *, owned: bool) -> None:
        self._shared = shared
        self._settings = settings
        self._scope = scope
        self._owned = owned
        self._urls: dict[int, tuple[tuple[ServerPlan, ...], str]] = {}

    @property
    def clock(self) -> Clock:
        """Return the clock that times this client's calls and helper sessions."""
        return self._settings.clock

    def fixes_key(self, options: RequestOptions | None) -> bool:
        """Return whether a call's effective options, its own, a view's, or the client's, fix an idempotency key."""
        return isinstance(self._call_settings(options, None).idempotency_key, IdempotencyKey)

    def patches(self, options: RequestOptions | None) -> tuple[tuple[HeaderPatch, ...], tuple[QueryPatch, ...]]:
        """Return the header and query patches of a call's effective options: the client's, a view's, and its own."""
        settings = self._call_settings(options, None)
        return settings.headers, settings.query

    def reconnects_after(self, error: TransportError, options: RequestOptions | None, operation_id: str | None) -> bool:
        """Return whether a transport failure reading a stream's body is one an automatic reconnection may follow.

        It is a read-phase failure the shared retry classification retries. A read timeout qualifies only when the
        call's own read timeout set its cap, not the stream's idle limit, which wins a tie.
        """
        if error.phase != "read" or transport_retry_reason(error, AttemptTrace(clock=self.clock)) is None:
            return False
        if not isinstance(error, PhaseTimeoutError):
            return True
        settings = self._call_settings(options, operation_id)
        read, idle = settings.stream_read_timeout, settings.stream_idle_timeout
        return read is not None and (idle is None or read < idle)

    def waiting(
        self, options: RequestOptions | None, session: OperationSession, operation_id: str | None
    ) -> LogicalCallContext:
        """Return a context a helper waits in before a child call of its session, sending nothing.

        Its sleeps wake when the client closes or the options' cancel token is cancelled, and end by the earlier of the
        options' deadline and the session's; the options' total timeout bounds each child call, not the waits between.
        Its errors name the session and the operation the wait comes before.
        """
        settings = replace(self._call_settings(options, operation_id), total_timeout=None)
        return _SessionWait(settings, self._scope, session, operation_id)

    def reconnect_backoff(
        self, options: RequestOptions | None, operation_id: str | None, previous_cap: float | None
    ) -> tuple[float, float]:
        """Return the backoff cap and the wait of a helper's next automatic reconnection, by the call's retry options.

        The cap starts at the initial delay and doubles up to the maximum delay, and full jitter draws the wait below
        it, as a retry's backoff does.
        """
        settings = self._call_settings(options, operation_id)
        planned = retry_delay(
            settings.retry,
            reason="read_error",
            server=None,
            timing=RetryTiming(previous_cap, 0.0, None, settings.clock.random),
        )
        assert not isinstance(planned, str)
        return planned.backoff_cap, planned.delay

    def protocol_defaults(self, name: str) -> ProtocolDefaults | None:
        """Return the defaults the client's protocol settings give one helper, or None."""
        if (protocols := self._shared.protocols) is None or isinstance(defaults := protocols.defaults, Unset):
            return None
        return defaults.get(name)

    def queue_store(self, name: str) -> object:
        """Return the queue store the client's protocol settings lend one helper, or None."""
        if (protocols := self._shared.protocols) is None or isinstance(stores := protocols.queue_stores, Unset):
            return None
        return stores.get(name)

    def protocol_options(self) -> ProtocolClientOptions | None:
        """Return the client's protocol settings, or None."""
        return self._shared.protocols

    def owned_connector(self, native: Callable[[], object]) -> object:
        """Return the WebSocket connector the client owns, created on first use and shared with its views."""
        if (connector := self._shared.socket_connector) is None:
            connector = self._shared.socket_connector = native()
        return connector

    def _raw_call(
        self, operation: OperationPlan[object, object], options: RequestOptions | None, session: OperationSession | None
    ) -> _Call:
        """Return the state of a raw call, a child of the helper session that gives one."""
        settings = self._call_settings(options, operation.operation_id)
        if session is None:
            return _Call(settings, self._scope, operation)
        return _SessionCall(settings, self._scope, operation, session)

    def circuit_key(self, group: object, origin: object) -> CircuitKey | None:
        """Return the key of one of the package's circuit groups at an origin, or None when no breaker is enabled."""
        from ..protocols import origins  # noqa: PLC0415 - Only circuit resets load the protocol origins.

        if not isinstance(group, str) or group not in self._shared.circuit_groups:
            raise ProtocolConfigurationError(field_path=("group",), condition="unknown_field")
        if not isinstance(origin, origins.Origin):
            raise ProtocolConfigurationError(field_path=("origin",), condition="invalid_value")
        if (breaker := self._shared.breaker) is None:
            return None
        return breaker.key((origin.scheme, origin.host, origin.port), group)

    def view(self, options: object) -> Self:
        """Return a view with the options layered on these, sharing the transport and counting its calls here too."""
        if not isinstance(options, RequestOptions):
            raise ConfigurationError(field_path=("options",), condition="invalid_type")
        settings = self._call_settings(options, None, "view")
        view = type(self)(self._shared, settings, self._scope.view(), owned=False)
        if not isinstance(options.auth, Unset) and options.auth is not None:
            view._adopt_auth(options.auth)  # noqa: SLF001 - The new view admits ownership through its own scope.
        return view

    def _adopt_auth(self, config: AuthConfig) -> None:
        from .auth import owned_providers  # noqa: PLC0415

        if providers := owned_providers(config):
            self._scope.adopt(partial(self._accept_providers, providers))

    def _accept_providers(
        self, providers: tuple[CloseableCredentialProvider | AsyncCloseableCredentialProvider, ...]
    ) -> None:
        """Register mode-validated provider identities inside the existing root admission lock."""
        registry = self._shared.providers
        if self._asynchronous:
            if registry is None:
                registry = self._shared.providers = AsyncOwnedProviders()
            assert isinstance(registry, AsyncOwnedProviders)
            for provider in providers:
                registry.adopt(provider)
        else:
            if registry is None:
                registry = self._shared.providers = OwnedProviders()
            assert isinstance(registry, OwnedProviders)
            for provider in providers:
                registry.adopt(provider)

    def _admitted(self, call: LogicalCallContext, options: RequestOptions | None) -> None:
        call.check()
        try:
            if options is not None and not isinstance(options.auth, Unset) and options.auth is not None:
                from .auth import owned_providers  # noqa: PLC0415

                providers = owned_providers(options.auth)
                self._scope.admit(partial(self._accept_providers, providers) if providers else None)
            else:
                self._scope.admit()
        except SDKError as error:
            raise call.failure(error) from None

    @staticmethod
    def _failure(error: BaseException, call: LogicalCallContext, delivery: DeliveryState) -> BaseException:
        """Classify a transport failure, honoring cancellation, closing and deadline precedence."""
        selected = call.failure(error, delivery_state=delivery)
        if not isinstance(selected, Exception):
            return selected
        if isinstance(selected, PhaseTimeoutError):
            return call.timeout_failure(selected, selected.phase, selected.delivery_state)
        if isinstance(selected, TransportError) and isinstance(selected.cause, httpx2.TimeoutException):
            return call.timeout_failure(selected.cause, selected.phase, selected.delivery_state)
        if isinstance(selected, TransportError) and call.streaming:
            selected.retry_stop_reason = "transport_not_retryable"
        if isinstance(selected, SDKError):
            return selected
        return call.snapshot_error(AdapterExecutionError(delivery_state=delivery, cause=selected))

    @staticmethod
    def _response_info(
        response: TransportResponse | AsyncTransportResponse,
        trace: AttemptTrace,
        request_id_header: str | None,
        call: LogicalCallContext,
        *,
        trusted: bool,
    ) -> ResponseInfo:
        """Return received metadata only after checking the response and the call's termination signals.

        A trusted adapter's response is used as it is; another one's status must be final and its trace intact.
        """
        call.delivery_state = DeliveryState.RESPONSE_STARTED
        status, headers = (
            (response.status_code, response.headers)
            if trusted
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

    def _call_settings(self, options: object, operation_id: str | None, origin: CompressionOrigin = "call") -> Settings:
        """Return the settings a call runs with: this client's or view's, with the call's options layered on them."""
        if options is None:
            return self._settings
        if not isinstance(options, RequestOptions):
            if (closed := self._scope.closing()) is not None:
                raise closed
            raise ConfigurationError(field_path=("options",), condition="invalid_type", operation_id=operation_id)
        if not isinstance(options.auth, Unset) and options.auth is not None:
            from .auth_policy import validate_auth_mode  # noqa: PLC0415

            validate_auth_mode(options.auth, asynchronous=self._asynchronous)
        return _layered(self._settings, options, operation_id, origin)

    def _bound(
        self, operation: OperationPlan[object, object] | None, config: AuthConfig | None
    ) -> BoundAuth | AsyncBoundAuth | None:
        """Select the auth a call binds without invoking callbacks, refusing missing credentials it requires."""
        security = None if operation is None else operation.security
        if config is None:
            if security is not None and security.alternatives and all(security.alternatives):
                from .errors import AuthConfigurationError  # noqa: PLC0415

                raise AuthConfigurationError(field_path=("auth",), condition="missing_credentials")
            return None
        from .auth_policy import bind_async_auth, bind_auth  # noqa: PLC0415

        return (
            bind_async_auth(config, security, self._shared.security_schemes)
            if self._asynchronous
            else bind_auth(config, security, self._shared.security_schemes)
        )

    def _bind_auth(self, call: _Call) -> None:
        """Bind effective security once, leaving anonymous calls without authentication state."""
        operation, config = call.operation, call.settings.auth
        if (bound := self._bound(operation, config)) is None or config is None:
            return
        from .auth_policy import validate_ownership, validate_patches  # noqa: PLC0415

        call.auth = _Authentication(bound)
        for headers in call.settings.headers:
            validate_patches(bound, headers, ())
        for query in call.settings.query:
            validate_patches(bound, (), query)
        if operation is not None:
            validate_ownership(
                bound,
                headers=_parameter_names(operation, "header"),
                query=_parameter_names(operation, "query"),
                cookies=_parameter_names(operation, "cookie"),
            )
            if operation.idempotency is not None:
                validate_ownership(bound, headers=(operation.idempotency.header_name,))

    @staticmethod
    def _auth_prepared(request: PreparedRequest[EncodedAttempt], call: _Call) -> None:
        from .auth_policy import validate_ownership  # noqa: PLC0415

        assert call.auth is not None
        validate_ownership(
            call.auth.bound,
            headers=(name for name, _ in request.headers),
            query=(unquote_plus(pair.partition("=")[0]) for pair in urlsplit(request.url).query.split("&") if pair),
            cookies=(
                pair.partition("=")[0].strip()
                for value in request.headers.get_all("cookie")
                for pair in value.split(";")
                if pair.strip()
            ),
        )

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
        url: str | None = None,
    ) -> tuple[PreparedRequest[EncodedAttempt], object]:
        """Return a call's request, and its body input when that builds its own attempts or else UNSET.

        Header patches apply in layers: the client's and views' over the generated headers, the parameters' over those,
        and the call's last; the body's media type and a narrowed Accept stay as the call chose them. A `url` a server
        gave replaces the one the operation's path and query build, without the query patches.
        """
        request = _parameters(operation, arguments)
        encoded = (
            None
            if operation.body is None
            else operation.body.encode(operation.operation_id, body, media_type, operation.codecs)
        )
        if url is None:
            base = self._base(operation, settings)
            path = request.path
            route = PLACEHOLDER.sub(lambda match: path[match[1]], operation.path) if path else operation.path
            if (
                path
                and ("/." in route or "/%2" in route)
                and dotted_route(route)
                and (name := _dot_parameter(operation.path, path)) is not None
            ):
                raise _encoding_error(operation, ("path", name), ParameterEncodingError(_DOT_CAUSE))
            query = self._call_query(operation, request.query, options)
            url = f"{base}{route}{'?' if query else ''}{query}"
        headers = [*self._shared.fixed]
        if accept is not None:
            headers.append(("Accept", accept))
        if request.cookies:
            request.headers.append(("Cookie", "; ".join(request.cookies)))
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

    def call_settings(self, options: RequestOptions | None, operation: OperationPlan[object, object]) -> Settings:
        """Return the settings a call of the operation runs with under these options."""
        return self._call_settings(options, operation.operation_id)

    def cache_store(self, name: str) -> object:
        """Return the cache store the client's protocol settings lend a helper, or None without one."""
        if (protocols := self._shared.protocols) is None or isinstance(stores := protocols.cache_stores, Unset):
            return None
        return stores.get(name)

    def cache_request(
        self, operation: OperationPlan[object, object], arguments: tuple[object, ...], options: RequestOptions | None
    ) -> CacheRequest:
        """Return what a cache fetch keys and sends: its settings, its request before auth, and its credentials.

        A cancelled, closing, or expired fetch is refused first, as a call is. The URL is the request's own as the
        client interprets it. The credentials are, for each credential the auth binds, its scheme, kind, and required
        scopes and the audience and requested scopes of an SDK token provider, and each signer's declared capabilities;
        they are None for a request that carries no credential, from the auth or from a credential header, a cookie, or
        a security scheme's header or query field.
        """
        settings = self._call_settings(options, operation.operation_id)
        LogicalCallContext(settings, self._scope, operation.operation_id).check()
        request, _ = self._prepare(
            operation,
            arguments,
            settings,
            body=UNSET,
            media_type=None,
            options=options,
            accept=operation.responses.accept,
            narrowed=False,
        )
        partition = None if (security := self._security_context()) is None else security.credential_partition
        url = absolute_target(request.url).url
        bound = self._bound(operation, settings.auth)
        from .security import secret_names  # noqa: PLC0415 - Only a cache fetch needs the schemes.

        names, queries = secret_names(self._shared.security_schemes)
        credential: WireValue = None
        if bound is not None:
            from .grants import grant_identity  # noqa: PLC0415 - Only an authenticated cache fetch keys its credentials.

            names = names.union(bound.managed_headers, ("cookie",) if bound.managed_cookies else ())
            credential = (
                tuple(
                    (item.scheme.name, item.scheme.kind, item.required_scopes, grant_identity(item.provider))
                    for item in bound.credentials
                ),
                tuple(
                    (
                        tuple(sorted(capabilities.allowed_origins)),
                        tuple(sorted(capabilities.managed_headers)),
                        tuple(sorted(capabilities.managed_query)),
                        capabilities.requires_body_digest,
                    )
                    for capabilities in (signer.capabilities for signer in bound.signers)
                ),
            )
        elif any(name.lower() in names for name, _ in request.headers) or any(
            unquote_plus(pair.partition("=")[0]) in queries for pair in urlsplit(url).query.split("&") if pair
        ):
            credential = ((), ())
        foreign = bound is not None and settings.auth is not self._shared.root_auth
        return CacheRequest(settings, request, url, credential, partition, foreign, names)

    def _security_context(self) -> ProtocolSecurityContext | None:
        """Return the client's protocol security context, or None without one."""
        if (protocols := self._shared.protocols) is None or isinstance(security := protocols.security, Unset):
            return None
        return security

    def follow_origins(
        self, operation: OperationPlan[object, object], options: RequestOptions | None
    ) -> frozenset[Origin]:
        """Return the origins a helper may follow a server's URLs to: its server's and those its security allows."""
        origins = {request_origin(self._base(operation, self._call_settings(options, operation.operation_id)))}
        if (context := self._security_context()) is not None:
            origins.update((origin.scheme, origin.host, origin.port) for origin in context.allowed_origins)
        return frozenset(origins)

    def checkpoint_security(
        self, operation: OperationPlan[object, object], options: RequestOptions | None
    ) -> tuple[WireValue, bool]:
        """Return what a helper's checkpoint is bound to, and whether it may leave the process.

        It is the credential partition and allowed origins of the client's protocol security context, the origin of the
        operation's server, the security schemes, kinds, and scopes the operation requires, and the identity of the
        call's auth, never a secret. A checkpoint of a call that may authenticate leaves only under a partition.
        """
        settings = self._call_settings(options, operation.operation_id)
        context = self._security_context()
        declared, auth = operation.security, settings.auth
        facts: WireValue = {
            "partition": None if context is None else context.credential_partition,
            "origins": ()
            if context is None
            else tuple(sorted((origin.scheme, origin.host, origin.port) for origin in context.allowed_origins)),
            "server": request_origin(self._base(operation, settings)),
            "requirements": None
            if declared is None
            else tuple(
                tuple(
                    (requirement.scheme.name, requirement.scheme.kind, *requirement.required_scopes)
                    for requirement in alternative
                )
                for alternative in declared.alternatives
            ),
            "auth": None if auth is None else _auth_identity(auth),
        }
        return facts, context is not None or (declared is None and auth is None)

    def _secret_positions(
        self, operation: OperationPlan[object, object] | None, options: RequestOptions | None
    ) -> tuple[frozenset[str], frozenset[str]]:
        """Return catalog and configured signer credential positions without acquiring credentials."""
        from .security import secret_names  # noqa: PLC0415

        headers, query = secret_names(self._shared.security_schemes)
        auth = self._call_settings(options, None if operation is None else operation.operation_id).auth
        if auth is not None:
            for signer in auth.signers:
                capabilities = signer.capabilities
                headers |= frozenset(name.lower() for name in capabilities.managed_headers)
                query |= frozenset(capabilities.managed_query)
        return headers, query

    def saved_response(
        self,
        info: ResponseInfo,
        operation: OperationPlan[object, object] | None,
        options: RequestOptions | None,
    ) -> ResponseInfo:
        """Copy response metadata without catalog, signer, or cookie credential positions."""
        headers, _ = self._secret_positions(operation, options)
        headers |= {"set-cookie", "set-cookie2"}
        source = None if operation is None else operation.request_id_header
        return replace(
            info,
            headers=HeadersView((name, value) for name, value in info.headers if name.lower() not in headers),
            request_id=None if source is None or source.lower() in headers else info.request_id,
            content_type=None if "content-type" in headers else info.content_type,
        )

    def queue_identity(
        self,
        operation: OperationPlan[object, object],
        request: PreparedRequest[EncodedAttempt],
        options: RequestOptions | None,
    ) -> WireValue:
        """Return immutable pre-auth replay facts, excluding every declared credential position."""
        from base64 import b64encode  # noqa: PLC0415

        auth = self._call_settings(options, operation.operation_id).auth
        if auth is not None:
            self._bound(operation, auth)
        headers, query = self._secret_positions(operation, options)
        target = absolute_target(request.url)
        return {
            "method": request.method,
            "url": strip_query(target.url, query),
            "headers": tuple((name.lower(), value) for name, value in request.headers if name.lower() not in headers),
            "body": None if request.body is None else b64encode(request.body.content).decode("ascii"),
        }

    def saved_queue_request(
        self,
        operation: OperationPlan[object, object],
        arguments: tuple[object, ...],
        body: object,
        media_type: str | MediaSelector | None,
        options: RequestOptions | None,
    ) -> WireValue:
        """Prepare the non-auth request a queue binds, using the ordinary encoding and patch rules."""
        request, _ = self._prepare(
            operation,
            arguments,
            self._call_settings(options, operation.operation_id),
            body=body,
            media_type=media_type,
            options=options,
            accept=operation.responses.accept,
            narrowed=False,
        )
        return self.queue_identity(operation, request, options)

    def unsaved_argument(
        self, operation: OperationPlan[object, object], saved: Sequence[WireValue | Unset]
    ) -> tuple[str, str] | None:
        """Return the location and name of the first given argument a checkpoint never saves, or None.

        It is a cookie, a header the client treats as a credential, a query parameter at the position of a declared
        security scheme, or a querystring whose value has a field at such a position.
        """
        headers, queries = self._secret_positions(operation, None)
        return next(
            (
                (spec.plan.location, spec.plan.name)
                for spec, value in zip(operation.parameters, saved, strict=True)
                if not isinstance(value, Unset) and _secret(spec, value, headers, queries)
            ),
            None,
        )

    def saved_request(  # noqa: PLR0913, PLR0917
        self,
        plan: _PagePlan,
        operation: OperationPlan[object, object],
        arguments: tuple[object, ...],
        body: object,
        media_type: str | MediaSelector | None,
        options: RequestOptions | None,
    ) -> tuple[tuple[WireValue | Unset, ...], tuple[WireValue, str, str | None] | None]:
        """Return the wire values of a helper call's arguments, and of its JSON body with its declared media type.

        They are encoded and checked as the call's first request encodes them; a body sent through a media selector
        also gives the selector's concrete media type. An argument `unsaved_argument` names is never saved, so a call
        giving one cannot be checkpointed.
        """
        self._call_settings(options, operation.operation_id)
        saved = tuple(
            value if isinstance(value, Unset) else _coded(operation, spec, partial(spec.encode, value))
            for spec, value in zip(operation.parameters, arguments, strict=True)
        )
        if (unsaved := self.unsaved_argument(operation, saved)) is not None:
            raise _unsaved(plan, ("arguments", *unsaved))
        request = operation.body
        if request is None or isinstance(body, Unset):
            return saved, None
        media = request.selected(operation.operation_id, media_type, operation.codecs)[0]
        try:
            wire = media.wire(body)
        except (*DATA_ERRORS, ValueError, TypeError) as error:
            raise RequestEncodingError(location=("body",), operation_id=operation.operation_id, cause=error) from None
        concrete = media_type.concrete_media if isinstance(media_type, MediaSelector) else None
        return saved, (wire, media.media_type, concrete)

    @staticmethod
    def restored_request(
        operation: OperationPlan[object, object],
        arguments: tuple[WireValue | Unset, ...],
        body: tuple[WireValue, str, str | None] | None,
    ) -> tuple[tuple[object, ...], object, str | MediaSelector | None]:
        """Return the arguments, body, and media type of a request a checkpoint saved, built from their wire values.

        Each value is validated and built as its codec builds a caller's wire value, and a concrete media type is
        selected as the operation's select method selects it; a value that does not fit raises RequestEncodingError.
        """
        restored = tuple(
            value if isinstance(value, Unset) else _coded(operation, spec, partial(spec.restored, value))
            for spec, value in zip(operation.parameters, arguments, strict=True)
        )
        if body is None or (request := operation.body) is None:
            return restored, UNSET, None
        from .codecs import request_media  # noqa: PLC0415 - Only a resumed request selects saved media.

        wire, declared, concrete = body
        media_type = declared if concrete is None else request_media(operation.codecs, declared, concrete)
        media = request.selected(operation.operation_id, media_type, operation.codecs)[0]
        try:
            return restored, media.restored(wire), media_type
        except (*DATA_ERRORS, ValueError, TypeError) as error:
            raise RequestEncodingError(location=("body",), operation_id=operation.operation_id, cause=error) from None

    def checked_page(
        self,
        operation: OperationPlan[object, object],
        request: Callable[[], tuple[tuple[object, ...], object, str | None]],
        media_type: str | MediaSelector | None,
        options: RequestOptions | None,
    ) -> tuple[str, HeadersView]:
        """Prepare a helper's request as its page's call prepares it, without sending, raising what that raises.

        The URL and headers it would send are returned, every patch applied.
        """
        arguments, body, url = request()
        settings = self._call_settings(options, operation.operation_id)
        prepared = self._prepare(
            operation,
            arguments,
            settings,
            body=body,
            media_type=media_type,
            options=options,
            accept=None,
            narrowed=False,
            url=url,
        )[0]
        return prepared.url, prepared.headers

    def checked_arguments(
        self, operation: OperationPlan[object, object], given: Mapping[int, WireValue], options: RequestOptions | None
    ) -> None:
        """Encode some arguments of a helper's request, by position, as its call encodes them, sending nothing.

        Arguments a later response gives are left out, even required ones; one that does not fit raises
        RequestEncodingError.
        """
        self._call_settings(options, operation.operation_id)
        for position, value in given.items():
            spec = operation.parameters[position]
            _coded(operation, spec, partial(_parameter, spec, value))

    def _page_request(
        self,
        call: _SessionCall,
        request: Callable[[], tuple[tuple[object, ...], object, str | None]],
        media_type: str | MediaSelector | None,
        options: RequestOptions | None,
        read_request: Callable[[str, HeadersView], None] | None,
    ) -> tuple[PreparedRequest[EncodedAttempt], object]:
        """Return a page's request, sent to the URL a server gave when the walk follows one, shown to any reader first.

        A followed URL is sent without the query fields of the package's security schemes and the auth's own, which
        the auth adds again, and to another origin than the server's without the credential and cookie headers, which
        only a provider authorized for that origin adds again.
        """
        operation = call.operation
        assert operation is not None
        arguments, sent, url = request()
        prepared, deferred = self._prepare(
            operation,
            arguments,
            call.settings,
            body=sent,
            media_type=media_type,
            options=options,
            accept=call.decoder.accept,
            narrowed=False,
            url=url,
        )
        if read_request is not None:
            read_request(prepared.url, prepared.headers)
        if url is None:
            return prepared, deferred
        server = call.server_origin = request_origin(self._base(operation, call.settings))
        url = strip_query(url, call.followed_query(self._shared.security_schemes))
        headers = prepared.headers
        if request_origin(url) != server:
            items, url = _uncredentialed(headers.items(), url, self._shared.security_schemes)
            headers = HeadersView(items)
        return PreparedRequest(method=prepared.method, url=url, headers=headers, body=prepared.body), deferred


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


def _transport(options: ClientOptions | None, http_client: object, adapter: object) -> ResolvedTransportOptions:
    resolved = resolve_transport_options(UNSET if options is None else options.transport)
    if resolved.retry_owner == "transport" and isinstance(adapter, Unset):
        raise ConfigurationError(field_path=("transport", "retry_owner"), condition="requires_adapter")
    if not isinstance(http_client, Unset) or not isinstance(adapter, Unset):
        for name in (
            "verify",
            "ssl_context",
            "proxy",
            "trust_env",
            "http2",
            "max_connections",
            "max_keepalive_connections",
            "keepalive_expiry",
        ):
            if getattr(resolved, name) != getattr(DEFAULT_TRANSPORT, name):
                raise ConfigurationError(field_path=("transport", name), condition="injected_transport")
    return resolved


def _adapter(
    http_client: httpx2.Client | Unset,
    ownership: str,
    transport_adapter: TransportAdapter | OwnedTransportAdapter[TransportAdapter] | Unset,
    transport: ResolvedTransportOptions,
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
        case _ if is_adapter(adapter):
            _declared(adapter)
            return adapter, owned
        case _:
            raise ConfigurationError(field_path=("transport_adapter",), condition="invalid_type")
    match http_client:
        case httpx2.Client():
            return Httpx2Transport(http_client), ownership == "owned"
        case Unset():
            return Httpx2Transport(native_client(transport), trusted_default=True, http2=transport.http2), True
        case _:
            pass
    raise ConfigurationError(field_path=("http_client",), condition="invalid_type")


def _async_adapter(
    http_client: httpx2.AsyncClient | Unset,
    ownership: str,
    transport_adapter: AsyncTransportAdapter | OwnedTransportAdapter[AsyncTransportAdapter] | Unset,
    transport: ResolvedTransportOptions,
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
        case _ if is_async_adapter(adapter):
            _declared(adapter)
            return adapter, owned
        case _:
            raise ConfigurationError(field_path=("transport_adapter",), condition="invalid_type")
    match http_client:
        case httpx2.AsyncClient():
            return AsyncHttpx2Transport(http_client), ownership == "owned"
        case Unset():
            return AsyncHttpx2Transport(
                native_async_client(transport), trusted_default=True, http2=transport.http2
            ), True
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


def _discarded(close: Callable[[], object], error: BaseException) -> bool:
    """Run a close while an error propagates, keeping its failure beside that error."""
    if (failure := quiet_close(close)) is None:
        return True
    if not isinstance(failure, Exception) and isinstance(error, Exception):
        raise failure
    cleanup_secondary(error, failure)
    return False


async def _adiscarded(close: Callable[[], Awaitable[None]], error: BaseException) -> None:
    """Await a close while an error propagates, keeping its failure beside that error."""
    if (failure := await quiet_aclose(close)) is None:
        return
    if not isinstance(failure, Exception) and isinstance(error, Exception):
        raise failure
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


def _finished_closes(failures: list[BaseException]) -> list[Exception]:
    """Publish cleanup interruption only after every owned resource was given its release opportunity."""
    ordinary: list[Exception] = []
    for failure in failures:
        if isinstance(failure, Exception):
            ordinary.append(failure)
        else:
            for secondary in failures:
                cleanup_secondary(failure, secondary)
            raise failure
    return ordinary


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
        providers = shared.providers if scope.owner is None else None
        if isinstance(providers, AsyncOwnedProviders):
            providers.start_aclose()
        failures = [failure for handle in remaining if (failure := await quiet_aclose(handle.aclose)) is not None]
        if owned and not shared.adapter_closed:
            shared.adapter_closed = True
            if (failure := await quiet_aclose(shared.adapter.aclose)) is not None:
                failures.append(failure)
        if isinstance(providers, AsyncOwnedProviders):
            failures.extend(await providers.drain())
        return _finished_closes(failures)
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
        parent_session_id=call.parent_session_id,
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
    """Acquire an async permit in the caller's task, which a stop of the call interrupts."""
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

    def _started(self, call: LogicalCallContext, path: str | None, options: RequestOptions | None) -> CallEvents | None:
        """Admit a call, reporting both boundary events when it is already stopped."""
        events = call_events(call.settings, call=call, path=path, asynchronous=False)
        try:
            self._admitted(call, options)
        except BaseException as error:  # noqa: BLE001
            failure = call.failure(error)
            try:
                if events is not None:
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
        settings = _client_settings(options)
        if settings.auth is not None:
            from .auth_policy import validate_auth_mode  # noqa: PLC0415

            validate_auth_mode(settings.auth, asynchronous=False)
        protocols = _protocol_options(options, defaults, asynchronous=False)
        transport = _transport(options, http_client, transport_adapter)
        adapter, owned = _adapter(http_client, http_client_ownership, transport_adapter, transport)
        shared = _Shared(defaults, adapter, transport, trusted=isinstance(adapter, Httpx2Transport))
        shared.protect(protocols, settings.clock, asynchronous=False)
        shared.root_auth = settings.auth
        result = cls(shared, settings, Scope(), owned=owned)
        if settings.auth is not None:
            result._adopt_auth(settings.auth)
        return result

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
        session: OperationSession | None = None,
        deadline: Deadline | None = None,
        admission: Callable[[], object] | None = None,
    ) -> Response[T]:
        """Execute one encoded logical call through its retry and redirect policy.

        A helper's call passes its session, and any deadline of its own: the call is a child of the session, and a
        deadline without a session is ignored.
        """
        settings = self._call_settings(options, operation.operation_id)
        call = (
            _Call(settings, self._scope, operation)
            if session is None
            else _SessionCall(settings, self._scope, operation, session, deadline)
        )
        if isinstance(call, _SessionCall):
            call.admission = admission
        events = call.events = self._started(call, operation.path, options)
        decoder = operation.responses

        def prepare() -> tuple[PreparedRequest[EncodedAttempt], object]:
            nonlocal decoder
            decoder = self._decoder(operation, response_media_type)
            call.decoder = decoder
            return self._prepare(
                operation,
                arguments,
                call.settings,
                body=body,
                media_type=media_type,
                options=options,
                accept=decoder.accept,
                narrowed=response_media_type is not None,
            )

        def receive(response: TransportResponse, info: ResponseInfo) -> Response[T]:
            received = self._read(response, info, decoder, call)
            call.check("decode")
            completed = _completed(decoder, info, received, call.settings, call.operation_id)
            call.check("decode")
            return completed

        try:
            if fields:
                body = operation.bound(body, fields, media_type)
            result = self._run(call, body, prepare, receive, options)
            call.check("decode")
            if events is not None:
                events.finish(result)
            call.check("decode")
        except BaseException as error:  # noqa: BLE001
            failure = call.stopped(error)
            if events is not None:
                events.ended(failure)
            raise failure from None
        else:
            return result
        finally:
            self._scope.release()
            call.finish()

    def execute_page(  # noqa: PLR0913
        self,
        plan: _PagePlan,
        operation: OperationPlan[T, object],
        request: Callable[[], tuple[tuple[object, ...], object, str | None]],
        build: Callable[[T, WireValue, bytes, ResponseInfo, str, frozenset[str]], R],
        *,
        body: object,
        media_type: str | MediaSelector | None,
        options: RequestOptions | None,
        session: OperationSession,
        max_page_bytes: int | None,
        read_request: Callable[[str, HeadersView], None] | None = None,
        failed: Callable[[BaseException, DeliveryState], None] | None = None,
    ) -> R:
        """Execute one page of a helper session as a child logical call, building what the page's response gives.

        The page reads at most `max_page_bytes` of its body, or the ordinary response limit without one, and its
        arguments, body, and any URL a server gave are taken when the call prepares; `body` is the caller's. What the
        page gives is built from its decoded body and its bytes, with the URL of the hop that returned it and the query
        fields a followed URL is without.
        """
        settings = self._call_settings(options, operation.operation_id)
        if page_limited := max_page_bytes is not None and (
            (limit := settings.max_response_bytes) is None or max_page_bytes <= limit
        ):
            settings = replace(settings, max_response_bytes=max_page_bytes)
        call = _SessionCall(settings, self._scope, operation, session)
        events = call.events = self._started(call, operation.path, options)
        decoder = call.decoder = operation.responses
        prepare = partial(self._page_request, call, request, media_type, options, read_request)

        def receive(response: TransportResponse, info: ResponseInfo) -> tuple[Response[T], R]:
            received = self._read(response, info, decoder, call)
            call.check("decode")
            data, wire, content = _page(decoder, info, received, call, plan, page_limited=page_limited)
            result = build(data, wire, content, info, call.url, call.followed_query(self._shared.security_schemes))
            call.check("decode")
            return Response(data=data, info=info), result

        try:
            completed, result = self._run(call, body, prepare, receive, options)
            call.check("decode")
            if events is not None:
                events.finish(completed)
            call.check("decode")
        except BaseException as error:  # noqa: BLE001
            failure = call.stopped(error)
            if failed is not None:
                failed(failure, _delivery(call))
            if events is not None:
                events.ended(failure)
            raise failure from None
        else:
            return result
        finally:
            self._scope.release()
            call.finish()

    def execute_cached(  # noqa: PLR0913, PLR0917
        self,
        operation: OperationPlan[T, object],
        request: PreparedRequest[EncodedAttempt],
        settings: Settings,
        modified: Callable[[Response[T], bytes], tuple[Response[T], R]],
        not_modified: Callable[[ResponseInfo], tuple[Response[T], R]],
        options: RequestOptions | None,
    ) -> R:
        """Send a cache fetch's prepared request as one logical call, building what its response gives.

        A 304 is built from the stored representation by `not_modified`; any other response is decoded as the
        operation's calls decode it and given to `modified` with its body after content decoding.
        """
        call = _Call(settings, self._scope, operation)
        events = call.events = self._started(call, operation.path, options)
        decoder = call.decoder = operation.responses

        def receive(response: TransportResponse, info: ResponseInfo) -> tuple[Response[T], R]:
            received = self._read(response, info, decoder, call)
            call.check("decode")
            built = (
                not_modified(info)
                if info.status_code == _NOT_MODIFIED
                else modified(_completed(decoder, info, received, call.settings, call.operation_id), received.content)
            )
            call.check("decode")
            return built

        try:
            completed, result = self._run(call, UNSET, lambda: (request, UNSET), receive, options)
            call.check("decode")
            if events is not None:
                events.finish(completed)
            call.check("decode")
        except BaseException as error:  # noqa: BLE001
            failure = call.stopped(error)
            if events is not None:
                events.ended(failure)
            raise failure from None
        else:
            return result
        finally:
            self._scope.release()
            call.finish()

    def execute_raw(  # noqa: PLR0912, PLR0913
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
        session: OperationSession | None = None,
    ) -> RawResponse:
        """Execute one encoded logical call through its retry and redirect policy.

        A helper's stream passes its session: the call is a child of it, and a response other than a declared success
        of the response media type raises the call's typed failure before the stream is handed over.
        """
        call = self._raw_call(operation, options, session)
        events = call.events = self._started(call, operation.path, options)
        decoder = operation.responses
        result: RawResponse | None = None
        handed = False

        def prepare() -> tuple[PreparedRequest[EncodedAttempt], object]:
            nonlocal decoder
            decoder = self._decoder(operation, response_media_type)
            call.decoder = decoder
            return self._prepare(
                operation,
                arguments,
                call.settings,
                body=body,
                media_type=media_type,
                options=options,
                accept=decoder.accept,
                narrowed=response_media_type is not None,
            )

        def receive(response: TransportResponse, info: ResponseInfo) -> RawResponse:
            return self._raw_response(response, info, call, stream=stream)

        try:
            if fields:
                body = operation.bound(body, fields, media_type)
            result = self._run(call, body, prepare, receive, options)
            call.check("send")
            if _auth_failed(call) or session is not None:
                result.raise_for_status()
            if session is not None:
                decoder.streamed(result.info)
            if stream:
                self._scope.handoff(result)
                handed = True
            if events is not None:
                events.finish(UNSET, handed_off=stream)
            call.check("send")
            if stream:
                call.handoff()
        except BaseException as error:  # noqa: BLE001
            failure = call.stopped(error)
            if result is not None:
                result.discard(failure)
            if events is not None:
                events.ended(failure)
            raise failure from None
        else:
            return result
        finally:
            if not handed:
                self._scope.release()
            if not call.streaming:
                call.finish()

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
        """Execute an unbound raw call with the same resource and retry ownership."""
        call = _Call(self._call_settings(options, None), self._scope)
        events = call.events = self._started(call, None, options)
        result: RawResponse | None = None
        handed = False

        def receive(response: TransportResponse, info: ResponseInfo) -> RawResponse:
            return self._raw_response(response, info, call, stream=stream)

        def prepare() -> tuple[PreparedRequest[EncodedAttempt], object]:
            return self._raw_prepared(method, url, body, options)

        try:
            result = self._run(call, body, prepare, receive, options)
            call.check("send")
            if _auth_failed(call):
                result.raise_for_status()
            if stream:
                self._scope.handoff(result)
                handed = True
            if events is not None:
                events.finish(UNSET, handed_off=stream)
            call.check("send")
            if stream:
                call.handoff()
        except BaseException as error:  # noqa: BLE001
            failure = call.stopped(error)
            if result is not None:
                result.discard(failure)
            if events is not None:
                events.ended(failure)
            raise failure from None
        else:
            return result
        finally:
            if not handed:
                self._scope.release()
            if not call.streaming:
                call.finish()

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

    def open_socket(  # noqa: PLR0913
        self,
        operation: OperationPlan[object, object],
        arguments: tuple[object, ...],
        adapter: TransportAdapter,
        *,
        options: RequestOptions | None,
        session: OperationSession,
        open_timeout: float | None,
        check: Callable[[HeadersView], None],
    ) -> tuple[RawResponse, LogicalCallContext]:
        """Open a WebSocket helper's handshake through its adapter, as one child call of the helper's session.

        The headers prepared pass the check before anything is sent. The 101 is handed over as a streaming handle,
        whose close closes the connection, with the call whose deadline bounds it; any other response raises the call's
        typed failure.
        """
        settings = self._call_settings(options, operation.operation_id)
        call = _SocketCall(settings, self._scope, operation, session, open_timeout)
        events = call.events = self._started(call, operation.path, options)
        call.decoder = operation.responses
        result: RawResponse | None = None
        handed = False

        def prepare() -> tuple[PreparedRequest[EncodedAttempt], object]:
            request, deferred = self._prepare(
                operation,
                arguments,
                call.settings,
                body=UNSET,
                media_type=None,
                options=options,
                accept=None,
                narrowed=False,
            )
            check(request.headers)
            return request, deferred

        def receive(response: TransportResponse, info: ResponseInfo) -> RawResponse:
            return self._raw_response(response, info, call, stream=True)

        try:
            result = self._run(call, UNSET, prepare, receive, options, adapter)
            call.check("send")
            if result.info.status_code != _SWITCHING:
                refused(result)
            self._scope.handoff(result)
            handed = True
            if events is not None:
                events.finish(UNSET, handed_off=True)
            call.check("send")
            call.handoff()
        except BaseException as error:  # noqa: BLE001
            failure = call.stopped(error)
            if result is not None:
                result.discard(failure)
            if events is not None:
                events.ended(failure)
            raise failure from None
        else:
            return result, call
        finally:
            if not handed:
                self._scope.release()
            if not call.streaming:
                call.finish()

    def _run(  # noqa: PLR0913, PLR0917
        self,
        call: _Call,
        body: object,
        prepare: Callable[[], tuple[PreparedRequest[EncodedAttempt], object]],
        receive: Callable[[TransportResponse, ResponseInfo], T],
        options: RequestOptions | None,
        adapter: TransportAdapter | None = None,
    ) -> T:
        """Own entry capture, the single encode, every hop, and final source release.

        A WebSocket handshake sends through its own adapter instead of the client's. A call of a grouped operation
        passes its circuit once encoded, before any credential or send, and records its outcome there once its hops
        and retries end.
        """
        entry: BodyBindings | None = None
        source: BodySource | None = None
        admission: tuple[Breaker, CircuitPermit] | None = None
        adapter = self._shared.adapter if adapter is None else adapter
        try:
            entry = (
                capture_body(body)
                if body is not UNSET and (isinstance(body, (FileBody, StreamBody)) or is_multipart(body))
                else None
            )
            call.check("encode")
            call.bind(adapter.capabilities, options)
            if call.settings.auth is not None or (call.operation is not None and call.operation.security is not None):
                self._bind_auth(call)
            if (events := call.events) is not None:
                events.emit(events.starting(call.settings))
            call.check("encode")
            request, deferred = prepare()
            request = call.prepared(request)
            if call.auth is not None:
                self._auth_prepared(request, call)
            coding = call.settings.compression
            request, compressing = (request, False) if coding is None else _compressed(coding, call, request, deferred)
            call.check("encode")
            if not isinstance(deferred, Unset):
                source = bind_body(deferred, entry=entry)
                source, entry = _gzip_source(source) if compressing else source, None
            elif entry is not None:
                abandoned, entry = entry, None
                _released(abandoned.close, call.operation_id, call.call_id)
            call.check("encode")
            if (breaker := self._shared.breaker) is not None:
                admission = _admission(self._shared, breaker, call, request.url)
            result = self._exchange(request, source, call, receive, adapter)
            if admission is not None:
                admitted, admission = admission, None
                _circuit_recorded(admitted, result, call)
        except BaseException as error:  # noqa: BLE001
            failure = call.failure(error)
            _abandoned(call, (source, entry), admission, failure)
            raise failure from None
        if source is not None:
            try:
                _released(source.close, call.operation_id, call.call_id)
            except BaseException as error:
                if isinstance(result, RawResponse):
                    result.discard(error)
                raise
        return result

    def _exchange(  # noqa: PLR0915
        self,
        original: PreparedRequest[EncodedAttempt],
        source: BodySource | None,
        call: _Call,
        receive: Callable[[TransportResponse, ResponseInfo], T],
        adapter: TransportAdapter,
    ) -> T:
        """Run resource candidates and their redirect hops without exposing intermediate payloads."""
        request = original
        events = call.events
        trusted = adapter is not self._shared.adapter or self._shared.trusted
        visited: frozenset[tuple[str, str]] = (
            frozenset({(request.method, request.url)}) if call.settings.redirects.enabled else _EMPTY_VISITED
        )
        if events is not None:
            events.prepare(request.url, call.attempt_index)
        while True:
            response: TransportResponse | None = None
            info: ResponseInfo | None = None
            sends_before = call.network_send_count
            call.response_transferred = False
            try:
                response = self._send(request, source, call, adapter)
                info = self._response_info(response, call.trace, call.request_id_header, call, trusted=trusted)
                call.received(info)
                if events is not None:
                    events.emit(events.responding(info))
                redirected = call.redirected(
                    request,
                    info,
                    visited,
                    replayable=not call.body_enabled or source is None or source.replayable,
                    schemes=self._shared.security_schemes,
                )
                if redirected is not None:
                    closing, response = response, None
                    _released(closing.close, call.operation_id, call.call_id)
                    call.check("send")
                    if events is not None:
                        events.emit(events.event("redirect", sent=True, status=info.status_code))
                        events.prepare(redirected.url, None)
                    request = redirected
                    visited |= {(request.method, request.url)}
                    call.hop_index += 1
                    call.trace = AttemptTrace(clock=call.settings.clock)
                    call.phase_caps = ()
                    continue
                planned = self._status_plan(info, source, call)
                if planned is None:
                    result = receive(response, info)
                    closing, response = response, None
                    if not call.response_transferred:
                        _released(closing.close, call.operation_id, call.call_id)
                    return result
                failure: BaseException = call.decoder.failure(info, b"", truncated=True)
                closing, response = response, None
                call.retry_blocked |= not _discarded(closing.close, failure)
            except BaseException as error:  # noqa: BLE001
                failure = self._exchange_failure(error, response, call, info)
                if not isinstance(failure, TransportError) or call.network_send_count == sends_before:
                    raise call.stopped(failure) from None
                planned = call.retry(
                    info,
                    failure,
                    replayable=source is None or source.replayable,
                    retry_owner=self._shared.transport.retry_owner,
                )
                if planned is None:
                    raise call.stopped(failure) from None
            self._wait_retry(planned, failure, call)
            request = original
            visited = call.restart(original)

    def _status_plan(self, info: ResponseInfo, source: BodySource | None, call: _Call) -> RetryDelay | None:
        """Plan a status retry, invalidating a rejected credential first and keeping the response when that fails."""
        planned = (
            call.retry(
                info,
                None,
                replayable=source is None or source.replayable,
                retry_owner=self._shared.transport.retry_owner,
            )
            if info.status_code >= _ERROR_STATUS
            else None
        )
        if planned is not None and planned.reason == "auth_invalid_token":
            self._invalidate(call, recovering=True)
            return None if call.retry_blocked else planned
        if planned is None and call.auth is not None and call.auth.rejected is not None:
            self._invalidate(call, recovering=False)
        return planned

    def _exchange_failure(
        self,
        error: BaseException,
        response: TransportResponse | None,
        call: _Call,
        info: ResponseInfo | None,
    ) -> BaseException:
        """Dispose the current response before publishing its classified failure and observed head."""
        events = call.events
        failure = self._failure(error, call, call.delivery_state)
        if isinstance(failure, SDKError) and info is not None:
            failure.info = info
        if response is not None and not call.response_transferred:
            call.retry_blocked |= not _discarded(response.close, failure)
        if (
            isinstance(failure, RedirectPolicyError)
            and info is None
            and (head := response_head(call.trace)) is not None
        ):
            call.delivery_state = DeliveryState.RESPONSE_STARTED
            failure.info = info = _info(head.status_code, head.headers, call.request_id_header, call)
            if events is not None:
                for hook_failure in events.notify(events.responding(info)):
                    add_secondary(failure, hook_failure)
        return failure

    @staticmethod
    def _wait_retry(planned: RetryDelay, failure: BaseException, call: _Call) -> None:
        """Finish the released candidate before its interruptible absolute wait."""
        events = call.events
        call.last_failure = failure
        if events is not None:
            events.finish(UNSET, error=failure, intermediate=True)
        call.resending(failure)
        if events is not None:
            events.emit(
                events.event(
                    "retry_scheduled",
                    sent=True,
                    duration=max(0.0, planned.not_before - call.monotonic()),
                    retry_reason=planned.reason,
                )
            )
        call.resending(failure)
        call.sleep_until(planned.not_before)
        call.resending(failure)

    def _raw_response(
        self,
        response: TransportResponse,
        info: ResponseInfo,
        call: _Call,
        *,
        stream: bool,
    ) -> RawResponse:
        """Own the final raw response before buffering, leaving its scope lease with the call until handoff."""
        source = response.iter_raw_bytes if self._shared.trusted else partial(_checked_chunks, response)
        handle = RawResponse(
            info,
            call.decoder,
            call.settings,
            call.operation_id,
            lambda error: self._failure(error, call, DeliveryState.RESPONSE_STARTED),
            source=source,
            close=response.close,
            scope=self._scope,
            events=call.events if stream else None,
            call=call,
            retry_stop_reason=call.stop_reason,
            status_secondary_errors=() if call.auth is None else call.auth.secondary_errors,
        )
        call.response_transferred = True
        if not stream:
            handle.read()
        return handle

    @staticmethod
    def _authenticate(call: _Call) -> None:
        from .auth_policy import (  # noqa: PLC0415
            BoundAuth,
            HopCredentials,
            accept_credential,
            authorize_hop,
            get_credential,
            refresh_credential,
            rejected_version,
        )

        auth = call.auth
        assert auth is not None
        bound = auth.bound
        assert isinstance(bound, BoundAuth)
        assert call.current_origin is not None
        authorize_hop(
            bound,
            origin=call.current_origin,
            server_origin=call.server_origin or call.initial_origin,
            raw=call.operation is None,
        )
        auth.rejected = None
        if not bound.credentials:
            return
        delivery = _delivery(call)
        with _auth_work(call):
            values: list[AcquiredCredential] = []
            for index, binding in enumerate(bound.credentials):
                call.check("auth")
                context = _credential_context(binding, call)
                value = get_credential(binding, context, delivery)
                call.check("auth")
                if (
                    (pending := auth.pending) is not None
                    and pending[0] == index
                    and rejected_version(value, pending[1])
                ):
                    values.append(refresh_credential(binding, context, delivery, call.settings.clock))
                    call.check("auth")
                else:
                    values.append(accept_credential(value, binding, context, delivery, call.settings.clock))
            auth.credentials = HopCredentials(tuple(values))
            auth.pending = None

    @staticmethod
    def _authenticated_request(
        request: PreparedRequest[BodyAttempt], attempt: BodyAttempt | None, source: BodySource | None, call: _Call
    ) -> PreparedRequest[BodyAttempt]:
        from .auth import SigningInput  # noqa: PLC0415
        from .auth_policy import BoundAuth, apply_signature, place_credentials, sign_request  # noqa: PLC0415
        from .native import finalize_unsigned  # noqa: PLC0415
        from .urls import origin_text, signing_query  # noqa: PLC0415

        auth = call.auth
        assert auth is not None
        bound = auth.bound
        assert isinstance(bound, BoundAuth)
        if attempt is not request.body:
            request = PreparedRequest(method=request.method, url=request.url, headers=request.headers, body=attempt)
        if auth.credentials is not None:
            request = place_credentials(request, bound, auth.credentials)
        if not bound.signers:
            return request
        request = finalize_unsigned(request)
        digest = None
        if bound.requires_body_digest and attempt is not None:
            from .body_sources import digest_body  # noqa: PLC0415

            digest = digest_body(attempt, source, check=partial(call.check, "auth"))
        assert call.current_origin is not None
        for signer in bound.signers:
            call.check("auth")
            signing = SigningInput(
                method=request.method,
                url=request.url,
                origin=origin_text(call.current_origin),
                query=signing_query(request.url),
                headers=request.headers,
                body_digest=digest,
                attempt_index=call.attempt_index,
                hop_index=call.hop_index,
            )
            fields = sign_request(signer.signer, signing, signer_index=signer.index)
            call.check("auth")
            request = apply_signature(request, fields, signer.capabilities)
        return request

    @staticmethod
    def _invalidate(call: _Call, *, recovering: bool) -> None:
        from .auth_policy import BoundAuth, invalidate_credential  # noqa: PLC0415

        auth = call.auth
        assert auth is not None
        rejected, auth.rejected = auth.rejected, None
        assert rejected is not None
        bound = auth.bound
        assert isinstance(bound, BoundAuth)
        if recovering:
            auth.recovery_used = True
        try:
            with _auth_work(call):
                invalidate_credential(bound.credentials[rejected[0]], rejected[1])
        except HookExecutionError:
            raise
        except Exception as error:  # noqa: BLE001
            call.check("auth")
            call.retry_blocked = True
            auth.secondary_errors = (*auth.secondary_errors, error)
            call.stop_reason = call.stop_reason or "callback_failure"
        else:
            if recovering:
                auth.pending = rejected

    def _send(  # noqa: PLR0912, PLR0915
        self,
        request: PreparedRequest[BodyAttempt],
        source: BodySource | None,
        call: _Call,
        adapter: TransportAdapter,
    ) -> TransportResponse:
        """Open one body after its permit and transfer resource ownership only after cleanup."""
        attempt = request.body
        permit: Permit | None = None
        events, trace = call.events, call.trace
        try:
            call.check("encode")
            call.retained()
            if isinstance(call, _SessionCall) and call.admission is not None:
                call.admission()
                call.check("send")
            self._authorize(source, call)
            if isinstance(call, _SessionCall) and call.admission is not None:
                call.admission()
                call.check("send")
            renewed = False
            while True:
                if (limiter := call.settings.limiter) is not None:
                    permit = _acquire(limiter, call, request.url, events)
                    call.check("limiter")
                    if events is not None:
                        events.emit(events.event("limiter_acquired"))
                if permit is None or not _reauthorizing(call):
                    break
                if renewed:
                    _usable_credentials(call)
                renewed = True
                releasing, permit = permit, None
                _released(partial(_release_permit, releasing), call.operation_id, call.call_id)
                self._authenticate(call)
            call.check("encode")
            call.retained()
            if source is not None and call.body_enabled:
                attempt = source.open(_context(call))
            call.check("encode")
            call.retained()
            request = self._outgoing(request, attempt, source, call)
            if events is not None and call.hop_index == 0:
                events.emit(events.attempting())
            call.retained()
            _usable_credentials(call)
            io = call.io_context(trace)
            if isinstance(call, _SessionCall) and call.admission is not None:
                call.admission()
                call.check("send")
            call.admit_send(redirect=call.hop_index != 0)
            if events is not None:
                events.sending()
            try:
                response = adapter.send(request, io)
            finally:
                call.observe_send(trace)
        except BaseException as error:  # noqa: BLE001
            failure = self._send_failure(error, attempt, permit, call)
            raise failure from None
        return self._sent(response, attempt, permit, call)

    def _authorize(self, source: BodySource | None, call: _Call) -> None:
        """Take the call's credentials before any permit, first refusing a body a signer cannot digest."""
        if (auth := call.auth) is None:
            return
        if source is not None and call.body_enabled and auth.bound.requires_body_digest:
            from .body_sources import require_digest_source  # noqa: PLC0415

            require_digest_source(source)
        self._authenticate(call)

    def _outgoing(
        self,
        request: PreparedRequest[BodyAttempt],
        attempt: BodyAttempt | None,
        source: BodySource | None,
        call: _Call,
    ) -> PreparedRequest[BodyAttempt]:
        """Place credentials and signatures, then check the body at each chunk as it is sent."""
        if call.auth is not None:
            request = self._authenticated_request(request, attempt, source, call)
        if attempt is None:
            return request
        return PreparedRequest(
            method=request.method, url=request.url, headers=request.headers, body=_CheckedBody(attempt, call)
        )

    @staticmethod
    def _sent(
        response: TransportResponse, attempt: BodyAttempt | None, permit: Permit | None, call: _Call
    ) -> TransportResponse:
        """Hand the response over with its permit once the attempt's body is released."""
        if permit is not None:
            response = _LimitedResponse(response, permit)
        if attempt is not None:
            try:
                _released(attempt.close, call.operation_id, call.call_id)
            except BaseException as error:  # noqa: BLE001
                failure = call.failure(error)
                call.retry_blocked |= not _discarded(response.close, failure)
                raise failure from None
        return response

    def _send_failure(
        self,
        error: BaseException,
        attempt: BodyAttempt | None,
        permit: Permit | None,
        call: _Call,
    ) -> BaseException:
        """Release all pre-response resources while preserving the original primary failure."""
        events = call.events
        failure = self._failure(error, call, _delivery(call))
        if isinstance(failure, TransportError):
            call.delivery_state = failure.delivery_state
            if events is not None:
                events.delivery = failure.delivery_state
        if attempt is not None:
            try:
                call.retry_blocked |= not _discarded(attempt.close, failure)
            except BaseException as interrupted:  # noqa: BLE001
                failure = call.failure(interrupted)
        if permit is not None:
            call.retry_blocked |= not _discarded(partial(_release_permit, permit), failure)
        return failure

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

    def reset_circuit(self, group: object, origin: object) -> None:
        """Close a circuit group's circuit at an origin for this client's partition, sending nothing."""
        if (key := self.circuit_key(group, origin)) is not None:
            breaker = self._shared.breaker
            assert breaker is not None
            breaker.reset(key)

    def close(self) -> None:
        """Stop new calls, wait for the active ones and open handles, and close the transport this client owns."""
        scope = self._scope
        if not scope.begin_close():
            return
        timeout = self._settings.cleanup_timeout
        remaining = scope.drain(timeout)
        failures = [failure for handle in remaining if (failure := quiet_close(handle.close)) is not None]
        shared = self._shared
        if self._owned and not shared.adapter_closed:
            shared.adapter_closed = True
            if (failure := quiet_close(shared.adapter.close)) is not None:
                failures.append(failure)
        if scope.owner is None and isinstance(shared.providers, OwnedProviders):
            failures.extend(shared.providers.close())
        pending, _ = scope.pending()
        if (error := _cleanup(pending, len(remaining), timeout, _finished_closes(failures))) is not None:
            raise error
        scope.finish()


def _checked_chunks(response: TransportResponse) -> Iterator[bytes]:
    return _raw(response.iter_raw_bytes())


def _achecked_chunks(response: AsyncTransportResponse) -> AsyncIterator[bytes]:
    return _araw(response.iter_raw_bytes())


class AsyncClientCore(_Core["AsyncTransportAdapter", "AsyncRawResponse"]):
    """Run the calls of an asyncio client and its views through one async transport adapter on one event loop."""

    __slots__ = ()
    _asynchronous: ClassVar[bool] = True

    async def _started(
        self, call: LogicalCallContext, path: str | None, options: RequestOptions | None
    ) -> CallEvents | None:
        """Admit a call, reporting both boundary events when it is already stopped."""
        events = call_events(call.settings, call=call, path=path, asynchronous=True)
        try:
            self._admitted(call, options)
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
        settings = _client_settings(options)
        if settings.auth is not None:
            from .auth_policy import validate_auth_mode  # noqa: PLC0415

            validate_auth_mode(settings.auth, asynchronous=True)
        protocols = _protocol_options(options, defaults, asynchronous=True)
        transport = _transport(options, http_client, transport_adapter)
        adapter, owned = _async_adapter(http_client, http_client_ownership, transport_adapter, transport)
        shared = _Shared(defaults, adapter, transport, trusted=isinstance(adapter, AsyncHttpx2Transport))
        shared.protect(protocols, settings.clock, asynchronous=True)
        shared.root_auth = settings.auth
        with suppress(RuntimeError):
            shared.loop = asyncio.get_running_loop()
        result = cls(shared, settings, Scope(), owned=owned)
        if settings.auth is not None:
            result._adopt_auth(settings.auth)
        return result

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
        session: OperationSession | None = None,
        deadline: Deadline | None = None,
        admission: Callable[[], object] | None = None,
    ) -> Response[T]:
        """Execute one encoded logical call through its retry and redirect policy.

        A helper's call passes its session, and any deadline of its own: the call is a child of the session, and a
        deadline without a session is ignored.
        """
        settings = self._call_settings(options, operation.operation_id)
        call = (
            _Call(settings, self._scope, operation)
            if session is None
            else _SessionCall(settings, self._scope, operation, session, deadline)
        )
        self._running(call.operation_id, call.call_id)
        if isinstance(call, _SessionCall):
            call.admission = admission
        events = call.events = await self._started(call, operation.path, options)
        decoder = operation.responses

        def prepare() -> tuple[PreparedRequest[EncodedAttempt], object]:
            nonlocal decoder
            decoder = self._decoder(operation, response_media_type)
            call.decoder = decoder
            return self._prepare(
                operation,
                arguments,
                call.settings,
                body=body,
                media_type=media_type,
                options=options,
                accept=decoder.accept,
                narrowed=response_media_type is not None,
            )

        async def receive(response: AsyncTransportResponse, info: ResponseInfo) -> Response[T]:
            received = await self._read(response, info, decoder, call)
            call.check("decode")
            completed = _completed(decoder, info, received, call.settings, call.operation_id)
            call.check("decode")
            return completed

        try:
            if fields:
                body = operation.bound(body, fields, media_type)
            result = await call.bounded(lambda: self._run(call, body, prepare, receive, options))
            call.check("decode")
            if events is not None:
                await events.afinish(result)
            call.check("decode")
        except BaseException as error:  # noqa: BLE001
            failure = call.stopped(error)
            if events is not None:
                await events.aended(failure)
            raise failure from None
        else:
            return result
        finally:
            self._scope.release()
            call.finish()

    async def execute_page(  # noqa: PLR0913
        self,
        plan: _PagePlan,
        operation: OperationPlan[T, object],
        request: Callable[[], tuple[tuple[object, ...], object, str | None]],
        build: Callable[[T, WireValue, bytes, ResponseInfo, str, frozenset[str]], R],
        *,
        body: object,
        media_type: str | MediaSelector | None,
        options: RequestOptions | None,
        session: OperationSession,
        max_page_bytes: int | None,
        read_request: Callable[[str, HeadersView], None] | None = None,
        failed: Callable[[BaseException, DeliveryState], None] | None = None,
    ) -> R:
        """Execute one page of a helper session as a child logical call, building what the page's response gives.

        The page reads at most `max_page_bytes` of its body, or the ordinary response limit without one, and its
        arguments, body, and any URL a server gave are taken when the call prepares; `body` is the caller's. What the
        page gives is built from its decoded body and its bytes, with the URL of the hop that returned it and the query
        fields a followed URL is without.
        """
        settings = self._call_settings(options, operation.operation_id)
        if page_limited := max_page_bytes is not None and (
            (limit := settings.max_response_bytes) is None or max_page_bytes <= limit
        ):
            settings = replace(settings, max_response_bytes=max_page_bytes)
        call = _SessionCall(settings, self._scope, operation, session)
        self._running(call.operation_id, call.call_id)
        events = call.events = await self._started(call, operation.path, options)
        decoder = call.decoder = operation.responses
        prepare = partial(self._page_request, call, request, media_type, options, read_request)

        async def receive(response: AsyncTransportResponse, info: ResponseInfo) -> tuple[Response[T], R]:
            received = await self._read(response, info, decoder, call)
            call.check("decode")
            data, wire, content = _page(decoder, info, received, call, plan, page_limited=page_limited)
            result = build(data, wire, content, info, call.url, call.followed_query(self._shared.security_schemes))
            call.check("decode")
            return Response(data=data, info=info), result

        try:
            completed, result = await call.bounded(lambda: self._run(call, body, prepare, receive, options))
            call.check("decode")
            if events is not None:
                await events.afinish(completed)
            call.check("decode")
        except BaseException as error:  # noqa: BLE001
            failure = call.stopped(error)
            if failed is not None:
                failed(failure, _delivery(call))
            if events is not None:
                await events.aended(failure)
            raise failure from None
        else:
            return result
        finally:
            self._scope.release()
            call.finish()

    async def execute_cached(  # noqa: PLR0913, PLR0917
        self,
        operation: OperationPlan[T, object],
        request: PreparedRequest[EncodedAttempt],
        settings: Settings,
        modified: Callable[[Response[T], bytes], tuple[Response[T], R]],
        not_modified: Callable[[ResponseInfo], tuple[Response[T], R]],
        options: RequestOptions | None,
    ) -> R:
        """Send a cache fetch's prepared request as one logical asyncio call, building what its response gives.

        A 304 is built from the stored representation by `not_modified`; any other response is decoded as the
        operation's calls decode it and given to `modified` with its body after content decoding.
        """
        call = _Call(settings, self._scope, operation)
        self._running(call.operation_id, call.call_id)
        events = call.events = await self._started(call, operation.path, options)
        decoder = call.decoder = operation.responses

        async def receive(response: AsyncTransportResponse, info: ResponseInfo) -> tuple[Response[T], R]:
            received = await self._read(response, info, decoder, call)
            call.check("decode")
            built = (
                not_modified(info)
                if info.status_code == _NOT_MODIFIED
                else modified(_completed(decoder, info, received, call.settings, call.operation_id), received.content)
            )
            call.check("decode")
            return built

        try:
            completed, result = await call.bounded(
                lambda: self._run(call, UNSET, lambda: (request, UNSET), receive, options)
            )
            call.check("decode")
            if events is not None:
                await events.afinish(completed)
            call.check("decode")
        except BaseException as error:  # noqa: BLE001
            failure = call.stopped(error)
            if events is not None:
                await events.aended(failure)
            raise failure from None
        else:
            return result
        finally:
            self._scope.release()
            call.finish()

    async def execute_raw(  # noqa: PLR0912, PLR0913
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
        session: OperationSession | None = None,
    ) -> AsyncRawResponse:
        """Execute one encoded logical call through its retry and redirect policy.

        A helper's stream passes its session: the call is a child of it, and a response other than a declared success
        of the response media type raises the call's typed failure before the stream is handed over.
        """
        call = self._raw_call(operation, options, session)
        self._running(call.operation_id, call.call_id)
        events = call.events = await self._started(call, operation.path, options)
        decoder = operation.responses
        result: AsyncRawResponse | None = None
        handed = False

        def prepare() -> tuple[PreparedRequest[EncodedAttempt], object]:
            nonlocal decoder
            decoder = self._decoder(operation, response_media_type)
            call.decoder = decoder
            return self._prepare(
                operation,
                arguments,
                call.settings,
                body=body,
                media_type=media_type,
                options=options,
                accept=decoder.accept,
                narrowed=response_media_type is not None,
            )

        async def receive(response: AsyncTransportResponse, info: ResponseInfo) -> AsyncRawResponse:
            return await self._raw_response(response, info, call, stream=stream)

        try:
            if fields:
                body = operation.bound(body, fields, media_type)
            result = await call.bounded(
                lambda: self._run(call, body, prepare, receive, options), cleanup=AsyncRawResponse.aclose
            )
            call.check("send")
            if _auth_failed(call) or session is not None:
                await result.raise_for_status()
            if session is not None:
                decoder.streamed(result.info)
            if stream:
                self._scope.handoff(result)
                handed = True
            if events is not None:
                await events.afinish(UNSET, handed_off=stream)
            call.check("send")
            if stream:
                call.handoff()
        except BaseException as error:  # noqa: BLE001
            failure = call.stopped(error)
            if result is not None:
                await result.discard(failure)
            if events is not None:
                await events.aended(failure)
            raise failure from None
        else:
            return result
        finally:
            if not handed:
                self._scope.release()
            if not call.streaming:
                call.finish()

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
        """Execute an unbound raw call with the same resource and retry ownership."""
        call = _Call(self._call_settings(options, None), self._scope)
        self._running(call.operation_id, call.call_id)
        events = call.events = await self._started(call, None, options)
        result: AsyncRawResponse | None = None
        handed = False

        async def receive(response: AsyncTransportResponse, info: ResponseInfo) -> AsyncRawResponse:
            return await self._raw_response(response, info, call, stream=stream)

        def prepare() -> tuple[PreparedRequest[EncodedAttempt], object]:
            return self._raw_prepared(method, url, body, options)

        try:
            result = await call.bounded(
                lambda: self._run(call, body, prepare, receive, options), cleanup=AsyncRawResponse.aclose
            )
            call.check("send")
            if _auth_failed(call):
                await result.raise_for_status()
            if stream:
                self._scope.handoff(result)
                handed = True
            if events is not None:
                await events.afinish(UNSET, handed_off=stream)
            call.check("send")
            if stream:
                call.handoff()
        except BaseException as error:  # noqa: BLE001
            failure = call.stopped(error)
            if result is not None:
                await result.discard(failure)
            if events is not None:
                await events.aended(failure)
            raise failure from None
        else:
            return result
        finally:
            if not handed:
                self._scope.release()
            if not call.streaming:
                call.finish()

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

    async def open_socket(  # noqa: PLR0913
        self,
        operation: OperationPlan[object, object],
        arguments: tuple[object, ...],
        adapter: AsyncTransportAdapter,
        *,
        options: RequestOptions | None,
        session: OperationSession,
        open_timeout: float | None,
        check: Callable[[HeadersView], None],
    ) -> tuple[AsyncRawResponse, LogicalCallContext]:
        """Open a WebSocket helper's handshake with asyncio, as the synchronous core does."""
        settings = self._call_settings(options, operation.operation_id)
        call = _SocketCall(settings, self._scope, operation, session, open_timeout)
        self._running(call.operation_id, call.call_id)
        events = call.events = await self._started(call, operation.path, options)
        call.decoder = operation.responses
        result: AsyncRawResponse | None = None
        handed = False

        def prepare() -> tuple[PreparedRequest[EncodedAttempt], object]:
            request, deferred = self._prepare(
                operation,
                arguments,
                call.settings,
                body=UNSET,
                media_type=None,
                options=options,
                accept=None,
                narrowed=False,
            )
            check(request.headers)
            return request, deferred

        async def receive(response: AsyncTransportResponse, info: ResponseInfo) -> AsyncRawResponse:
            return await self._raw_response(response, info, call, stream=True)

        try:
            result = await call.bounded(
                lambda: self._run(call, UNSET, prepare, receive, options, adapter), cleanup=AsyncRawResponse.aclose
            )
            call.check("send")
            if result.info.status_code != _SWITCHING:
                await arefused(result)
            self._scope.handoff(result)
            handed = True
            if events is not None:
                await events.afinish(UNSET, handed_off=True)
            call.check("send")
            call.handoff()
        except BaseException as error:  # noqa: BLE001
            failure = call.stopped(error)
            if result is not None:
                await result.discard(failure)
            if events is not None:
                await events.aended(failure)
            raise failure from None
        else:
            return result, call
        finally:
            if not handed:
                self._scope.release()
            if not call.streaming:
                call.finish()

    async def _run(  # noqa: PLR0913, PLR0917
        self,
        call: _Call,
        body: object,
        prepare: Callable[[], tuple[PreparedRequest[EncodedAttempt], object]],
        receive: Callable[[AsyncTransportResponse, ResponseInfo], Awaitable[T]],
        options: RequestOptions | None,
        adapter: AsyncTransportAdapter | None = None,
    ) -> T:
        """Own entry capture, the single encode, every hop, and final source release, passing grouped calls' circuit.

        A WebSocket handshake sends through its own adapter instead of the client's.
        """
        entry: AsyncBodyBindings | None = None
        source: AsyncBodySource | None = None
        admission: tuple[Breaker, CircuitPermit] | None = None
        adapter = self._shared.adapter if adapter is None else adapter
        try:
            entry = (
                await capture_async_body(body, cleanup=call.cleanup)
                if body is not UNSET and (isinstance(body, (AsyncFileBody, AsyncStreamBody)) or is_multipart(body))
                else None
            )
            call.check("encode")
            call.bind(adapter.capabilities, options)
            if call.settings.auth is not None or (call.operation is not None and call.operation.security is not None):
                self._bind_auth(call)
            if (events := call.events) is not None:
                await events.aemit(events.starting(call.settings))
            call.check("encode")
            request, deferred = prepare()
            request = call.prepared(request)
            if call.auth is not None:
                self._auth_prepared(request, call)
            coding = call.settings.compression
            request, compressing = (request, False) if coding is None else _compressed(coding, call, request, deferred)
            call.check("encode")
            if not isinstance(deferred, Unset):
                source = await bind_async_body(deferred, entry=entry, cleanup=call.cleanup)
                source, entry = _agzip_source(source) if compressing else source, None
            elif entry is not None:
                abandoned, entry = entry, None
                await call.cleanup(abandoned.aclose)
            call.check("encode")
            if (breaker := self._shared.breaker) is not None:
                admission = await _aadmission(self._shared, breaker, call, request.url)
            result = await self._exchange(request, source, call, receive, adapter)
            if admission is not None:
                admitted, admission = admission, None
                await _acircuit_recorded(admitted, result, call)
        except BaseException as error:  # noqa: BLE001
            failure = call.failure(error)
            await _aabandoned(call, (source, entry), admission, failure)
            raise failure from None
        if source is not None:
            try:
                await call.cleanup(source.aclose)
            except BaseException as error:
                if isinstance(result, AsyncRawResponse):
                    await result.discard(error)
                raise
        return result

    async def _exchange(  # noqa: PLR0915
        self,
        original: PreparedRequest[EncodedAttempt],
        source: AsyncBodySource | None,
        call: _Call,
        receive: Callable[[AsyncTransportResponse, ResponseInfo], Awaitable[T]],
        adapter: AsyncTransportAdapter,
    ) -> T:
        """Run resource candidates and their redirect hops without exposing intermediate payloads."""
        request = original
        events = call.events
        trusted = adapter is not self._shared.adapter or self._shared.trusted
        visited: frozenset[tuple[str, str]] = (
            frozenset({(request.method, request.url)}) if call.settings.redirects.enabled else _EMPTY_VISITED
        )
        if events is not None:
            events.prepare(request.url, call.attempt_index)
        while True:
            response: AsyncTransportResponse | None = None
            info: ResponseInfo | None = None
            sends_before = call.network_send_count
            call.response_transferred = False
            try:
                response = await self._send(request, source, call, adapter)
                info = self._response_info(response, call.trace, call.request_id_header, call, trusted=trusted)
                call.received(info)
                if events is not None:
                    await events.aemit(events.responding(info))
                redirected = call.redirected(
                    request,
                    info,
                    visited,
                    replayable=not call.body_enabled or source is None or source.replayable,
                    schemes=self._shared.security_schemes,
                )
                if redirected is not None:
                    closing, response = response, None
                    await call.cleanup(closing.aclose)
                    call.check("send")
                    if events is not None:
                        await events.aemit(events.event("redirect", sent=True, status=info.status_code))
                        events.prepare(redirected.url, None)
                    request = redirected
                    visited |= {(request.method, request.url)}
                    call.hop_index += 1
                    call.trace = AttemptTrace(clock=call.settings.clock)
                    call.phase_caps = ()
                    continue
                planned = await self._status_plan(info, source, call)
                if planned is None:
                    result = await receive(response, info)
                    closing, response = response, None
                    if not (call.response_transferred or (isinstance(closing, AsyncHttpx2Response) and closing.closed)):
                        await call.cleanup(closing.aclose)
                    return result
                failure: BaseException = call.decoder.failure(info, b"", truncated=True)
                closing, response = response, None
                await call.cleanup(closing.aclose, error=failure)
            except BaseException as error:  # noqa: BLE001
                failure = await self._exchange_failure(error, response, call, info)
                if not isinstance(failure, TransportError) or call.network_send_count == sends_before:
                    raise call.stopped(failure) from None
                planned = call.retry(
                    info,
                    failure,
                    replayable=source is None or source.replayable,
                    retry_owner=self._shared.transport.retry_owner,
                )
                if planned is None:
                    raise call.stopped(failure) from None
            await self._wait_retry(planned, failure, call)
            request = original
            visited = call.restart(original)

    async def _status_plan(self, info: ResponseInfo, source: AsyncBodySource | None, call: _Call) -> RetryDelay | None:
        """Plan a status retry, invalidating a rejected credential first and keeping the response when that fails."""
        planned = (
            call.retry(
                info,
                None,
                replayable=source is None or source.replayable,
                retry_owner=self._shared.transport.retry_owner,
            )
            if info.status_code >= _ERROR_STATUS
            else None
        )
        if planned is not None and planned.reason == "auth_invalid_token":
            await self._invalidate(call, recovering=True)
            return None if call.retry_blocked else planned
        if planned is None and call.auth is not None and call.auth.rejected is not None:
            await self._invalidate(call, recovering=False)
        return planned

    async def _exchange_failure(
        self,
        error: BaseException,
        response: AsyncTransportResponse | None,
        call: _Call,
        info: ResponseInfo | None,
    ) -> BaseException:
        """Dispose the current response before publishing its classified failure and observed head."""
        events = call.events
        failure = self._failure(error, call, call.delivery_state)
        if isinstance(failure, SDKError) and info is not None:
            failure.info = info
        if response is not None and not call.response_transferred:
            await call.cleanup(response.aclose, error=failure)
        if (
            isinstance(failure, RedirectPolicyError)
            and info is None
            and (head := response_head(call.trace)) is not None
        ):
            call.delivery_state = DeliveryState.RESPONSE_STARTED
            failure.info = info = _info(head.status_code, head.headers, call.request_id_header, call)
            if events is not None:
                for hook_failure in await events.anotify(events.responding(info)):
                    add_secondary(failure, hook_failure)
        return failure

    @staticmethod
    async def _wait_retry(planned: RetryDelay, failure: BaseException, call: _Call) -> None:
        """Finish the released candidate before its interruptible absolute wait."""
        events = call.events
        call.last_failure = failure
        if events is not None:
            await events.afinish(UNSET, error=failure, intermediate=True)
        call.resending(failure)
        if events is not None:
            await events.aemit(
                events.event(
                    "retry_scheduled",
                    sent=True,
                    duration=max(0.0, planned.not_before - call.monotonic()),
                    retry_reason=planned.reason,
                )
            )
        call.resending(failure)
        await call.asleep_until(planned.not_before)
        call.resending(failure)

    async def _raw_response(
        self,
        response: AsyncTransportResponse,
        info: ResponseInfo,
        call: _Call,
        *,
        stream: bool,
    ) -> AsyncRawResponse:
        """Own the final raw response before buffering, leaving its scope lease with the call until handoff."""
        source = response.iter_raw_bytes if self._shared.trusted else partial(_achecked_chunks, response)
        handle = AsyncRawResponse(
            info,
            call.decoder,
            call.settings,
            call.operation_id,
            lambda error: self._failure(error, call, DeliveryState.RESPONSE_STARTED),
            source=source,
            close=response.aclose,
            scope=self._scope,
            events=call.events if stream else None,
            call=call,
            retry_stop_reason=call.stop_reason,
            status_secondary_errors=() if call.auth is None else call.auth.secondary_errors,
        )
        call.response_transferred = True
        if not stream:
            await handle.read()
        return handle

    @staticmethod
    async def _authenticate(call: _Call) -> None:
        from .auth_policy import (  # noqa: PLC0415
            AsyncBoundAuth,
            AsyncHopCredentials,
            accept_credential,
            aget_credential,
            arefresh_credential,
            authorize_hop,
            rejected_version,
        )

        auth = call.auth
        assert auth is not None
        bound = auth.bound
        assert isinstance(bound, AsyncBoundAuth)
        assert call.current_origin is not None
        authorize_hop(
            bound,
            origin=call.current_origin,
            server_origin=call.server_origin or call.initial_origin,
            raw=call.operation is None,
        )
        auth.rejected = None
        if not bound.credentials:
            return
        delivery = _delivery(call)
        async with _aauth_work(call):
            values: list[AcquiredCredential] = []
            for index, binding in enumerate(bound.credentials):
                call.check("auth")
                context = _credential_context(binding, call)
                value = await aget_credential(binding, context, delivery)
                call.check("auth")
                if (
                    (pending := auth.pending) is not None
                    and pending[0] == index
                    and rejected_version(value, pending[1])
                ):
                    values.append(await arefresh_credential(binding, context, delivery, call.settings.clock))
                    call.check("auth")
                else:
                    values.append(accept_credential(value, binding, context, delivery, call.settings.clock))
            auth.credentials = AsyncHopCredentials(tuple(values))
            auth.pending = None

    @staticmethod
    async def _authenticated_request(
        request: PreparedRequest[AsyncBodyAttempt],
        attempt: AsyncBodyAttempt | None,
        source: AsyncBodySource | None,
        call: _Call,
    ) -> PreparedRequest[AsyncBodyAttempt]:
        from .auth import SigningInput  # noqa: PLC0415
        from .auth_policy import AsyncBoundAuth, apply_signature, asign_request, place_credentials  # noqa: PLC0415
        from .native import finalize_unsigned  # noqa: PLC0415
        from .urls import origin_text, signing_query  # noqa: PLC0415

        auth = call.auth
        assert auth is not None
        bound = auth.bound
        assert isinstance(bound, AsyncBoundAuth)
        if attempt is not request.body:
            request = PreparedRequest(method=request.method, url=request.url, headers=request.headers, body=attempt)
        if auth.credentials is not None:
            request = place_credentials(request, bound, auth.credentials)
        if not bound.signers:
            return request
        request = finalize_unsigned(request)
        digest = None
        if bound.requires_body_digest and attempt is not None:
            from .body_sources import adigest_body  # noqa: PLC0415

            digest = await adigest_body(attempt, source, check=partial(call.check, "auth"))
        assert call.current_origin is not None
        for signer in bound.signers:
            call.check("auth")
            signing = SigningInput(
                method=request.method,
                url=request.url,
                origin=origin_text(call.current_origin),
                query=signing_query(request.url),
                headers=request.headers,
                body_digest=digest,
                attempt_index=call.attempt_index,
                hop_index=call.hop_index,
            )
            fields = await asign_request(signer.signer, signing, signer_index=signer.index)
            call.check("auth")
            request = apply_signature(request, fields, signer.capabilities)
        return request

    @staticmethod
    async def _invalidate(call: _Call, *, recovering: bool) -> None:
        from .auth_policy import AsyncBoundAuth, ainvalidate_credential  # noqa: PLC0415

        auth = call.auth
        assert auth is not None
        rejected, auth.rejected = auth.rejected, None
        assert rejected is not None
        bound = auth.bound
        assert isinstance(bound, AsyncBoundAuth)
        if recovering:
            auth.recovery_used = True
        try:
            async with _aauth_work(call):
                await ainvalidate_credential(bound.credentials[rejected[0]], rejected[1])
        except HookExecutionError:
            raise
        except Exception as error:  # noqa: BLE001
            call.check("auth")
            call.retry_blocked = True
            auth.secondary_errors = (*auth.secondary_errors, error)
            call.stop_reason = call.stop_reason or "callback_failure"
        else:
            if recovering:
                auth.pending = rejected

    async def _send(  # noqa: PLR0912, PLR0915
        self,
        request: PreparedRequest[AsyncBodyAttempt],
        source: AsyncBodySource | None,
        call: _Call,
        adapter: AsyncTransportAdapter,
    ) -> AsyncTransportResponse:
        """Open one body after its permit and transfer resource ownership only after cleanup."""
        attempt = request.body
        permit: AsyncPermit | None = None
        events, trace = call.events, call.trace
        try:
            call.check("encode")
            call.retained()
            if isinstance(call, _SessionCall) and call.admission is not None:
                await cast("Awaitable[None]", call.admission())
                call.check("send")
            await self._authorize(source, call)
            if isinstance(call, _SessionCall) and call.admission is not None:
                await cast("Awaitable[None]", call.admission())
                call.check("send")
            renewed = False
            while True:
                if (limiter := call.settings.limiter) is not None:
                    permit = await _aacquire(limiter, call, request.url, events)
                    call.check("limiter")
                    if events is not None:
                        await events.aemit(events.event("limiter_acquired"))
                if permit is None or not _reauthorizing(call):
                    break
                if renewed:
                    _usable_credentials(call)
                renewed = True
                releasing, permit = permit, None
                await call.cleanup(partial(_arelease_permit, releasing))
                await self._authenticate(call)
            call.check("encode")
            call.retained()
            if source is not None and call.body_enabled:
                attempt = await source.aopen(_context(call))
            call.check("encode")
            call.retained()
            request = await self._outgoing(request, attempt, source, call)
            if events is not None and call.hop_index == 0:
                await events.aemit(events.attempting())
            call.retained()
            _usable_credentials(call)
            io = call.io_context(trace)
            if isinstance(call, _SessionCall) and call.admission is not None:
                await cast("Awaitable[None]", call.admission())
                call.check("send")
            call.admit_send(redirect=call.hop_index != 0)
            if events is not None:
                events.sending()
            try:
                response = await adapter.send(request, io)
            finally:
                call.observe_send(trace)
        except BaseException as error:  # noqa: BLE001
            failure = await self._send_failure(error, attempt, permit, call)
            raise failure from None
        return await self._sent(response, attempt, permit, call)

    async def _authorize(self, source: AsyncBodySource | None, call: _Call) -> None:
        """Take the call's credentials before any permit, first refusing a body a signer cannot digest."""
        if (auth := call.auth) is None:
            return
        if source is not None and call.body_enabled and auth.bound.requires_body_digest:
            from .body_sources import require_digest_source  # noqa: PLC0415

            require_digest_source(source)
        await self._authenticate(call)

    async def _outgoing(
        self,
        request: PreparedRequest[AsyncBodyAttempt],
        attempt: AsyncBodyAttempt | None,
        source: AsyncBodySource | None,
        call: _Call,
    ) -> PreparedRequest[AsyncBodyAttempt]:
        """Place credentials and signatures, then check the body at each chunk as it is sent."""
        if call.auth is not None:
            request = await self._authenticated_request(request, attempt, source, call)
        if attempt is None:
            return request
        return PreparedRequest(
            method=request.method, url=request.url, headers=request.headers, body=_AsyncCheckedBody(attempt, call)
        )

    @staticmethod
    async def _sent(
        response: AsyncTransportResponse, attempt: AsyncBodyAttempt | None, permit: AsyncPermit | None, call: _Call
    ) -> AsyncTransportResponse:
        """Hand the response over with its permit once the attempt's body is released."""
        if permit is not None:
            response = _AsyncLimitedResponse(response, permit)
        if attempt is not None and not isinstance(attempt, EncodedAttempt):
            try:
                await call.cleanup(attempt.aclose)
            except BaseException as error:  # noqa: BLE001
                failure = call.failure(error)
                await call.cleanup(response.aclose, error=failure)
                raise failure from None
        return response

    async def _send_failure(
        self,
        error: BaseException,
        attempt: AsyncBodyAttempt | None,
        permit: AsyncPermit | None,
        call: _Call,
    ) -> BaseException:
        """Release all pre-response resources while preserving the original primary failure."""
        events = call.events
        failure = self._failure(error, call, _delivery(call))
        if isinstance(failure, TransportError):
            call.delivery_state = failure.delivery_state
            if events is not None:
                events.delivery = failure.delivery_state
        if attempt is not None:
            try:
                await call.cleanup(attempt.aclose, error=failure)
            except BaseException as interrupted:  # noqa: BLE001
                failure = call.failure(interrupted)
        if permit is not None:
            await call.cleanup(partial(_arelease_permit, permit), error=failure)
        return failure

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

    async def reset_circuit(self, group: object, origin: object) -> None:
        """Close a circuit group's circuit at an origin for this client's partition, sending nothing."""
        if (key := self.circuit_key(group, origin)) is not None:
            breaker = self._shared.breaker
            assert breaker is not None
            await breaker.areset(key)

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
            providers = (
                shared.providers.pending()
                if scope.owner is None and isinstance(shared.providers, AsyncOwnedProviders)
                else 0
            )
            raise CleanupError(
                pending_calls=pending if pending or remaining or providers else 1,
                pending_leases=remaining,
                pending_providers=providers,
                timeout=timeout,
            )
        await scope.adrain(max(0.0, deadline - monotonic()))
        pending, remaining = scope.pending()
        if not (pending or remaining):
            scope.finish()
            tasks.pop(scope, None)
        failures = task_result(task)
        if (error := _cleanup(pending, remaining, timeout, failures)) is not None:
            raise error
