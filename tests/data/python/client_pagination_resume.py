"""Checkpoint pagers and resume them: saved requests and pages, limits, sessions, security, and refused states."""

from __future__ import annotations

import base64
import importlib
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.data.python.client_pagination import Harness, adrained, drained, fetched, item_id, progress
from tests.data.python.client_runtime import Exchange, describe, json_response, record, run

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_SERVER: Final = "https://api.example.com/v1"
_OTHER: Final = "https://other.example.com"
_PAST: Final = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _page(*ids: str, **members: object) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a responder of one page of users with the given members."""
    return json_response(200, {"data": [{"id": value} for value in ids], **members})


def _headed(*ids: str, headers: tuple[tuple[str, str], ...], **members: object) -> Callable[[httpx2.Request], Any]:
    """Return a responder of one page of users with response headers."""
    payload = {"data": [{"id": value} for value in ids], **members}
    return lambda _: httpx2.Response(200, headers=list(headers), json=payload)


def _envelope(state: Any) -> dict[str, Any]:
    """Return the exported JSON envelope of a state."""
    return json.loads(state.export())


def _saved(lines: list[str], label: str, state: Any) -> None:
    """Report what an exported state saved: its protocol state and its payload, never its fingerprints."""
    envelope = _envelope(state)
    payload = base64.b64decode(envelope["payload"])
    lines.append(f"  {label} saved {json.dumps(envelope['state'], sort_keys=True)} payload={payload!r}")


def _crafted(harness: Harness, state: Any, saved: object = None, **fields: Any) -> Any:
    """Return a state with another's fingerprints and a replaced protocol state, payload, or expiry."""
    envelope = _envelope(state)
    return harness.protocols.ResumeState(
        helper_fingerprint=envelope["helper_fingerprint"],
        security_fingerprint=envelope["security_fingerprint"],
        state=envelope["state"] if saved is None else saved,
        payload=fields.get("payload", base64.b64decode(envelope["payload"])),
        expires_at=fields.get("expires_at"),
    )


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
    _security(harness, exchange, lines)
    run(lambda: _async_resume(harness, lines))


