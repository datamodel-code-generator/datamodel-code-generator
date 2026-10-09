"""Execution shared by declared client helpers, separate from ordinary operation calls."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, cast
from urllib.parse import unquote_plus, urlsplit

import httpx2

from ..client.client import (
    AdapterT,
    Call,
    Core,
    HandleT,
    R,
    ReceivedBody,
    T,
    build_request,
    decode_response,
    delivery_state,
    request_body,
    strip_credentials,
)
from ..client.client import AsyncClientCore as NativeAsyncClientCore
from ..client.client import ClientCore as NativeClientCore
from ..client.logical import Delivery, LogicalCallContext
from ..client.native import request_fields, wire_fields
from ..client.raw import AsyncRawResponse, RawResponse, arefused, refused
from ..client.responses import HeadersView, Response
from ..client.retry import RetryTiming, retry_delay
from ..client.timing import ResolvedTimeoutOptions
from ..client.urls import absolute_target, request_origin, strip_query
from ..model_codecs.unset import UNSET, Unset

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from ..client.errors import APIConnectionError
    from ..client.logical import OperationSession
    from ..client.operations import OperationPlan, ParameterSpec, ResponseDecoder
    from ..client.options import RequestOptions, Settings
    from ..client.responses import ResponseInfo
    from ..client.retry import RetryDelay
    from ..client.security import SecuritySchemeEntry
    from ..client.timing import Budget
    from ..client.urls import Origin
    from ..model_codecs.media import JSONValue
    from .options import HelperSettings

_NOT_MODIFIED = 304
_SWITCHING = 101


def _secret(spec: ParameterSpec, value: JSONValue, headers: frozenset[str], queries: frozenset[str]) -> bool:
    """Return whether an argument carries credentials: a credential header or a scheme's query field.

    Exploded form and deepObject query parameters send only their property names or bracketed names, including
    additional properties. Other query serializers retain the declaration name as their emitted field.
    """
    plan = spec.plan
    name = plan.name
    secret = False
    match plan.location:
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


def _page(
    decoder: ResponseDecoder[T],
    info: ResponseInfo,
    body: ReceivedBody,
) -> tuple[T, JSONValue, bytes]:
    """Return a page's value, wire value, and body, or raise the failure of a page that does not decode."""
    if (problem := body.problem) is not None and body.success:
        raise problem
    content = body.content
    data, wire = decoder.decode_page(info, content, truncated=body.truncated or problem is not None, problem=problem)
    return data, wire, content


def stored_value(operation: OperationPlan[T], info: ResponseInfo, body: bytes) -> T:
    """Decode a stored success body as a call's decoder decodes a received one."""
    received = ReceivedBody(success=True)
    received.add(body)
    return decode_response(operation.responses, info, received, operation.operation_id).data


@dataclass(frozen=True, slots=True)
class CacheRequest:
    """What a cache fetch keys and sends, prepared once before its call.

    `call` places the request's credentials as its send does. `credential_headers` are the lowercase names of the
    headers credentials travel in, and `credential_queries` the query fields of the package's security schemes.
    """

    settings: Settings
    request: httpx2.Request
    call: Call
    credential_headers: frozenset[str]
    credential_queries: frozenset[str]


@dataclass(frozen=True, slots=True)
class CacheIdentity:
    """Whom a cache fetch asks as: its URL with any query credentials, and the values of the headers that tell it.

    The headers are the credential headers and every header the request's Auth added or changed, by lowercase name in
    order, each with its values, none for a header the request lacks. `authenticated` tells whether the request
    carries credentials.
    """

    url: str
    headers: tuple[tuple[str, tuple[str, ...]], ...]
    authenticated: bool

    def matches(self, sent: httpx2.Request) -> bool:
        """Return whether a sent request asks as this identity: the same URL and the same values of the same headers."""
        return absolute_target(str(sent.url)).url == self.url and self.headers == tuple(
            (name, tuple(sent.headers.get_list(name))) for name, _ in self.headers
        )


