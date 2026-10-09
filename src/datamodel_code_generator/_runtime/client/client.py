"""Shared native HTTP client calls, request preparation, retries and decoding.

Roots close their created HTTP client once. Views share that root and never own resources;
borrowed HTTP clients and providers retain their caller's lifetime.
"""

from __future__ import annotations

import inspect
import re
from contextlib import (
    AbstractAsyncContextManager,
    AbstractContextManager,
    asynccontextmanager,
    contextmanager,
)
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, ClassVar, Final, Generic, Literal, TypeVar, cast
from urllib.parse import quote, unquote_plus, urlsplit

import httpx2
from typing_extensions import Self, TypeIs

from ..model_codecs.errors import ParameterEncodingError
from ..model_codecs.parameters import FragmentContribution, QueryStringContribution, encode_parameter
from ..model_codecs.unset import UNSET, Unset
from .bodies import EncodedAttempt, is_file_input
from .body_sources import RequestCoding, bind_body, capture_body
from .errors import (
    APIConnectionError,
    APIStatusError,
    AuthError,
    ConfigurationError,
    DecodeError,
    DeliveryState,
    ProtocolError,
    RetryStopReason,
    SDKError,
    add_secondary,
    is_hook_failure,
    is_http_error,
    is_transport,
    kept_primary,
    too_large,
)
from .events import CallEvents, aauth_ended, auth_ended, call_events
from .hooks import LimiterContext
from .logical import LogicalCallContext
from .media import normalized
from .multipart import MultipartSource, is_multipart, new_boundary
from .native import (
    async_decoded_bytes,
    async_response_bytes,
    decoded_bytes,
    native_async_client,
    native_client,
    native_error,
    native_timeout,
    request_fields,
    response_bytes,
    transport_retry_reason,
    wire_fields,
)
from .operations import ResponseDecoder, request_errors
from .options import (
    DEFAULT_TIMEOUT,
    DEFAULT_TRANSPORT,
    ClientOptions,
    HeaderPatch,
    IdempotencyKey,
    QueryPatch,
    RequestOptions,
    ServerSelection,
    Settings,
    TimeoutOptions,
    awaited,
    checked_base_url,
    context,
    layered_retry,
    resolve_transport_options,
)
from .paths import PLACEHOLDER, dot_segment, dotted_route, path_segments
from .raw import AsyncRawResponse, RawResponse
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
from .timing import ResolvedTimeoutOptions
from .urls import URLValidationError, absolute_target, request_origin, strip_query

if TYPE_CHECKING:
    from collections.abc import (
        AsyncGenerator,
        AsyncIterator,
        Awaitable,
        Callable,
        Generator,
        Iterable,
        Iterator,
        Sequence,
    )
    from typing import TypeGuard

    from ..model_codecs.media import JSONValue
    from ..model_codecs.parameters import ParameterFragment, ParameterPlan
    from .auth import (
        AuthConfig,
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
    from .bodies import AsyncContent, SyncContent
    from .body_sources import BodyBindings, BodySource
    from .hooks import AsyncLimiter, AsyncPermit, Limiter, Permit
    from .multipart import AsyncBodyInput, BodyInput
    from .operations import OperationPlan, ParameterSpec, ServerPlan
    from .options import ResolvedTransportOptions
    from .retry import RetryDelay
    from .security import SecuritySchemeEntry
    from .timing import Clock
    from .urls import Origin

T = TypeVar("T")
R = TypeVar("R")
AdapterT = TypeVar("AdapterT")
HandleT = TypeVar("HandleT")

MAX_ERROR_BODY_BYTES: Final = 64 * 1024
CLEANUP_TIMEOUT: Final = 5.0
_TOKEN: Final = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_DOT_CAUSE: Final = "A path value cannot make its segment '.' or '..', which URL normalization removes"
_OWNERSHIPS: Final = frozenset({"borrowed", "owned"})
_MIN_STATUS: Final = 200
_NOT_MODIFIED: Final = 304
_MAX_STATUS: Final = 599
_ERROR_STATUS: Final = 400
_BODY: Final = "datamodel_code_generator.body"
_SWITCHING: Final = 101
_UNAUTHORIZED: Final = 401


@dataclass(frozen=True, slots=True, kw_only=True)
class ClientDefaults:
    """The generated defaults of one client package, its helpers' kinds by name, and its request content coding."""

    security_schemes: tuple[SecuritySchemeEntry, ...] = ()
    helpers: tuple[tuple[str, str], ...] = ()
    request_coding: RequestCoding | None = None


_DEFAULT_SERVER: Final = ServerSelection()


def _layered(
    settings: Settings,
    layer: ClientOptions | RequestOptions,
    operation_id: str | None = None,
) -> Settings:
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
        settings.max_stream_bytes if isinstance(layer.max_stream_bytes, Unset) else layer.max_stream_bytes,
        (*settings.headers, layer.headers) if layer.headers else settings.headers,
        (*settings.query, layer.query) if layer.query else settings.query,
        settings.hooks if isinstance(layer.hooks, Unset) else layer.hooks,
        settings.context if isinstance(layer.context, Unset) else context({**settings.context, **layer.context}),
        settings.async_hooks if isinstance(layer.hooks, Unset) else awaited(layer.hooks),
        retry=layered_retry(settings.retry, layer.retry, operation_id),
        follow_redirects=settings.follow_redirects
        if isinstance(layer.follow_redirects, Unset)
        else layer.follow_redirects,
        idempotency_key=settings.idempotency_key if isinstance(layer.idempotency_key, Unset) else layer.idempotency_key,
        auth=settings.auth if isinstance(layer.auth, Unset) else layer.auth,
        timeout=_timeouts(settings.timeout, layer.timeout),
        total_timeout=settings.total_timeout if isinstance(layer.total_timeout, Unset) else layer.total_timeout,
        limiter=settings.limiter if isinstance(layer.limiter, Unset) else layer.limiter,
        compression=layer.compression if isinstance(layer, ClientOptions) else settings.compression,
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


def _client_settings(options: object, http_client: object) -> Settings:
    phases = DEFAULT_TIMEOUT
    if isinstance(http_client, (httpx2.Client, httpx2.AsyncClient)):
        timeout = http_client.timeout
        phases = ResolvedTimeoutOptions(
            connect=timeout.connect,
            read=timeout.read,
            write=timeout.write,
            pool=timeout.pool,
        )
    settings = Settings(None, _DEFAULT_SERVER, None, MAX_ERROR_BODY_BYTES, None, timeout=phases)
    match options:
        case None:
            return settings
        case ClientOptions():
            if not isinstance(clock := options.clock, Unset):
                settings = replace(settings, clock=clock)
            return _layered(settings, options)
        case _:
            pass
    raise ConfigurationError(field_path=("options",), reason="invalid_type")


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
                raise ConfigurationError(field_path=("headers", name), reason=condition, operation_id=operation_id)


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


def _server_url(operation: OperationPlan[object], selection: ServerSelection) -> str:
    operation_id = operation.operation_id
    if selection.index >= len(operation.servers):
        raise ConfigurationError(field_path=("server", "index"), reason="out_of_range", operation_id=operation_id)
    server = operation.servers[selection.index]
    declared = {variable.name: variable for variable in server.variables}
    if not selection.variables.keys() <= declared.keys():
        raise ConfigurationError(field_path=("server", "variables"), reason="undeclared", operation_id=operation_id)
    values: dict[str, str] = {}
    for name, variable in declared.items():
        values[name] = value = selection.variables.get(name, variable.default)
        if variable.enum and value not in variable.enum:
            raise ConfigurationError(
                field_path=("server", "variables", name), reason="not_allowed", operation_id=operation_id
            )
    return checked_base_url(PLACEHOLDER.sub(lambda match: values[match[1]], server.url), ("server",)).rstrip("/")


def request_decode_error(
    operation: OperationPlan[object], location: tuple[str | int, ...], error: BaseException | None = None
) -> DecodeError:
    return DecodeError(
        reason="unencodable", direction="request", location=location, operation_id=operation.operation_id, cause=error
    )


def exploded_object(plan: ParameterPlan) -> bool:
    """Return whether a parameter sends each property of its object value as a field of its own."""
    return plan.shape == "object" and plan.explode and plan.style in {"form", "cookie"}


def encode_parameter_value(operation: OperationPlan[object], spec: ParameterSpec, code: Callable[[], R]) -> R:
    """Return what coding an argument gives, raising a codec's refusal as the argument's encoding error."""
    try:
        return code()
    except request_errors(spec.codec) as error:
        raise request_decode_error(operation, (spec.plan.location, spec.plan.name), error) from None


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


def _parameters(operation: OperationPlan[object], arguments: tuple[object, ...]) -> _Request:
    """Return a call's request with its parameters encoded."""
    request = _Request()
    for spec, value in zip(operation.parameters, arguments, strict=True):
        plan = spec.plan
        if isinstance(value, Unset):
            if plan.required:
                raise request_decode_error(operation, (plan.location, plan.name))
            continue
        try:
            request.add(encode_parameter(plan, spec.dump(value)), plan.name)
        except request_errors(spec.codec) as error:
            raise request_decode_error(operation, (plan.location, plan.name), error) from None
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


class ReceivedBody:
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
        attempt_count=call.attempt_count,
        elapsed=call.monotonic() - call.started,
        content_type=None if content_type is None else normalized(content_type),
        request_id=None if request_id_header is None else headers.get(request_id_header),
    )


