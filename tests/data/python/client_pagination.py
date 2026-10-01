"""Page through generated cursor pagination helpers: traversal, cursors, end conditions, limits, cycles, and pages."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.data.python.client_runtime import Exchange, describe, json_response, raw_response, record, run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterable
    from types import ModuleType

_ABSENT: Final = object()
_BACKENDS_PAGES: Final = ({"data": [{"id": "1"}, {"id": "2"}], "next_cursor": "a"}, {"data": [{"id": "3"}]})


def users(*ids: str, cursor: object = _ABSENT, **headers: str) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a responder of one page of users, with a next cursor unless it is absent."""
    payload: dict[str, object] = {"data": [{"id": value} for value in ids]}
    if cursor is not _ABSENT:
        payload["next_cursor"] = cursor
    return json_response(200, payload, **headers)


def item_id(item: object) -> str:
    """Return the id or name of an item of any backend: a model attribute or a TypedDict key; a string is itself."""
    if isinstance(item, str):
        return item
    for name in ("id", "name"):
        if isinstance(item, dict) and name in item:
            return str(item[name])
        if hasattr(item, name):
            return str(getattr(item, name))
    return repr(item)


def summary(page: Any) -> str:
    """Summarize a page by its item ids, continuation, status, and the sends of its call; None stays None."""
    if page is None:
        return "None"
    info = page.response
    return (
        f"[{','.join(item_id(item) for item in page.items)}] continuation={page.continuation!r} "
        f"status={info.status_code} sends={info.network_send_count}"
    )


def fetched(lines: list[str], label: str, call: Callable[[], Any]) -> Any:
    """Report one page, or the failure of fetching it."""
    try:
        page = call()
    except Exception as error:  # noqa: BLE001
        lines.append(f"  {label} ! {describe(error)}")
        return None
    lines.append(f"  {label} = {summary(page)}")
    return page


async def afetched(lines: list[str], label: str, call: Callable[[], Any]) -> Any:
    """Report one page an asyncio helper fetches, or the failure of fetching it."""
    try:
        page = await call()
    except Exception as error:  # noqa: BLE001
        lines.append(f"  {label} ! {describe(error)}")
        return None
    lines.append(f"  {label} = {summary(page)}")
    return page


def _label(value: Any) -> str:
    return f"[{','.join(item_id(item) for item in value.items)}]" if hasattr(value, "continuation") else item_id(value)


def drained(lines: list[str], label: str, values: Iterable[Any]) -> None:
    """Report every item or page an iterator yields, then its end or the failure that stopped it."""
    seen: list[str] = []
    try:
        seen.extend(_label(value) for value in values)
    except Exception as error:  # noqa: BLE001
        lines.append(f"  {label} {seen} ! {describe(error)}")
        return
    lines.append(f"  {label} {seen}")


async def adrained(lines: list[str], label: str, values: AsyncIterator[Any]) -> None:
    """Report every item or page an asyncio iterator yields, then its end or the failure that stopped it."""
    seen: list[str] = []
    try:
        async for value in values:
            seen.append(_label(value))
    except Exception as error:  # noqa: BLE001
        lines.append(f"  {label} {seen} ! {describe(error)}")
        return
    lines.append(f"  {label} {seen}")


def progress(pager: Any) -> str:
    """Return a pager's progress as a plain dictionary."""
    return repr(dict(pager.progress))


class Harness:
    """A generated pagination package's public modules and the typed argument values its helpers take."""

    def __init__(self, package: ModuleType) -> None:
        """Import the modules the scenarios use."""
        self.package = package
        self.options, self.protocols, self.errors = (
            importlib.import_module(f"{package.__name__}.{name}") for name in ("options", "protocols", "errors")
        )

    def argument(self, resource: str, operation: str, location: str, name: str, wire: object) -> object:
        """Return the value an argument takes for a wire value, as its request codec builds it."""
        types = importlib.import_module(f"{self.package.__name__}.types.{resource}")
        return getattr(types, f"{operation}RequestCodecs").parameter(location=location, name=name).from_wire(wire)

    def cursor(self, wire: str) -> object:
        """Return a starting cursor of the users listing."""
        return self.argument("users", "ListUsers", "query", "cursor", wire)

    def client_options(self, **settings: Any) -> Any:
        """Return client options that retry at once, with any other settings."""
        options = self.options
        return options.ClientOptions(retry=options.RetryOptions(initial_delay=0, jitter="none"), **settings)


