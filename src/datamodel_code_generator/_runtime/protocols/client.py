"""Execution shared by declared client helpers, separate from ordinary operation calls."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, cast
from urllib.parse import unquote_plus, urlsplit

import httpx2

from ..client import client as native_core
from ..client.client import AsyncClientCore as NativeAsyncClientCore
from ..client.client import (
    Call,
    R,
    ReceivedBody,
    T,
    build_request,
    decode_response,
    delivery_state,
    encode_argument,
    encode_parameter_value,
    request_body,
    request_decode_error,
    strip_credentials,
)
from ..client.client import ClientCore as NativeClientCore
from ..client.errors import (
    APIConnectionError,
    ConfigurationError,
    DeliveryState,
    ProtocolSizeError,
    is_phase_timeout,
    too_large,
)
from ..client.logical import LogicalCallContext
from ..client.native import request_fields
from ..client.operations import request_errors
from ..client.options import HeaderPatch, IdempotencyKey, QueryPatch, RequestOptions, Settings
from ..client.raw import AsyncRawResponse, RawResponse, arefused, refused
from ..client.responses import HeadersView, Response
from ..client.retry import RetryTiming, retry_delay
from ..client.timing import ResolvedTimeoutOptions, on_clock
from ..client.urls import absolute_target, request_origin, strip_query
from ..model_codecs.unset import UNSET, Unset
from .options import ClientOptions, ProtocolClientOptions

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from typing import Protocol

    from ..client.logical import OperationSession
    from ..client.operations import OperationPlan, ParameterSpec, ResponseDecoder
    from ..client.responses import ResponseInfo
    from ..client.retry import RetryDelay
    from ..client.security import SecuritySchemeEntry
    from ..client.timing import Deadline
    from ..client.urls import Origin
    from ..model_codecs.media import JSONValue
    from .options import ProtocolDefaults, ProtocolSecurityContext
    from .references import OperationRef

_NOT_MODIFIED = 304
_SWITCHING = 101
_UNAUTHORIZED = 401


if TYPE_CHECKING:

    class _PagePlan(Protocol):
        """The identity of a protocol helper and of the operation its pages call."""

        @property
        def helper_id(self) -> str:
            """The helper's dotted name."""
            raise NotImplementedError

        @property
        def operation(self) -> OperationRef:
            """The reference of the operation the helper calls."""
            raise NotImplementedError


def _secret(spec: ParameterSpec, value: JSONValue, headers: frozenset[str], queries: frozenset[str]) -> bool:
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


def _unsaved(plan: _PagePlan, path: tuple[str, ...]) -> ConfigurationError:
    return ConfigurationError(
        field_path=path, reason="wrong_capability", helper_id=plan.helper_id, operation=plan.operation
    )


def _page(  # ruff: ignore[too-many-arguments]
    decoder: ResponseDecoder[T],
    info: ResponseInfo,
    body: ReceivedBody,
    call: Call,
    plan: _PagePlan,
    *,
    page_limited: bool,
) -> tuple[T, JSONValue, bytes]:
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
        raise too_large(info, limit, body.size)
    if (problem := body.problem) is not None and body.success:
        raise problem
    content = body.content
    data, wire = decoder.decode_page(info, content, truncated=body.truncated or problem is not None, problem=problem)
    return data, wire, content


def stored_value(operation: OperationPlan[T], info: ResponseInfo, body: bytes, settings: Settings) -> T:
    """Decode a stored success body as a call's decoder decodes a received one, under the call's settings."""
    received = ReceivedBody(settings.max_response_bytes, success=True)
    received.add(body)
    return decode_response(operation.responses, info, received, settings, operation.operation_id).data


@dataclass(frozen=True, slots=True)
class CacheRequest:
    """What a cache fetch keys and sends, prepared once before its call.

    `credentials` identifies the credentials the request carries, None for none; `foreign_auth` tells that a view or
    the call replaced the client's own auth; `credential_headers` are the lowercase names of the headers credentials
    travel in, which the auth may add after the cache looked the request up.
    """

    settings: Settings
    request: httpx2.Request
    url: str
    credentials: object
    partition: str | None
    foreign_auth: bool
    credential_headers: frozenset[str]


