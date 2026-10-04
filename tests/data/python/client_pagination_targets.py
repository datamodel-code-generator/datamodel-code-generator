"""Write cursors and binding values into headers, paths, querystrings, and JSON bodies of the next pages."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.data.python.client_pagination import Harness, adrained, afetched, drained, fetched, users
from tests.data.python.client_runtime import Exchange, argument, json_response, record, request_body, run

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType


def _tagged(*ids: str, cursor: object, headers: list[tuple[str, str]]) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a responder of one page of users with repeatable response headers."""
    payload: dict[str, object] = {"data": [{"id": value} for value in ids]}
    if cursor is not None:
        payload["next_cursor"] = cursor
    return lambda _: httpx2.Response(200, headers=headers, json=payload)


def _found(*ids: str, **members: object) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a responder of one page of search results with the given members."""
    return json_response(200, {"data": [{"id": value} for value in ids], **members})


def _search_body(harness: Harness, wire: object) -> object:
    """Return the search request body of a wire value, as its body codec builds it."""
    return request_body(harness.package, "search", None, wire)


def pagination_targets(package: ModuleType, lines: list[str]) -> None:
    """Write cursors to a header and a path, bindings from the first and the last page, and a JSON body."""
    harness = Harness(package)
    exchange = Exchange(lines)
    with exchange.client() as native, package.Client(http_client=native) as api:
        _parameters(harness, api, exchange, lines)
        _overrides(harness, api, exchange, lines)
        _paths(harness, api, exchange, lines)
        _bindings(harness, api, exchange, lines)
        _bodies(harness, api, exchange, lines)
    run(lambda: _async_targets(harness, lines))


def _parameters(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Write the cursor to a header named in another case, over the caller's own."""
    helpers = api.protocols.users
    exchange.respond(users("1", cursor="a"), users("2", cursor="b"), users("3"))
    drained(lines, "header cursor", helpers.by_header.iterate())
    start = harness.argument("listUsers", "header", "X-Cursor", "start")
    exchange.respond(users("1", cursor="a"), users("2"))
    drained(lines, "header cursor over the caller's", helpers.by_header.iterate(x_cursor=start))