def _identity(prepared: CacheRequest, authed: httpx2.Request) -> CacheIdentity:
    """Return the identity of a cache fetch's request as its Auth gives it to be sent first."""
    before, after = prepared.request, authed.headers
    given = {name.lower() for name, _ in (*request_fields(before), *request_fields(authed))}
    names = prepared.credential_headers.union(
        name for name in given if before.headers.get_list(name) != after.get_list(name)
    )
    headers = tuple((name, tuple(after.get_list(name))) for name in sorted(names))
    url = absolute_target(str(authed.url)).url
    queries = prepared.credential_queries
    authenticated = (
        any(values for _, values in headers)
        or authed.url != before.url
        or any(unquote_plus(pair.partition("=")[0]) in queries for pair in urlsplit(url).query.split("&") if pair)
    )
    return CacheIdentity(url, headers, authenticated)


def _unsent(request: httpx2.Request) -> httpx2.Request:
    """Return a copy of a bodiless request for an Auth to place credentials on without sending it."""
    return httpx2.Request(request.method, request.url, headers=wire_fields(request_fields(request)))


class _SessionCall(Call):
    """A child call of a protocol helper session, which also bounds the call's deadline and send admissions.

    It keeps the URL it sends, credentials excluded, against which a page's relative URLs resolve, unless HTTPX2
    followed a redirect elsewhere.
    """

    __slots__ = ("session", "url")

    def __init__(
        self,
        settings: Settings,
        operation: OperationPlan[object],
        session: OperationSession,
        bound: Budget | None = None,
    ) -> None:
        """Bind the call to its session, ending it no later than the session or a helper's own bound does."""
        super().__init__(settings, operation)
        self.session = session
        self.url = ""
        for limit in (session.deadline, bound):
            if limit is not None and ((deadline := self.deadline) is None or limit.at < deadline.at):
                self.deadline = limit

    def prepared(self, request: httpx2.Request) -> httpx2.Request:
        """Prepare the request as an ordinary call does, keeping its URL."""
        request = super().prepared(request)
        self.url = str(request.url)
        return request

    @staticmethod
    def followed_query(schemes: tuple[SecuritySchemeEntry, ...]) -> frozenset[str]:
        """Return the query fields a followed URL is sent and saved without, whatever its origin.

        They are the positions of the package's declared security schemes, where the call's credentials go again.
        """
        from ..client.security import secret_names  # ruff: ignore[import-outside-top-level] - Only a followed URL needs the schemes.

        return secret_names(schemes)[1]


