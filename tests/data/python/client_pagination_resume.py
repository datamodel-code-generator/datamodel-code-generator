"""Checkpoint pagers and resume them: saved requests and continuations, limits, sessions, and refused states."""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING, Any, Final

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
from tests.data.python.client_runtime import Exchange, describe, json_response, record, run

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_SERVER: Final = "https://api.example.com/v1"
_OTHER: Final = "https://other.example.com"


def _envelope(state: Any) -> dict[str, Any]:
    """Return the exported JSON of a token."""
    return json.loads(state.export())


def _saved(lines: list[str], label: str, state: Any) -> None:
    """Report what an exported token saved: its protocol state, never its helper identity."""
    lines.append(f"  {label} saved {json.dumps(_envelope(state)['state'], sort_keys=True)}")


def _crafted(harness: Harness, state: Any, saved: object) -> Any:
    """Return a token of another's helper with a replaced protocol state."""
    return harness.protocols.ResumeState(helper=_envelope(state)["helper"], state=saved)


def _replaced(state: Any, path: tuple[object, ...], value: object) -> dict[str, Any]:
    """Return the protocol state of an export with one member, reached through keys and indices, replaced."""
    saved = _envelope(state)["state"]
    target = saved
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return saved


def _taken(lines: list[str], label: str, pager: Any, count: int) -> None:
    """Report the first items a pager yields."""
    record(lines, label, lambda: [item_id(next(pager)) for _ in range(count)])


def _resume_state(error: object) -> str:
    """Return whether a failure keeps a resume state."""
    return f"resume_state={type(getattr(error, 'resume_state', None)).__name__}"


def _failure(call: Callable[[], object]) -> BaseException | None:
    try:
        call()
    except Exception as error:  # ruff: ignore[blind-except]
        return error
    return None


class _Checkpointing:
    """A hook that checkpoints a pager once, while one of its pages is being fetched."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.pager: Any = None

    def on_event(self, event: Any) -> None:
        if event.name == "attempt_start" and (pager := self.pager) is not None:
            self.pager = None
            record(self.lines, "checkpoint while fetching", pager.checkpoint)


def pagination_resume(package: ModuleType, lines: list[str]) -> None:
    """Checkpoint and resume pagers through the synchronous and asyncio clients."""
    harness = Harness(package)
    exchange = Exchange(lines)
    with exchange.client() as native, package.Client(http_client=native, options=harness.client_options()) as api:
        _items(harness, api, exchange, lines)
        _pages(harness, api, exchange, lines)
        _limits(harness, api, exchange, lines)
        _continuations(harness, api, exchange, lines)
        _requests(harness, api, exchange, lines)
        _refusals(harness, api, exchange, lines)
        _malformed(harness, api, exchange, lines)
        _validated(harness, api, exchange, lines)
        _credentials(harness, api, lines)
        _starts(harness, api, exchange, lines)
    run(lambda: _async_resume(harness, lines))


def _items(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Resume from the middle of a page: the page is fetched again and the items it delivered are skipped."""
    helper = api.protocols.users.all
    limit = harness.argument("users", "ListUsers", "query", "limit", 3)
    trace = harness.argument("users", "ListUsers", "header", "X-Trace", "t")
    pager = helper.iterate(limit=limit, x_trace=trace)
    _saved(lines, "before any page", pager.checkpoint())
    exchange.respond(user_page("1", "2", "3", next_cursor="a"))
    _taken(lines, "first items", pager, 1)
    state = pager.checkpoint()
    _saved(lines, "mid page", state)
    lines.append(f"  state repr {state!r} progress {progress(pager)}")
    resumed = helper.resume(harness.protocols.import_state(state.export()))
    lines.append(f"  resumed progress {progress(resumed)}")
    record(lines, "resumed pages", resumed.iter_pages)
    exchange.respond(user_page("1", "2", "3", next_cursor="a"), user_page("4", next_cursor="b"), user_page("5"))
    drained(lines, "resumed items", resumed)
    lines.append(f"  resumed progress after {progress(resumed)}")
    exchange.respond(user_page("4", next_cursor="b"), user_page("5"))
    drained(lines, "original items", pager)
    again = helper.resume(state)
    exchange.respond(user_page("1", "2", "3", next_cursor="a"))
    _taken(lines, "resumed again", again, 2)
    _saved(lines, "resumed checkpoint", again.checkpoint())
    exchange.respond(user_page("4"))
    drained(lines, "resumed again rest", again)
    fresh = helper.resume(helper.iterate(limit=limit).checkpoint())
    exchange.respond(user_page("1"))
    drained(lines, "resumed before any page", fresh)
    exchange.respond(user_page("1", "2"))
    last = helper.iterate()
    _taken(lines, "last page first", last, 1)
    exchange.respond(user_page("1", "2"))
    drained(lines, "resumed last page", helper.resume(last.checkpoint()))
    finished = helper.iterate()
    exchange.respond(user_page("1"))
    drained(lines, "finished", finished)
    _saved(lines, "finished", finished.checkpoint())
    drained(lines, "resumed after the last page", helper.resume(finished.checkpoint()))
    closed = helper.iterate()
    closed.close()
    _saved(lines, "closed", closed.checkpoint())