def _overrides(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Refuse call options patching a header or a query parameter the helper writes, before sending."""
    options = harness.options.RequestOptions
    helpers = api.protocols.users
    for label, helper, settings in (
        ("header patch of the cursor", helpers.by_header, options(headers=(("x-cursor", "mine"),))),
        ("query patch of a binding", helpers.bound, options(query=(("limit", "9"),))),
        ("query patch of the cursor", helpers.bound, options(query=(("cursor", "mine"),))),
    ):
        record(lines, f"{label} pager", lambda helper=helper, settings=settings: helper.iterate(options=settings))
        record(lines, f"{label} page", lambda helper=helper, settings=settings: helper.page(options=settings))
    exchange.respond(users("1", cursor="a"))
    extra = options(query=(("extra", "1"),))
    first = fetched(lines, "page with another query", lambda: helpers.by_header.page(options=extra))
    record(
        lines,
        "next page with a header patch of the cursor",
        lambda: helpers.by_header.next_page(first, options=options(headers=(("X-Cursor", "mine"),))),
    )
    exchange.respond(users("2"))
    fetched(
        lines,
        "next page with another header",
        lambda: helpers.by_header.next_page(first, options=options(headers=(("X-Other", "kept"),))),
    )


def _paths(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Write the cursor to a path parameter, refusing a dot segment before the next page is sent."""
    helper = api.protocols.folders.all
    folder = harness.argument("listFolder", "path", "folder", "f1")
    exchange.respond(users("1", cursor="f2"), users("2"))
    drained(lines, "path cursor", helper.iterate(folder=folder))
    for segment in (".", ".."):
        exchange.respond(users("1", cursor=segment))
        drained(lines, f"path cursor {segment!r}", helper.iterate(folder=folder))


def _bindings(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Carry the first page's header, the last page's status and headers, and a literal into the next requests."""
    helper = api.protocols.users.bound
    tags = harness.argument("listUsers", "query", "tags", ["mine"])
    exchange.respond(
        _tagged("1", cursor="a", headers=[("X-Snapshot", "s1"), ("X-Tag", "t1"), ("X-Tag", "t2")]),
        _tagged("2", cursor="b", headers=[("X-Snapshot", "s2"), ("X-Tag", "t3")]),
        _tagged("3", cursor=None, headers=[]),
    )
    drained(lines, "bound values", helper.iterate(tags=tags))
    exchange.respond(_tagged("1", cursor="a", headers=[("X-Tag", "t1")]))
    drained(lines, "missing first header", helper.iterate())
    exchange.respond(_tagged("1", cursor="a", headers=[("X-Snapshot", "s1")]))
    drained(lines, "missing every header", helper.iterate())
    exchange.respond(_tagged("1", cursor=None, headers=[]))
    drained(lines, "missing values of the last page", helper.iterate())
    exchange.respond(
        _tagged("1", cursor="a", headers=[("X-Snapshot", "s1"), ("X-Tag", "t1")]),
        _tagged("2", cursor="b", headers=[("X-Tag", "t2")]),
        _tagged("3", cursor=None, headers=[]),
    )
    first = fetched(lines, "bound page", helper.page)
    second = fetched(lines, "bound next page", lambda: helper.next_page(first))
    fetched(lines, "bound last page", lambda: helper.next_page(second))


def _bodies(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Write the cursor and bindings into a JSON body, creating its missing parent objects only.

    A member is found through allOf too. The server's cursor is sent past its property's schema, while the caller's
    body is checked as in any call.
    """
    helper = api.protocols.searches.all
    exchange.respond(_found("1", next="n1", last="1"), _found("2", next="n2", last=None), _found("3"))
    drained(lines, "body writes", helper.iterate(body=_search_body(harness, {"query": "a"})))
    exchange.respond(_found("1", next="n1", last="1"), _found("2"))
    beside = _search_body(harness, {"query": "a", "page": {"mode": "m"}, "range": {"to": "z"}})
    drained(lines, "body writes beside members", helper.iterate(body=beside))
    exchange.respond(_found("1", next="longer", last="1"), _found("2"))
    drained(lines, "a long server cursor", helper.iterate(body=_search_body(harness, {"query": "a"})))


async def _async_targets(harness: Harness, lines: list[str]) -> None:
    """Write a header cursor, bindings, and a body cursor with asyncio, refusing a header patch of the cursor."""
    package = harness.package
    exchange = Exchange(lines)
    async with exchange.async_client() as native, package.AsyncClient(http_client=native) as api:
        exchange.respond(users("1", cursor="a"), users("2"))
        await adrained(lines, "async header cursor", api.protocols.users.by_header.iterate())
        exchange.respond(
            _tagged("1", cursor="a", headers=[("X-Snapshot", "s1"), ("X-Tag", "t1")]),
            _tagged("2", cursor=None, headers=[]),
        )
        await adrained(lines, "async bound values", api.protocols.users.bound.iterate())
        helper = api.protocols.searches.all
        exchange.respond(_found("1", next="n1", last="1"), _found("2"))
        first = await afetched(
            lines, "async body page", lambda: helper.page(body=_search_body(harness, {"query": "a"}))
        )
        await afetched(lines, "async body next page", lambda: helper.next_page(first))
        patched = harness.options.RequestOptions(headers=(("X-Cursor", "mine"),))
        record(
            lines,
            "async header patch of the cursor",
            lambda: api.protocols.users.by_header.iterate(options=patched),
        )


_FIRST_PAGES: Final = (("labels", ""), ("reserved", "%2e"), ("files", "."))
_PATH_CURSORS: Final = (
    ("reserved", "listReserved", {"name": "r1"}, ("%2e", "%2E.", "%2e%2f", "a")),
    ("labels", "listLabels", {"name": "l1"}, ("", ".", "..", "a")),
    ("files", "listFiles", {"name": "f1"}, (".", "..")),
    ("pairs", "listPairs", {"owner": "", "name": "p1"}, (".", "..")),
    ("pairs", "listPairs", {"owner": "o", "name": "p1"}, (".",)),
    ("pairs", "listPairs", {"owner": ".", "name": "p1"}, ("", "a")),
    ("joined", "listJoined", {"prefix": "p", "name": "j1"}, ("", "a")),
)


def path_arguments(package: ModuleType, operation_id: str, values: dict[str, object]) -> dict[str, object]:
    """Return the path arguments of wire values, as the operation builds saved ones."""
    return {name: argument(package, operation_id, "path", name, value) for name, value in values.items()}


def pagination_paths(package: ModuleType, lines: list[str]) -> None:
    """Refuse a server's cursor only when it encodes its path segment, in the parameter's style, as a dot segment.

    A cursor its parameter cannot encode, or one making a dot segment of a segment the caller's argument shares, is
    refused before the next page is sent as any call's path value is; a literal binding encoding to `...` is sent. A
    caller's own dot segment, and an empty array that cannot fill its segment, are refused before the first page.
    """
    harness = Harness(package)
    exchange = Exchange(lines)
    with exchange.client() as native, package.Client(http_client=native) as api:
        for resource, operation, values, cursors in _PATH_CURSORS:
            helper = getattr(api.protocols, resource).all
            arguments = path_arguments(harness.package, operation, values)
            for cursor in cursors:
                exchange.respond(users("1", cursor=cursor), users("2"))
                drained(lines, f"{resource} {values} cursor {cursor!r}", helper.iterate(**arguments))
                exchange.responders.clear()
        exchange.respond(users("1", cursor="a"), users("2", cursor="b"), users("3"))
        owner = path_arguments(package, "listPairs", {"owner": "o", "name": "p1"})
        drained(lines, "pairs with the owner on three pages", api.protocols.pairs.all.iterate(**owner))
        tags = harness.argument("listTags", "path", "tags", ["t"])
        for tag in ("a.b", "a"):
            exchange.respond(_tagged("1", cursor="c", headers=[("X-Tag", tag)]), users("2"))
            drained(lines, f"tag header {tag!r}", api.protocols.tags.all.iterate(tags=tags))
            exchange.responders.clear()
        empty = harness.argument("listTags", "path", "tags", [])
        record(lines, "tags of no tag", lambda: api.tags.list_tags(tags=empty))
        drained(lines, "tag pages of no tag", api.protocols.tags.all.iterate(tags=empty))
        for resource, value in _FIRST_PAGES:
            arguments = path_arguments(harness.package, f"list{resource.title()}", {"name": value})
            exchange.respond(users("1"))
            fetched(
                lines,
                f"{resource} first page {value!r}",
                partial(getattr(api.protocols, resource).all.page, **arguments),
            )
            exchange.responders.clear()
        scope = harness.argument("listScoped", "path", "scope", "s1")
        exchange.respond(users("1", cursor="c"), users("2"))
        drained(lines, "literal label scope", api.protocols.scopes.all.iterate(scope=scope))
    run(lambda: _async_paths(harness, lines))


async def _async_paths(harness: Harness, lines: list[str]) -> None:
    exchange = Exchange(lines)
    async with exchange.async_client() as native, harness.package.AsyncClient(http_client=native) as api:
        for resource, cursor in (("reserved", "%2e"), ("labels", "..")):
            exchange.respond(users("1", cursor=cursor), users("2"))
            arguments = path_arguments(harness.package, f"list{resource.title()}", {"name": "x"})
            await adrained(
                lines, f"async {resource} cursor {cursor!r}", getattr(api.protocols, resource).all.iterate(**arguments)
            )
            exchange.responders.clear()
        for resource, value in _FIRST_PAGES:
            arguments = path_arguments(harness.package, f"list{resource.title()}", {"name": value})
            exchange.respond(users("1"))
            page = partial(getattr(api.protocols, resource).all.page, **arguments)
            await afetched(lines, f"async {resource} first page {value!r}", page)
            exchange.responders.clear()


def pagination_querystring(package: ModuleType, lines: list[str]) -> None:
    """Write the cursor and bindings into declared properties of a querystring encoded once, and end normally."""
    criteria = argument(package, "search", "querystring", "criteria", {"term": "a b"})
    exchange = Exchange(lines)
    with exchange.client() as native, package.Client(http_client=native) as api:
        search, lookup = api.protocols.search, api.protocols.lookup
        for label, helper, arguments in (
            ("querystring cursor", search.all, {"criteria": criteria}),
            ("querystring cursor without criteria", search.all, {}),
            ("querystring binding", search.fixed, {"criteria": criteria}),
            ("json querystring null binding", lookup.all, {}),
        ):
            exchange.respond(json_response(200, ["x"]), json_response(200, ["y"]))
            first = fetched(lines, label, lambda helper=helper, arguments=arguments: helper.page(**arguments))
            fetched(lines, f"{label} next page", lambda helper=helper, first=first: helper.next_page(first))
        exchange.respond(json_response(200, ["x"]), json_response(200, ["y"]))
        drained(lines, "querystring status pages", search.all.iterate(criteria=criteria))
        exchange.respond(
            json_response(200, ["x"], **{"X-Next": "b&c"}),
            json_response(200, ["y"], **{"X-Next": "d"}),
            json_response(200, ["z"]),
        )
        drained(lines, "querystring header pages", search.next.iterate(criteria=criteria))