def _encoded(content: object, media_type: str | None) -> tuple[EncodedAttempt | None, object]:
    """Split a body into the attempt of bytes, sent as they are, and an input that builds its own attempts, or UNSET."""
    if type(content) is bytes:
        return EncodedAttempt(content, media_type), UNSET
    return None, content


def build_request(*, method: str, url: str, headers: HeadersView, body: EncodedAttempt | None) -> httpx2.Request:
    """Build a prepared request, keeping its body attempt, since HTTPX2 frames an absent body as an empty one."""
    return httpx2.Request(
        method,
        url,
        headers=wire_fields(headers.items()),
        content=None if body is None else body.content,
        extensions={} if body is None else {_BODY: body},
    )


def request_body(request: httpx2.Request) -> EncodedAttempt | None:
    return cast("EncodedAttempt | None", request.extensions.get(_BODY))


def _checked_raw(method: object, url: object) -> tuple[str, str]:
    """Normalize the raw method and validate its native URL before sending."""
    if not isinstance(method, str) or not _TOKEN.fullmatch(method):
        raise ConfigurationError(field_path=("method",), reason="invalid_value")
    if not isinstance(url, str):
        raise ConfigurationError(field_path=("url",), reason="invalid_url")
    try:
        target = absolute_target(url)
    except URLValidationError as error:
        raise ConfigurationError(field_path=("url",), reason="invalid_url", cause=error) from None
    return method.upper(), target.url


def delivery_state(call: Call) -> DeliveryState:
    return call.furthest()


def _received(decoder: ResponseDecoder[object], status: int, settings: Settings) -> ReceivedBody:
    success = decoder.success(status)
    return ReceivedBody(settings.max_response_bytes if success else settings.max_error_body_bytes, success=success)


def decode_response(
    decoder: ResponseDecoder[T], info: ResponseInfo, body: ReceivedBody, settings: Settings, operation_id: str | None
) -> Response[T]:
    if body.overflow:
        assert settings.max_response_bytes is not None
        raise too_large(info, settings.max_response_bytes, body.size, operation_id)
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


RAW_DECODER: Final[ResponseDecoder[object]] = ResponseDecoder((), ())


def _retry_error(error: BaseException) -> TypeGuard[APIStatusError | APIConnectionError]:
    return is_http_error(error) or is_transport(error)


def _compressed(
    call: Call, request: httpx2.Request, deferred: object, coding: RequestCoding | None
) -> tuple[httpx2.Request, RequestCoding | None]:
    """Encode a declared request body with the package's coding unless the client disabled it, replacing its framing.

    Return the request and the coding that a body building its own attempts still needs.
    """
    if coding is None or call.settings.compression is None or (operation := call.operation) is None:
        return request, None
    if coding.token not in operation.accepted_content_encodings or (
        request_body(request) is None and isinstance(deferred, Unset)
    ):
        return request, None
    if request.headers.get_list("content-encoding"):
        raise ConfigurationError(field_path=("headers", "Content-Encoding"), reason="managed")
    attempt = request_body(request)
    body = None if attempt is None else coding.attempt(attempt, partial(call.check, "encode"))
    headers = HeadersView((
        *(pair for pair in request_fields(request) if pair[0].lower() != "content-length"),
        ("Content-Encoding", coding.token),
    ))
    return build_request(method=request.method, url=str(request.url), headers=headers, body=body), coding


def _close_body(owner: BodySource | BodyBindings | None, call: Call, error: BaseException | None = None) -> None:
    """Close the files a call opened from paths; a close failure stays beside an error already propagating."""
    if owner is not None:
        _close_failed(owner.close(), call, error)


async def _aclose_body(owner: BodySource | BodyBindings | None, call: Call, error: BaseException | None = None) -> None:
    """Close the files an asyncio call opened from paths, in a thread, as the synchronous call reports them."""
    if owner is not None:
        _close_failed(await owner.aclose(), call, error)


def _close_failed(failures: list[OSError], call: Call, error: BaseException | None) -> None:
    if not failures:
        return
    if error is not None:
        add_secondary(error, *failures)
        return
    failure = SDKError(
        reason="cleanup_failed", operation_id=call.operation_id, call_id=call.call_id, cause=failures.pop(0)
    )
    add_secondary(failure, *failures)
    raise failure


def _framing(
    request: httpx2.Request, attempt: SyncContent | AsyncContent | EncodedAttempt | None
) -> list[tuple[str, str]]:
    """Return a request's headers without the framing HTTPX2 writes, with the Content-Length of a measured stream."""
    headers = [
        (name, value)
        for name, value in request_fields(request)
        if name.lower() not in {"host", "content-length", "transfer-encoding"}
    ]
    if not isinstance(attempt, EncodedAttempt | None) and (length := attempt.content_length) is not None:
        headers.append(("Content-Length", str(length)))
    return headers


class _Replayed:
    """A body HTTPX2 sends again when it follows a redirect: the opened attempt first, then its source reopened.

    A source that cannot be read again raises StreamConsumed, as a one-shot body does natively.
    """

    __slots__ = ("_attempt", "_source")

    def __init__(self, attempt: SyncContent, source: BodySource) -> None:
        self._attempt: SyncContent | None = attempt
        self._source = source

    def __iter__(self) -> Iterator[bytes]:
        if (attempt := self._attempt) is None:
            if not self._source.replayable:
                raise httpx2.StreamConsumed
            attempt = self._source.open()
        self._attempt = None
        return attempt.iter_bytes()


class _AsyncReplayed:
    """An asyncio body HTTPX2 sends again when it follows a redirect, as `_Replayed` is."""

    __slots__ = ("_attempt", "_source")

    def __init__(self, attempt: AsyncContent, source: BodySource) -> None:
        self._attempt: AsyncContent | None = attempt
        self._source = source

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._chunks()

    async def _chunks(self) -> AsyncIterator[bytes]:
        if (attempt := self._attempt) is None:
            if not self._source.replayable:
                raise httpx2.StreamConsumed
            attempt = await self._source.aopen()
        self._attempt = None
        async for chunk in attempt.aiter_bytes():
            yield chunk


def _hook_code(hook: Callable[..., object]) -> object:
    """Return the code a hook runs, a partial's or a callable object's `__call__` included."""
    target = hook.func if isinstance(hook, partial) else hook
    return getattr(target, "__code__", None) or getattr(type(target).__call__, "__code__", None)


def _in_hook(error: Exception, hooks: list[Callable[..., object]]) -> bool:
    """Return whether a failure was raised while one of the hooks ran."""
    codes = {_hook_code(hook) for hook in hooks}
    trace = error.__traceback__
    while trace is not None:
        if trace.tb_frame.f_code in codes:
            return True
        trace = trace.tb_next
    return False


def _answered(error: Exception, outgoing: httpx2.Request, hooks: list[Callable[..., object]]) -> bool:
    """Return whether a native failure came from a later request, a redirect or an Auth flow's, so one was answered.

    A one-shot body asked for again can only follow a redirect, so its StreamConsumed also means one was answered, as
    does a failure the HTTP client's own response hooks raise, which run only once a response arrived.
    """
    if isinstance(error, httpx2.StreamConsumed) or (hooks and _in_hook(error, hooks)):
        return True
    try:
        failed = error.request if isinstance(error, httpx2.RequestError) else outgoing
    except RuntimeError:
        return False
    return failed is not outgoing


def _redirects(response: httpx2.Response) -> int:
    """Count the redirects HTTPX2 followed, leaving out the responses an Auth flow answered, such as a challenge."""
    return sum(hop.has_redirect_location for hop in response.history)


def _token_unreceived(response: httpx2.Response) -> bool:
    """Return whether a followed redirect dropped the Authorization header the first request carried."""
    return bool(response.history) and (
        "authorization" in response.history[0].request.headers and "authorization" not in response.request.headers
    )


def _credentialed(request: httpx2.Request, schemes: tuple[SecuritySchemeEntry, ...]) -> bool:
    """Return whether a request carries a value at a declared scheme's header, query, or cookie, Authorization aside."""
    from .security import protected_positions  # noqa: PLC0415 - Only a package declaring schemes checks them.

    headers, query, cookies = protected_positions(schemes)
    sent = request.headers
    if any(name in sent for name in headers) or (query and any(name in request.url.params for name in query)):
        return True
    return bool(cookies) and any(
        pair.partition("=")[0].strip() in cookies for value in sent.get_list("cookie") for pair in value.split(";")
    )