def _pages(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Resume page iteration, continue resumed pages one call at a time, and checkpoint after a failed page."""
    helper = api.protocols.users.all
    pager = helper.iterate()
    exchange.respond(user_page("1", "2", next_cursor="a"))
    first = next(pager.iter_pages())
    lines.append(f"  first page {[item_id(item) for item in first.items]}")
    state = pager.checkpoint()
    _saved(lines, "pages", state)
    exchange.respond(user_page("3", next_cursor="b"), user_page("4"))
    resumed = helper.resume(state)
    pages = resumed.iter_pages()
    second = fetched(lines, "resumed page", lambda: next(pages))
    fetched(lines, "next page of a resumed page", lambda: helper.next_page(second))
    lines.append(f"  resumed page progress {progress(resumed)}")
    exchange.respond(
        user_page("1", next_cursor="a"),
        json_response(503, {"message": "busy"}),
        json_response(503, {"message": "busy"}),
        json_response(503, {"message": "busy"}),
    )
    failed = helper.iterate()
    drained(lines, "second page failing", failed)
    exchange.respond(user_page("2"))
    drained(lines, "resumed after the failed page", helper.resume(failed.checkpoint()))
    hook = _Checkpointing(lines)
    watched = helper.iterate(options=harness.options.RequestOptions(hooks=(hook,)))
    hook.pager = watched
    exchange.respond(user_page("1"))
    drained(lines, "checkpointed while fetching", watched)


def _limits(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Keep a checkpoint on limit and cycle errors; pages and items count on, while sends start afresh."""
    protocols, options = harness.protocols, harness.options
    helper = api.protocols.users.all
    exchange.respond(user_page("1", "2", "3", next_cursor="a"))
    limited = helper.iterate(pagination_options=protocols.PaginationOptions(max_items=2))
    error = _failure(lambda: list(limited))
    lines.append(f"  item limit ! {describe(error)} {_resume_state(error)}")
    state = getattr(error, "resume_state", None)
    _saved(lines, "item limit", state)
    exchange.respond(user_page("1", "2", "3", next_cursor="a"))
    drained(lines, "same item limit", helper.resume(state, pagination_options=protocols.PaginationOptions(max_items=2)))
    exchange.respond(user_page("1", "2", "3", next_cursor="a"), user_page("4"))
    raised = helper.resume(state, pagination_options=protocols.PaginationOptions(max_items=10))
    drained(lines, "raised item limit", raised)
    lines.append(f"    progress {progress(raised)}")
    exchange.respond(user_page("1", next_cursor="a"), user_page("2", next_cursor="b"))
    pages = helper.iterate(pagination_options=protocols.PaginationOptions(max_pages=2))
    error = _failure(lambda: list(pages))
    lines.append(f"  page limit ! {describe(error)} {_resume_state(error)}")
    exchange.respond(user_page("3"))
    drained(
        lines,
        "raised page limit",
        helper.resume(
            getattr(error, "resume_state", None), pagination_options=protocols.PaginationOptions(max_pages=3)
        ),
    )
    exchange.respond(user_page("1", next_cursor="a"))
    sends = helper.iterate(session_options=options.SessionOptions(max_network_sends=1))
    error = _failure(lambda: list(sends))
    lines.append(f"  send limit ! {describe(error)} {_resume_state(error)}")
    exchange.respond(user_page("2", next_cursor="b"))
    budget = helper.resume(
        getattr(error, "resume_state", None), session_options=options.SessionOptions(max_network_sends=1)
    )
    drained(lines, "new session budget", budget)
    lines.append(f"    progress {progress(budget)}")
    exchange.respond(user_page("1", next_cursor="a"), user_page("2", next_cursor="a"))
    cycling = helper.iterate()
    error = _failure(lambda: list(cycling))
    lines.append(f"  cycle ! {describe(error)} {_resume_state(error)}")
    exchange.respond(user_page("3", next_cursor="a"))
    drained(lines, "resumed cycle", helper.resume(getattr(error, "resume_state", None)))


def _continuations(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Resume each continuation kind: snapshot bindings, offsets, next URLs, Link headers, and body cursors."""
    users = api.protocols.users
    exchange.respond(
        headed_page("1", headers=(("X-Snapshot", "s1"),), next_cursor="a"),
        headed_page("2", headers=(("X-Snapshot", "s2"),), next_cursor="b"),
        user_page("3"),
    )
    snapshot = users.snapshot.iterate()
    _taken(lines, "snapshot first", snapshot, 1)
    state = snapshot.checkpoint()
    _saved(lines, "snapshot", state)
    drained(lines, "resumed snapshot", users.snapshot.resume(state))
    exchange.respond(user_page("1", "2", has_more=True), user_page("3", has_more=False))
    offsets = users.offsets.iterate()
    _taken(lines, "offset first", offsets, 2)
    state = offsets.checkpoint()
    _saved(lines, "offsets", state)
    drained(lines, "resumed offsets", users.offsets.resume(state))
    exchange.respond(user_page("1", next=f"{_SERVER}/users?cursor=2"), user_page("2"))
    follow = users.follow.iterate()
    _taken(lines, "followed first", follow, 1)
    state = follow.checkpoint()
    _saved(lines, "followed", state)
    drained(lines, "resumed followed", users.follow.resume(state))
    moved = harness.options.RequestOptions(base_url=f"{_OTHER}/v1")
    record(lines, "followed at another server", lambda: users.follow.resume(state, options=moved))
    exchange.respond(headed_page("1", headers=(("Link", "<?page=2>; rel=next"),)), user_page("2"))
    linked = users.linked.iterate()
    _taken(lines, "linked first", linked, 1)
    state = linked.checkpoint()
    _saved(lines, "linked", state)
    drained(lines, "resumed linked", users.linked.resume(state))
    _echoed(harness, users, exchange, lines)
    searches = api.protocols.searches.all
    body = _body(harness, "searches", "Search", {"query": "a"})
    exchange.respond(user_page("1", next_cursor="c1"), user_page("2", next_cursor="c2"), user_page("3"))
    search = searches.iterate(body=body)
    _taken(lines, "search first", search, 1)
    state = search.checkpoint()
    _saved(lines, "search", state)
    drained(lines, "resumed search", searches.resume(state))
    exchange.respond(user_page("1", next_cursor="a"), user_page("2"))
    archive = api.protocols.archive.all
    path = archive.iterate(cursor=harness.argument("archive", "ListArchive", "path", "cursor", "start"))
    _taken(lines, "archive first", path, 1)
    drained(lines, "resumed archive", archive.resume(path.checkpoint()))


def _echoed(harness: Harness, users: Any, exchange: Exchange, lines: list[str]) -> None:
    """Save and send a same-origin URL without a declared security scheme's query field it echoes.

    A crafted state whose URL carries the field resumes without it too.
    """
    exchange.respond(user_page("1", next=f"{_SERVER}/users?api_key=leak&cursor=2"), user_page("2"))
    follow = users.follow.iterate()
    _taken(lines, "echoed key followed first", follow, 1)
    state = follow.checkpoint()
    _saved(lines, "followed without the echoed key", state)
    drained(lines, "resumed without the echoed key", users.follow.resume(state))
    exchange.respond(headed_page("1", headers=(("Link", "<?api_key=leak&page=2>; rel=next"),)), user_page("2"))
    linked = users.linked.iterate()
    _taken(lines, "echoed key linked first", linked, 1)
    _saved(lines, "linked without the echoed key", linked.checkpoint())
    drained(lines, "resumed link without the echoed key", users.linked.resume(linked.checkpoint()))
    keyed = _replaced(state, ("page", 2, 0), f"{_SERVER}/users?cursor=2&api_key=leak")
    exchange.respond(user_page("2"))
    drained(lines, "crafted URL with a key", users.follow.resume(_crafted(harness, state, keyed)))


def _starts(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Resume offsets and page numbers from a caller's start, after a whole page and in the middle of one.

    The resumed call's own options are refused as the call refuses them, not as the checkpoint's.
    """
    users = api.protocols.users
    for label, helper, name, start in (
        ("offset", users.offsets, "offset", 10),
        ("page number", users.numbered, "page", 3),
    ):
        for taken in (2, 1):
            exchange.respond(user_page("1", "2", has_more=True))
            pager = helper.iterate(**{name: harness.argument("users", "ListUsers", "query", name, start)})
            _taken(lines, f"{label} from {start} taking {taken}", pager, taken)
            again = () if taken == 2 else (user_page("1", "2", has_more=True),)
            exchange.respond(*again, user_page("3", has_more=False))
            drained(lines, f"{label} resumed after {taken}", helper.resume(pager.checkpoint()))
    options = harness.options.RequestOptions
    searches = api.protocols.searches.all
    saved = searches.iterate(body=_body(harness, "searches", "Search", {"query": "a"})).checkpoint()
    framed = options(headers=(("Content-Type", "text/plain"),))
    record(lines, "resumed with another body media type", lambda: searches.resume(saved, options=framed))
    queries = api.protocols.queries.all
    filtered = queries.iterate(filter=harness.argument("queries", "Query", "querystring", "filter", {"term": "a"}))
    patched = options(query=(("debug", "1"),))
    record(lines, "resumed with a query patch", lambda: queries.resume(filtered.checkpoint(), options=patched))
    defaults = harness.client_options(query=(("offset", "40"),))
    with exchange.client() as native, type(api)(http_client=native, options=defaults) as patched_api:
        offsets = patched_api.protocols.users.offsets
        exchange.respond(user_page("1", "2", has_more=True), user_page("3", has_more=False))
        pager = offsets.iterate()
        _taken(lines, "offset from a client default taking 2", pager, 2)
        drained(lines, "offset from a client default resumed", offsets.resume(pager.checkpoint()))


def _body(harness: Harness, resource: str, operation: str, wire: object) -> object:
    """Return the request body of a wire value, as its body codec builds it."""
    types = importlib.import_module(f"{harness.package.__name__}.types.{resource}")
    return getattr(types, f"{operation}RequestCodecs").body().from_wire(wire)


def _requests(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Save encoded arguments and selected media, and refuse cookies."""
    protocols = harness.protocols
    helper = api.protocols.users.all
    limit = harness.argument("users", "ListUsers", "query", "limit", 2)
    _saved(lines, "arguments", helper.iterate(limit=limit).checkpoint())
    session = harness.argument("users", "ListUsers", "cookie", "session", "secret")
    record(lines, "cookie argument", helper.iterate(session=session).checkpoint)
    exchange.respond(user_page("1", next_cursor="a"))
    error = _failure(
        lambda: list(helper.iterate(session=session, pagination_options=protocols.PaginationOptions(max_pages=1)))
    )
    lines.append(f"  cookie limit ! {describe(error)} {_resume_state(error)}")
    searches = api.protocols.searches.all
    record(lines, "body its codec refuses", searches.iterate(body=5).checkpoint)
    types = importlib.import_module(f"{harness.package.__name__}.types.searches")
    selector = types.SearchRequestCodecs.select_request_media(
        declared_media="application/json", concrete_media="application/json; charset=utf-8"
    )
    exchange.respond(user_page("1", next_cursor="c1"), user_page("2"))
    selected = searches.iterate(body=_body(harness, "searches", "Search", {"query": "a"}), media_type=selector)
    _taken(lines, "selected media first", selected, 1)
    state = selected.checkpoint()
    _saved(lines, "selected media", state)
    drained(lines, "resumed selected media", searches.resume(state))
    record(lines, "not a state", lambda: helper.resume(b"state"))
    record(lines, "another helper's state", lambda: api.protocols.users.snapshot.resume(helper.iterate().checkpoint()))


def _refusals(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Check a resumed continuation as one a server just gave, sending nothing for a refused one."""
    protocols = harness.protocols
    helper = api.protocols.users.all
    exchange.respond(user_page("1", "2", next_cursor="abcdef"))
    pager = helper.iterate()
    _taken(lines, "long cursor first", pager, 2)
    state = pager.checkpoint()
    record(
        lines,
        "cursor over the resumed limit",
        lambda: helper.resume(state, pagination_options=protocols.PaginationOptions(max_cursor_bytes=4)),
    )
    exchange.respond(user_page("1", next_cursor="a"))
    archive = api.protocols.archive.all
    path = archive.iterate(cursor=harness.argument("archive", "ListArchive", "path", "cursor", "start"))
    _taken(lines, "archive", path, 1)
    dotted = _crafted(harness, path.checkpoint(), _replaced(path.checkpoint(), ("page", 2, 0), ".."))
    record(lines, "dot segment cursor", lambda: archive.resume(dotted))
    exchange.respond(user_page("1", next=f"{_SERVER}/users?cursor=2"))
    follow = api.protocols.users.follow
    started = follow.iterate()
    _taken(lines, "followed", started, 1)
    state = started.checkpoint()
    other = _crafted(harness, state, _replaced(state, ("page", 2, 0), f"{_OTHER}/v1/users?cursor=2"))
    record(lines, "URL at another origin", lambda: follow.resume(other))


def _malformed(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Refuse a state that does not fit the helper as malformed, before sending."""
    helper = api.protocols.users.all
    exchange.respond(user_page("1", "2", next_cursor="a"))
    pager = helper.iterate()
    _taken(lines, "malformed source", pager, 2)
    state = pager.checkpoint()
    saved = _envelope(state)["state"]
    session = ["secret"]
    for label, broken in (
        ("unknown member", {**saved, "extra": 1}),
        ("arguments not an array", _replaced(state, ("arguments",), {})),
        ("negative page index", _replaced(state, ("page", 0), -1)),
        ("page of three fields", _replaced(state, ("page",), [0, 2, ["a"]])),
        ("negative skip", _replaced(state, ("skip",), -1)),
        ("skip after the last page", {**saved, "page": [0, 2, [], []], "skip": 1}),
        ("cookie value", _replaced(state, ("arguments", 4), session)),
        ("undeclared body", _replaced(state, ("body",), [{}, "application/json", None])),
    ):
        record(lines, label, lambda broken=broken: helper.resume(_crafted(harness, state, broken)))
    searches = api.protocols.searches.all
    exchange.respond(user_page("1", next_cursor="c1"))
    search = searches.iterate(body=_body(harness, "searches", "Search", {"query": "a"}))
    _taken(lines, "search source", search, 1)
    saved = search.checkpoint()
    for label, replaced in (
        ("undeclared body media", _replaced(saved, ("body", 1), "text/plain")),
        ("short body", _replaced(saved, ("body",), [{}])),
    ):
        record(lines, label, lambda replaced=replaced: searches.resume(_crafted(harness, saved, replaced)))


def _validated(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Build saved values as their codecs build a caller's and prepare the next request, sending nothing when refused.

    A literal binding sends the plan's value, and an offset must be where the pages reached from the first request's.
    """
    users = api.protocols.users
    state = users.all.iterate().checkpoint()
    for label, index, value in (
        ("cursor over its maxLength", 0, "123456789"),
        ("limit of another type", 2, "1;drop"),
        ("header with a line break", 3, "a\r\nAuthorization: Bearer x"),
        ("header with a NUL", 3, "a\x00b"),
    ):
        record(
            lines,
            label,
            lambda index=index, value=value: users.all.resume(
                _crafted(harness, state, _replaced(state, ("arguments", index), [value]))
            ),
        )
    archive = api.protocols.archive.all
    start = archive.iterate(cursor=harness.argument("archive", "ListArchive", "path", "cursor", "start")).checkpoint()
    for value in (".", ".."):
        record(
            lines,
            f"path argument {value!r}",
            lambda value=value: archive.resume(_crafted(harness, start, _replaced(start, ("arguments", 0), [value]))),
        )
    searches = api.protocols.searches.all
    saved = searches.iterate(body=_body(harness, "searches", "Search", {"query": "a"})).checkpoint()
    for label, path, value in (
        ("body its schema refuses", ("body", 0), {"query": 5, "extra": "x"}),
        ("media type its selector refuses", ("body", 2), "text/plain"),
        ("unparsable media type", ("body", 2), "%%%"),
        ("media type with a line break", ("body", 2), "application/json\r\nX-Injected: 1"),
    ):
        record(
            lines,
            label,
            lambda path=path, value=value: searches.resume(_crafted(harness, saved, _replaced(saved, path, value))),
        )
    exchange.respond(user_page("1", next_cursor="a"))
    limited = users.limited.iterate()
    _taken(lines, "literal binding first", limited, 1)
    literal = limited.checkpoint()
    exchange.respond(user_page("2"))
    drained(
        lines,
        "literal binding from the plan",
        users.limited.resume(_crafted(harness, literal, _replaced(literal, ("page", 3, 0), 99))),
    )
    exchange.respond(headed_page("1", headers=(("X-Snapshot", "s1"),), next_cursor="a"))
    snapshot = users.snapshot.iterate()
    _taken(lines, "snapshot source", snapshot, 1)
    bound = snapshot.checkpoint()
    record(
        lines,
        "bound header with a line break",
        lambda: users.snapshot.resume(_crafted(harness, bound, _replaced(bound, ("page", 3, 0), "s\r\nX-Injected: 1"))),
    )
    exchange.respond(user_page("1", "2", has_more=True))
    offsets = users.offsets.iterate()
    _taken(lines, "offset source", offsets, 2)
    position = offsets.checkpoint()
    for value in (-5, 7):
        record(
            lines,
            f"offset {value}",
            lambda value=value: users.offsets.resume(
                _crafted(harness, position, _replaced(position, ("page", 2, 0), value))
            ),
        )


def _credentials(harness: Harness, api: Any, lines: list[str]) -> None:
    """Never save an argument at a credential position, a querystring field included, nor resume one."""
    keyed = api.protocols.keyed.all
    for label, location, name, value in (
        ("query key argument", "query", "api_key", "k"),
        ("header key argument", "header", "X-Api-Key", "k"),
        ("proxy credential argument", "header", "Proxy-Authorization", "Basic x"),
    ):
        argument = harness.argument("keyed", "ListKeyedUsers", location, name, value)
        record(lines, label, keyed.iterate(**{name.lower().replace("-", "_"): argument}).checkpoint)
    queries = api.protocols.queries.all
    keyless = harness.argument("queries", "Query", "querystring", "filter", {"term": "a"})
    keyed_filter = harness.argument("queries", "Query", "querystring", "filter", {"term": "a", "api_key": "k"})
    record(lines, "querystring with a key field", queries.iterate(filter=keyed_filter).checkpoint)
    state = queries.iterate(filter=keyless).checkpoint()
    _saved(lines, "querystring", state)
    record(
        lines,
        "saved querystring with a key field",
        lambda: queries.resume(
            _crafted(harness, state, _replaced(state, ("arguments", 0), [{"term": "a", "api_key": "k"}]))
        ),
    )


async def _async_resume(harness: Harness, lines: list[str]) -> None:
    """Checkpoint and resume asyncio pagers, in items and in pages, without awaiting either."""
    exchange = Exchange(lines)
    async with exchange.async_client() as native, harness.package.AsyncClient(http_client=native) as api:
        helper = api.protocols.users.all
        exchange.respond(user_page("1", "2", next_cursor="a"))
        pager = helper.iterate()
        first = await anext(aiter(pager))
        lines.append(f"  async first {item_id(first)}")
        state = pager.checkpoint()
        _saved(lines, "async mid page", state)
        exchange.respond(user_page("1", "2", next_cursor="a"), user_page("3"))
        await adrained(lines, "async resumed items", helper.resume(state))
        exchange.respond(user_page("1", next_cursor="a"), user_page("2"))
        pages = helper.iterate()
        await anext(aiter(pages.iter_pages()))
        await adrained(lines, "async resumed pages", helper.resume(pages.checkpoint()).iter_pages())
        record(lines, "async another helper's state", lambda: api.protocols.users.snapshot.resume(state))
        follow = api.protocols.users.follow
        exchange.respond(user_page("1", next="?api_key=leak&cursor=2"), user_page("2"))
        followed = follow.iterate()
        await anext(aiter(followed))
        state = followed.checkpoint()
        _saved(lines, "async followed without the echoed key", state)
        await adrained(lines, "async resumed without the echoed key", follow.resume(state))
