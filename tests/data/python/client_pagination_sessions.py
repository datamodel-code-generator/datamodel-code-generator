"""Bound pagination sessions: options, send budgets, deadlines, cancellation, hooks, limiters, and OAuth sends."""

from __future__ import annotations

import asyncio
import importlib
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from tests.data.python.client_oauth import Adapter, AsyncAdapter, AsyncResponse, Response
from tests.data.python.client_pagination import Harness, adrained, drained, fetched, progress, users
from tests.data.python.client_runtime import (
    Exchange,
    arecord,
    describe,
    injected,
    json_response,
    raw_response,
    record,
    run,
)

if TYPE_CHECKING:
    from types import ModuleType

    import httpx2

_TOKEN: Final = "https://auth.example.com/token"


def _issued(value: str) -> bytes:
    return json.dumps({
        "access_token": value,
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": "users.read",
    }).encode()


def _session(error: BaseException | None) -> str:
    """Return whether a failure names a helper session, without its identifier."""
    return f"session={getattr(error, 'parent_session_id', None) is not None}"


def _failure(call: Any) -> BaseException | None:
    try:
        call()
    except Exception as error:  # noqa: BLE001
        return error
    return None


class _Events:
    """A hook keeping each event's name and helper session, with an action run on some events."""

    def __init__(self, **actions: Any) -> None:
        self.events: list[tuple[str, str | None]] = []
        self.actions = actions

    def on_event(self, event: Any) -> None:
        self.events.append((event.name, event.parent_session_id))
        if (action := self.actions.get(event.name)) is not None:
            action(event)

    def sessions(self) -> set[str | None]:
        """Return the distinct helper sessions of the events kept so far, then forget them."""
        sessions = {session for _, session in self.events}
        self.events.clear()
        return sessions


class _AsyncEvents(_Events):
    async def on_event(self, event: Any) -> None:  # ty: ignore[invalid-method-override]
        super().on_event(event)
        if event.name == "attempt_start" and self.actions.pop("cancel", None) and (task := asyncio.current_task()):
            task.cancel()
            await asyncio.sleep(0)


class _Permit:
    def release(self) -> None:
        pass


class _AsyncPermit:
    async def release(self) -> None:
        pass


class _Limiter:
    """A limiter that keeps each context's helper session and runs an action while a page holds the pager."""

    def __init__(self) -> None:
        self.sessions: list[str | None] = []
        self.action: Any = None

    def acquire(self, context: Any) -> _Permit:
        self.sessions.append(context.parent_session_id)
        if (action := self.action) is not None:
            self.action = None
            action()
        return _Permit()


class _AsyncLimiter(_Limiter):
    async def acquire(self, context: Any) -> _AsyncPermit:  # ty: ignore[invalid-method-override]
        self.sessions.append(context.parent_session_id)
        if (action := self.action) is not None:
            self.action = None
            await action()
        return _AsyncPermit()


def _fail(event: Any) -> None:
    msg = f"hook failed on {event.name}"
    raise RuntimeError(msg)


@injected
def _interrupting(_request: httpx2.Request) -> httpx2.Response:
    raise KeyboardInterrupt


def pagination_sessions(package: ModuleType, lines: list[str]) -> None:
    """Bound one pager's pages by its session, and fail it where a page or its session fails."""
    harness = Harness(package)
    exchange = Exchange(lines)
    with exchange.client() as native, package.Client(http_client=native, options=harness.client_options()) as api:
        _options(harness, api, lines)
        _sends(harness, api, exchange, lines)
        _deadlines(harness, api, exchange, lines)
        _failures(harness, api, exchange, lines)
        _concurrency(harness, api, exchange, lines)
    _defaults(harness, exchange, lines)
    _hooks(harness, exchange, lines)
    _closing(harness, exchange, lines)
    run(lambda: _async_sessions(harness, lines))