def strip_credentials(
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


def _parameter_names(operation: OperationPlan[object], location: str) -> Iterator[str]:
    for parameter in operation.parameters:
        plan = parameter.plan
        if plan.location == location:
            if exploded_object(plan):
                yield from (field.name for field in plan.fields)
            else:
                yield plan.name


@contextmanager
def _auth_work(call: Call) -> Generator[None, None, None]:

    events = call.events
    started = call.monotonic() if events is not None else 0.0
    try:
        if events is not None:
            events.emit(events.event("auth_start", sent=events.sent))
        yield

    except BaseException as error:  # noqa: BLE001
        failure = call.failure(error)
        if events is not None:
            auth_ended(events, started, failure)
        raise failure from None
    if events is not None:
        auth_ended(events, started)


@asynccontextmanager
async def _aauth_work(call: Call) -> AsyncGenerator[None, None]:

    events = call.events
    started = call.monotonic() if events is not None else 0.0
    try:
        if events is not None:
            await events.aemit(events.event("auth_start", sent=events.sent))
        yield

    except BaseException as error:  # noqa: BLE001
        failure = call.failure(error)
        if events is not None:
            await aauth_ended(events, started, failure)
        raise failure from None
    if events is not None:
        await aauth_ended(events, started)


def _credential_context(binding: BoundCredential | AsyncBoundCredential, call: Call) -> CredentialContext:
    from .auth import CredentialContext  # noqa: PLC0415
    from .urls import origin_text  # noqa: PLC0415

    assert call.current_origin is not None
    return CredentialContext(
        scheme=binding.scheme.name,
        required_scopes=binding.required_scopes,
        audience=None,
        origin=origin_text(call.current_origin),
        deadline=call.deadline,
    )


def _expired_credentials(call: Call) -> bool:
    from .auth_policy import credentials_expired  # noqa: PLC0415

    assert call.auth is not None
    credentials = call.auth.credentials
    return credentials is not None and credentials_expired(credentials, now=call.monotonic())


def _reauthorizing(call: Call) -> bool:
    """Return whether the call's credentials expired while it waited for a permit."""
    return call.auth is not None and _expired_credentials(call)


def _auth_failed(call: Call) -> bool:
    """Return whether a local invalidation failed, which a raw call raises instead of returning its response."""
    return call.auth is not None and bool(call.auth.secondary_errors)


def _usable_credentials(call: Call) -> None:
    if call.auth is not None and _expired_credentials(call):
        raise AuthError(reason="token_expired", delivery_state=delivery_state(call))


class Call(LogicalCallContext):
    """Bind operation policy once while retaining the logical call's single ownership record."""

    handshake: ClassVar[bool] = False

    __slots__ = (
        "attempt_index",
        "auth",
        "current_origin",
        "decoder",
        "events",
        "idempotency",
        "initial_origin",
        "key",
        "last_failure",
        "last_info",
        "method",
        "operation",
        "permit",
        "previous_cap",
        "raw_response",
        "received_at",
        "received_wall_time",
        "request_id_header",
        "response_transferred",
        "retry_headers",
        "retry_safety",
        "server_origin",
        "stop_reason",
    )

    def __init__(self, settings: Settings, operation: OperationPlan[object] | None = None) -> None:
        super().__init__(settings, None if operation is None else operation.operation_id)
        self.auth: _Authentication | None = None
        self.operation = operation
        self.decoder: ResponseDecoder[object] = RAW_DECODER
        self.events: CallEvents | None = None
        self.request_id_header = None if operation is None else operation.request_id_header
        self.retry_safety: Literal["method_default", "idempotent", "never"] = (
            "method_default" if operation is None else operation.retry_safety
        )
        self.idempotency = None if operation is None else operation.idempotency
        key = settings.idempotency_key
        self.key = IdempotencyKey.new() if self.idempotency is not None and isinstance(key, Unset) else key
        self.last_failure: BaseException | None = None
        self.last_info: ResponseInfo | None = None
        self.raw_response = False
        self.retry_headers = EMPTY_RETRY_HEADERS
        self.initial_origin: Origin | None = None
        self.current_origin: Origin | None = None
        self.server_origin: Origin | None = None
        self.attempt_index = 0
        self.previous_cap: float | None = None
        self.stop_reason: RetryStopReason | None = None
        self.method = ""
        self.received_at = self.started
        self.received_wall_time = 0.0
        self.response_transferred = False
        self.permit: Permit | AsyncPermit | None = None

    def bind(self) -> None:
        """Validate operation-bound controls before hooks, encoding, and any send."""
        operation = self.operation
        if self.idempotency is None and isinstance(self.key, IdempotencyKey):
            raise ConfigurationError(field_path=("idempotency_key",), reason="not_declared")
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

    def prepared(self, request: httpx2.Request) -> httpx2.Request:
        """Retain the original method and attach the call's sole declared idempotency key."""
        self.method = request.method
        if self.auth is not None:
            self.initial_origin = self.current_origin = request_origin(str(request.url))
        if self.idempotency is None:
            return request
        name = self.idempotency.header_name
        if request.headers.get_list(name):
            raise ConfigurationError(field_path=("headers", name), reason="managed")
        if not isinstance(self.key, IdempotencyKey):
            return request
        return build_request(
            method=request.method,
            url=str(request.url),
            headers=HeadersView((*request_fields(request), (name, self.key.value))),
            body=request_body(request),
        )

    def received(self, info: ResponseInfo) -> None:
        """Retain header receipt timing before callbacks and body decoding."""
        self.last_info = info
        self.received_at = self.monotonic()
        self.received_wall_time = self.settings.clock.time()

    def retry(
        self,
        info: ResponseInfo | None,
        error: APIConnectionError | None,
        *,
        replayable: bool,
    ) -> RetryDelay | None:
        """Apply the ordered pure gates and retain one absolute delay before response disposal."""
        if error is None:
            self.check("send")
        elif (expired := self.expired("send", cause=error.cause)) is not None:
            expired.phase = error.phase
            raise expired
        if self.retry_blocked:
            self.stop_reason = "callback_failure"
            return None
        retry = self.settings.retry
        headers = None if info is None else info.headers
        hint = should_retry(headers, self.retry_headers.should_retry_header)
        reason = (
            transport_retry_reason(error)
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
        now = self.monotonic()
        self.stop_reason = retry_stop(
            RetryState(
                failure_kind="auth" if auth_candidate else "transport" if error is not None else "status",
                reason=reason,
                method=self.method,
                retry_safety=self.retry_safety,
                idempotency=self.idempotency if isinstance(self.key, IdempotencyKey) else None,
                delivery_state=self.delivery_state,
                delivered_before=self.earlier is not DeliveryState.NOT_SENT,
                attempt_count=self.attempt_count,
                body_replayable=replayable,
                server_hint=hint,
            ),
            retry,
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
        if isinstance(failure, APIConnectionError) and self.delivery_state is not DeliveryState.NOT_SENT:
            self.stop_reason = "unknown_delivery"
        if _retry_error(failure):
            failure.retry_stop_reason = self.stop_reason
            if self.auth is not None:
                for secondary in self.auth.secondary_errors:
                    if all(existing is not secondary for existing in failure.secondary_errors):
                        add_secondary(failure, secondary)
        return failure

    def resending(self, error: BaseException) -> None:
        """Recheck termination before waiting or opening another body."""
        self.check("sleep")
        if self.retry_blocked:
            self.stop_reason = "callback_failure"
            raise self.stopped(error)

    def restart(self, original: httpx2.Request) -> None:
        """Begin the next resource candidate from the once-encoded original request."""
        self.attempt_index += 1
        self.current_origin = self.initial_origin
        self.stop_reason = None
        if self.events is not None:
            self.events.prepare(str(original.url), self.attempt_index)

    def follow(self, outgoing: httpx2.Request, schemes: tuple[SecuritySchemeEntry, ...]) -> bool | None:
        """Return whether HTTPX2 follows a redirect of the outgoing request, or None for the HTTP client's own choice.

        A request carrying a credential at a position a declared security scheme names, other than Authorization,
        which HTTPX2 drops across origins itself, or a signature, is never redirected; any other takes the call's
        setting, or else the HTTP client's.
        """
        if (self.auth is not None and self.auth.bound.signers) or (schemes and _credentialed(outgoing, schemes)):
            return False
        return self.settings.follow_redirects


class _Shared(Generic[AdapterT]):
    """The native client and construction ownership shared by root and option views."""

    def __init__(
        self, defaults: ClientDefaults, http_client: AdapterT, transport: ResolvedTransportOptions, *, created: bool
    ) -> None:
        self.http_client = http_client
        self.created = created
        self.closed = False
        self.transport = transport
        self.security_schemes = defaults.security_schemes
        self.request_coding = defaults.request_coding
        coding = cast("httpx2.Client | httpx2.AsyncClient", http_client).headers.get("accept-encoding")
        self.fixed: tuple[tuple[str, str], ...] = () if coding is None else (("Accept-Encoding", coding),)
        self.options: ClientOptions | None = None
        self.root_auth: AuthConfig | None = None
        self.sockets: set[Callable[[], None]] = set()


class Core(Generic[AdapterT, HandleT]):
    __slots__ = ("_settings", "_shared", "_urls")
    _asynchronous: ClassVar[bool] = False

    def __init__(self, shared: _Shared[AdapterT], settings: Settings) -> None:
        self._shared = shared
        self._settings = settings
        self._urls: dict[int, tuple[tuple[ServerPlan, ...], str]] = {}

    @property
    def clock(self) -> Clock:
        """Return the clock that times this client's calls and helper sessions."""
        return self._settings.clock

    def _raw_call(self, operation: OperationPlan[object], options: RequestOptions | None, call: Call | None) -> Call:
        """Prepare an ordinary raw call or retain the helper's prepared call."""
        if call is None:
            call = Call(self._call_settings(options, operation.operation_id), operation)
        call.raw_response = True
        return call

    def helper_view(self, build: Callable[[_Shared[AdapterT], Settings], R]) -> R:
        """Bind a declared helper to this view's shared resources and effective settings."""
        return build(self._shared, self._settings)

    def view(self, options: object) -> Self:
        """Layer request options while sharing the native client with the root."""
        if not isinstance(options, RequestOptions):
            raise ConfigurationError(field_path=("options",), reason="invalid_type")
        return type(self)(self._shared, self._call_settings(options, None))

    def _admitted(self, call: LogicalCallContext) -> None:
        if self._shared.closed:
            raise call.snapshot_error(ConfigurationError(reason="client_closed", field_path=("client", "closed")))
        call.check()

    @staticmethod
    def _failure(error: BaseException, call: LogicalCallContext, delivery: DeliveryState) -> BaseException:
        """Preserve cancellation and classify an ordinary failure."""
        return Core._classified(error, call, delivery) if isinstance(error, Exception) else error

    @staticmethod
    def _classified(error: Exception, call: LogicalCallContext, delivery: DeliveryState) -> SDKError:
        """Classify an ordinary failure by its public send boundary.

        A phase timeout whose cap was the call's remaining time is the call's deadline expiring.
        """
        failure = (
            error
            if isinstance(error, SDKError)
            else native_error(
                error,
                send_started=call.delivery_state is not DeliveryState.NOT_SENT,
                response_started=delivery is DeliveryState.RESPONSE_STARTED,
            )
        )
        return call.snapshot_error(failure)

    @staticmethod
    def _response_info(
        response: httpx2.Response, request_id_header: str | None, call: LogicalCallContext
    ) -> ResponseInfo:
        call.delivery_state = DeliveryState.RESPONSE_STARTED
        return _info(response.status_code, HeadersView(response.headers.multi_items()), request_id_header, call)

    def _raw_prepared(
        self, method: object, url: object, body: object, options: RequestOptions | None
    ) -> tuple[httpx2.Request, object]:
        """Return a raw call's request to any URL, with the client's fixed headers.

        Bytes are the request's attempt; any other body is returned beside it, to build its own attempt.
        """
        verb, target = _checked_raw(method, url)
        if self._settings.query or (options is not None and options.query):
            base, _, explicit = target.partition("?")
            call = () if options is None else options.query
            query = _query(self._settings.query, [pair for pair in explicit.split("&") if pair], call)
            target = f"{base}?{query}" if query else base
        media_type = None
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
        return build_request(method=verb, url=target, headers=headers, body=attempt), deferred

    def _base(self, operation: OperationPlan[object], settings: Settings) -> str:
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
    def _decoder(operation: OperationPlan[T], response_media_type: str | None) -> ResponseDecoder[T]:
        """Return the operation's decoder, narrowed to the call's response media or else the operation's."""
        decoder = operation.responses
        media_type = response_media_type or operation.response_media_type
        return decoder if media_type is None else decoder.narrowed(operation.operation_id, media_type)

    def _call_settings(self, options: object, operation_id: str | None) -> Settings:
        """Return the settings a call runs with: this client's or view's, with the call's options layered on them."""
        if options is None:
            return self._settings
        if not isinstance(options, RequestOptions):
            raise ConfigurationError(field_path=("options",), reason="invalid_type", operation_id=operation_id)
        if not isinstance(options.auth, Unset) and options.auth is not None:
            from .auth_policy import validate_auth_mode  # noqa: PLC0415

            validate_auth_mode(options.auth, asynchronous=self._asynchronous)
        return _layered(self._settings, options, operation_id)

    def _bound(
        self, operation: OperationPlan[object] | None, config: AuthConfig | None
    ) -> BoundAuth | AsyncBoundAuth | None:
        """Select the auth a call binds without invoking callbacks, refusing missing credentials it requires."""
        security = None if operation is None else operation.security
        if config is None:
            if security is not None and security.alternatives and all(security.alternatives):
                raise ConfigurationError(field_path=("auth",), reason="missing_credentials")
            return None
        from .auth_policy import bind_async_auth, bind_auth  # noqa: PLC0415

        return (
            bind_async_auth(config, security, self._shared.security_schemes)
            if self._asynchronous
            else bind_auth(config, security, self._shared.security_schemes)
        )

    def _bind_auth(self, call: Call) -> None:
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
    def _auth_prepared(request: httpx2.Request, call: Call) -> None:
        from .auth_policy import validate_ownership  # noqa: PLC0415

        assert call.auth is not None
        validate_ownership(
            call.auth.bound,
            headers=(name for name, _ in request_fields(request)),
            query=(
                unquote_plus(pair.partition("=")[0]) for pair in urlsplit(str(request.url)).query.split("&") if pair
            ),
            cookies=(
                pair.partition("=")[0].strip()
                for value in request.headers.get_list("cookie")
                for pair in value.split(";")
                if pair.strip()
            ),
        )

    def _prepare(  # noqa: PLR0913
        self,
        operation: OperationPlan[object],
        arguments: tuple[object, ...],
        settings: Settings,
        *,
        body: object,
        media_type: str | None,
        options: RequestOptions | None,
        accept: str | None,
        narrowed: bool,
        url: str | None = None,
        checked: Callable[[HeadersView], None] | None = None,
    ) -> tuple[httpx2.Request, object]:
        """Return a call's request, and its body input when that builds its own attempts or else UNSET.

        Header patches apply in layers: the client's and views' over the generated headers, the parameters' over those,
        and the call's last; the body's media type and a narrowed Accept stay as the call chose them. A `url` a server
        gave replaces the one the operation's path and query build, without the query patches. `checked` sees the
        headers before the native request adds its own.
        """
        request = _parameters(operation, arguments)
        encoded = None if operation.body is None else operation.body.encode(operation.operation_id, body, media_type)
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
                raise request_decode_error(operation, ("path", name), ParameterEncodingError(_DOT_CAUSE))
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
        if checked is not None:
            checked(prepared)
        if encoded is not None:
            attempt, deferred = _encoded(encoded.content, encoded.media_type)
        return build_request(method=operation.method, url=url, headers=prepared, body=attempt), deferred

    def _call_query(self, operation: OperationPlan[object], pairs: list[str], options: RequestOptions | None) -> str:
        """Return a typed call's query: its parameters' pairs, patched when a layer patches them.

        An operation whose querystring parameter carries its whole query takes no query patch.
        """
        call = () if options is None else options.query
        if not self._settings.query and not call:
            return "&".join(pairs)
        if any(spec.plan.location == "querystring" for spec in operation.parameters):
            raise ConfigurationError(
                field_path=("query",), reason="conflicts_with_querystring", operation_id=operation.operation_id
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

    def call_settings(self, options: RequestOptions | None, operation: OperationPlan[object]) -> Settings:
        """Return the settings a call of the operation runs with under these options."""
        return self._call_settings(options, operation.operation_id)


def _transport(options: ClientOptions | None, http_client: object) -> ResolvedTransportOptions:
    resolved = resolve_transport_options(UNSET if options is None else options.transport)
    if http_client is not None and not isinstance(http_client, Unset):
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
                raise ConfigurationError(field_path=("transport", name), reason="injected_transport")
    return resolved


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
    )


def _release_permit(permit: Permit) -> None:
    try:
        permit.release()
    except Exception as error:  # noqa: BLE001
        raise SDKError(reason="limiter_failed", cause=error) from None


async def _arelease_permit(permit: AsyncPermit) -> None:
    try:
        await permit.release()
    except Exception as error:  # noqa: BLE001
        raise SDKError(reason="limiter_failed", cause=error) from None


def _acquire(limiter: Limiter | AsyncLimiter, call: LogicalCallContext, url: str, events: CallEvents | None) -> Permit:
    """Acquire a mode-correct permit, leaving all post-acquisition work to its owner."""
    if not _is_limiter(limiter):
        raise ConfigurationError(field_path=("limiter",), reason="async_limiter")
    if events is not None:
        events.origin = _limiter_context(call, url).origin
        events.emit(events.event("limiter_wait"))
    try:
        permit = limiter.acquire(_limiter_context(call, url))
    except Exception as error:  # noqa: BLE001
        raise SDKError(reason="limiter_failed", cause=error) from None
    if not _is_permit(permit):
        raise SDKError(reason="limiter_failed", cause=TypeError("Expected a synchronous Permit"))
    return permit


async def _aacquire(
    limiter: Limiter | AsyncLimiter, call: LogicalCallContext, url: str, events: CallEvents | None
) -> AsyncPermit:
    """Acquire an async permit in the caller's task, which a stop of the call interrupts."""
    if not _is_async_limiter(limiter):
        raise ConfigurationError(field_path=("limiter",), reason="sync_limiter")
    if events is not None:
        events.origin = _limiter_context(call, url).origin
        await events.aemit(events.event("limiter_wait"))
    try:
        permit = await limiter.acquire(_limiter_context(call, url))
    except Exception as error:  # noqa: BLE001
        raise SDKError(reason="limiter_failed", cause=error) from None
    if not _is_async_permit(permit):
        raise SDKError(reason="limiter_failed", cause=TypeError("Expected an asynchronous Permit"))
    return permit


class ClientCore(Core["httpx2.Client", "RawResponse"]):
    """Run the calls of a synchronous client and its views through one transport adapter."""

    __slots__ = ()

    def _started(self, call: LogicalCallContext, path: str | None) -> CallEvents | None:
        """Admit a call, reporting both boundary events when it is already stopped."""
        events = call_events(call.settings, call=call, path=path, asynchronous=False)
        try:
            self._admitted(call)
        except BaseException as error:  # noqa: BLE001
            failure = call.failure(error)
            if events is not None:
                events.ended(failure)
            raise failure from None
        return events

    @classmethod
    def create(
        cls,
        defaults: ClientDefaults,
        *,
        options: ClientOptions | None = None,
        http_client: httpx2.Client | Unset | None = UNSET,
    ) -> Self:
        """Borrow a mode-correct native client, or create and own one from transport settings."""
        settings = _client_settings(options, http_client)
        if settings.auth is not None:
            from .auth_policy import validate_auth_mode  # noqa: PLC0415

            validate_auth_mode(settings.auth, asynchronous=False)
        if options is not None:
            options.check_helpers(defaults.helpers, asynchronous=cls._asynchronous)
        transport = _transport(options, http_client)
        created = http_client is None or isinstance(http_client, Unset)
        if created:
            http_client = native_client(transport)
        elif not isinstance(http_client, httpx2.Client):
            raise ConfigurationError(field_path=("http_client",), reason="invalid_type")
        shared = _Shared(defaults, http_client, transport, created=created)
        shared.options, shared.root_auth = options, settings.auth
        return cls(shared, settings)

    def execute(  # noqa: PLR0913
        self,
        operation: OperationPlan[T],
        arguments: tuple[object, ...],
        *,
        body: object = UNSET,
        fields: tuple[object, ...] = (),
        media_type: str | None = None,
        options: RequestOptions | None = None,
        response_media_type: str | None = None,
    ) -> Response[T]:
        """Execute one encoded logical call through its retry and redirect policy."""
        settings = self._call_settings(options, operation.operation_id)
        call = Call(settings, operation)
        events = call.events = self._started(call, operation.path)
        decoder = operation.responses

        def prepare() -> tuple[httpx2.Request, object]:
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

        def receive(response: httpx2.Response, info: ResponseInfo) -> Response[T]:
            received = self._read(response, info, decoder, call)

            return decode_response(decoder, info, received, call.settings, call.operation_id)

        try:
            if fields:
                body = operation.bound(body, fields, media_type)
            result = self._run(call, body, prepare, receive)

            if events is not None:
                events.finish(result)

        except BaseException as error:  # noqa: BLE001
            failure = call.stopped(error)
            if events is not None:
                events.ended(failure)
            raise failure from None
        else:
            return result

    def execute_raw(  # noqa: PLR0913
        self,
        operation: OperationPlan[object],
        arguments: tuple[object, ...],
        *,
        body: object = UNSET,
        fields: tuple[object, ...] = (),
        media_type: str | None = None,
        options: RequestOptions | None = None,
        response_media_type: str | None = None,
        stream: bool = False,
        _call: Call | None = None,
    ) -> RawResponse:
        """Execute one encoded logical call through its retry and redirect policy.

        A helper supplies its prepared call. A response other than a declared success of the response media type
        raises the call's typed failure before the stream is handed over.
        """
        call = self._raw_call(operation, options, _call)
        call.handing_off = stream
        events = call.events = self._started(call, operation.path)
        decoder = operation.responses
        result: RawResponse | None = None

        def prepare() -> tuple[httpx2.Request, object]:
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

        def receive(response: httpx2.Response, info: ResponseInfo) -> RawResponse:
            return self._raw_response(response, info, call, stream=stream)

        try:
            if fields:
                body = operation.bound(body, fields, media_type)
            result = self._run(call, body, prepare, receive)

            if _auth_failed(call) or _call is not None:
                result.raise_for_status()
            if _call is not None:
                decoder.streamed(result.info)
            if events is not None:
                events.finish(UNSET, handed_off=stream)

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

    def stream(  # noqa: PLR0913
        self,
        operation: OperationPlan[object],
        arguments: tuple[object, ...],
        *,
        body: object = UNSET,
        fields: tuple[object, ...] = (),
        media_type: str | None = None,
        options: RequestOptions | None = None,
        response_media_type: str | None = None,
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
        body: BodyInput[JSONValue] | Unset = UNSET,
        options: RequestOptions | None = None,
        stream: bool = False,
    ) -> RawResponse:
        """Execute an unbound raw call with the same resource and retry ownership."""
        call = Call(self._call_settings(options, None))
        call.handing_off = stream
        events = call.events = self._started(call, None)
        result: RawResponse | None = None

        def receive(response: httpx2.Response, info: ResponseInfo) -> RawResponse:
            return self._raw_response(response, info, call, stream=stream)

        def prepare() -> tuple[httpx2.Request, object]:
            return self._raw_prepared(method, url, body, options)

        try:
            result = self._run(call, body, prepare, receive)

            if _auth_failed(call):
                result.raise_for_status()
            if events is not None:
                events.finish(UNSET, handed_off=stream)

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

    def stream_raw(
        self,
        method: str,
        url: str,
        *,
        body: BodyInput[JSONValue] | Unset = UNSET,
        options: RequestOptions | None = None,
    ) -> AbstractContextManager[RawResponse]:
        """Return a block that sends a raw request on entry and yields its streaming response until exit."""
        return _streamed(lambda: self.request_raw(method, url, body=body, options=options, stream=True))

    def _run(
        self,
        call: Call,
        body: object,
        prepare: Callable[[], tuple[httpx2.Request, object]],
        receive: Callable[[httpx2.Response, ResponseInfo], T],
    ) -> T:
        """Own entry capture, the single encode, every hop, and closing the files opened from paths."""
        entry: BodyBindings | None = None
        source: BodySource | None = None
        try:
            entry = capture_body(body) if body is not UNSET and (is_file_input(body) or is_multipart(body)) else None

            call.bind()
            if call.settings.auth is not None or (call.operation is not None and call.operation.security is not None):
                self._bind_auth(call)
            if (events := call.events) is not None:
                events.emit(events.starting(call.settings))

            request, deferred = prepare()
            request = call.prepared(request)
            if call.auth is not None:
                self._auth_prepared(request, call)
            request, coding = _compressed(call, request, deferred, self._shared.request_coding)

            if not isinstance(deferred, Unset):
                source = bind_body(deferred, entry=entry)
                if coding is not None:
                    source = coding.source(source)

            result = self._exchange(request, source, call, receive)
        except BaseException as error:  # noqa: BLE001
            failure = call.failure(error)
            _close_body(source or entry, call, failure)
            raise failure from None
        try:
            _close_body(source or entry, call)
        except SDKError as error:
            if isinstance(result, RawResponse):
                result.discard(error)
            raise
        return result

    def _exchange(
        self,
        original: httpx2.Request,
        source: BodySource | None,
        call: Call,
        receive: Callable[[httpx2.Response, ResponseInfo], T],
    ) -> T:
        """Read and decode error bodies before deciding whether a complete status response may retry."""
        events = call.events
        if events is not None:
            events.prepare(str(original.url), call.attempt_index)
        while True:
            response: httpx2.Response | None = None
            info: ResponseInfo | None = None
            failure: BaseException | None = None
            sends_before = call.sends
            call.response_transferred = False
            try:
                response = self._send(original, source, call)
                info = self._response_info(response, call.request_id_header, call)
                call.received(info)
                if events is not None:
                    events.emit(events.responding(info))
                planned = self._status_plan(info, source, call)
                if planned is None:
                    result = receive(response, info)
                    closing, response = response, None
                    if not call.response_transferred:
                        self._close_response(closing, call)
                    return result
                failure = call.decoder.failure(info, b"", truncated=True)
                closing, response = response, None
                self._close_response(closing, call, failure)
            except BaseException as error:  # noqa: BLE001
                failure = self._exchange_failure(error, response, call, info)
                if not is_transport(failure) or call.sends == sends_before:
                    raise call.stopped(failure) from None
                planned = call.retry(None, failure, replayable=source is None or source.replayable)
                if planned is None:
                    raise call.stopped(failure) from None
            self._wait_retry(planned, cast("BaseException", failure), call)
            call.restart(original)

    def _status_plan(self, info: ResponseInfo, source: BodySource | None, call: Call) -> RetryDelay | None:
        """Plan a status retry, invalidating a rejected credential first and keeping the response when that fails."""
        if call.handshake and info.status_code != _SWITCHING:
            return call.retry(
                info,
                None,
                replayable=source is None or source.replayable,
            )
        planned = (
            call.retry(
                info,
                None,
                replayable=source is None or source.replayable,
            )
            if info.status_code >= _ERROR_STATUS
            else None
        )
        if call.token_unreceived:
            return None if planned is not None and planned.reason == "auth_invalid_token" else planned
        if planned is not None and planned.reason == "auth_invalid_token":
            self._invalidate(call, recovering=True)
            return None if call.retry_blocked else planned
        if planned is None and call.auth is not None and call.auth.rejected is not None:
            self._invalidate(call, recovering=False)
        return planned

    def _exchange_failure(
        self, error: BaseException, response: httpx2.Response | None, call: Call, info: ResponseInfo | None
    ) -> BaseException:
        failure = self._failure(error, call, call.delivery_state)
        if isinstance(failure, SDKError) and info is not None:
            failure.info = info
        if response is not None and not call.response_transferred:
            self._close_response(response, call, failure)
        return failure

    @staticmethod
    def _wait_retry(planned: RetryDelay, failure: BaseException, call: Call) -> None:
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

    def _raw_response(self, response: httpx2.Response, info: ResponseInfo, call: Call, *, stream: bool) -> RawResponse:
        permit, call.permit = call.permit, None

        def release() -> None:
            self._release_resources(
                call,
                (response.close, partial(_release_permit, cast("Permit", permit)))
                if permit is not None
                else (response.close,),
            )

        handle = RawResponse(
            info,
            call.decoder,
            call.settings,
            call.operation_id,
            lambda error: self._classified(error, call, DeliveryState.RESPONSE_STARTED),
            source=partial(decoded_bytes, response, info, call.operation_id),
            raw_source=partial(response_bytes, response),
            close=release,
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
    def _authenticate(call: Call) -> None:
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
        delivery = delivery_state(call)
        with _auth_work(call):
            values: list[AcquiredCredential] = []
            for index, binding in enumerate(bound.credentials):
                context = _credential_context(binding, call)
                value = get_credential(binding, context, delivery)

                if (
                    (pending := auth.pending) is not None
                    and pending[0] == index
                    and rejected_version(value, pending[1])
                ):
                    values.append(refresh_credential(binding, context, delivery, call.settings.clock))

                else:
                    values.append(accept_credential(value, binding, context, delivery, call.settings.clock))
            auth.credentials = HopCredentials(tuple(values))
            auth.pending = None

    @staticmethod
    def _authenticated_request(request: httpx2.Request, call: Call) -> httpx2.Request:
        from .auth import SigningInput  # noqa: PLC0415
        from .auth_policy import BoundAuth, apply_signature, place_credentials, sign_request  # noqa: PLC0415
        from .urls import origin_text, signing_query  # noqa: PLC0415

        auth = call.auth
        assert auth is not None
        bound = auth.bound
        assert isinstance(bound, BoundAuth)
        if auth.credentials is not None:
            request = place_credentials(request, bound, auth.credentials)
        if not bound.signers:
            return request
        assert call.current_origin is not None
        for signer in bound.signers:
            signing = SigningInput(
                method=request.method,
                url=str(request.url),
                origin=origin_text(call.current_origin),
                query=signing_query(str(request.url)),
                headers=HeadersView(request_fields(request)),
                attempt_index=call.attempt_index,
            )
            fields = sign_request(signer.signer, signing, delivery_state(call))

            request = apply_signature(request, fields, signer.capabilities)
        return request

    @staticmethod
    def _invalidate(call: Call, *, recovering: bool) -> None:
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
        except Exception as error:
            if is_hook_failure(error):
                raise

            call.retry_blocked = True
            auth.secondary_errors = (*auth.secondary_errors, error)
            call.stop_reason = call.stop_reason or "callback_failure"
        else:
            if recovering:
                auth.pending = rejected

    def _send(
        self,
        request: httpx2.Request,
        source: BodySource | None,
        call: Call,
    ) -> httpx2.Response:
        """Send one native request and hand its response and permit to the call together."""
        attempt: SyncContent | EncodedAttempt | None = request_body(request)
        permit: Permit | None = None
        try:
            call.next_send()

            self._authorize(call)
            renewed = False
            while True:
                if (limiter := call.settings.limiter) is not None:
                    permit = _acquire(limiter, call, str(request.url), call.events)

                    if call.events is not None:
                        call.events.emit(call.events.event("limiter_acquired"))
                if permit is None or not _reauthorizing(call):
                    break
                if renewed:
                    _usable_credentials(call)
                renewed = True
                releasing, permit = permit, None
                _release_permit(releasing)
                self._authenticate(call)

            if source is not None:
                attempt = source.open()

            outgoing = self._outgoing(request, attempt, source, call)
            if call.events is not None:
                call.events.emit(call.events.attempting())
            _usable_credentials(call)
            call.admit_send()
            if call.events is not None:
                call.events.sending()
            try:
                response = self._native_send(outgoing, call)
            except Exception as error:  # noqa: BLE001
                native_failure = native_error(
                    error,
                    send_started=True,
                    response_started=_answered(error, outgoing, self._shared.http_client.event_hooks["response"]),
                )
                call.delivery_state = native_failure.delivery_state
                raise native_failure from None
            call.delivery_state = DeliveryState.RESPONSE_STARTED
            call.permit, permit = permit, None
            return response  # noqa: TRY300
        except BaseException as error:  # noqa: BLE001
            failure = self._failure(error, call, call.delivery_state)
            closes: list[Callable[[], None]] = []
            if permit is not None:
                closes.append(partial(_release_permit, permit))
            self._release_resources(call, tuple(closes), failure)
            raise failure from None

    def _native_send(self, outgoing: httpx2.Request, call: Call) -> httpx2.Response:
        """Send through the HTTP client with its own auth, following redirects only as the call allows."""
        client = self._shared.http_client
        if (follow := call.follow(outgoing, self._shared.security_schemes)) is None:
            response = client.send(outgoing, stream=True)
        else:
            response = client.send(outgoing, stream=True, follow_redirects=follow)
        call.redirects_followed = _redirects(response)
        call.token_unreceived = _token_unreceived(response)
        return response

    @staticmethod
    def _release_resources(
        call: Call, closes: tuple[Callable[[], None], ...], error: BaseException | None = None
    ) -> None:
        primary = error
        for close in closes:
            try:
                close()
            except BaseException as failure:  # noqa: BLE001, PERF203
                call.retry_blocked = True
                released = (
                    SDKError(
                        reason="cleanup_failed", operation_id=call.operation_id, call_id=call.call_id, cause=failure
                    )
                    if isinstance(failure, Exception) and not isinstance(failure, SDKError)
                    else failure
                )
                primary = released if primary is None else kept_primary(primary, released)
        if primary is not None and primary is not error:
            raise primary

    def _close_response(self, response: httpx2.Response, call: Call, error: BaseException | None = None) -> None:
        permit, call.permit = call.permit, None
        closes = (
            (response.close,) if permit is None else (response.close, partial(_release_permit, cast("Permit", permit)))
        )
        self._release_resources(call, closes, error)

    def _authorize(self, call: Call) -> None:
        """Take the call's credentials before any permit."""
        if call.auth is None:
            return
        self._authenticate(call)

    def _outgoing(
        self,
        request: httpx2.Request,
        attempt: SyncContent | EncodedAttempt | None,
        source: BodySource | None,
        call: Call,
    ) -> httpx2.Request:
        """Hand the body to HTTPX2, which frames it, and fix the timeout before credential placement and signing.

        Bytes are given as they are and other bodies reopen their source, so a redirect HTTPX2 follows sends them
        again; a stream of a known length is sent with that Content-Length.
        """
        content: bytes | _Replayed | None = None
        if isinstance(attempt, EncodedAttempt):
            content = attempt.content
        elif attempt is not None:
            assert source is not None
            content = _Replayed(attempt, source)
        outgoing = httpx2.Request(
            request.method,
            request.url,
            headers=wire_fields(_framing(request, attempt)),
            content=content,
            extensions={"timeout": native_timeout(call.timeout())},
        )
        if call.auth is not None:
            outgoing = self._authenticated_request(outgoing, call)
        return outgoing

    @staticmethod
    def _read(
        response: httpx2.Response,
        info: ResponseInfo,
        decoder: ResponseDecoder[object],
        call: LogicalCallContext,
    ) -> ReceivedBody:
        received = _received(decoder, info.status_code, call.settings)
        chunks = decoded_bytes(response, info, call.operation_id)
        try:
            for chunk in chunks:
                if not received.add(chunk):
                    break
        except ProtocolError as error:
            received.problem = error
        return received

    def close(self) -> None:
        """Close only the native client this root created, at most once even if close fails.

        The WebSocket sessions open on it close first, so that none of their readers outlives its connection.
        """
        shared = self._shared
        if shared.closed:
            return
        shared.closed = True
        if shared.created:
            try:
                for close in tuple(shared.sockets):
                    close()
                shared.http_client.close()
            except Exception as error:  # noqa: BLE001
                raise SDKError(reason="close_failed", cause=error) from None


class AsyncClientCore(Core["httpx2.AsyncClient", "AsyncRawResponse"]):
    """Run the calls of an asyncio client and its views through one async transport adapter on one event loop."""

    __slots__ = ()
    _asynchronous: ClassVar[bool] = True

    async def _started(self, call: LogicalCallContext, path: str | None) -> CallEvents | None:
        """Admit a call, reporting both boundary events when it is already stopped."""
        events = call_events(call.settings, call=call, path=path, asynchronous=True)
        try:
            self._admitted(call)
        except BaseException as error:  # noqa: BLE001
            failure = call.failure(error)
            if events is not None:
                await events.aended(failure, starting=True)
            raise failure from None
        return events

    @classmethod
    def create(
        cls,
        defaults: ClientDefaults,
        *,
        options: ClientOptions | None = None,
        http_client: httpx2.AsyncClient | Unset | None = UNSET,
    ) -> Self:
        """Borrow a mode-correct native client, or create and own one from transport settings."""
        settings = _client_settings(options, http_client)
        if settings.auth is not None:
            from .auth_policy import validate_auth_mode  # noqa: PLC0415

            validate_auth_mode(settings.auth, asynchronous=True)
        if options is not None:
            options.check_helpers(defaults.helpers, asynchronous=cls._asynchronous)
        transport = _transport(options, http_client)
        created = http_client is None or isinstance(http_client, Unset)
        if created:
            http_client = native_async_client(transport)
        elif not isinstance(http_client, httpx2.AsyncClient):
            raise ConfigurationError(field_path=("http_client",), reason="invalid_type")
        shared = _Shared(defaults, http_client, transport, created=created)
        shared.options, shared.root_auth = options, settings.auth
        return cls(shared, settings)

    async def execute(  # noqa: PLR0913
        self,
        operation: OperationPlan[T],
        arguments: tuple[object, ...],
        *,
        body: object = UNSET,
        fields: tuple[object, ...] = (),
        media_type: str | None = None,
        options: RequestOptions | None = None,
        response_media_type: str | None = None,
    ) -> Response[T]:
        """Execute one encoded logical call through its retry and redirect policy."""
        settings = self._call_settings(options, operation.operation_id)
        call = Call(settings, operation)
        events = call.events = await self._started(call, operation.path)
        decoder = operation.responses

        def prepare() -> tuple[httpx2.Request, object]:
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

        async def receive(response: httpx2.Response, info: ResponseInfo) -> Response[T]:
            received = await self._read(response, info, decoder, call)

            return decode_response(decoder, info, received, call.settings, call.operation_id)

        try:
            if fields:
                body = operation.bound(body, fields, media_type)
            result = await self._run(call, body, prepare, receive)

            if events is not None:
                await events.afinish(result)

        except BaseException as error:  # noqa: BLE001
            failure = call.stopped(error)
            if events is not None:
                await events.aended(failure)
            raise failure from None
        else:
            return result

    async def execute_raw(  # noqa: PLR0913
        self,
        operation: OperationPlan[object],
        arguments: tuple[object, ...],
        *,
        body: object = UNSET,
        fields: tuple[object, ...] = (),
        media_type: str | None = None,
        options: RequestOptions | None = None,
        response_media_type: str | None = None,
        stream: bool = False,
        _call: Call | None = None,
    ) -> AsyncRawResponse:
        """Execute one encoded logical call through its retry and redirect policy.

        A helper supplies its prepared call. A response other than a declared success of the response media type
        raises the call's typed failure before the stream is handed over.
        """
        call = self._raw_call(operation, options, _call)
        call.handing_off = stream
        events = call.events = await self._started(call, operation.path)
        decoder = operation.responses
        result: AsyncRawResponse | None = None

        def prepare() -> tuple[httpx2.Request, object]:
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

        async def receive(response: httpx2.Response, info: ResponseInfo) -> AsyncRawResponse:
            return await self._raw_response(response, info, call, stream=stream)

        try:
            if fields:
                body = operation.bound(body, fields, media_type)
            result = await self._run(call, body, prepare, receive)

            if _auth_failed(call) or _call is not None:
                await result.raise_for_status()
            if _call is not None:
                decoder.streamed(result.info)
            if events is not None:
                await events.afinish(UNSET, handed_off=stream)

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

    def stream(  # noqa: PLR0913
        self,
        operation: OperationPlan[object],
        arguments: tuple[object, ...],
        *,
        body: object = UNSET,
        fields: tuple[object, ...] = (),
        media_type: str | None = None,
        options: RequestOptions | None = None,
        response_media_type: str | None = None,
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
        body: AsyncBodyInput[JSONValue] | Unset = UNSET,
        options: RequestOptions | None = None,
        stream: bool = False,
    ) -> AsyncRawResponse:
        """Execute an unbound raw call with the same resource and retry ownership."""
        call = Call(self._call_settings(options, None))
        call.handing_off = stream
        events = call.events = await self._started(call, None)
        result: AsyncRawResponse | None = None

        async def receive(response: httpx2.Response, info: ResponseInfo) -> AsyncRawResponse:
            return await self._raw_response(response, info, call, stream=stream)

        def prepare() -> tuple[httpx2.Request, object]:
            return self._raw_prepared(method, url, body, options)

        try:
            result = await self._run(call, body, prepare, receive)

            if _auth_failed(call):
                await result.raise_for_status()
            if events is not None:
                await events.afinish(UNSET, handed_off=stream)

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

    def stream_raw(
        self,
        method: str,
        url: str,
        *,
        body: AsyncBodyInput[JSONValue] | Unset = UNSET,
        options: RequestOptions | None = None,
    ) -> AbstractAsyncContextManager[AsyncRawResponse]:
        """Return a block that sends a raw request on entry and yields its streaming response until exit."""
        return _astreamed(lambda: self.request_raw(method, url, body=body, options=options, stream=True))

    async def _run(
        self,
        call: Call,
        body: object,
        prepare: Callable[[], tuple[httpx2.Request, object]],
        receive: Callable[[httpx2.Response, ResponseInfo], Awaitable[T]],
    ) -> T:
        """Own entry capture, the single encode, every hop, and closing the files opened from paths."""
        entry: BodyBindings | None = None
        source: BodySource | None = None
        try:
            entry = capture_body(body) if body is not UNSET and (is_file_input(body) or is_multipart(body)) else None

            call.bind()
            if call.settings.auth is not None or (call.operation is not None and call.operation.security is not None):
                self._bind_auth(call)
            if (events := call.events) is not None:
                await events.aemit(events.starting(call.settings))

            request, deferred = prepare()
            request = call.prepared(request)
            if call.auth is not None:
                self._auth_prepared(request, call)
            request, coding = _compressed(call, request, deferred, self._shared.request_coding)

            if not isinstance(deferred, Unset):
                source = bind_body(deferred, entry=entry, asynchronous=True)
                if coding is not None:
                    source = coding.source(source)

            result = await self._exchange(request, source, call, receive)
        except BaseException as error:  # noqa: BLE001
            failure = call.failure(error)
            await call.cleanup(partial(_aclose_body, source or entry, call, failure), error=failure)
            raise failure from None
        try:
            await _aclose_body(source or entry, call)
        except BaseException as error:
            if isinstance(result, AsyncRawResponse):
                await result.discard(error)
            raise
        return result

    async def _exchange(
        self,
        original: httpx2.Request,
        source: BodySource | None,
        call: Call,
        receive: Callable[[httpx2.Response, ResponseInfo], Awaitable[T]],
    ) -> T:
        """Read and decode error bodies before deciding whether a complete status response may retry."""
        events = call.events
        if events is not None:
            events.prepare(str(original.url), call.attempt_index)
        while True:
            response: httpx2.Response | None = None
            info: ResponseInfo | None = None
            failure: BaseException | None = None
            sends_before = call.sends
            call.response_transferred = False
            try:
                response = await self._send(original, source, call)
                info = self._response_info(response, call.request_id_header, call)
                call.received(info)
                if events is not None:
                    await events.aemit(events.responding(info))
                planned = await self._status_plan(info, source, call)
                if planned is None:
                    result = await receive(response, info)
                    closing, response = response, None
                    if not call.response_transferred:
                        await self._close_response(closing, call)
                    return result
                failure = call.decoder.failure(info, b"", truncated=True)
                closing, response = response, None
                await self._close_response(closing, call, failure)
            except BaseException as error:  # noqa: BLE001
                failure = await self._exchange_failure(error, response, call, info)
                if not is_transport(failure) or call.sends == sends_before:
                    raise call.stopped(failure) from None
                planned = call.retry(None, failure, replayable=source is None or source.replayable)
                if planned is None:
                    raise call.stopped(failure) from None
            await self._wait_retry(planned, cast("BaseException", failure), call)
            call.restart(original)

    async def _status_plan(self, info: ResponseInfo, source: BodySource | None, call: Call) -> RetryDelay | None:
        """Plan a status retry, invalidating a rejected credential first and keeping the response when that fails."""
        if call.handshake and info.status_code != _SWITCHING:
            return call.retry(
                info,
                None,
                replayable=source is None or source.replayable,
            )
        planned = (
            call.retry(
                info,
                None,
                replayable=source is None or source.replayable,
            )
            if info.status_code >= _ERROR_STATUS
            else None
        )
        if call.token_unreceived:
            return None if planned is not None and planned.reason == "auth_invalid_token" else planned
        if planned is not None and planned.reason == "auth_invalid_token":
            await self._invalidate(call, recovering=True)
            return None if call.retry_blocked else planned
        if planned is None and call.auth is not None and call.auth.rejected is not None:
            await self._invalidate(call, recovering=False)
        return planned

    async def _exchange_failure(
        self, error: BaseException, response: httpx2.Response | None, call: Call, info: ResponseInfo | None
    ) -> BaseException:
        failure = self._failure(error, call, call.delivery_state)
        if isinstance(failure, SDKError) and info is not None:
            failure.info = info
        if response is not None and not call.response_transferred:
            await self._close_response(response, call, failure)
        return failure

    @staticmethod
    async def _wait_retry(planned: RetryDelay, failure: BaseException, call: Call) -> None:
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
        self, response: httpx2.Response, info: ResponseInfo, call: Call, *, stream: bool
    ) -> AsyncRawResponse:
        permit, call.permit = call.permit, None

        async def release() -> None:
            await self._release_resources(
                call,
                (response.aclose, partial(_arelease_permit, cast("AsyncPermit", permit)))
                if permit is not None
                else (response.aclose,),
            )

        handle = AsyncRawResponse(
            info,
            call.decoder,
            call.settings,
            call.operation_id,
            lambda error: self._classified(error, call, DeliveryState.RESPONSE_STARTED),
            source=partial(async_decoded_bytes, response, info, call.operation_id),
            raw_source=partial(async_response_bytes, response),
            close=release,
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
    async def _authenticate(call: Call) -> None:
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
        delivery = delivery_state(call)
        async with _aauth_work(call):
            values: list[AcquiredCredential] = []
            for index, binding in enumerate(bound.credentials):
                context = _credential_context(binding, call)
                value = await aget_credential(binding, context, delivery)

                if (
                    (pending := auth.pending) is not None
                    and pending[0] == index
                    and rejected_version(value, pending[1])
                ):
                    values.append(await arefresh_credential(binding, context, delivery, call.settings.clock))

                else:
                    values.append(accept_credential(value, binding, context, delivery, call.settings.clock))
            auth.credentials = AsyncHopCredentials(tuple(values))
            auth.pending = None

    @staticmethod
    async def _authenticated_request(
        request: httpx2.Request,
        call: Call,
    ) -> httpx2.Request:
        from .auth import SigningInput  # noqa: PLC0415
        from .auth_policy import AsyncBoundAuth, apply_signature, asign_request, place_credentials  # noqa: PLC0415
        from .urls import origin_text, signing_query  # noqa: PLC0415

        auth = call.auth
        assert auth is not None
        bound = auth.bound
        assert isinstance(bound, AsyncBoundAuth)
        if auth.credentials is not None:
            request = place_credentials(request, bound, auth.credentials)
        if not bound.signers:
            return request
        assert call.current_origin is not None
        for signer in bound.signers:
            signing = SigningInput(
                method=request.method,
                url=str(request.url),
                origin=origin_text(call.current_origin),
                query=signing_query(str(request.url)),
                headers=HeadersView(request_fields(request)),
                attempt_index=call.attempt_index,
            )
            fields = await asign_request(signer.signer, signing, delivery_state(call))

            request = apply_signature(request, fields, signer.capabilities)
        return request

    @staticmethod
    async def _invalidate(call: Call, *, recovering: bool) -> None:
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
        except Exception as error:
            if is_hook_failure(error):
                raise

            call.retry_blocked = True
            auth.secondary_errors = (*auth.secondary_errors, error)
            call.stop_reason = call.stop_reason or "callback_failure"
        else:
            if recovering:
                auth.pending = rejected

    async def _send(
        self,
        request: httpx2.Request,
        source: BodySource | None,
        call: Call,
    ) -> httpx2.Response:
        """Send one native request and hand its response and permit to the call together."""
        attempt: AsyncContent | EncodedAttempt | None = request_body(request)
        permit: AsyncPermit | None = None
        try:
            call.next_send()

            await self._authorize(call)
            renewed = False
            while True:
                if (limiter := call.settings.limiter) is not None:
                    permit = await _aacquire(limiter, call, str(request.url), call.events)

                    if call.events is not None:
                        await call.events.aemit(call.events.event("limiter_acquired"))
                if permit is None or not _reauthorizing(call):
                    break
                if renewed:
                    _usable_credentials(call)
                renewed = True
                releasing, permit = permit, None
                await call.cleanup(partial(_arelease_permit, releasing))
                await self._authenticate(call)

            if source is not None:
                attempt = await source.aopen()

            outgoing = await self._outgoing(request, attempt, source, call)
            if call.events is not None:
                await call.events.aemit(call.events.attempting())
            _usable_credentials(call)
            call.admit_send()
            if call.events is not None:
                call.events.sending()
            try:
                response = await self._native_send(outgoing, call)
            except Exception as error:  # noqa: BLE001
                native_failure = native_error(
                    error,
                    send_started=True,
                    response_started=_answered(error, outgoing, self._shared.http_client.event_hooks["response"]),
                )
                call.delivery_state = native_failure.delivery_state
                raise native_failure from None
            call.delivery_state = DeliveryState.RESPONSE_STARTED
            call.permit, permit = permit, None
            return response  # noqa: TRY300
        except BaseException as error:  # noqa: BLE001
            failure = self._failure(error, call, call.delivery_state)
            closes: list[Callable[[], Awaitable[None]]] = []
            if permit is not None:
                closes.append(partial(_arelease_permit, permit))
            await self._release_resources(call, tuple(closes), failure)
            raise failure from None

    async def _native_send(self, outgoing: httpx2.Request, call: Call) -> httpx2.Response:
        """Send through the asyncio HTTP client with its own auth, following redirects only as the call allows."""
        client = self._shared.http_client
        if (follow := call.follow(outgoing, self._shared.security_schemes)) is None:
            response = await client.send(outgoing, stream=True)
        else:
            response = await client.send(outgoing, stream=True, follow_redirects=follow)
        call.redirects_followed = _redirects(response)
        call.token_unreceived = _token_unreceived(response)
        return response

    @staticmethod
    async def _release_resources(
        call: Call, closes: tuple[Callable[[], Awaitable[None]], ...], error: BaseException | None = None
    ) -> None:
        primary = error
        for close in closes:
            try:
                await call.cleanup(close)
            except BaseException as failure:  # noqa: BLE001, PERF203
                call.retry_blocked = True
                released = (
                    SDKError(
                        reason="cleanup_failed", operation_id=call.operation_id, call_id=call.call_id, cause=failure
                    )
                    if isinstance(failure, Exception) and not isinstance(failure, SDKError)
                    else failure
                )
                primary = released if primary is None else kept_primary(primary, released)
        if primary is not None and primary is not error:
            raise primary

    async def _close_response(self, response: httpx2.Response, call: Call, error: BaseException | None = None) -> None:
        permit, call.permit = call.permit, None
        closes = (
            (response.aclose,)
            if permit is None
            else (response.aclose, partial(_arelease_permit, cast("AsyncPermit", permit)))
        )
        await self._release_resources(call, closes, error)

    async def _authorize(self, call: Call) -> None:
        """Take the call's credentials before any permit."""
        if call.auth is None:
            return
        await self._authenticate(call)

    async def _outgoing(
        self,
        request: httpx2.Request,
        attempt: AsyncContent | EncodedAttempt | None,
        source: BodySource | None,
        call: Call,
    ) -> httpx2.Request:
        """Hand the body to HTTPX2, which frames it, and fix the timeout before credential placement and signing.

        Bytes are given as they are and other bodies reopen their source, so a redirect HTTPX2 follows sends them
        again; a stream of a known length is sent with that Content-Length.
        """
        content: bytes | _AsyncReplayed | None = None
        if isinstance(attempt, EncodedAttempt):
            content = attempt.content
        elif attempt is not None:
            assert source is not None
            content = _AsyncReplayed(attempt, source)
        outgoing = httpx2.Request(
            request.method,
            request.url,
            headers=wire_fields(_framing(request, attempt)),
            content=content,
            extensions={"timeout": native_timeout(call.timeout())},
        )
        if call.auth is not None:
            outgoing = await self._authenticated_request(outgoing, call)
        return outgoing

    @staticmethod
    async def _read(
        response: httpx2.Response,
        info: ResponseInfo,
        decoder: ResponseDecoder[object],
        call: LogicalCallContext,
    ) -> ReceivedBody:
        received = _received(decoder, info.status_code, call.settings)
        chunks = async_decoded_bytes(response, info, call.operation_id)
        try:
            async for chunk in chunks:
                if not received.add(chunk):
                    break
        except ProtocolError as error:
            received.problem = error
        return received

    async def aclose(self) -> None:
        """Close only the native client this root created, at most once even if close fails."""
        shared = self._shared
        if shared.closed:
            return
        shared.closed = True
        if shared.created:
            try:
                await shared.http_client.aclose()
            except Exception as error:  # noqa: BLE001
                raise SDKError(reason="close_failed", cause=error) from None
