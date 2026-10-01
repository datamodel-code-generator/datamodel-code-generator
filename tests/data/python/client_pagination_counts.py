"""Page through offset and page-number helpers: counted positions, their end evidence, and its failures."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from tests.data.python.client_pagination import Harness, adrained, afetched, drained, fetched, users
from tests.data.python.client_runtime import Exchange, json_response, run

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    import httpx2


def _page(*ids: str, **members: object) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a responder of one page of users with the given members."""
    return json_response(200, {"data": [{"id": value} for value in ids], **members})


def pagination_counts(package: ModuleType, lines: list[str]) -> None:
    """Count offsets by a literal step or by items, page numbers by a literal step, and end by has_more or total."""
    harness = Harness(package)
    exchange = Exchange(lines)
    with exchange.client() as native, package.Client(http_client=native) as api:
        _steps(harness, api, exchange, lines)
        _starts(harness, api, exchange, lines)
        _items(api, exchange, lines)
        _flags(api, exchange, lines)
        _numbers(api, exchange, lines)
        _pages(api, exchange, lines)
    run(lambda: _async_counts(harness, lines))


def _steps(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Advance an offset by its literal step through an empty page until has_more is false."""
    helper = api.protocols.users.offsets
    exchange.respond(_page("1", "2", has_more=True), _page(has_more=True), _page("3", has_more=False))
    drained(lines, "literal offsets", helper.iterate())
    exchange.respond(_page("1"))
    drained(lines, "missing has_more", helper.iterate())
    exchange.respond(_page("1", "2", has_more=True))
    limits = harness.protocols.PaginationOptions(max_pages=1)
    drained(lines, "literal offsets past the page limit", helper.iterate(pagination_options=limits))
    exchange.respond(_page("1", has_more=True), _page("2", has_more=False))
    pager = helper.iterate()
    drained(lines, "literal offset pages", pager.iter_pages())
    lines.append(f"  literal offset progress {dict(pager.progress)!r}")
    exchange.respond(_page("1", total=15), _page("2", total=15))
    drained(lines, "body offsets", api.protocols.searches.all.iterate(body=_search(harness, {"query": "a"})))


def _search(harness: Harness, wire: object) -> object:
    """Return the search request body of a wire value, as its body codec builds it."""
    types = importlib.import_module(f"{harness.package.__name__}.types.searches")
    return types.SearchRequestCodecs.body().from_wire(wire)


def _starts(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Start at the caller's own position in a query, a header, a JSON body, or a querystring, refusing a non-integer.

    The total of an offset still counts from the first position, wherever the traversal starts.
    """
    listing = api.protocols.users
    start = harness.argument("users", "ListUsers", "query", "offset", 40)
    exchange.respond(_page("1", "2", has_more=True), _page("3", has_more=False))
    drained(lines, "query start", listing.offsets.iterate(offset=start))
    exchange.respond(_page("1", "2", total=42))
    drained(lines, "query start near the total", listing.items.iterate(offset=start))
    exchange.respond(_page("1", has_more=True), _page("2", has_more=False))
    first = fetched(lines, "query start page", lambda: listing.offsets.page(offset=start))
    fetched(lines, "query start next page", lambda: listing.offsets.next_page(first))
    numbered = harness.argument("users", "ListUsers", "header", "X-Page", 5)
    exchange.respond(users("1", **{"X-Has-More": "true"}), users("2", **{"X-Has-More": "false"}))
    drained(lines, "header start", listing.by_header.iterate(x_page=numbered))
    exchange.respond(_page("1", total=45), _page("2", total=45))
    body = _search(harness, {"query": "a", "window": {"start": 30}})
    drained(lines, "body start", api.protocols.searches.all.iterate(body=body))
    types = importlib.import_module(f"{harness.package.__name__}.types.finds")
    codec = types.FindRequestCodecs.parameter(location="querystring", name="criteria")
    for label, arguments in (
        ("querystring start", {"criteria": codec.from_wire({"term": "a", "start": 10})}),
        ("querystring without a start", {"criteria": codec.from_wire({"term": "a"})}),
        ("no querystring", {}),
    ):
        exchange.respond(_page("1", has_more=True), _page("2", has_more=False))
        drained(lines, label, api.protocols.finds.all.iterate(**arguments))
    options = harness.options
    checked = options.RequestOptions(validation=options.ValidationOptions(request="schema"))
    drained(lines, "text start", listing.offsets.iterate(offset="forty"))
    exchange.respond(_page("1", has_more=True), _page("2", has_more=False))
    integral = harness.argument("users", "ListUsers", "query", "position", 40.0)
    drained(lines, "integral number start", listing.positions.iterate(position=integral))
    fraction = harness.argument("users", "ListUsers", "query", "position", 40.5)
    drained(lines, "fractional number start", listing.positions.iterate(position=fraction))
    page = harness.argument("users", "ListUsers", "query", "page", 3)
    exchange.respond(users("1", "2", **{"X-Total-Count": "3"}), users(**{"X-Total-Count": "3"}))
    drained(lines, "page number start ended by an empty page", listing.pages.iterate(page=page))
    model = _search(harness, {"query": "a", "window": {"start": 1}}).value
    texted = model.model_copy(update={"window": type(model.window).model_construct(start="x")})
    for label, settings in (("unchecked text body start", None), ("checked text body start", checked)):
        drained(lines, label, api.protocols.searches.all.iterate(body=texted, options=settings))


def _items(api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Advance an offset by each page's item count until the total, refusing an empty page that continues."""
    helper = api.protocols.users.items
    exchange.respond(_page("1", "2", "3", total=5), _page("4", "5", total=5))
    drained(lines, "counted offsets", helper.iterate())
    exchange.respond(_page(total=0))
    drained(lines, "zero total", helper.iterate())
    exchange.respond(_page("1", "2", "3", total=4), _page("4", "5", "6", total=4))
    drained(lines, "offsets past the total", helper.iterate())
    exchange.respond(_page("1", total=3), _page(total=3))
    drained(lines, "empty page before the total", helper.iterate())
    for label, members in (
        ("negative total", {"total": -1}),
        ("missing total", {}),
    ):
        exchange.respond(_page("1", **members))
        drained(lines, label, helper.iterate())


def _flags(api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Read has_more from an untyped member and from a header, refusing anything but a boolean."""
    helper = api.protocols.users.loose
    exchange.respond(_page("1", more=True), _page("2", "3", more=True), _page(more=False))
    drained(lines, "offsets from the first position", helper.iterate())
    for label, members in (
        ("string has_more", {"more": "yes"}),
        ("null has_more", {"more": None}),
        ("missing untyped has_more", {}),
    ):
        exchange.respond(_page("1", **members))
        drained(lines, label, helper.iterate())
    helper = api.protocols.users.by_header
    exchange.respond(users("1", **{"X-Has-More": "true"}), users("2", **{"X-Has-More": "false"}))
    drained(lines, "header page numbers", helper.iterate())
    for label, headers in (
        ("capitalized has_more header", {"X-Has-More": "True"}),
        ("missing has_more header", {}),
    ):
        exchange.respond(users("1", **headers))
        drained(lines, label, helper.iterate())


def _numbers(api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Advance page numbers by a literal step until the items delivered reach a header's or a member's total.

    A page without items ends them too, and a negative header total is a value error like a negative member.
    """
    helper = api.protocols.users.pages
    exchange.respond(users("1", "2", **{"X-Total-Count": "3"}), users("3", **{"X-Total-Count": "3"}))
    drained(lines, "page numbers", helper.iterate())
    for label, headers in (
        ("signed total header", {"X-Total-Count": "-1"}),
        ("spelled total header", {"X-Total-Count": "three"}),
        ("huge total header", {"X-Total-Count": "9" * 5000}),
        ("missing total header", {}),
    ):
        exchange.respond(users("1", **headers))
        drained(lines, label, helper.iterate())
    helper = api.protocols.users.counted
    exchange.respond(_page("1", count=3), _page(count=3))
    drained(lines, "page numbers ended by an empty page", helper.iterate())
    for label, members in (
        ("fractional total", {"count": 2.5}),
        ("boolean total", {"count": True}),
        ("null total", {"count": None}),
    ):
        exchange.respond(_page("1", **members))
        drained(lines, label, helper.iterate())


def _pages(api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Fetch counted pages one call at a time, continuing an earlier page again at the same position."""
    helper = api.protocols.users.items
    exchange.respond(_page("1", "2", total=3), _page("3", total=3), _page("3", total=3))
    first = fetched(lines, "first counted page", helper.page)
    fetched(lines, "next counted page", lambda: helper.next_page(first))
    second = fetched(lines, "next counted page again", lambda: helper.next_page(first))
    fetched(lines, "after the last counted page", lambda: helper.next_page(second))


async def _async_counts(harness: Harness, lines: list[str]) -> None:
    """Count offsets and page numbers with asyncio, in items and in single pages."""
    package = harness.package
    exchange = Exchange(lines)
    async with exchange.async_client() as native, package.AsyncClient(http_client=native) as api:
        exchange.respond(_page("1", "2", has_more=True), _page("3", has_more=False))
        await adrained(lines, "async literal offsets", api.protocols.users.offsets.iterate())
        helper = api.protocols.users.pages
        exchange.respond(users("1", **{"X-Total-Count": "2"}), users("2", **{"X-Total-Count": "2"}))
        first = await afetched(lines, "async first page number", helper.page)
        second = await afetched(lines, "async next page number", lambda: helper.next_page(first))
        await afetched(lines, "async after the last page number", lambda: helper.next_page(second))