def _grant_identity(provider: object) -> tuple[str | None, tuple[str, ...]] | None:
    """Return the audience and requested scopes an OAuth token provider of the SDK declares, or None for any other."""
    identity = getattr(provider, "grant_identity", None)
    return cast("tuple[str | None, tuple[str, ...]]", identity()) if callable(identity) else None


class _SessionCall(Call):
    """A child call of a protocol helper session, which also bounds the call's deadline and send admissions.

    It keeps the URL of the hop it sends, credentials excluded, against which a page's relative URLs resolve.
    """

    __slots__ = ("session", "url")

    def __init__(
        self,
        settings: Settings,
        operation: OperationPlan[object],
        session: OperationSession,
        bound: Deadline | None = None,
    ) -> None:
        """Bind the call to its session, ending it no later than the session or a helper's own bound does."""
        super().__init__(settings, operation)
        self.session = session
        self.url = ""
        for limit in (session.deadline, None if bound is None else on_clock(bound, settings.clock)):
            if limit is not None and ((deadline := self.deadline) is None or limit.at < deadline.at):
                self.deadline = limit

    def prepared(self, request: httpx2.Request) -> httpx2.Request:
        """Prepare the request as an ordinary call does, keeping its URL."""
        request = super().prepared(request)
        self.url = str(request.url)
        return request

    def followed_query(self, schemes: tuple[SecuritySchemeEntry, ...]) -> frozenset[str]:
        """Return the query fields a followed URL is sent and saved without, whatever its origin.

        They are the positions of the package's declared security schemes and the fields the call's auth places, which
        the auth adds again itself.
        """
        from ..client.security import secret_names  # ruff: ignore[import-outside-top-level] - Only a followed URL needs the schemes.

        query = secret_names(schemes)[1]
        return query if (auth := self.auth) is None else query | auth.bound.managed_query

    def restart(self, original: httpx2.Request) -> frozenset[tuple[str, str]]:
        """Begin the next resource candidate at the original URL."""
        self.url = str(original.url)
        return super().restart(original)

    def redirected(
        self,
        request: httpx2.Request,
        info: ResponseInfo,
        visited: frozenset[tuple[str, str]],
        *,
        replayable: bool,
        schemes: tuple[SecuritySchemeEntry, ...],
    ) -> httpx2.Request | None:
        """Resolve a redirect as an ordinary call does, keeping the URL of the hop it allows."""
        hop = super().redirected(request, info, visited, replayable=replayable, schemes=schemes)
        if hop is not None:
            self.url = str(hop.url)
        return hop


class _SocketCall(_SessionCall):
    """The handshake of a WebSocket helper: a session child call whose open has one cap for all of its phases.

    The cap is the least of the open timeout and the connect, read, and write timeouts, bounded by the deadline, so a
    cap the deadline binds ends the call with its deadline APITimeoutError. WebSockets have no pool.
    """

    handshake = True

    __slots__ = ("open_timeout",)

    def __init__(
        self,
        settings: Settings,
        operation: OperationPlan[object],
        session: OperationSession,
        open_timeout: float | None,
    ) -> None:
        """Bind the call to its session and keep the open timeout."""
        super().__init__(settings, operation, session)
        self.open_timeout = open_timeout

    def timeout(self) -> ResolvedTimeoutOptions:
        configured = self.settings.timeout
        limits = [
            value
            for value in (self.open_timeout, configured.connect, configured.read, configured.write, self.remaining())
            if value is not None
        ]
        cap = min(limits) if limits else None
        return ResolvedTimeoutOptions(connect=cap, read=cap, write=cap, pool=None)

    def retry(
        self,
        info: ResponseInfo | None,
        error: APIConnectionError | None,
        *,
        replayable: bool,
    ) -> RetryDelay | None:
        """Retry only a handshake proven unsent; a received refusal or an uncertain open stays terminal."""
        if info is None and error is not None and error.delivery_state is DeliveryState.NOT_SENT:
            return super().retry(info, error, replayable=replayable)
        self.check("send")
        self.stop_reason = (
            "callback_failure"
            if self.retry_blocked
            else "auth_unrefreshable"
            if info is not None and info.status_code == _UNAUTHORIZED
            else "status_not_retryable"
            if info is not None
            else "transport_not_retryable"
        )
        return None

    def redirected(  # ruff: ignore[no-self-use] - Overrides the shared redirect policy.
        self,
        request: httpx2.Request,  # ruff: ignore[unused-method-argument]
        info: ResponseInfo,  # ruff: ignore[unused-method-argument]
        visited: frozenset[tuple[str, str]],  # ruff: ignore[unused-method-argument]
        *,
        replayable: bool,  # ruff: ignore[unused-method-argument]
        schemes: tuple[SecuritySchemeEntry, ...],  # ruff: ignore[unused-method-argument]
    ) -> httpx2.Request | None:
        """Keep every received handshake refusal terminal."""
        return None