def _options(harness: Harness, api: Any, lines: list[str]) -> None:
    """Refuse options of other types and a fixed idempotency key, from any options layer, before sending anything."""
    options, protocols = harness.options, harness.protocols
    helper = api.protocols.users.all
    for label, settings in (
        ("pagination options of another type", {"pagination_options": options.SessionOptions()}),
        ("request options of another type", {"options": protocols.PaginationOptions()}),
        ("session options of another type", {"session_options": options.RequestOptions()}),
        ("fixed idempotency key", {"options": options.RequestOptions(idempotency_key=options.IdempotencyKey.new())}),
    ):
        record(lines, f"iterate with {label}", lambda settings=settings: helper.iterate(**settings))
        record(lines, f"page with {label}", lambda settings=settings: helper.page(**settings))
    record(lines, "next page of options of another type", lambda: helper.next_page(None, options="fast"))
    fixed = options.IdempotencyKey.new()
    scoped = api.with_options(options.RequestOptions(idempotency_key=fixed)).protocols.users.all
    record(lines, "iterate with a fixed key from scoped options", scoped.iterate)
    with harness.package.Client(options=harness.client_options(idempotency_key=fixed)) as keyed:
        record(lines, "page with a fixed key from client options", keyed.protocols.users.all.page)


def _sends(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Admit a page only while its session has a send slot, and stop a retry or a redirect without one."""
    options = harness.options
    helper = api.protocols.users.all

    def sends(limit: int | None, **settings: Any) -> Any:
        return helper.iterate(session_options=options.SessionOptions(max_network_sends=limit), **settings)

    pager = sends(0)
    drained(lines, "no send slot", pager)
    lines.append(f"    progress {progress(pager)} {_session(_failure(lambda: next(pager)))}")
    exchange.respond(users("1", cursor="a"))
    pager = sends(1)
    drained(lines, "one send slot", pager)
    lines.append(f"    progress {progress(pager)}")
    exchange.respond(json_response(503, {"message": "busy"}))
    error = _failure(lambda: next(sends(1)))
    lines.append(f"  retry without a send slot ! {describe(error)} stop={getattr(error, 'retry_stop_reason', None)}")
    redirects = options.RequestOptions(redirects=options.RedirectOptions(enabled=True))
    exchange.respond(raw_response(302, b"", Location="https://api.example.com/users?moved=1"))
    error = _failure(lambda: next(sends(1, options=redirects)))
    lines.append(
        f"  redirect without a send slot ! {describe(error)} cause={type(getattr(error, 'cause', None)).__name__} "
        f"counters={getattr(error, 'network_send_count', None)}/{getattr(error, 'network_send_budget_used', None)} "
        f"{_session(error)}"
    )
    exchange.respond(users("1", cursor="a"), users("2"))
    drained(lines, "no send limit", sends(None))
    drained(lines, "call without a send slot", helper.iterate(options=options.RequestOptions(max_network_sends=0)))
    exchange.respond(
        json_response(500, {"message": "down"}),
        json_response(500, {"message": "down"}),
        json_response(500, {"message": "down"}),
    )
    pager = helper.iterate()
    error = _failure(lambda: next(pager))
    lines.append(f"  typed error after retries ! {describe(error)} body={getattr(error, 'body', None)!r}")
    record(lines, "after the typed error", lambda: next(pager))


def _deadlines(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """End a session at its total timeout or deadline, bounding each page by it, without sending after it."""
    options = harness.options
    helper = api.protocols.users.all
    for label, session in (
        ("session timeout", options.SessionOptions(total_timeout=0)),
        ("earlier session timeout", options.SessionOptions(total_timeout=0, deadline=options.Deadline.after(60))),
        ("earlier session deadline", options.SessionOptions(total_timeout=60, deadline=options.Deadline.after(0))),
    ):
        pager = helper.iterate(session_options=session)
        error = _failure(lambda pager=pager: next(pager))
        lines.append(f"  {label} ! {describe(error)} {_session(error)} {progress(pager)}")
    deadline = options.Deadline.after(1.0)
    pager = helper.iterate(session_options=options.SessionOptions(deadline=deadline))
    exchange.respond(users("1", cursor="a"))
    record(lines, "first item before the deadline", lambda pager=pager: next(pager).id)
    time.sleep(max(deadline.remaining(), 0) + 0.05)
    record(lines, "next page after the deadline", lambda: next(pager))
    exchange.respond(users("1"))
    drained(lines, "no session timeout", helper.iterate(session_options=options.SessionOptions(total_timeout=None)))
    drained(lines, "page timeout", helper.iterate(options=options.RequestOptions(total_timeout=0)))
    exchange.respond(users("1", cursor="a"))
    first = fetched(lines, "page before an expired session", helper.page)
    fetched(
        lines,
        "next page in an expired session",
        lambda: helper.next_page(first, session_options=options.SessionOptions(total_timeout=0)),
    )


def _failures(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Fail a pager at a cancelled token or an interruption, refusing every later step."""
    options = harness.options
    helper = api.protocols.users.all
    token = options.CancelToken()
    pager = helper.iterate(options=options.RequestOptions(cancel_token=token))
    exchange.respond(users("1", cursor="a"))
    record(lines, "item before cancelling", lambda pager=pager: next(pager).id)
    token.cancel()
    record(lines, "item after cancelling", lambda: next(pager))
    record(lines, "item after the cancelled page", lambda: next(pager))
    pager = helper.iterate()
    exchange.respond(_interrupting)
    try:
        next(pager)
    except KeyboardInterrupt:
        lines.append("  interrupted page: KeyboardInterrupt")
    record(lines, "item after the interruption", lambda: next(pager))
    lines.append(f"    progress {progress(pager)}")


def _concurrency(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Refuse consuming or closing a pager while one of its pages is being fetched."""
    options = harness.options
    limiter = _Limiter()
    pager = api.protocols.users.all.iterate(options=options.RequestOptions(limiter=limiter))

    def concurrent() -> None:
        record(lines, "item while fetching", lambda: next(pager))
        record(lines, "close while fetching", pager.close)

    limiter.action = concurrent
    exchange.respond(users("1"))
    drained(lines, "items after the concurrent steps", pager)
    lines.append(f"  limiter sessions {len(set(limiter.sessions))} named={None not in limiter.sessions}")


def _defaults(harness: Harness, exchange: Exchange, lines: list[str]) -> None:
    """Take each limit from the call, then the client's helper defaults, then the kind's, refusing unknown helpers."""
    package, options, protocols = harness.package, harness.options, harness.protocols
    values = json.loads(
        (Path(__file__).parents[1] / "generation_platform/client/defaults.json").read_text(encoding="utf-8")
    )["pagination_limits"]
    for label, pagination, session in (
        ("default traversal", None, None),
        ("explicit page budget", protocols.PaginationOptions(max_pages=values["explicit_pages"]), None),
        ("explicit send budget", None, options.SessionOptions(max_network_sends=values["explicit_sends"])),
    ):
        quiet = Exchange([])
        with quiet.client() as native, package.Client(http_client=native, options=harness.client_options()) as api:
            pager = api.protocols.users.all.iterate(pagination_options=pagination, session_options=session)
            count, failure = 0, "none"
            try:
                for index in range(values["pages"]):
                    quiet.respond(*[json_response(503, {"message": "busy"})] * values["retries_per_page"])
                    quiet.respond(
                        users(str(index), cursor=str(index + 1)) if index + 1 < values["pages"] else users(str(index))
                    )
                    next(pager)
                    count += 1
                    quiet.lines.clear()
            except Exception as error:  # noqa: BLE001
                failure = describe(error)
            lines.append(f"  {label}: items={count} failure={failure} progress={progress(pager)}")
            pager.close()
    for label, pagination in (
        ("default item budget", None),
        ("explicit item budget", protocols.PaginationOptions(max_items=values["explicit_items"])),
    ):
        quiet = Exchange([])
        with quiet.client() as native, package.Client(http_client=native, options=harness.client_options()) as api:
            quiet.respond(users(*["x"] * values["items"]))
            pager = api.protocols.users.all.iterate(pagination_options=pagination)
            count, failure = 0, "none"
            try:
                for _ in pager:
                    count += 1
            except Exception as error:  # noqa: BLE001
                failure = describe(error)
            lines.append(f"  {label}: items={count} failure={failure} progress={progress(pager)}")
    for label, session in (
        ("default session clock", None),
        ("explicit session clock", options.SessionOptions(total_timeout=values["total_timeout"])),
    ):
        ticks = [0.0]
        clock = options.Clock(monotonic=lambda ticks=ticks: ticks[0], time=lambda: 0.0)
        quiet = Exchange([])
        with (
            quiet.client() as native,
            package.Client(http_client=native, options=harness.client_options(clock=clock)) as api,
        ):
            quiet.respond(users("1", cursor="a"))
            pager = api.protocols.users.all.iterate(session_options=session)
            record(lines, f"{label} first", lambda pager=pager: next(pager).id)
            ticks[0] += values["clock_step"]
            if session is None:
                quiet.respond(users("2"))
            record(lines, f"{label} after former timeout", lambda pager=pager: next(pager).id)
            lines.append(f"    progress {progress(pager)}")
            pager.close()
    defaults = protocols.ProtocolDefaults(
        options=protocols.PaginationOptions(max_items=1), session=options.SessionOptions(max_network_sends=2)
    )
    for label, entries in (
        ("unknown helper", {"users.everyone": defaults}),
        ("options of another kind", {"users.all": protocols.ProtocolDefaults(options=protocols.PollOptions())}),
    ):
        settings = options.ClientOptions(protocols=options.ProtocolClientOptions(defaults=entries))
        record(lines, f"client with defaults of an {label}", lambda settings=settings: package.Client(options=settings))
        record(
            lines,
            f"async client with defaults of an {label}",
            lambda settings=settings: package.AsyncClient(options=settings),
        )
    for label, settings in (
        ("no defaults", options.ProtocolClientOptions()),
        ("empty defaults", options.ProtocolClientOptions(defaults={})),
    ):
        record(
            lines,
            f"client with {label}",
            lambda settings=settings: package.Client(options=options.ClientOptions(protocols=settings)).close(),
        )
    client_options = harness.client_options(
        protocols=options.ProtocolClientOptions(
            defaults={"users.all": defaults, "users.search": protocols.ProtocolDefaults()}
        )
    )
    with exchange.client() as native, package.Client(http_client=native, options=client_options) as api:
        helper = api.protocols.users.all
        exchange.respond(users("1", "2"))
        drained(lines, "default item limit", helper.iterate())
        exchange.respond(users("1", "2"))
        drained(
            lines, "empty call options keep defaults", helper.iterate(pagination_options=protocols.PaginationOptions())
        )
        exchange.respond(users("1", "2"))
        drained(lines, "call item limit", helper.iterate(pagination_options=protocols.PaginationOptions(max_items=3)))
        unlimited = protocols.PaginationOptions(max_items=None)
        exchange.respond(users("1", cursor="a"), users("2", cursor="b"))
        drained(lines, "default send limit", helper.iterate(pagination_options=unlimited).iter_pages())
        exchange.respond(users("1", cursor="a"), users("2", cursor="b"), users("3"))
        drained(
            lines,
            "call send limit",
            helper.iterate(
                pagination_options=unlimited, session_options=options.SessionOptions(max_network_sends=None)
            ).iter_pages(),
        )
        exchange.respond(users("1", "2"))
        drained(lines, "helper without defaults", api.protocols.users.by_header.iterate())
        exchange.respond(users("1", "2"))
        drained(lines, "view keeps defaults", api.with_options(options.RequestOptions()).protocols.users.all.iterate())


def _hooks(harness: Harness, exchange: Exchange, lines: list[str]) -> None:
    """Name a pager's session on every event and error of its pages, none on ordinary calls, a new one per pager."""
    package, options = harness.package, harness.options
    events = _Events()
    limiter = _Limiter()
    settings = harness.client_options(hooks=(events,), limiter=limiter)
    with exchange.client() as native, package.Client(http_client=native, options=settings) as api:
        exchange.respond(users("1", cursor="a"), users("2"))
        drained(lines, "hooked items", api.protocols.users.all.iterate())
        first = events.sessions()
        exchange.respond(users("1"))
        drained(lines, "another pager", api.protocols.users.all.iterate())
        second = events.sessions()
        exchange.respond(users("1"))
        record(lines, "ordinary call", lambda: len(api.users.list_users().data))
        ordinary = events.sessions()
        lines.extend((
            (
                f"  one session per pager={len(first) == len(second) == 1 and first != second} "
                f"named={None not in first | second} ordinary={ordinary}"
            ),
            f"  limiter sessions {len(set(limiter.sessions))} ordinary={limiter.sessions[-1]}",
        ))
        exchange.respond(json_response(200, {"data": [{"id": "1"}]}))
        error = _failure(lambda: next(api.protocols.loose.all.iterate()))
        failed = events.sessions()
        lines.append(
            f"  failed page ! {describe(error)} same session={failed == {getattr(error, 'parent_session_id', None)}}"
        )
    failing = _Events(call_end=_fail)
    with (
        exchange.client() as native,
        package.Client(http_client=native, options=options.ClientOptions(hooks=(failing,))) as api,
    ):
        exchange.respond(users("1", cursor="a"))
        pager = api.protocols.users.all.iterate()
        error = _failure(lambda: next(pager))
        completed = getattr(error, "completed_result", None)
        lines.append(
            f"  call end hook failure ! {describe(error)} completed={len(completed.data.data)} {_session(error)}"
        )
        record(lines, "after the hook failure", lambda: next(pager))


def _closing(harness: Harness, exchange: Exchange, lines: list[str]) -> None:
    """Refuse the next page of a pager whose client closed after its first page."""
    with exchange.client() as native:
        api = harness.package.Client(http_client=native, options=harness.client_options())
        pager = api.protocols.users.all.iterate()
        exchange.respond(users("1", cursor="a"))
        record(lines, "item before closing the client", lambda pager=pager: next(pager).id)
        api.close()
        record(lines, "item after closing the client", lambda: next(pager))
        record(lines, "item after the closed client", lambda: next(pager))


async def _async_sessions(harness: Harness, lines: list[str]) -> None:  # noqa: PLR0914 - Exercise pagination session limits together.
    """Bound asyncio pages by their session and fail the pager where a page fails or its task is cancelled."""
    package, options, protocols = harness.package, harness.options, harness.protocols
    values = json.loads(
        (Path(__file__).parents[1] / "generation_platform/client/defaults.json").read_text(encoding="utf-8")
    )["pagination_limits"]
    for label, pagination, session in (
        ("default traversal", None, None),
        ("explicit page budget", protocols.PaginationOptions(max_pages=values["explicit_pages"]), None),
        ("explicit send budget", None, options.SessionOptions(max_network_sends=values["explicit_sends"])),
    ):
        quiet = Exchange([])
        async with (
            quiet.async_client() as native,
            package.AsyncClient(http_client=native, options=harness.client_options()) as api,
        ):
            pager = api.protocols.users.all.iterate(pagination_options=pagination, session_options=session)
            count, failure = 0, "none"
            try:
                for index in range(values["pages"]):
                    quiet.respond(*[json_response(503, {"message": "busy"})] * values["retries_per_page"])
                    quiet.respond(
                        users(str(index), cursor=str(index + 1)) if index + 1 < values["pages"] else users(str(index))
                    )
                    await anext(pager)
                    count += 1
                    quiet.lines.clear()
            except Exception as error:  # noqa: BLE001
                failure = describe(error)
            lines.append(f"  async {label}: items={count} failure={failure} progress={progress(pager)}")
            await pager.aclose()
    for label, pagination in (
        ("default item budget", None),
        ("explicit item budget", protocols.PaginationOptions(max_items=values["explicit_items"])),
    ):
        quiet = Exchange([])
        async with (
            quiet.async_client() as native,
            package.AsyncClient(http_client=native, options=harness.client_options()) as api,
        ):
            quiet.respond(users(*["x"] * values["items"]))
            pager = api.protocols.users.all.iterate(pagination_options=pagination)
            count, failure = 0, "none"
            try:
                async for _ in pager:
                    count += 1
            except Exception as error:  # noqa: BLE001
                failure = describe(error)
            lines.append(f"  async {label}: items={count} failure={failure} progress={progress(pager)}")
    for label, session in (
        ("default session clock", None),
        ("explicit session clock", options.SessionOptions(total_timeout=values["total_timeout"])),
    ):
        ticks = [0.0]
        clock = options.Clock(monotonic=lambda ticks=ticks: ticks[0], time=lambda: 0.0)
        quiet = Exchange([])
        async with (
            quiet.async_client() as native,
            package.AsyncClient(http_client=native, options=harness.client_options(clock=clock)) as api,
        ):
            quiet.respond(users("1", cursor="a"))
            pager = api.protocols.users.all.iterate(session_options=session)
            await arecord(lines, f"async {label} first", lambda pager=pager: anext(pager))
            ticks[0] += values["clock_step"]
            if session is None:
                quiet.respond(users("2"))
            await arecord(lines, f"async {label} after former timeout", lambda pager=pager: anext(pager))
            lines.append(f"    progress {progress(pager)}")
            await pager.aclose()
    exchange = Exchange(lines)
    events = _AsyncEvents()
    limiter = _AsyncLimiter()
    settings = harness.client_options(hooks=(events,), limiter=limiter)
    async with exchange.async_client() as native, package.AsyncClient(http_client=native, options=settings) as api:
        helper = api.protocols.users.all
        exchange.respond(users("1", cursor="a"), users("2"))
        await adrained(lines, "async hooked items", helper.iterate())
        lines.append(f"  async sessions {len(events.sessions())} named={None not in limiter.sessions}")
        redirects = options.RequestOptions(redirects=options.RedirectOptions(enabled=True))
        exchange.respond(raw_response(302, b"", Location="https://api.example.com/users?moved=1"))
        await adrained(
            lines,
            "async redirect without a send slot",
            helper.iterate(session_options=options.SessionOptions(max_network_sends=1), options=redirects),
        )
        exchange.respond(raw_response(302, b"", Location="https://api.example.com/users?moved=1"))
        await arecord(
            lines,
            "async page redirected without a send slot",
            lambda: helper.page(session_options=options.SessionOptions(max_network_sends=1), options=redirects),
        )
        exchange.respond(json_response(200, {"data": [{"id": "1"}]}))
        await adrained(lines, "async failed page", api.protocols.loose.all.iterate())
        pager = helper.iterate()

        async def concurrent() -> None:
            await arecord(lines, "async item while fetching", lambda pager=pager: anext(pager))
            await arecord(lines, "async close while fetching", pager.aclose)

        limiter.action = concurrent
        exchange.respond(users("1"))
        await adrained(lines, "async items after the concurrent steps", pager)
        events.actions["cancel"] = True
        cancelled = helper.iterate()
        try:
            await anext(cancelled)
        except asyncio.CancelledError:
            if task := asyncio.current_task():
                task.uncancel()
            lines.append("  async cancelled page: CancelledError")
        await adrained(lines, "async after the cancellation", cancelled)


_AUTH_ROWS: Final = (("pages share one token", None, (users("1", cursor="a"), users("2"))),)


def _auth_line(pager: Any, adapter: Adapter) -> str:
    return f"    progress {progress(pager)} token sends={adapter.sends}"


def pagination_auth(package: ModuleType, lines: list[str]) -> None:
    """Authenticate every page with one OAuth token, whose request takes no send slot of the session."""
    harness = Harness(package)
    auth, transports, responses = (
        importlib.import_module(f"{package.__name__}.{name}") for name in ("auth", "transports", "responses")
    )
    exchange = Exchange(lines)
    for label, sends, pages in _AUTH_ROWS:
        tokens = (Response(responses, 200, _issued("token-1")), Response(responses, 200, _issued("token-2")))
        adapter = Adapter(transports, *tokens)
        provider = auth.ClientCredentialsProvider(
            _TOKEN,
            client_id="c",
            client_secret=auth.StaticCredentialProvider(auth.ApiKeyCredential("s")),
            token_transport=adapter,
        )
        settings = harness.client_options(auth=auth.AuthConfig({"oauth": provider}))
        with provider, exchange.client() as native, package.Client(http_client=native, options=settings) as api:
            exchange.respond(*pages)
            pager = api.protocols.secure.users.iterate(
                session_options=harness.options.SessionOptions(max_network_sends=sends)
            )
            drained(lines, label, pager)
            lines.append(_auth_line(pager, adapter))
    run(lambda: _async_auth(harness, auth, transports, responses, lines))


async def _async_auth(
    harness: Harness, auth: ModuleType, transports: ModuleType, responses: ModuleType, lines: list[str]
) -> None:
    exchange = Exchange(lines)
    for label, sends, pages in _AUTH_ROWS:
        tokens = (AsyncResponse(responses, 200, _issued("token-1")), AsyncResponse(responses, 200, _issued("token-2")))
        adapter = AsyncAdapter(transports, *tokens)
        provider = auth.AsyncClientCredentialsProvider(
            _TOKEN,
            client_id="c",
            client_secret=auth.AsyncStaticCredentialProvider(auth.ApiKeyCredential("s")),
            token_transport=adapter,
        )
        settings = harness.client_options(auth=auth.AuthConfig({"oauth": provider}))
        async with (
            exchange.async_client() as native,
            harness.package.AsyncClient(http_client=native, options=settings) as api,
        ):
            exchange.respond(*pages)
            pager = api.protocols.secure.users.iterate(
                session_options=harness.options.SessionOptions(max_network_sends=sends)
            )
            await adrained(lines, f"async {label}", pager)
            lines.append(_auth_line(pager, adapter))
        await provider.aclose()
