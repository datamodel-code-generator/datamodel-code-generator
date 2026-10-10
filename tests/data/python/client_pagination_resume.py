"""Resume generated pagers from server cursors, counts and URLs with caller-supplied arguments."""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING, Any

from tests.data.python.client_pagination import (
    Harness,
    adrained,
    drained,
    fetched,
    headed_page,
    item_id,
    progress,
    user_page,
)
from tests.data.python.client_runtime import Exchange, json_response, record, request_body, run

if TYPE_CHECKING:
    from types import ModuleType

_SERVER = "https://api.example.com/v1"


def pagination_resume(package: ModuleType, lines: list[str]) -> None:
    """Restart pages using server values in a fresh session without capturing operation arguments."""
    harness = Harness(package)
    exchange = Exchange(lines)
    with exchange.client() as native, package.Client(http_client=native, **harness.client_options()) as api:
        _cursors(harness, api, exchange, lines)
        _counts(api, exchange, lines)
        _urls(harness, api, exchange, lines)
        _bodies(harness, api, exchange, lines)
        _limits(harness, api, exchange, lines)
    run(lambda: _async_resume(harness, lines))


def _cursors(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    helper = api.protocols.users.all
    arguments = {"limit": 3, "X_Trace": "trace-secret", "session": "cookie-secret"}
    pager = helper.iterate(**arguments)
    record(lines, "initial checkpoint", pager.checkpoint)
    exchange.respond(user_page("1", "2", next_cursor="a"))
    fetched(lines, "first page", lambda: next(pager.iter_pages()))
    state = pager.checkpoint()
    lines.append(f"  server value={state!r} encoded={json.dumps(state)} progress={progress(pager)}")
    resumed = helper.resume(json.loads(json.dumps(state)), limit=2, X_Trace="new-trace", session="new-cookie")
    lines.append(f"  resumed progress={progress(resumed)}")
    exchange.respond(user_page("3"))
    drained(lines, "caller arguments supplied again", resumed)
    record(lines, "exhausted checkpoint", resumed.checkpoint)

    partial = helper.iterate(cursor="start")
    record(lines, "initial caller cursor", partial.checkpoint)
    exchange.respond(user_page("1", "2", next_cursor="next"))
    record(lines, "partial first item", lambda: item_id(next(partial)))
    record(lines, "partial boundary", partial.checkpoint)
    exchange.respond(user_page("1", "2"))
    drained(lines, "partial page repeats from boundary", helper.resume(partial.checkpoint(), cursor="start"))
    exchange.respond(user_page("1", next_cursor="a"), user_page("2", "3", next_cursor="b"))
    partial = helper.iterate()
    record(lines, "previous page item", lambda: item_id(next(partial)))
    record(lines, "partial next page item", lambda: item_id(next(partial)))
    record(lines, "previous server boundary", partial.checkpoint)
    exchange.respond(user_page("2", "3"))
    drained(lines, "restart buffered page", helper.resume(partial.checkpoint()))
    partial.close()
    record(lines, "closed checkpoint", partial.checkpoint)

    active = helper.iterate()

    def receiving(request: Any) -> Any:
        record(lines, "checkpoint during fetch", active.checkpoint)
        return user_page("5")(request)

    exchange.respond(receiving)
    drained(lines, "fetching checkpoint preserves traversal", active)

    exchange.respond(user_page("4", next_cursor="a"))
    cycling = helper.resume("a")
    drained(lines, "resumed cursor cycle", cycling)
    record(lines, "cycle checkpoint", cycling.checkpoint)
    none = harness.protocols.PaginationOptions(max_items=0)
    drained(lines, "resumed zero item limit sends nothing", helper.resume("a", pagination_options=none))
    folder = harness.argument("listArchive", "path", "cursor", "a1")
    for segment in ("a2", ".."):
        exchange.respond(user_page("6"))
        drained(lines, f"path continuation {segment!r}", api.protocols.archive.all.resume(segment, cursor=folder))
        exchange.responders.clear()
    record(lines, "non-JSON continuation", lambda: helper.resume(object()))
    record(lines, "nonfinite continuation", lambda: helper.resume(float("nan")))
    record(lines, "removed cursor cap", lambda: harness.protocols.PaginationOptions(max_cursor_bytes=1))
    record(lines, "removed page cap", lambda: harness.protocols.PaginationOptions(max_page_bytes=1))


def _counts(api: Any, exchange: Exchange, lines: list[str]) -> None:
    for name, members, expected in (
        ("offsets", {"has_more": True}, 2),
        ("numbered", {"has_more": True}, 2),
    ):
        helper = getattr(api.protocols.users, name)
        pager = helper.iterate()
        record(lines, f"{name} initial checkpoint", pager.checkpoint)
        exchange.respond(user_page("1", "2", **members))
        fetched(lines, f"{name} first page", lambda pager=pager: next(pager.iter_pages()))
        record(lines, f"{name} server count", pager.checkpoint)
        exchange.respond(user_page("3", has_more=False))
        drained(lines, f"{name} resumed", helper.resume(expected))
        for label, value in (("negative", -1), ("boolean", True), ("text", "2")):
            record(lines, f"{name} {label}", lambda value=value, helper=helper: helper.resume(value))
    exchange.respond(user_page("1", "2", has_more=True))
    partial = api.protocols.users.offsets.iterate(offset=7)
    record(lines, "initial offset", partial.checkpoint)
    record(lines, "partial offset item", lambda: item_id(next(partial)))
    record(lines, "partial offset boundary", partial.checkpoint)
    helper = api.protocols.users.pages
    exchange.respond(user_page("1", "2", total=3), user_page("3", total=3))
    drained(lines, "total ends a first run at its last page", helper.iterate())
    for label, past in (("an empty page", user_page(total=3)), ("an error", json_response(404, {"detail": "no page"}))):
        exchange.respond(user_page("3", total=3), past)
        drained(lines, f"total resumed counts from its page to {label}", helper.resume(2))


def _urls(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    for name, responder in (
        ("follow", user_page("1", next=f"{_SERVER}/users?cursor=a&api_key=server-secret")),
        ("linked", headed_page("1", headers=(("Link", "<?page=2&api_key=server-secret>; rel=next"),))),
    ):
        helper = getattr(api.protocols.users, name)
        pager = helper.iterate()
        exchange.respond(responder)
        fetched(lines, f"{name} first page", lambda pager=pager: next(pager.iter_pages()))
        state = pager.checkpoint()
        lines.append(f"  {name} server URL={state!r}")
        exchange.respond(user_page("2"))
        drained(lines, f"{name} resumed", helper.resume(state))
        for label, value in (
            ("other origin", "https://other.example.com/users"),
            ("userinfo", "https://user:secret@api.example.com/users"),
            ("relative URL", "/users?cursor=a"),
            ("fragment", f"{_SERVER}/users#secret"),
            ("bad percent", f"{_SERVER}/users?cursor=%zz"),
            ("wrong type", 2),
        ):
            record(lines, f"{name} {label}", lambda value=value, helper=helper: helper.resume(value))
    supplied = api.protocols.users.follow.resume(f"{_SERVER}/users?cursor=a&api_key=server-secret")
    state = supplied.checkpoint()
    lines.append(f"  supplied URL checkpoint={state!r} key kept={'api_key' in state}")
    exchange.respond(user_page("3", next=f"{_SERVER}/users?cursor=a&api_key=server-secret"))
    drained(lines, "supplied URL removes echoed key", supplied)
    state = api.protocols.users.follow.resume(f"{_SERVER}/users?cursor=a&sig=stale").checkpoint()
    lines.append(f"  supplied URL checkpoint={state!r} sig kept={'sig' in state}")


def _bodies(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    helper = api.protocols.searches.all
    body = request_body(harness.package, "search", None, {"query": "original", "cursor": "start"})
    pager = helper.iterate(body=body)
    record(lines, "initial body cursor", pager.checkpoint)
    exchange.respond(user_page("1", next_cursor="a"))
    fetched(lines, "body first page", lambda: next(pager.iter_pages()))
    state = pager.checkpoint()
    record(lines, "body checkpoint is cursor only", pager.checkpoint)
    replacement = request_body(harness.package, "search", None, {"query": "replacement"})
    exchange.respond(user_page("2"))
    drained(lines, "caller supplies replacement body", helper.resume(state, body=replacement))
    record(lines, "required body supplied again", lambda: helper.resume(state))

    exchange.respond(user_page("1", next_cursor="a"))
    pager = api.protocols.users.snapshot.iterate(X_Trace="first")
    exchange.responders[0] = headed_page("1", headers=(("X-Snapshot", "s1"),), next_cursor="a")
    fetched(lines, "bound first page", lambda: next(pager.iter_pages()))
    exchange.respond(headed_page("2", headers=(("X-Snapshot", "s2"),), next_cursor="b"), user_page("3"))
    drained(lines, "caller resupplies binding", api.protocols.users.snapshot.resume(pager.checkpoint(), X_Trace="s1"))
    exchange.respond(user_page("4"))
    drained(lines, "literal binding on resumed first page", api.protocols.users.limited.resume("a", limit=9))
    since = harness.argument("listUsers", "query", "since", "2026-01-02")
    exchange.respond(user_page("5", next_cursor="b", since="2026-01-03"), user_page("6"))
    drained(
        lines, "typed binding target keeps the caller's argument", api.protocols.users.since.resume("a", since=since)
    )


def _limits(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    helper = api.protocols.users.all
    pager = helper.iterate(pagination_options=harness.protocols.PaginationOptions(max_pages=1))
    exchange.respond(user_page("1", next_cursor="a"))
    drained(lines, "page limit", pager)
    record(lines, "limited checkpoint", pager.checkpoint)
    exchange.respond(user_page("2"))
    drained(
        lines,
        "resume starts fresh page limit",
        helper.resume(pager.checkpoint(), pagination_options=harness.protocols.PaginationOptions(max_pages=1)),
    )
    pager = helper.iterate(pagination_options=harness.protocols.PaginationOptions(max_items=1))
    exchange.respond(user_page("1", "2", next_cursor="a"))
    drained(lines, "item limit at partial page", pager)
    record(lines, "limited partial boundary", pager.checkpoint)
    exchange.respond(user_page("1", "2"))
    drained(
        lines,
        "caller chooses larger item limit",
        helper.resume(pager.checkpoint(), pagination_options=harness.protocols.PaginationOptions(max_items=2)),
    )
    pager = helper.iterate(pagination_options=harness.protocols.PaginationOptions(max_items=3))
    exchange.respond(user_page("1", "2", next_cursor="a"), user_page("3", "4", next_cursor="b"))
    drained(lines, "item limit in a later page", pager)
    record(lines, "limited boundary before its page", pager.checkpoint)


async def _async_resume(harness: Harness, lines: list[str]) -> None:
    exchange = Exchange(lines)
    package = harness.package
    async with exchange.async_client() as native, package.AsyncClient(http_client=native) as api:
        helper = api.protocols.users.all
        pager = helper.iterate(limit=2, session="cookie-secret")
        exchange.respond(user_page("1", "2", next_cursor="a"))
        record(lines, "async initial checkpoint", pager.checkpoint)
        record(lines, "async initial resumed", lambda: dict(helper.resume(None, limit=2).progress))
        items = aiter(pager)
        lines.append(f"  async first item={item_id(await anext(items))}")
        record(lines, "async partial checkpoint", pager.checkpoint)
        await anext(items)
        state = pager.checkpoint()
        record(lines, "async complete page cursor", pager.checkpoint)
        exchange.respond(user_page("3"))
        await adrained(lines, "async supplied arguments", helper.resume(state, limit=1, session="new-cookie"))
        await pager.aclose()
        record(lines, "async closed checkpoint", pager.checkpoint)