class _SocketCall(_SessionCall):
    """The handshake of a WebSocket helper: a session child call whose open has one cap for all of its phases.

    The cap is the least of the open timeout and the connect, read, write, and pool timeouts, bounded by the deadline,
    so a cap the deadline binds ends the call with its deadline APITimeoutError. A handshake is never redirected.
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
            for value in (
                self.open_timeout,
                configured.connect,
                configured.read,
                configured.write,
                configured.pool,
                self.remaining(),
            )
            if value is not None
        ]
        cap = min(limits) if limits else None
        return ResolvedTimeoutOptions(connect=cap, read=cap, write=cap, pool=cap)

    def follow(self, outgoing: httpx2.Request, schemes: tuple[SecuritySchemeEntry, ...]) -> bool:  # noqa: ARG002, PLR6301
        """Never follow a redirect of the handshake: a refused upgrade is terminal."""
        return False

    def retry(
        self,
        info: ResponseInfo | None,
        error: APIConnectionError | None,
        *,
        replayable: bool,
    ) -> RetryDelay | None:
        """Retry only a handshake proven unsent; a received refusal or an uncertain open stays terminal."""
        if info is None and error is not None and self.furthest() is Delivery.NOT_SENT:
            return super().retry(info, error, replayable=replayable)
        self.check()
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


class _ProtocolCore(Core[AdapterT, HandleT]):
    """Prepare and restore helper requests using the ordinary client's encoding and resource ownership."""

    __slots__ = ()

    def stream_call(
        self, operation: OperationPlan[object], options: RequestOptions | None, session: OperationSession
    ) -> Call:
        """Bind a stream's child call to its helper session before native execution."""
        return _SessionCall(self._call_settings(options, operation.operation_id), operation, session)

    def fixed_key(
        self, options: RequestOptions | None, operations: Iterable[OperationPlan[object] | None]
    ) -> tuple[str, ...] | None:
        """Return where a call fixes an idempotency key of the operations, or None.

        It is the call's own key, or a header of a declared key's name that the call's extra headers or the client's
        or a view's default headers send.
        """
        if options is not None and isinstance(options.idempotency_key, str):
            return ("options", "idempotency_key")
        names = {plan.idempotency.header_name.lower() for plan in operations if plan and plan.idempotency}
        return self.named(options, lambda name, value: value is not None and name.lower() in names) if names else None

    def named(
        self, options: RequestOptions | None, matches: Callable[[str, str | None], bool], *, query: bool = False
    ) -> tuple[str, ...] | None:
        """Return the field path of the first header, or query name, of a call's layers that matches, or None.

        The call's own extra ones come first, then the default ones the client and its views merged, except those the
        call's own replace or remove.
        """
        call = None if options is None else options.extra_query if query else options.extra_headers
        fold = str if query else str.lower
        replaced: set[str] = set()
        for name, value in () if call is None else call.items():
            if matches(name, value):
                return ("options", "extra_query" if query else "extra_headers", name)
            replaced.add(fold(name))
        for name, value in self._settings.query if query else self._settings.headers:
            if fold(name) not in replaced and matches(name, value):
                return ("default_query" if query else "default_headers", name)
        return None

    @staticmethod
    def reconnects_after(error: APIConnectionError) -> bool:
        """Allow reconnection after native read failures."""
        return isinstance(error.cause, (httpx2.ReadError, httpx2.ReadTimeout, httpx2.RemoteProtocolError))

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

    def _helpers(self) -> HelperSettings:
        """Return the helper settings of the client's root."""
        return cast("HelperSettings", self._shared.protocols)

    def helper_defaults(self, name: str) -> object:
        """Return the options the client's `helper_defaults` give one helper, or None."""
        return self._helpers().defaults.get(name)

    def cache_store(self, name: str, create: Callable[[], object]) -> object:
        """Return the store the client lends a cache helper, or else the one its root creates with `create` first.

        Views share their root's stores; another client never does.
        """
        helpers = self._helpers()
        if (store := helpers.cache_stores.get(name) or helpers.created.get(name)) is None:
            store = helpers.created.setdefault(name, create())
        return store

    def cache_request(
        self, operation: OperationPlan[object], arguments: tuple[object, ...], options: RequestOptions | None
    ) -> CacheRequest:
        """Return what a cache fetch keys and sends: its settings, its request before auth, and its call.

        A fetch on a closed client or past its deadline is refused first, as a call is, and so is a required operation
        that no credentials authenticate.
        """
        settings = self._call_settings(options, operation.operation_id)
        call = Call(settings, operation)
        self._admitted(call)
        request, _ = self._prepare(
            operation,
            arguments,
            settings,
            body=UNSET,
            media_type=None,
            options=options,
            accept=operation.responses.accept,
        )
        if operation.security is not None:
            self._bind_auth(call)
        headers, queries = self._secret_positions()
        return CacheRequest(settings, request, call, headers, queries)

    def follow_origins(self, operation: OperationPlan[object], options: RequestOptions | None) -> frozenset[Origin]:
        """Return the origins a helper may follow a server's URLs to: its server's and the `allowed_origins`."""
        origins = {request_origin(self._base(operation, self._call_settings(options, operation.operation_id)))}
        origins.update((origin.scheme, origin.host, origin.port) for origin in self._helpers().allowed_origins)
        return frozenset(origins)

    def follow_query(self) -> frozenset[str]:
        """Return the query fields a followed URL a caller gives is kept and sent without, as a server's is."""
        return self._secret_positions()[1]

    def _secret_positions(self) -> tuple[frozenset[str], frozenset[str]]:
        """Return the header and query positions of the package's declared security schemes and credential headers."""
        from ..client.security import secret_names  # ruff: ignore[import-outside-top-level]

        return secret_names(self._shared.security_schemes)

    def credential_argument(
        self, operation: OperationPlan[object], written: Sequence[JSONValue | Unset]
    ) -> tuple[str, str] | None:
        """Return the location and name of the first written argument that carries credentials, or None.

        It is a header the client treats as a credential, a query parameter at the position of a declared security
        scheme, or a querystring whose value has a field at such a position; generation already refuses a write to a
        cookie or to a fixed credential name, so these are the fields a server value names at run time.
        """
        headers, queries = self._secret_positions()
        return next(
            (
                (spec.plan.location, spec.plan.name)
                for spec, value in zip(operation.parameters, written, strict=True)
                if not isinstance(value, Unset) and _secret(spec, value, headers, queries)
            ),
            None,
        )

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

    @property
    def sockets(self) -> set[Callable[[], None]]:
        """Return the closes of the WebSocket sessions open on the HTTP client, which closing its creator runs first."""
        return self._shared.sockets

    def execute_page(  # ruff: ignore[too-many-arguments]
        self,
        operation: OperationPlan[T],
        request: Callable[[], tuple[tuple[object, ...], object, str | None]],
        build: Callable[[T, JSONValue, bytes, ResponseInfo, str, frozenset[str]], R],
        *,
        body: object,
        media_type: str | None,
        options: RequestOptions | None,
        session: OperationSession,
        read_request: Callable[[str, HeadersView], None] | None = None,
        failed: Callable[[BaseException, Delivery], None] | None = None,
    ) -> R:
        """Execute one page of a helper session as a child logical call, building what the page's response gives.

        The page uses the ordinary response limit. Its arguments, body, and any URL a server gave are taken when
        the call prepares; `body` is the caller's. What the
        page gives is built from its decoded body and its bytes, with the URL of the hop that returned it and the query
        fields a followed URL is without.
        """
        settings = self._call_settings(options, operation.operation_id)
        call = _SessionCall(settings, operation, session)
        self._admitted(call)
        decoder = call.decoder = operation.responses
        prepare = partial(self._page_request, call, request, media_type, options, read_request)

        def receive(response: httpx2.Response, info: ResponseInfo) -> tuple[Response[T], R]:
            received = self._read(response, info, decoder, call)

            data, wire, content = _page(decoder, info, received)
            url = str(response.url) if response.history else call.url
            result = build(data, wire, content, info, url, call.followed_query(self._shared.security_schemes))

            return Response(data=data, info=info), result

        try:
            _, result = self._run(call, body, prepare, receive)

        except BaseException as error:  # ruff: ignore[blind-except]
            failure = call.stopped(error)
            if failed is not None:
                failed(failure, delivery_state(call))
            raise failure from failure.__cause__
        else:
            return result

    def cache_identity(self, prepared: CacheRequest) -> CacheIdentity:
        """Return a cache fetch's identity: its request as the call's Auth gives it to be sent first, sending nothing.

        The Auth is the call's own, its credentials', or else the HTTP client's. Its flow runs on a copy of the request
        and is closed at its first request; the Auth's failure is the call's.
        """
        call, request = prepared.call, _unsent(prepared.request)
        client = self._shared.http_client
        auth = call.native_auth(
            self._shared.credentials, source=None, send=partial(client.send, auth=None, follow_redirects=False)
        )
        if (auth := client.auth if isinstance(auth, Unset) else auth) is not None:
            flow = auth.sync_auth_flow(request)
            try:
                request = next(flow)
            except Exception as error:  # noqa: BLE001 - A failing Auth fails the call, as its send would.
                failure = self._classified(error, call)
                raise failure from failure.__cause__
            finally:
                flow.close()
        return _identity(prepared, request)

    def execute_cached(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
        self,
        operation: OperationPlan[T],
        request: httpx2.Request,
        settings: Settings,
        modified: Callable[[Response[T], bytes, bool, httpx2.Request], tuple[Response[T], R]],
        not_modified: Callable[[ResponseInfo, bool, httpx2.Request], tuple[Response[T], R]],
        options: RequestOptions | None,  # ruff: ignore[unused-method-argument]
    ) -> R:
        """Send a cache fetch's prepared request as one logical call, building what its response gives.

        A 304 is built from the stored representation by `not_modified`; any other response is decoded as the
        operation's calls decode it and given to `modified` with its body after content decoding. Both learn whether
        the response answered a redirect, and the request it answered as its Auth sent it.
        """
        call = Call(settings, operation)
        self._admitted(call)
        decoder = call.decoder = operation.responses

        def receive(response: httpx2.Response, info: ResponseInfo) -> tuple[Response[T], R]:
            received = self._read(response, info, decoder, call)

            redirected = call.redirects_followed > 0
            return (
                not_modified(info, redirected, response.request)
                if info.status_code == _NOT_MODIFIED
                else modified(
                    decode_response(decoder, info, received, call.operation_id),
                    received.content,
                    redirected,
                    response.request,
                )
            )

        try:
            _, result = self._run(call, UNSET, lambda: (request, UNSET), receive)

        except BaseException as error:  # ruff: ignore[blind-except]
            failure = call.stopped(error)
            raise failure from failure.__cause__
        else:
            return result

    def open_socket(  # ruff: ignore[too-many-arguments]
        self,
        operation: OperationPlan[object],
        arguments: tuple[object, ...],
        upgrade: Mapping[str, str],
        *,
        options: RequestOptions | None,
        session: OperationSession,
        open_timeout: float | None,
        check: Callable[[HeadersView], None],
        accept: Callable[[ResponseInfo], None],
    ) -> tuple[RawResponse, LogicalCallContext, httpx2.Response]:
        """Send a WebSocket helper's handshake through the HTTP client, as one child call of the helper's session.

        The headers prepared pass the check before the upgrade headers join them and anything is sent. A 101 the accept
        check passes is handed over as a streaming handle, with the call whose deadline bounds it and the native
        response whose network stream the session takes; any other response raises the call's typed failure.
        """
        settings = self._call_settings(options, operation.operation_id)
        call = _SocketCall(settings, operation, session, open_timeout)
        self._admitted(call)
        call.decoder = operation.responses
        result: RawResponse | None = None
        opened: list[httpx2.Response] = []

        def prepare() -> tuple[httpx2.Request, object]:
            request, deferred = self._prepare(
                operation,
                arguments,
                call.settings,
                body=UNSET,
                media_type=None,
                options=options,
                accept=None,
                checked=check,
            )
            request.headers.update(upgrade)
            return request, deferred

        def receive(response: httpx2.Response, info: ResponseInfo) -> RawResponse:
            opened.append(response)
            return self._raw_response(response, info, call, stream=True)

        try:
            result = self._run(call, UNSET, prepare, receive)

            if result.info.status_code != _SWITCHING:
                refused(result)
            accept(result.info)
            call.handoff()
        except BaseException as error:  # ruff: ignore[blind-except]
            failure = call.stopped(error)
            if result is not None:
                result.discard(failure)
            raise failure from failure.__cause__
        else:
            return result, call, opened[-1]