def pagination(package: ModuleType, lines: list[str]) -> None:
    """Traverse, end, limit, and continue cursor pages through the synchronous and asyncio clients."""
    harness = Harness(package)
    exchange = Exchange(lines)
    with exchange.client() as native, package.Client(http_client=native, options=harness.client_options()) as api:
        _traversals(harness, api, exchange, lines)
        _modes(harness, api, exchange, lines)
        _pages(harness, api, exchange, lines)
        _ends(harness, api, exchange, lines)
        _data(harness, api, exchange, lines)
        _sizes(harness, api, exchange, lines)
        _cycles(api, exchange, lines)
        _limits(harness, api, exchange, lines)
        _targets(harness, api, exchange, lines)
    run(lambda: _async_pagination(harness, lines))


def _traversals(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Fetch pages lazily, one at a time, through empty pages and a retried page, stopping at the last one."""
    helper = api.protocols.users.all
    limit = harness.argument("users", "ListUsers", "query", "limit", 2)
    pager = helper.iterate(limit=limit)
    lines.append(f"  created pager {progress(pager)}")
    exchange.respond(users("1", "2", cursor="a"), users(cursor="b"), users("3"))
    drained(lines, "items", pager)
    lines.append(f"  progress {progress(pager)}")
    drained(lines, "items again", pager)
    pager = helper.iterate()
    exchange.respond(users("1", "2", cursor="a"))
    record(lines, "first item", lambda: item_id(next(pager)))
    pager.close()
    pager.close()
    record(lines, "after close", lambda: next(pager))
    lines.append(f"  closed after one page {progress(pager)}")
    with helper.iterate() as managed:
        exchange.respond(
            users("1", cursor="a"),
            json_response(429, {"message": "slow down"}, **{"Retry-After": "0"}),
            users("2"),
        )
        drained(lines, "retried items", managed)
        lines.append(f"  retried progress {progress(managed)}")
    record(lines, "after block", lambda: next(managed))
    started = helper.iterate(cursor=harness.cursor("start"))
    exchange.respond(users("9"))
    drained(lines, "started items", started)


def _modes(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Keep a pager to the mode it was first used in, reporting a closed pager as closed in either mode."""
    del harness
    helper = api.protocols.users.all
    pages = helper.iterate()
    iterator = pages.iter_pages()
    record(lines, "pages then items", lambda: next(pages))
    record(lines, "pages then iter", lambda: iter(pages))
    exchange.respond(users("1", cursor="a"), users("2"))
    drained(lines, "pages", iterator)
    drained(lines, "pages again", iterator)
    record(lines, "pages iter_pages again", lambda: list(pages.iter_pages()))
    items = helper.iterate()
    record(lines, "iterable", lambda: iter(items) is items)
    record(lines, "items then pages", lambda: items.iter_pages())
    closed = helper.iterate()
    closed.iter_pages()
    closed.close()
    record(lines, "items of closed pages", lambda: next(closed))


def _pages(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Fetch pages one call at a time, refusing pages another helper or a caller made."""
    helper = api.protocols.users.all
    exchange.respond(users("1", cursor="a"), users("2", cursor="b"), users("3"))
    first = fetched(lines, "page", helper.page)
    second = fetched(lines, "next page", lambda: helper.next_page(first))
    last = fetched(lines, "last page", lambda: helper.next_page(second))
    fetched(lines, "after the last page", lambda: helper.next_page(last))
    exchange.respond(users("1", cursor="a"))
    fetched(lines, "same page again", lambda: helper.next_page(first))
    record(lines, "page of another helper", lambda: api.protocols.users.by_header.next_page(first))
    made = harness.protocols.Page(items=(), data=first.data, response=first.response)
    record(lines, "page a caller made", lambda: helper.next_page(made))
    record(lines, "not a page", lambda: helper.next_page("page"))
    record(lines, "page repr", lambda: repr(made).split(", response=")[0])
    for label, arguments in (
        ("page items", {"items": [], "data": None, "response": first.response}),
        ("page response", {"items": (), "data": None, "response": None}),
        ("page continuation", {"items": (), "data": None, "response": first.response, "continuation": "a"}),
    ):
        record(lines, label, lambda arguments=arguments: harness.protocols.Page(**arguments))
    record(lines, "page frozen", lambda: setattr(first, "data", None))
    record(lines, "page equality", lambda: (first == first, first == second, hash(first) == hash(first)))


def _ends(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """End on a missing, null, or declared cursor, and on an empty one only where it is declared an end."""
    del harness
    everyone = api.protocols.users.all
    by_header = api.protocols.users.by_header
    for label, helper, responses in (
        ("missing end", everyone, (users("1"),)),
        ("null end", everyone, (users("1", cursor=None),)),
        ("empty cursor", everyone, (users("1", cursor=""), users("2"))),
        ("header end value", by_header, (users("1", **{"X-Next": "n1"}), users("2", **{"X-Next": "done"}))),
        ("empty header end", by_header, (users("1", **{"X-Next": ""}),)),
        ("missing header end", by_header, (users("1"),)),
    ):
        exchange.respond(*responses)
        drained(lines, label, helper.iterate())


def _data(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Refuse missing and null cursors and items no end condition covers, and duplicate cursor headers."""
    del harness
    loose = api.protocols.loose.all
    nested = api.protocols.nested.all
    for label, helper, responder in (
        ("missing loose cursor", loose, json_response(200, {"data": [{"id": "1"}]})),
        ("null nested cursor", nested, json_response(200, {"result": {"items": [], "next": None}})),
        ("missing items", loose, json_response(200, {"next": None})),
        ("null items", loose, json_response(200, {"data": None, "next": None})),
        ("items of another type", loose, json_response(200, {"data": {"id": "1"}, "next": None})),
        ("missing nested result", nested, json_response(200, {})),
        ("null nested items", nested, json_response(200, {"result": {"items": None}})),
        (
            "duplicate cursor header",
            api.protocols.users.by_header,
            lambda _: httpx2.Response(
                200, headers=[("X-Next", "a"), ("x-next", "b")], json={"data": [{"id": "1"}]}
            ),
        ),
        ("invalid page", loose, raw_response(200, b"{", "application/json")),
        ("page its model refuses", loose, json_response(200, {"data": [{"name": "x"}]})),
    ):
        exchange.respond(responder)
        drained(lines, label, helper.iterate())
    exchange.respond(json_response(200, {"data": [{"id": "1"}], "next": 7}), json_response(200, {"data": [], "next": None}))
    drained(lines, "integer cursor", loose.iterate())
    exchange.respond(
        json_response(200, {"result": {"items": [{"id": "1"}], "next": "n1"}}),
        json_response(200, {"result": {"items": [{"id": "2"}]}}),
    )
    drained(lines, "nested items", nested.iterate())


def _sizes(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Refuse cursors and pages over their limits, and a page over a smaller response limit as any call does."""
    options, protocols = harness.options, harness.protocols
    helper = api.protocols.users.all
    for label, settings, responder in (
        ("cursor over its limit", {"pagination_options": protocols.PaginationOptions(max_cursor_bytes=4)}, users("1", cursor="abcde")),
        ("multibyte cursor", {"pagination_options": protocols.PaginationOptions(max_cursor_bytes=4)}, users("1", cursor="ééé")),
        ("page over its limit", {"pagination_options": protocols.PaginationOptions(max_page_bytes=10)}, users("1")),
        ("response limit smaller", {"options": options.RequestOptions(max_response_bytes=10)}, users("1")),
        (
            "response limit removed",
            {"options": options.RequestOptions(max_response_bytes=None), "pagination_options": protocols.PaginationOptions(max_page_bytes=10)},
            users("1"),
        ),
        ("equal limits", {"options": options.RequestOptions(max_response_bytes=10), "pagination_options": protocols.PaginationOptions(max_page_bytes=10)}, users("1")),
    ):
        exchange.respond(responder)
        drained(lines, label, helper.iterate(**settings))
    exchange.respond(
        raw_response(200, b"\x1f\x8b-broken", "application/json", **{"content-encoding": "gzip"}),
    )
    drained(lines, "broken coding", helper.iterate())
    schema = options.RequestOptions(validation=options.ValidationOptions(response="schema"))
    exchange.respond(json_response(200, {"data": [{"id": 1}]}), users("1"))
    drained(lines, "schema validation", api.protocols.loose.all.iterate(options=schema))
    drained(lines, "schema validation valid", api.protocols.users.all.iterate(options=schema))


def _cycles(api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Deliver a page that repeats an earlier continuation, then refuse to continue."""
    helper = api.protocols.users.all
    exchange.respond(users("1", cursor="a"), users("2", cursor="b"), users("3", cursor="a"))
    pager = helper.iterate()
    drained(lines, "cycle pages", pager.iter_pages())
    lines.append(f"  cycle progress {progress(pager)}")
    record(lines, "after the cycle", lambda: next(pager.iter_pages()))
    exchange.respond(users("1", cursor="a"), users("2", cursor="a"))
    first = fetched(lines, "cycle page", helper.page)
    second = fetched(lines, "repeating page", lambda: helper.next_page(first))
    fetched(lines, "after the repeating page", lambda: helper.next_page(second))
    exchange.respond(json_response(200, {"data": [], "next": 5}), json_response(200, {"data": [], "next": 5}))
    drained(lines, "cycle of integer cursors", api.protocols.loose.all.iterate())


def _limits(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Stop at page and item limits only while pages remain, and at zero items without sending."""
    protocols = harness.protocols
    helper = api.protocols.users.all
    for label, settings, responses, pages in (
        ("page limit at the last page", {"max_pages": 2}, (users("1", cursor="a"), users("2")), False),
        ("page limit with pages left", {"max_pages": 2}, (users("1", cursor="a"), users("2", cursor="b")), False),
        ("no page limit", {"max_pages": None}, (users("1", cursor="a"), users("2")), False),
        ("item limit at the last item", {"max_items": 2}, (users("1", "2"),), False),
        ("item limit with items left", {"max_items": 2}, (users("1", "2", "3"),), False),
        ("item limit with pages left", {"max_items": 2}, (users("1", "2", cursor="a"),), False),
        ("no item limit", {"max_items": None}, (users("1", "2", cursor="a"), users("3")), False),
        ("item limit of pages", {"max_items": 1}, (users("1", "2", cursor="a"),), True),
        ("item limit of the last page", {"max_items": 1}, (users("1", "2"),), True),
        ("zero items", {"max_items": 0}, (), False),
        ("zero items of pages", {"max_items": 0}, (), True),
    ):
        exchange.respond(*responses)
        pager = helper.iterate(pagination_options=protocols.PaginationOptions(**settings))
        drained(lines, label, pager.iter_pages() if pages else pager)
        lines.append(f"    progress {progress(pager)}")
    record(lines, "zero items page", lambda: helper.page(pagination_options=protocols.PaginationOptions(max_items=0)))
    exchange.respond(users("1", cursor="a"))
    first = fetched(lines, "page for limits", helper.page)
    for label, settings in (("page limit", {"max_pages": 1}), ("item limit", {"max_items": 1})):
        record(lines, f"next page past its {label}", lambda settings=settings: helper.next_page(
            first, pagination_options=protocols.PaginationOptions(**settings)
        ))


def _targets(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Write the cursor to a path parameter or a status-driven query, sending the first page's body again."""
    exchange.respond(users("1", cursor="b2"), users("2"))
    drained(
        lines,
        "path cursor",
        api.protocols.archive.all.iterate(cursor=harness.argument("archive", "ListArchive", "path", "cursor", "a1")),
    )
    exchange.respond(raw_response(206, b'{"data":[{"id":"1"}]}', "application/json"), users("2"))
    drained(lines, "status cursor", api.protocols.statuses.all.iterate())
    body = _query(harness)
    exchange.respond(users("1", cursor="n"), users("2"))
    drained(lines, "body sent again", api.protocols.users.search.iterate(body=body))
    exchange.respond(users("1", cursor=None))
    drained(lines, "body media", api.protocols.users.search.iterate(body=body, media_type="application/json"))
    exchange.respond(json_response(200, {"data": [{"id": "1"}], "token": "t1"}))
    drained(lines, "cursor its parameter refuses", api.protocols.loose.tokens.iterate())
    exchange.respond(json_response(200, [{"name": "a"}], **{"X-Next": "n1"}), json_response(200, [{"name": "b"}]))
    drained(lines, "root list", api.protocols.labels.all.iterate())
    exchange.respond(json_response(200, [{"name": "a"}], **{"X-Next": "n1"}), json_response(200, [{"name": "b"}]))
    drained(lines, "named root list", api.protocols.labels.sets.iterate())


def _query(harness: Harness) -> object:
    types = importlib.import_module(f"{harness.package.__name__}.types.users")
    return types.SearchUsersRequestCodecs.body().from_wire({"name": "a"})


async def _async_pagination(harness: Harness, lines: list[str]) -> None:
    """Traverse, end, limit, and continue cursor pages with asyncio."""
    package, protocols = harness.package, harness.protocols
    exchange = Exchange(lines)
    async with (
        exchange.async_client() as native,
        package.AsyncClient(http_client=native, options=harness.client_options()) as api,
    ):
        helper = api.protocols.users.all
        pager = helper.iterate()
        lines.append(f"  async created pager {progress(pager)}")
        exchange.respond(users("1", "2", cursor="a"), users(cursor="b"), users("3"))
        await adrained(lines, "async items", pager)
        await adrained(lines, "async items again", pager)
        lines.append(f"  async progress {progress(pager)}")
        async with helper.iterate() as managed:
            exchange.respond(users("1", cursor="a"), users("2", cursor="b"), users("3", cursor="a"))
            await adrained(lines, "async cycle pages", managed.iter_pages())
            record(lines, "async after the cycle", managed.iter_pages)
        record(lines, "async after block", managed.iter_pages)
        mixed = helper.iterate()
        record(lines, "async items then pages", lambda: (aiter(mixed), mixed.iter_pages()))
        pages = helper.iterate()
        pages.iter_pages()
        record(lines, "async pages then aiter", lambda: aiter(pages))
        exchange.respond(users("1", cursor="a"))
        await adrained(
            lines,
            "async item limit",
            helper.iterate(pagination_options=protocols.PaginationOptions(max_items=1, max_pages=5)).iter_pages(),
        )
        await adrained(
            lines, "async zero items", helper.iterate(pagination_options=protocols.PaginationOptions(max_items=0))
        )
        exchange.respond(users("1", cursor="a"), users("2"))
        first = await afetched(lines, "async page", helper.page)
        last = await afetched(lines, "async next page", lambda: helper.next_page(first))
        await afetched(lines, "async after the last page", lambda: helper.next_page(last))
        await afetched(lines, "async page of another helper", lambda: api.protocols.users.by_header.next_page(first))
        await afetched(
            lines,
            "async zero items page",
            lambda: helper.page(pagination_options=protocols.PaginationOptions(max_items=0)),
        )
        exchange.respond(users("1"))
        await adrained(
            lines,
            "async response limit smaller",
            helper.iterate(options=harness.options.RequestOptions(max_response_bytes=10)),
        )
        closed = helper.iterate()
        await closed.aclose()
        await closed.aclose()
        await adrained(lines, "async after close", closed)


def pagination_backends(package: ModuleType, lines: list[str]) -> None:
    """Read the items of every backend's page models, root lists, and optional members through their accessors.

    Items of another type fail response validation in either mode, before the helper reads them.
    """
    exchange = Exchange(lines)
    with exchange.client() as native, package.Client(http_client=native) as api:
        exchange.respond(*(json_response(200, page) for page in _BACKENDS_PAGES))
        drained(lines, "users", api.protocols.users.all.iterate())
        exchange.respond(json_response(200, _BACKENDS_PAGES[0]), json_response(200, _BACKENDS_PAGES[1]))
        first = fetched(lines, "page", api.protocols.users.all.page)
        fetched(lines, "next page", lambda: api.protocols.users.all.next_page(first))
        exchange.respond(
            json_response(200, {"result": {"items": [{"id": "1"}], "next": "n"}}),
            json_response(200, {"result": {"items": []}}),
        )
        drained(lines, "nested", api.protocols.nested.all.iterate())
        for helper in (api.protocols.labels.all, api.protocols.labels.sets):
            exchange.respond(json_response(200, [{"name": "a"}], **{"X-Next": "n"}), json_response(200, []))
            drained(lines, "labels", helper.iterate())
        exchange.respond(
            json_response(200, {"data": [{"id": "1"}], "next": 3}), json_response(200, {"data": [], "next": None})
        )
        drained(lines, "loose", api.protocols.loose.all.iterate())
        options = importlib.import_module(f"{package.__name__}.options")
        for mode in ("native", "schema"):
            settings = options.RequestOptions(validation=options.ValidationOptions(response=mode))
            for label, helper, payload in (
                ("users", api.protocols.users.all, {"data": "users"}),
                ("loose", api.protocols.loose.all, {"data": "users", "next": None}),
            ):
                exchange.respond(json_response(200, payload))
                drained(lines, f"{mode} {label} of another type", helper.iterate(options=settings))
    run(lambda: _async_backends(package, lines))


async def _async_backends(package: ModuleType, lines: list[str]) -> None:
    exchange = Exchange(lines)
    async with exchange.async_client() as native, package.AsyncClient(http_client=native) as api:
        exchange.respond(*(json_response(200, page) for page in _BACKENDS_PAGES))
        await adrained(lines, "async users", api.protocols.users.all.iterate())
        exchange.respond(json_response(200, _BACKENDS_PAGES[0]), json_response(200, _BACKENDS_PAGES[1]))
        await adrained(lines, "async user pages", api.protocols.users.all.iterate().iter_pages())


def pagination_limits(package: ModuleType, lines: list[str]) -> None:
    """Send a server's cursor as it came, past its parameter's schema, while a caller's start cursor is validated."""
    harness = Harness(package)
    options = harness.options
    checked = options.RequestOptions(validation=options.ValidationOptions(request="schema"))
    exchange = Exchange(lines)
    with exchange.client() as native, package.Client(http_client=native) as api:
        helper = api.protocols.codes.all
        for label, settings in (("default", None), ("schema", checked)):
            exchange.respond(
                json_response(200, {"data": [{"id": "1"}], "next": "longer"}), json_response(200, {"data": [{"id": "2"}]})
            )
            drained(lines, f"{label} validation of a long server cursor", helper.iterate(options=settings))
        start = type(harness.argument("codes", "ListCodes", "query", "cursor", "ab").value).model_construct("longer")
        drained(lines, "schema validation of a long start cursor", helper.iterate(cursor=start, options=checked))
