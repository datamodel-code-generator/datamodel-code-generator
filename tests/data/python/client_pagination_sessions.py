"""Bound pagination sessions: options, send budgets, deadlines, cancellation, HTTP client hooks, and OAuth sends."""

from __future__ import annotations

import asyncio
import importlib
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from tests.data.python.client_oauth import Response, Script
from tests.data.python.client_pagination import Harness, adrained, drained, fetched, progress, users
from tests.data.python.client_runtime import (
    Exchange,
    arecord,
    describe,
    injected,
    json_response,
    record,
    run,
)

if TYPE_CHECKING:
    from types import ModuleType

    import httpx2


def _foreign(package: ModuleType) -> ModuleType:
    """Return the runtime options module, whose helper options include those of kinds the package does not declare."""
    return importlib.import_module(f"{package.__name__}._runtime.protocols.options")


def _issued(value: str) -> bytes:
    return json.dumps({
        "access_token": value,
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": "users.read",
    }).encode()


def _failure(call: Any) -> BaseException | None:
    try:
        call()
    except Exception as error:  # noqa: BLE001
        return error
    return None


class _Sends:
    """A native request hook reporting each request and running a pending action just before it is sent."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.action: Any = None

    def __call__(self, request: httpx2.Request) -> None:
        self.lines.append(f"    request hook {request.method} {request.url}")
        if (action := self.action) is not None:
            self.action = None
            action()


class _AsyncSends(_Sends):
    async def __call__(self, request: httpx2.Request) -> None:  # ty: ignore[invalid-method-override]
        self.lines.append(f"    async request hook {request.method} {request.url}")
        if (action := self.action) is not None:
            self.action = None
            await action()


def _received(lines: list[str]) -> Any:
    """Return a native response hook reporting each response's status."""

    def received(response: httpx2.Response) -> None:
        lines.append(f"    response hook {response.status_code}")

    return received


def _fail(response: httpx2.Response) -> None:
    msg = f"response hook failed on {response.status_code}"
    raise RuntimeError(msg)


@injected
def _interrupting(_request: httpx2.Request) -> httpx2.Response:
    raise KeyboardInterrupt


def pagination_sessions(package: ModuleType, lines: list[str]) -> None:
    """Bound one pager's pages by its session, and fail it where a page or its session fails."""
    harness = Harness(package)
    exchange = Exchange(lines)
    with exchange.client() as native, package.Client(http_client=native, **harness.client_options()) as api:
        _options(harness, api, lines)
        _status_errors(harness, api, exchange, lines)
        _deadlines(harness, api, exchange, lines)
        _failures(harness, api, exchange, lines)
    _concurrency(harness, exchange, lines)
    _defaults(harness, exchange, lines)
    _hooks(harness, exchange, lines)
    _closing(harness, exchange, lines)
    run(lambda: _async_sessions(harness, lines))


def _options(harness: Harness, api: Any, lines: list[str]) -> None:
    """Refuse options of other types and a call's fixed idempotency key before sending anything."""
    options, protocols = harness.options, harness.protocols
    helper = api.protocols.users.all
    for label, settings in (
        ("pagination options of another type", {"pagination_options": options.RequestOptions()}),
        ("request options of another type", {"options": protocols.PaginationOptions()}),
        ("fixed idempotency key", {"options": options.RequestOptions(idempotency_key="fixed-key")}),
    ):
        record(lines, f"iterate with {label}", lambda settings=settings: helper.iterate(**settings))
        record(lines, f"page with {label}", lambda settings=settings: helper.page(**settings))
    record(lines, "next page of options of another type", lambda: helper.next_page(None, options="fast"))