class _SessionWait(LogicalCallContext):
    """A wait of a protocol helper session between its child calls, ending no later than the session does."""

    __slots__ = ("session",)

    def __init__(self, settings: Settings, session: OperationSession, operation_id: str | None) -> None:
        """Bind the wait to its session and the operation it comes before."""
        super().__init__(settings, operation_id)
        self.session = session
        if (limit := session.deadline) is not None and ((deadline := self.deadline) is None or limit.at < deadline.at):
            self.deadline = limit


class _ProtocolCore(native_core.Core[native_core.AdapterT, native_core.HandleT]):
    """Prepare and restore helper requests using the ordinary client's encoding and resource ownership."""

    __slots__ = ()

    def stream_call(
        self, operation: OperationPlan[object], options: RequestOptions | None, session: OperationSession
    ) -> Call:
        """Bind a stream's child call to its helper session before native execution."""
        return _SessionCall(self._call_settings(options, operation.operation_id), operation, session)

    def fixes_key(self, options: RequestOptions | None) -> bool:
        """Return whether a call's effective options, its own, a view's, or the client's, fix an idempotency key."""
        return isinstance(self._call_settings(options, None).idempotency_key, IdempotencyKey)

    def patches(self, options: RequestOptions | None) -> tuple[tuple[HeaderPatch, ...], tuple[QueryPatch, ...]]:
        """Return the header and query patches of a call's effective options: the client's, a view's, and its own."""
        settings = self._call_settings(options, None)
        return settings.headers, settings.query

    def reconnects_after(
        self, error: APIConnectionError, options: RequestOptions | None, operation_id: str | None
    ) -> bool:
        """Return whether a transport failure reading a stream's body is one an automatic reconnection may follow.

        It is a read-phase failure the shared retry classification retries. A read timeout qualifies only when the
        call's own read timeout set its cap, not the stream's idle limit, which wins a tie.
        """
        if error.phase != "read" or not isinstance(
            error.cause, (httpx2.ReadError, httpx2.ReadTimeout, httpx2.RemoteProtocolError)
        ):
            return False
        if not is_phase_timeout(error):
            return True
        settings = self._call_settings(options, operation_id)
        read, idle = settings.stream_read_timeout, settings.stream_idle_timeout
        return read is not None and (idle is None or read < idle)

    def waiting(
        self, options: RequestOptions | None, session: OperationSession, operation_id: str | None
    ) -> LogicalCallContext:
        """Return a context a helper waits in before a child call of its session, sending nothing.

        Its sleeps propagate native cancellation and end by the earlier of the
        options' deadline and the session's; the options' total timeout bounds each child call, not the waits between.
        Its errors name the session and the operation the wait comes before.
        """
        settings = replace(self._call_settings(options, operation_id), total_timeout=None)
        return _SessionWait(settings, session, operation_id)

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
        if (protocols := self.protocol_options()) is None or isinstance(defaults := protocols.defaults, Unset):
            return None
        return defaults.get(name)

    def protocol_options(self) -> ProtocolClientOptions | None:
        """Return the client's protocol settings, or None."""
        options = self._shared.options
        if options is None or not isinstance(options, ClientOptions) or isinstance(options.protocols, Unset):
            return None
        return options.protocols

    def owned_connector(self, native: Callable[[], object]) -> object:
        """Return the WebSocket connector shared by the root and its views, creating it only on use."""
        if (connector := self._shared.socket_connector) is None:
            connector = self._shared.socket_connector = native()
        return connector

    def cache_store(self, name: str) -> object:
        """Return the cache store the client's protocol settings lend a helper, or None without one."""
        if (protocols := self.protocol_options()) is None or isinstance(stores := protocols.cache_stores, Unset):
            return None
        return stores.get(name)

    def cache_request(
        self, operation: OperationPlan[object], arguments: tuple[object, ...], options: RequestOptions | None
    ) -> CacheRequest:
        """Return what a cache fetch keys and sends: its settings, its request before auth, and its credentials.

        A fetch on a closed client or past its deadline is refused first, as a call is. The URL is the request's own
        as the client interprets it. The credentials are, for each credential the auth binds, its scheme, kind, and
        required scopes and the audience and requested scopes of an SDK token provider, and each signer's declared
        capabilities; they are None for a request that carries no credential, from the auth or from a credential
        header, a cookie, or a security scheme's header or query field.
        """
        settings = self._call_settings(options, operation.operation_id)
        self._admitted(LogicalCallContext(settings, operation.operation_id))
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
        url = absolute_target(str(request.url)).url
        bound = self._bound(operation, settings.auth)
        from ..client.security import secret_names  # ruff: ignore[import-outside-top-level] - Only a cache fetch needs the schemes.

        names, queries = secret_names(self._shared.security_schemes)
        credential: object = None
        if bound is not None:
            names = names.union(bound.managed_headers, ("cookie",) if bound.managed_cookies else ())
            credential = (
                tuple(
                    (item.scheme.name, item.scheme.kind, item.required_scopes, _grant_identity(item.provider))
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
        elif any(name.lower() in names for name, _ in request_fields(request)) or any(
            unquote_plus(pair.partition("=")[0]) in queries for pair in urlsplit(url).query.split("&") if pair
        ):
            credential = ((), ())
        foreign = bound is not None and settings.auth is not self._shared.root_auth
        return CacheRequest(settings, request, url, credential, partition, foreign, names)

    def _security_context(self) -> ProtocolSecurityContext | None:
        """Return the client's protocol security context, or None without one."""
        if (protocols := self.protocol_options()) is None or isinstance(security := protocols.security, Unset):
            return None
        return security

    def follow_origins(self, operation: OperationPlan[object], options: RequestOptions | None) -> frozenset[Origin]:
        """Return the origins a helper may follow a server's URLs to: its server's and those its security allows."""
        origins = {request_origin(self._base(operation, self._call_settings(options, operation.operation_id)))}
        if (context := self._security_context()) is not None:
            origins.update((origin.scheme, origin.host, origin.port) for origin in context.allowed_origins)
        return frozenset(origins)

    def _secret_positions(
        self, operation: OperationPlan[object] | None, options: RequestOptions | None
    ) -> tuple[frozenset[str], frozenset[str]]:
        """Return catalog and configured signer credential positions without acquiring credentials."""
        from ..client.security import secret_names  # ruff: ignore[import-outside-top-level]

        headers, query = secret_names(self._shared.security_schemes)
        auth = self._call_settings(options, None if operation is None else operation.operation_id).auth
        if auth is not None:
            for signer in auth.signers:
                capabilities = signer.capabilities
                headers |= frozenset(name.lower() for name in capabilities.managed_headers)
                query |= frozenset(capabilities.managed_query)
        return headers, query

    def unsaved_argument(
        self, operation: OperationPlan[object], saved: Sequence[JSONValue | Unset]
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

    def saved_request(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
        self,
        plan: _PagePlan,
        operation: OperationPlan[object],
        arguments: tuple[object, ...],
        body: object,
        media_type: str | None,
        options: RequestOptions | None,
    ) -> tuple[tuple[JSONValue | Unset, ...], tuple[JSONValue, str, str | None] | None]:
        """Return the wire values of a helper call's arguments, and of its JSON body with its declared media type.

        They are encoded and checked as the call's first request encodes them; a body sent as a concrete media type
        other than its declared one also gives the type sent. An argument `unsaved_argument` names is never saved, so a
        call giving one cannot be checkpointed.
        """
        self._call_settings(options, operation.operation_id)
        saved = tuple(
            value if isinstance(value, Unset) else encode_parameter_value(operation, spec, partial(spec.dump, value))
            for spec, value in zip(operation.parameters, arguments, strict=True)
        )
        if (unsaved := self.unsaved_argument(operation, saved)) is not None:
            raise _unsaved(plan, ("arguments", *unsaved))
        request = operation.body
        if request is None or isinstance(body, Unset):
            return saved, None
        media, sent = request.selected(operation.operation_id, media_type)
        try:
            wire = media.dump(body)
        except request_errors(media.codec) as error:
            raise request_decode_error(operation, ("body",), error) from None
        return saved, (wire, media.media_type, None if sent == media.media_type else sent)

    @staticmethod
    def restored_request(
        operation: OperationPlan[object],
        arguments: tuple[JSONValue | Unset, ...],
        body: tuple[JSONValue, str, str | None] | None,
    ) -> tuple[tuple[object, ...], object, str | None]:
        """Return the arguments, body, and media type of a request a checkpoint saved, built from their wire values.

        Each value is validated against its schema and built into its native value, and a concrete media type is
        selected as a call's is; a value that does not fit, or a concrete type that selects another declared media,
        raises a request DecodeError.
        """
        restored = tuple(
            value
            if isinstance(value, Unset)
            else encode_parameter_value(operation, spec, partial(spec.restored, value))
            for spec, value in zip(operation.parameters, arguments, strict=True)
        )
        if body is None or (request := operation.body) is None:
            return restored, UNSET, None
        wire, declared, concrete = body
        media_type = declared if concrete is None else concrete
        if (media := request.selected(operation.operation_id, media_type)[0]).media_type != declared:
            raise request_decode_error(operation, ("body",))
        try:
            return restored, media.restored(wire), media_type
        except request_errors(media.codec) as error:
            raise request_decode_error(operation, ("body",), error) from None

    def checked_page(
        self,
        operation: OperationPlan[object],
        request: Callable[[], tuple[tuple[object, ...], object, str | None]],
        media_type: str | None,
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
        return str(prepared.url), HeadersView(request_fields(prepared))

    def checked_arguments(
        self,
        operation: OperationPlan[object],
        given: Mapping[int, JSONValue],
        options: RequestOptions | None,
    ) -> None:
        """Encode some arguments of a helper's request, by position, as its call encodes them, sending nothing.

        Arguments a later response gives are left out, even required ones; one that does not fit raises
        a request DecodeError.
        """
        self._call_settings(options, operation.operation_id)
        for position, value in given.items():
            spec = operation.parameters[position]
            encode_parameter_value(operation, spec, partial(encode_argument, spec, value))

    def _page_request(
        self,
        call: _SessionCall,
        request: Callable[[], tuple[tuple[object, ...], object, str | None]],
        media_type: str | None,
        options: RequestOptions | None,
        read_request: Callable[[str, HeadersView], None] | None,
    ) -> tuple[httpx2.Request, object]:
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
            read_request(str(prepared.url), HeadersView(request_fields(prepared)))
        if url is None:
            return prepared, deferred
        server = call.server_origin = request_origin(self._base(operation, call.settings))
        url = strip_query(url, call.followed_query(self._shared.security_schemes))
        headers = HeadersView(request_fields(prepared))
        if request_origin(url) != server:
            items, url = strip_credentials(headers.items(), url, self._shared.security_schemes)
            headers = HeadersView(items)
        return build_request(method=prepared.method, url=url, headers=headers, body=request_body(prepared)), deferred


class ClientCore(_ProtocolCore["httpx2.Client", "RawResponse"], NativeClientCore):
    """Run declared helpers through the shared native HTTP client."""

    __slots__ = ()

    @classmethod
    def from_client(cls, core: NativeClientCore) -> ClientCore:
        """Bind the declared helpers to the ordinary client's shared resources and option view."""
        return core.helper_view(cls)

    def execute_page(  # ruff: ignore[too-many-arguments]
        self,
        plan: _PagePlan,
        operation: OperationPlan[T],
        request: Callable[[], tuple[tuple[object, ...], object, str | None]],
        build: Callable[[T, JSONValue, bytes, ResponseInfo, str, frozenset[str]], R],
        *,
        body: object,
        media_type: str | None,
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
        call = _SessionCall(settings, operation, session)
        events = call.events = self._started(call, operation.path)
        decoder = call.decoder = operation.responses
        prepare = partial(self._page_request, call, request, media_type, options, read_request)

        def receive(response: httpx2.Response, info: ResponseInfo) -> tuple[Response[T], R]:
            received = self._read(response, info, decoder, call)
            call.check("decode")
            data, wire, content = _page(decoder, info, received, call, plan, page_limited=page_limited)
            result = build(data, wire, content, info, call.url, call.followed_query(self._shared.security_schemes))
            call.check("decode")
            return Response(data=data, info=info), result

        try:
            completed, result = self._run(call, body, prepare, receive)
            call.check("decode")
            if events is not None:
                events.finish(completed)
            call.check("decode")
        except BaseException as error:  # ruff: ignore[blind-except]
            failure = call.stopped(error)
            if failed is not None:
                failed(failure, delivery_state(call))
            if events is not None:
                events.ended(failure)
            raise failure from None
        else:
            return result

    def execute_cached(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
        self,
        operation: OperationPlan[T],
        request: httpx2.Request,
        settings: Settings,
        modified: Callable[[Response[T], bytes, bool], tuple[Response[T], R]],
        not_modified: Callable[[ResponseInfo, bool], tuple[Response[T], R]],
        options: RequestOptions | None,  # ruff: ignore[unused-method-argument]
    ) -> R:
        """Send a cache fetch's prepared request as one logical call, building what its response gives.

        A 304 is built from the stored representation by `not_modified`; any other response is decoded as the
        operation's calls decode it and given to `modified` with its body after content decoding. Both learn whether
        the response answered a redirect.
        """
        call = Call(settings, operation)
        events = call.events = self._started(call, operation.path)
        decoder = call.decoder = operation.responses

        def receive(response: httpx2.Response, info: ResponseInfo) -> tuple[Response[T], R]:
            received = self._read(response, info, decoder, call)
            call.check("decode")
            built = (
                not_modified(info, call.redirects_followed > 0)
                if info.status_code == _NOT_MODIFIED
                else modified(
                    decode_response(decoder, info, received, call.settings, call.operation_id),
                    received.content,
                    call.redirects_followed > 0,
                )
            )
            call.check("decode")
            return built

        try:
            completed, result = self._run(call, UNSET, lambda: (request, UNSET), receive)
            call.check("decode")
            if events is not None:
                events.finish(completed)
            call.check("decode")
        except BaseException as error:  # ruff: ignore[blind-except]
            failure = call.stopped(error)
            if events is not None:
                events.ended(failure)
            raise failure from None
        else:
            return result

    def open_socket(  # ruff: ignore[too-many-arguments]
        self,
        operation: OperationPlan[object],
        arguments: tuple[object, ...],
        opener: Callable[[httpx2.Request, LogicalCallContext], httpx2.Response],
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
        call = _SocketCall(settings, operation, session, open_timeout)
        events = call.events = self._started(call, operation.path)
        call.decoder = operation.responses
        result: RawResponse | None = None

        def prepare() -> tuple[httpx2.Request, object]:
            return self._prepare(
                operation,
                arguments,
                call.settings,
                body=UNSET,
                media_type=None,
                options=options,
                accept=None,
                narrowed=False,
                checked=check,
            )

        def receive(response: httpx2.Response, info: ResponseInfo) -> RawResponse:
            return self._raw_response(response, info, call, stream=True)

        try:
            result = self._run(call, UNSET, prepare, receive, opener)
            call.check("send")
            if result.info.status_code != _SWITCHING:
                refused(result)
            if events is not None:
                events.finish(UNSET, handed_off=True)
            call.check("send")
            call.handoff()
        except BaseException as error:  # ruff: ignore[blind-except]
            failure = call.stopped(error)
            if result is not None:
                result.discard(failure)
            if events is not None:
                events.ended(failure)
            raise failure from None
        else:
            return result, call


class AsyncClientCore(_ProtocolCore["httpx2.AsyncClient", "AsyncRawResponse"], NativeAsyncClientCore):
    """Run declared helpers through the shared native HTTP client."""

    __slots__ = ()

    @classmethod
    def from_client(cls, core: NativeAsyncClientCore) -> AsyncClientCore:
        """Bind the declared helpers to the ordinary client's shared resources and option view."""
        return core.helper_view(cls)

    async def execute_page(  # ruff: ignore[too-many-arguments]
        self,
        plan: _PagePlan,
        operation: OperationPlan[T],
        request: Callable[[], tuple[tuple[object, ...], object, str | None]],
        build: Callable[[T, JSONValue, bytes, ResponseInfo, str, frozenset[str]], R],
        *,
        body: object,
        media_type: str | None,
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
        call = _SessionCall(settings, operation, session)
        events = call.events = await self._started(call, operation.path)
        decoder = call.decoder = operation.responses
        prepare = partial(self._page_request, call, request, media_type, options, read_request)

        async def receive(response: httpx2.Response, info: ResponseInfo) -> tuple[Response[T], R]:
            received = await self._read(response, info, decoder, call)
            call.check("decode")
            data, wire, content = _page(decoder, info, received, call, plan, page_limited=page_limited)
            result = build(data, wire, content, info, call.url, call.followed_query(self._shared.security_schemes))
            call.check("decode")
            return Response(data=data, info=info), result

        try:
            completed, result = await call.bounded(lambda: self._run(call, body, prepare, receive))
            call.check("decode")
            if events is not None:
                await events.afinish(completed)
            call.check("decode")
        except BaseException as error:  # ruff: ignore[blind-except]
            failure = call.stopped(error)
            if failed is not None:
                failed(failure, delivery_state(call))
            if events is not None:
                await events.aended(failure)
            raise failure from None
        else:
            return result

    async def execute_cached(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
        self,
        operation: OperationPlan[T],
        request: httpx2.Request,
        settings: Settings,
        modified: Callable[[Response[T], bytes, bool], tuple[Response[T], R]],
        not_modified: Callable[[ResponseInfo, bool], tuple[Response[T], R]],
        options: RequestOptions | None,  # ruff: ignore[unused-method-argument]
    ) -> R:
        """Send a cache fetch's prepared request as one logical asyncio call, building what its response gives.

        A 304 is built from the stored representation by `not_modified`; any other response is decoded as the
        operation's calls decode it and given to `modified` with its body after content decoding. Both learn whether
        the response answered a redirect.
        """
        call = Call(settings, operation)
        events = call.events = await self._started(call, operation.path)
        decoder = call.decoder = operation.responses

        async def receive(response: httpx2.Response, info: ResponseInfo) -> tuple[Response[T], R]:
            received = await self._read(response, info, decoder, call)
            call.check("decode")
            built = (
                not_modified(info, call.redirects_followed > 0)
                if info.status_code == _NOT_MODIFIED
                else modified(
                    decode_response(decoder, info, received, call.settings, call.operation_id),
                    received.content,
                    call.redirects_followed > 0,
                )
            )
            call.check("decode")
            return built

        try:
            completed, result = await call.bounded(lambda: self._run(call, UNSET, lambda: (request, UNSET), receive))
            call.check("decode")
            if events is not None:
                await events.afinish(completed)
            call.check("decode")
        except BaseException as error:  # ruff: ignore[blind-except]
            failure = call.stopped(error)
            if events is not None:
                await events.aended(failure)
            raise failure from None
        else:
            return result

    async def open_socket(  # ruff: ignore[too-many-arguments]
        self,
        operation: OperationPlan[object],
        arguments: tuple[object, ...],
        opener: Callable[[httpx2.Request, LogicalCallContext], Awaitable[httpx2.Response]],
        *,
        options: RequestOptions | None,
        session: OperationSession,
        open_timeout: float | None,
        check: Callable[[HeadersView], None],
    ) -> tuple[AsyncRawResponse, LogicalCallContext]:
        """Open a WebSocket helper's handshake with asyncio, as the synchronous core does."""
        settings = self._call_settings(options, operation.operation_id)
        call = _SocketCall(settings, operation, session, open_timeout)
        events = call.events = await self._started(call, operation.path)
        call.decoder = operation.responses
        result: AsyncRawResponse | None = None

        def prepare() -> tuple[httpx2.Request, object]:
            return self._prepare(
                operation,
                arguments,
                call.settings,
                body=UNSET,
                media_type=None,
                options=options,
                accept=None,
                narrowed=False,
                checked=check,
            )

        async def receive(response: httpx2.Response, info: ResponseInfo) -> AsyncRawResponse:
            return await self._raw_response(response, info, call, stream=True)

        try:
            result = await call.bounded(
                lambda: self._run(call, UNSET, prepare, receive, opener), cleanup=AsyncRawResponse.aclose
            )
            call.check("send")
            if result.info.status_code != _SWITCHING:
                await arefused(result)
            if events is not None:
                await events.afinish(UNSET, handed_off=True)
            call.check("send")
            call.handoff()
        except BaseException as error:  # ruff: ignore[blind-except]
            failure = call.stopped(error)
            if result is not None:
                await result.discard(failure)
            if events is not None:
                await events.aended(failure)
            raise failure from None
        else:
            return result, call