class AsyncClientCore(_ProtocolCore["httpx2.AsyncClient", "AsyncRawResponse"], NativeAsyncClientCore):
    """Run declared helpers through the shared native HTTP client."""

    __slots__ = ()

    @classmethod
    def from_client(cls, core: NativeAsyncClientCore) -> AsyncClientCore:
        """Bind the declared helpers to the ordinary client's shared resources and option view."""
        return core.helper_view(cls)

    async def execute_page(  # ruff: ignore[too-many-arguments]
        self,
        operation: OperationPlan[T],
        request: Callable[[], tuple[tuple[object, ...], object, str | None]],
        build: Callable[[T, JSONValue, bytes, ResponseInfo, str, frozenset[str]], R],
        *,
        body: object,
        media_type: str | None,
        options: RequestOptions | None,
        session: OperationSession,
        read_request: Callable[[str, HeadersView], None] | None = None,
        failed: Callable[[BaseException, Delivery], None] | None = None,
    ) -> R:
        """Execute one page of a helper session as a child logical call, building what the page's response gives.

        The page uses the ordinary response limit. Its arguments, body, and any URL a server gave are taken when
        the call prepares; `body` is the caller's. What the
        page gives is built from its decoded body and its bytes, with the URL of the hop that returned it and the query
        fields a followed URL is without.
        """
        settings = self._call_settings(options, operation.operation_id)
        call = _SessionCall(settings, operation, session)
        self._admitted(call)
        decoder = call.decoder = operation.responses
        prepare = partial(self._page_request, call, request, media_type, options, read_request)

        async def receive(response: httpx2.Response, info: ResponseInfo) -> tuple[Response[T], R]:
            received = await self._read(response, info, decoder, call)

            data, wire, content = _page(decoder, info, received)
            url = str(response.url) if response.history else call.url
            result = build(data, wire, content, info, url, call.followed_query(self._shared.security_schemes))

            return Response(data=data, info=info), result

        try:
            _, result = await self._run(call, body, prepare, receive)

        except BaseException as error:  # ruff: ignore[blind-except]
            failure = call.stopped(error)
            if failed is not None:
                failed(failure, delivery_state(call))
            raise failure from failure.__cause__
        else:
            return result

    async def acache_identity(self, prepared: CacheRequest) -> CacheIdentity:
        """Return a cache fetch's identity with asyncio, as the synchronous core does."""
        call, request = prepared.call, _unsent(prepared.request)
        client = self._shared.http_client
        auth = call.native_auth(
            self._shared.credentials, source=None, async_send=partial(client.send, auth=None, follow_redirects=False)
        )
        if (auth := client.auth if isinstance(auth, Unset) else auth) is not None:
            flow = auth.async_auth_flow(request)
            try:
                request = await anext(flow)
            except Exception as error:  # noqa: BLE001 - A failing Auth fails the call, as its send would.
                failure = self._classified(error, call)
                raise failure from failure.__cause__
            finally:
                await flow.aclose()
        return _identity(prepared, request)

    async def execute_cached(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
        self,
        operation: OperationPlan[T],
        request: httpx2.Request,
        settings: Settings,
        modified: Callable[[Response[T], bytes, bool, httpx2.Request], tuple[Response[T], R]],
        not_modified: Callable[[ResponseInfo, bool, httpx2.Request], tuple[Response[T], R]],
        options: RequestOptions | None,  # ruff: ignore[unused-method-argument]
    ) -> R:
        """Send a cache fetch's prepared request as one logical asyncio call, building what its response gives.

        A 304 is built from the stored representation by `not_modified`; any other response is decoded as the
        operation's calls decode it and given to `modified` with its body after content decoding. Both learn whether
        the response answered a redirect, and the request it answered as its Auth sent it.
        """
        call = Call(settings, operation)
        self._admitted(call)
        decoder = call.decoder = operation.responses

        async def receive(response: httpx2.Response, info: ResponseInfo) -> tuple[Response[T], R]:
            received = await self._read(response, info, decoder, call)

            redirected = call.redirects_followed > 0
            return (
                not_modified(info, redirected, response.request)
                if info.status_code == _NOT_MODIFIED
                else modified(
                    decode_response(decoder, info, received, call.operation_id),
                    received.content,
                    redirected,
                    response.request,
                )
            )

        try:
            _, result = await self._run(call, UNSET, lambda: (request, UNSET), receive)

        except BaseException as error:  # ruff: ignore[blind-except]
            failure = call.stopped(error)
            raise failure from failure.__cause__
        else:
            return result

    async def open_socket(  # ruff: ignore[too-many-arguments]
        self,
        operation: OperationPlan[object],
        arguments: tuple[object, ...],
        upgrade: Mapping[str, str],
        *,
        options: RequestOptions | None,
        session: OperationSession,
        open_timeout: float | None,
        check: Callable[[HeadersView], None],
        accept: Callable[[ResponseInfo], None],
    ) -> tuple[AsyncRawResponse, LogicalCallContext, httpx2.Response]:
        """Open a WebSocket helper's handshake with asyncio, as the synchronous core does."""
        settings = self._call_settings(options, operation.operation_id)
        call = _SocketCall(settings, operation, session, open_timeout)
        self._admitted(call)
        call.decoder = operation.responses
        result: AsyncRawResponse | None = None
        opened: list[httpx2.Response] = []

        def prepare() -> tuple[httpx2.Request, object]:
            request, deferred = self._prepare(
                operation,
                arguments,
                call.settings,
                body=UNSET,
                media_type=None,
                options=options,
                accept=None,
                checked=check,
            )
            request.headers.update(upgrade)
            return request, deferred

        async def receive(response: httpx2.Response, info: ResponseInfo) -> AsyncRawResponse:
            opened.append(response)
            return await self._raw_response(response, info, call, stream=True)

        try:
            result = await self._run(call, UNSET, prepare, receive)

            if result.info.status_code != _SWITCHING:
                await arefused(result)
            accept(result.info)
            call.handoff()
        except BaseException as error:  # ruff: ignore[blind-except]
            failure = call.stopped(error)
            if result is not None:
                await result.discard(failure)
            raise failure from failure.__cause__
        else:
            return result, call, opened[-1]