def _items(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Resume from the middle of a page: its saved body is decoded again and its items left come first."""
    helper = api.protocols.users.all
    limit = harness.argument("users", "ListUsers", "query", "limit", 3)
    trace = harness.argument("users", "ListUsers", "header", "X-Trace", "t")
    pager = helper.iterate(limit=limit, x_trace=trace)
    _saved(lines, "before any page", pager.checkpoint())
    exchange.respond(_page("1", "2", "3", next_cursor="a"), _page("4", next_cursor="b"), _page("5"))
    _taken(lines, "first items", pager, 1)
    state = pager.checkpoint()
    _saved(lines, "mid page", state)
    lines.append(f"  state repr {state!r} progress {progress(pager)}")
    resumed = helper.resume(harness.protocols.import_state(state.export()))
    lines.append(f"  resumed progress {progress(resumed)}")
    record(lines, "resumed pages", resumed.iter_pages)
    drained(lines, "resumed items", resumed)
    lines.append(f"  resumed progress after {progress(resumed)}")
    exchange.respond(_page("4", next_cursor="b"), _page("5"))
    drained(lines, "original items", pager)
    again = helper.resume(state)
    _taken(lines, "resumed again", again, 2)
    _saved(lines, "resumed checkpoint", again.checkpoint())
    exchange.respond(_page("4"))
    drained(lines, "resumed again rest", again)
    fresh = helper.resume(helper.iterate(limit=limit).checkpoint())
    exchange.respond(_page("1"))
    drained(lines, "resumed before any page", fresh)
    exchange.respond(_page("1", "2"))
    last = helper.iterate()
    _taken(lines, "last page first", last, 1)
    drained(lines, "resumed last page", helper.resume(last.checkpoint()))
    closed = helper.iterate()
    closed.close()
    _saved(lines, "closed", closed.checkpoint())


def _pages(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Resume page iteration, continue resumed pages one call at a time, and checkpoint after a failed page."""
    helper = api.protocols.users.all
    pager = helper.iterate()
    exchange.respond(_page("1", "2", next_cursor="a"))
    first = next(pager.iter_pages())
    lines.append(f"  first page {[item_id(item) for item in first.items]}")
    state = pager.checkpoint()
    _saved(lines, "pages", state)
    exchange.respond(_page("3", next_cursor="b"), _page("4"))
    resumed = helper.resume(state)
    pages = resumed.iter_pages()
    second = fetched(lines, "resumed page", lambda: next(pages))
    fetched(lines, "next page of a resumed page", lambda: helper.next_page(second))
    lines.append(f"  resumed page progress {progress(resumed)}")
    exchange.respond(
        _page("1", next_cursor="a"),
        json_response(503, {"message": "busy"}),
        json_response(503, {"message": "busy"}),
        json_response(503, {"message": "busy"}),
    )
    failed = helper.iterate()
    drained(lines, "second page failing", failed)
    exchange.respond(_page("2"))
    drained(lines, "resumed after the failed page", helper.resume(failed.checkpoint()))
    hook = _Checkpointing(lines)
    watched = helper.iterate(options=harness.options.RequestOptions(hooks=(hook,)))
    hook.pager = watched
    exchange.respond(_page("1"))
    drained(lines, "checkpointed while fetching", watched)


def _limits(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Keep a checkpoint on limit and cycle errors; pages and items count on, while sends start afresh."""
    protocols, options = harness.protocols, harness.options
    helper = api.protocols.users.all
    exchange.respond(_page("1", "2", "3", next_cursor="a"))
    limited = helper.iterate(pagination_options=protocols.PaginationOptions(max_items=2))
    error = _failure(lambda: list(limited))
    lines.append(f"  item limit ! {describe(error)} {_resume_state(error)}")
    state = getattr(error, "resume_state", None)
    _saved(lines, "item limit", state)
    drained(lines, "same item limit", helper.resume(state, pagination_options=protocols.PaginationOptions(max_items=2)))
    exchange.respond(_page("4"))
    raised = helper.resume(state, pagination_options=protocols.PaginationOptions(max_items=10))
    drained(lines, "raised item limit", raised)
    lines.append(f"    progress {progress(raised)}")
    exchange.respond(_page("1", next_cursor="a"), _page("2", next_cursor="b"))
    pages = helper.iterate(pagination_options=protocols.PaginationOptions(max_pages=2))
    error = _failure(lambda: list(pages))
    lines.append(f"  page limit ! {describe(error)} {_resume_state(error)}")
    exchange.respond(_page("3"))
    drained(
        lines,
        "raised page limit",
        helper.resume(
            getattr(error, "resume_state", None), pagination_options=protocols.PaginationOptions(max_pages=3)
        ),
    )
    exchange.respond(_page("1", next_cursor="a"))
    sends = helper.iterate(session_options=options.SessionOptions(max_network_sends=1))
    error = _failure(lambda: list(sends))
    lines.append(f"  send limit ! {describe(error)} {_resume_state(error)}")
    exchange.respond(_page("2", next_cursor="b"))
    budget = helper.resume(
        getattr(error, "resume_state", None), session_options=options.SessionOptions(max_network_sends=1)
    )
    drained(lines, "new session budget", budget)
    lines.append(f"    progress {progress(budget)}")
    exchange.respond(_page("1", next_cursor="a"), _page("2", next_cursor="a"))
    cycling = helper.iterate()
    error = _failure(lambda: list(cycling))
    lines.append(f"  cycle ! {describe(error)} {_resume_state(error)}")
    drained(lines, "resumed cycle", helper.resume(getattr(error, "resume_state", None)))


def _continuations(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Resume each continuation kind: snapshot bindings, offsets, next URLs, Link headers, and body cursors."""
    users = api.protocols.users
    exchange.respond(
        _headed("1", headers=(("X-Snapshot", "s1"),), next_cursor="a"),
        _headed("2", headers=(("X-Snapshot", "s2"),), next_cursor="b"),
        _page("3"),
    )
    snapshot = users.snapshot.iterate()
    _taken(lines, "snapshot first", snapshot, 1)
    state = snapshot.checkpoint()
    _saved(lines, "snapshot", state)
    drained(lines, "resumed snapshot", users.snapshot.resume(state))
    exchange.respond(_page("1", "2", has_more=True), _page("3", has_more=False))
    offsets = users.offsets.iterate()
    _taken(lines, "offset first", offsets, 2)
    state = offsets.checkpoint()
    _saved(lines, "offsets", state)
    drained(lines, "resumed offsets", users.offsets.resume(state))
    exchange.respond(_page("1", next=f"{_SERVER}/users?cursor=2"), _page("2"))
    follow = users.follow.iterate()
    _taken(lines, "followed first", follow, 1)
    state = follow.checkpoint()
    _saved(lines, "followed", state)
    drained(lines, "resumed followed", users.follow.resume(state))
    moved = harness.options.RequestOptions(base_url=f"{_OTHER}/v1")
    record(lines, "followed at another server", lambda: users.follow.resume(state, options=moved))
    exchange.respond(_headed("1", headers=(("Link", "<?page=2>; rel=next"),)), _page("2"))
    linked = users.linked.iterate()
    _taken(lines, "linked first", linked, 1)
    state = linked.checkpoint()
    _saved(lines, "linked", state)
    drained(lines, "resumed linked", users.linked.resume(state))
    searches = api.protocols.searches.all
    body = _body(harness, "searches", "Search", {"query": "a"})
    exchange.respond(_page("1", next_cursor="c1"), _page("2", next_cursor="c2"), _page("3"))
    search = searches.iterate(body=body)
    _taken(lines, "search first", search, 1)
    state = search.checkpoint()
    _saved(lines, "search", state)
    drained(lines, "resumed search", searches.resume(state))
    exchange.respond(_page("1", next_cursor="a"), _page("2"))
    archive = api.protocols.archive.all
    path = archive.iterate(cursor=harness.argument("archive", "ListArchive", "path", "cursor", "start"))
    _taken(lines, "archive first", path, 1)
    drained(lines, "resumed archive", archive.resume(path.checkpoint()))


def _body(harness: Harness, resource: str, operation: str, wire: object) -> object:
    """Return the request body of a wire value, as its body codec builds it."""
    types = importlib.import_module(f"{harness.package.__name__}.types.{resource}")
    return getattr(types, f"{operation}RequestCodecs").body().from_wire(wire)


def _requests(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Save validated arguments and selected media, and refuse cookies and arguments their schemas refuse."""
    options, protocols = harness.options, harness.protocols
    helper = api.protocols.users.all
    checked = options.RequestOptions(validation=options.ValidationOptions(arguments="pydantic"))
    limit = harness.argument("users", "ListUsers", "query", "limit", 2)
    _saved(lines, "pydantic arguments", helper.iterate(limit=limit, options=checked).checkpoint())
    session = harness.argument("users", "ListUsers", "cookie", "session", "secret")
    record(lines, "cookie argument", helper.iterate(session=session).checkpoint)
    exchange.respond(_page("1", next_cursor="a"))
    error = _failure(
        lambda: list(helper.iterate(session=session, pagination_options=protocols.PaginationOptions(max_pages=1)))
    )
    lines.append(f"  cookie limit ! {describe(error)} {_resume_state(error)}")
    schema = options.RequestOptions(validation=options.ValidationOptions(request="schema"))
    record(lines, "argument its schema refuses", helper.iterate(cursor="too-long-cursor", options=schema).checkpoint)
    searches = api.protocols.searches.all
    record(lines, "body its schema refuses", searches.iterate(body={"query": 1}, options=schema).checkpoint)
    types = importlib.import_module(f"{harness.package.__name__}.types.searches")
    selector = types.SearchRequestCodecs.select_request_media(
        declared_media="application/json", concrete_media="application/json; charset=utf-8"
    )
    exchange.respond(_page("1", next_cursor="c1"), _page("2"))
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
    exchange.respond(_page("1", "2", next_cursor="abcdef"))
    pager = helper.iterate()
    _taken(lines, "long cursor first", pager, 1)
    state = pager.checkpoint()
    record(
        lines,
        "cursor over the resumed limit",
        lambda: helper.resume(state, pagination_options=protocols.PaginationOptions(max_cursor_bytes=4)),
    )
    exchange.respond(_page("1", next_cursor="a"))
    archive = api.protocols.archive.all
    path = archive.iterate(cursor=harness.argument("archive", "ListArchive", "path", "cursor", "start"))
    _taken(lines, "archive", path, 1)
    dotted = _crafted(harness, path.checkpoint(), _replaced(path.checkpoint(), ("page", 2, 0), ".."))
    record(lines, "dot segment cursor", lambda: archive.resume(dotted))
    exchange.respond(_page("1", next=f"{_SERVER}/users?cursor=2"))
    follow = api.protocols.users.follow
    started = follow.iterate()
    _taken(lines, "followed", started, 1)
    state = started.checkpoint()
    other = _crafted(harness, state, _replaced(state, ("page", 2, 0), f"{_OTHER}/v1/users?cursor=2"))
    record(lines, "URL at another origin", lambda: follow.resume(other))
    record(lines, "expired state", lambda: follow.resume(_crafted(harness, state, expires_at=_PAST)))


def _malformed(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Refuse a state or saved page that does not fit the helper as malformed, before sending."""
    helper = api.protocols.users.all
    exchange.respond(_page("1", "2", next_cursor="a"))
    pager = helper.iterate()
    _taken(lines, "malformed source", pager, 1)
    state = pager.checkpoint()
    session = ["secret"]
    for label, saved, fields in (
        ("unknown member", {**_envelope(state)["state"], "extra": 1}, {}),
        ("arguments not an array", _replaced(state, ("arguments",), {}), {}),
        ("negative page index", _replaced(state, ("page", 0), -1), {}),
        ("bad history digest", _replaced(state, ("page", 5, 0, 0), "zz"), {}),
        ("text status", _replaced(state, ("page", 6, 1), "200"), {}),
        ("cookie value", _replaced(state, ("arguments", 4), session), {}),
        ("undeclared body", _replaced(state, ("body",), [{}, "application/json"]), {}),
        ("too many items left", _replaced(state, ("page", 6, 0), 3), {}),
        ("payload without items left", _replaced(state, ("page", 6), None), {}),
        ("payload it cannot decode", None, {"payload": b"{"}),
    ):
        record(
            lines, label, lambda saved=saved, fields=fields: helper.resume(_crafted(harness, state, saved, **fields))
        )
    searches = api.protocols.searches.all
    exchange.respond(_page("1", next_cursor="c1"))
    search = searches.iterate(body=_body(harness, "searches", "Search", {"query": "a"}))
    _taken(lines, "search source", search, 1)
    saved = search.checkpoint()
    for label, replaced in (
        ("undeclared body media", _replaced(saved, ("body", 1), "text/plain")),
        ("short body", _replaced(saved, ("body",), [{}])),
    ):
        record(lines, label, lambda replaced=replaced: searches.resume(_crafted(harness, saved, replaced)))


def _security(harness: Harness, exchange: Exchange, lines: list[str]) -> None:
    """Bind checkpoints to the credential partition and auth, exporting authenticated ones only under a partition."""
    package, options, protocols = harness.package, harness.options, harness.protocols
    auth = importlib.import_module(f"{package.__name__}.auth")
    token = auth.StaticTokenProvider(auth.AccessToken("token"))
    tenant = protocols.ProtocolSecurityContext(credential_partition="tenant")
    other = protocols.ProtocolSecurityContext(credential_partition="other")
    bearer = auth.AuthConfig({"bearer": token})
    states: dict[str, Any] = {}
    for label, settings in (
        ("anonymous", options.ClientOptions()),
        ("tenant", options.ClientOptions(protocols=options.ProtocolClientOptions(security=tenant))),
        ("other tenant", options.ClientOptions(protocols=options.ProtocolClientOptions(security=other))),
        ("bearer", options.ClientOptions(auth=bearer)),
        ("bearer tenant", options.ClientOptions(auth=bearer, protocols=options.ProtocolClientOptions(security=tenant))),
    ):
        with exchange.client() as native, package.Client(http_client=native, options=settings) as api:
            helper = api.protocols.secure.users
            states[label] = state = helper.iterate().checkpoint()
            record(lines, f"{label} export", lambda state=state: len(state.export()) > 0)
            for source, saved in states.items():
                record(
                    lines, f"{label} resumes {source}", lambda saved=saved, helper=helper: helper.resume(saved).progress
                )
            if label.startswith("bearer"):
                exchange.respond(_page("1"))
                drained(lines, f"{label} resumed", helper.resume(state))


async def _async_resume(harness: Harness, lines: list[str]) -> None:
    """Checkpoint and resume asyncio pagers, in items and in pages, without awaiting either."""
    exchange = Exchange(lines)
    async with exchange.async_client() as native, harness.package.AsyncClient(http_client=native) as api:
        helper = api.protocols.users.all
        exchange.respond(_page("1", "2", next_cursor="a"), _page("3"))
        pager = helper.iterate()
        first = await anext(aiter(pager))
        lines.append(f"  async first {item_id(first)}")
        state = pager.checkpoint()
        _saved(lines, "async mid page", state)
        await adrained(lines, "async resumed items", helper.resume(state))
        exchange.respond(_page("1", next_cursor="a"), _page("2"))
        pages = helper.iterate()
        await anext(aiter(pages.iter_pages()))
        await adrained(lines, "async resumed pages", helper.resume(pages.checkpoint()).iter_pages())
        record(lines, "async another helper's state", lambda: api.protocols.users.snapshot.resume(state))