def _status_errors(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Raise a page's final error status after its retries and fail the pager with it."""
    del harness
    helper = api.protocols.users.all
    exchange.respond(
        json_response(500, {"message": "down"}),
        json_response(500, {"message": "down"}),
        json_response(500, {"message": "down"}),
    )
    pager = helper.iterate()
    error = _failure(lambda: next(pager))
    lines.append(f"  status error after retries ! {describe(error)} body={getattr(error, 'body', None)!r}")
    record(lines, "after the status error", lambda: next(pager))


def _deadlines(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """End a session at its total timeout or deadline, bounding each page by it, without sending after it."""
    options, protocols = harness.options, harness.protocols
    helper = api.protocols.users.all
    for label, session in (("session timeout", protocols.PaginationOptions(total_timeout=0)),):
        pager = helper.iterate(pagination_options=session)
        error = _failure(lambda pager=pager: next(pager))
        lines.append(f"  {label} ! {describe(error)} {progress(pager)}")
    pager = helper.iterate(pagination_options=protocols.PaginationOptions(total_timeout=1.0))
    exchange.respond(users("1", cursor="a"))
    record(lines, "first item before the deadline", lambda pager=pager: next(pager).id)
    time.sleep(1.05)
    record(lines, "next page after the deadline", lambda: next(pager))
    exchange.respond(users("1"))
    drained(
        lines, "no session timeout", helper.iterate(pagination_options=protocols.PaginationOptions(total_timeout=None))
    )
    drained(lines, "page timeout", helper.iterate(options=options.RequestOptions(total_timeout=0)))
    exchange.respond(users("1", cursor="a"))
    first = fetched(lines, "page before an expired session", helper.page)
    fetched(
        lines,
        "next page in an expired session",
        lambda: helper.next_page(first, pagination_options=protocols.PaginationOptions(total_timeout=0)),
    )


def _failures(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Fail a pager at an interruption, refusing every later step."""
    del harness
    pager = api.protocols.users.all.iterate()
    exchange.respond(_interrupting)
    try:
        next(pager)
    except KeyboardInterrupt:
        lines.append("  interrupted page: KeyboardInterrupt")
    record(lines, "item after the interruption", lambda: next(pager))
    lines.append(f"    progress {progress(pager)}")


def _concurrency(harness: Harness, exchange: Exchange, lines: list[str]) -> None:
    """Refuse consuming or closing a pager while one of its pages is being sent."""
    sends = _Sends(lines)
    with (
        exchange.client(event_hooks={"request": [sends]}) as native,
        harness.package.Client(http_client=native, **harness.client_options()) as api,
    ):
        pager = api.protocols.users.all.iterate()

        def concurrent() -> None:
            record(lines, "item while fetching", lambda: next(pager))
            record(lines, "close while fetching", pager.close)

        sends.action = concurrent
        exchange.respond(users("1"))
        drained(lines, "items after the concurrent steps", pager)


def _defaults(harness: Harness, exchange: Exchange, lines: list[str]) -> None:
    """Take each limit from the call, then the client's helper defaults, then the kind's, refusing unknown helpers."""
    package, options, protocols = harness.package, harness.options, harness.protocols
    values = json.loads(
        (Path(__file__).parents[1] / "generation_platform/client/defaults.json").read_text(encoding="utf-8")
    )["pagination_limits"]
    for label, pagination in (
        ("default traversal", None),
        ("explicit page budget", protocols.PaginationOptions(max_pages=values["explicit_pages"])),
    ):
        quiet = Exchange([])
        with quiet.client() as native, package.Client(http_client=native, **harness.client_options()) as api:
            pager = api.protocols.users.all.iterate(pagination_options=pagination)
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
        with quiet.client() as native, package.Client(http_client=native, **harness.client_options()) as api:
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
        ("explicit session clock", protocols.PaginationOptions(total_timeout=values["total_timeout"])),
    ):
        ticks = [0.0]
        clock = options.Clock(monotonic=lambda ticks=ticks: ticks[0], time=lambda: 0.0)
        quiet = Exchange([])
        with (
            quiet.client() as native,
            package.Client(http_client=native, **harness.client_options(clock=clock)) as api,
        ):
            quiet.respond(users("1", cursor="a"))
            pager = api.protocols.users.all.iterate(pagination_options=session)
            record(lines, f"{label} first", lambda pager=pager: next(pager).id)
            ticks[0] += values["clock_step"]
            if session is None:
                quiet.respond(users("2"))
            record(lines, f"{label} after former timeout", lambda pager=pager: next(pager).id)
            lines.append(f"    progress {progress(pager)}")
            pager.close()
    defaults = protocols.PaginationOptions(max_items=1, total_timeout=60)
    for label, entries in (
        ("unknown helper", {"users.everyone": defaults}),
        ("options of another kind", {"users.all": _foreign(package).PollOptions()}),
    ):
        record(
            lines,
            f"client with defaults of an {label}",
            lambda entries=entries: package.Client(helper_defaults=entries),
        )
        record(
            lines,
            f"async client with defaults of an {label}",
            lambda entries=entries: package.AsyncClient(helper_defaults=entries),
        )
    for label, entries in (("no defaults", None), ("empty defaults", {})):
        record(
            lines,
            f"client with {label}",
            lambda entries=entries: package.Client(helper_defaults=entries).close(),
        )
    client_options = harness.client_options(
        helper_defaults={"users.all": defaults, "users.search": protocols.PaginationOptions()}
    )
    with exchange.client() as native, package.Client(http_client=native, **client_options) as api:
        helper = api.protocols.users.all
        exchange.respond(users("1", "2"))
        drained(lines, "default item limit", helper.iterate())
        exchange.respond(users("1", "2"))
        drained(
            lines, "empty call options keep defaults", helper.iterate(pagination_options=protocols.PaginationOptions())
        )
        exchange.respond(users("1", "2"))
        drained(lines, "call item limit", helper.iterate(pagination_options=protocols.PaginationOptions(max_items=3)))
        exchange.respond(users("1", "2"))
        drained(lines, "helper without defaults", api.protocols.users.by_header.iterate())
        exchange.respond(users("1", "2"))
        drained(lines, "view keeps defaults", api.with_options().protocols.users.all.iterate())


def _hooks(harness: Harness, exchange: Exchange, lines: list[str]) -> None:
    """Run the HTTP client's own hooks for every page of a pager, failing the pager where a response hook fails."""
    package, settings = harness.package, harness.client_options()
    hooks = {"request": [_Sends(lines)], "response": [_received(lines)]}
    with exchange.client(event_hooks=hooks) as native, package.Client(http_client=native, **settings) as api:
        exchange.respond(users("1", cursor="a"), users("2"))
        drained(lines, "hooked items", api.protocols.users.all.iterate())
        exchange.respond(json_response(200, {"data": [{"id": "1"}]}))
        error = _failure(lambda: next(api.protocols.loose.all.iterate()))
        lines.append(f"  failed page ! {describe(error)}")
    with (
        exchange.client(event_hooks={"response": [_fail]}) as native,
        package.Client(http_client=native, **settings) as api,
    ):
        exchange.respond(users("1", cursor="a"))
        pager = api.protocols.users.all.iterate()
        error = _failure(lambda: next(pager))
        lines.append(f"  response hook failure ! {describe(error)}")
        record(lines, "after the hook failure", lambda: next(pager))


def _closing(harness: Harness, exchange: Exchange, lines: list[str]) -> None:
    """Refuse the next page of a pager whose client closed after its first page."""
    with exchange.client() as native:
        api = harness.package.Client(http_client=native, **harness.client_options())
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
    for label, pagination in (
        ("default traversal", None),
        ("explicit page budget", protocols.PaginationOptions(max_pages=values["explicit_pages"])),
    ):
        quiet = Exchange([])
        async with (
            quiet.async_client() as native,
            package.AsyncClient(http_client=native, **harness.client_options()) as api,
        ):
            pager = api.protocols.users.all.iterate(pagination_options=pagination)
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
            package.AsyncClient(http_client=native, **harness.client_options()) as api,
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
        ("explicit session clock", protocols.PaginationOptions(total_timeout=values["total_timeout"])),
    ):
        ticks = [0.0]
        clock = options.Clock(monotonic=lambda ticks=ticks: ticks[0], time=lambda: 0.0)
        quiet = Exchange([])
        async with (
            quiet.async_client() as native,
            package.AsyncClient(http_client=native, **harness.client_options(clock=clock)) as api,
        ):
            quiet.respond(users("1", cursor="a"))
            pager = api.protocols.users.all.iterate(pagination_options=session)
            await arecord(lines, f"async {label} first", lambda pager=pager: anext(pager))
            ticks[0] += values["clock_step"]
            if session is None:
                quiet.respond(users("2"))
            await arecord(lines, f"async {label} after former timeout", lambda pager=pager: anext(pager))
            lines.append(f"    progress {progress(pager)}")
            await pager.aclose()
    exchange = Exchange(lines)
    sends = _AsyncSends(lines)
    async with (
        exchange.async_client(event_hooks={"request": [sends]}) as native,
        package.AsyncClient(http_client=native, **harness.client_options()) as api,
    ):
        helper = api.protocols.users.all
        exchange.respond(users("1", cursor="a"), users("2"))
        await adrained(lines, "async hooked items", helper.iterate())
        exchange.respond(json_response(200, {"data": [{"id": "1"}]}))
        await adrained(lines, "async failed page", api.protocols.loose.all.iterate())
        pager = helper.iterate()

        async def concurrent() -> None:
            await arecord(lines, "async item while fetching", lambda pager=pager: anext(pager))
            await arecord(lines, "async close while fetching", pager.aclose)

        sends.action = concurrent
        exchange.respond(users("1"))
        await adrained(lines, "async items after the concurrent steps", pager)

        async def cancel() -> None:
            if task := asyncio.current_task():
                task.cancel()
                await asyncio.sleep(0)

        sends.action = cancel
        cancelled = helper.iterate()
        try:
            await anext(cancelled)
        except asyncio.CancelledError:
            if task := asyncio.current_task():
                task.uncancel()
            lines.append("  async cancelled page: CancelledError")
        await adrained(lines, "async after the cancellation", cancelled)


_AUTH_ROWS: Final = (("pages share one token", (users("1", cursor="a"), users("2"))),)


def _auth_line(pager: Any, script: Script) -> str:
    return f"    progress {progress(pager)} token sends={script.sends}"


def pagination_auth(package: ModuleType, lines: list[str]) -> None:
    """Authenticate every page with one OAuth token, whose request takes no send slot of the session."""
    harness = Harness(package)
    auth = importlib.import_module(f"{package.__name__}.auth")
    exchange = Exchange(lines)
    for label, pages in _AUTH_ROWS:
        script = Script(Response(200, _issued("token-1")), Response(200, _issued("token-2")))
        provider = auth.OauthClientCredentials(client_id="c", client_secret="s", http_client=script.client())
        settings = harness.client_options()
        with exchange.client() as native, package.Client(http_client=native, **settings, oauth=provider) as api:
            exchange.respond(*pages)
            pager = api.protocols.secure.users.iterate(pagination_options=harness.protocols.PaginationOptions())
            drained(lines, label, pager)
            lines.append(_auth_line(pager, script))
    run(lambda: _async_auth(harness, auth, lines))


async def _async_auth(harness: Harness, auth: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    for label, pages in _AUTH_ROWS:
        script = Script(Response(200, _issued("token-1")), Response(200, _issued("token-2")))
        provider = auth.OauthClientCredentials(client_id="c", client_secret="s", http_client=script.async_client())
        settings = harness.client_options()
        async with (
            exchange.async_client() as native,
            harness.package.AsyncClient(http_client=native, **settings, oauth=provider) as api,
        ):
            exchange.respond(*pages)
            pager = api.protocols.secure.users.iterate(pagination_options=harness.protocols.PaginationOptions())
            await adrained(lines, f"async {label}", pager)
            lines.append(_auth_line(pager, script))
