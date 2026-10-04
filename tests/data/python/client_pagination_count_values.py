"""Exercise integral JSON counts and layered starting positions through generated clients and real TLS."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs

import httpx2

from tests.data.python.client_pagination import Harness, adrained, afetched, drained, fetched
from tests.data.python.client_runtime import Exchange, raw_response, record, arecord, run

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from types import ModuleType


_TOTALS = (
    b"1",
    b"1.0",
    b"1e0",
    b"0.0",
    b"-0.0",
    b"-1.0",
    b"true",
    b"1.5",
    b"null",
    b'"1.0"',
    b"9007199254740993.0",
    b"9007199254740993.00000000000000000001",
)


def _total_response(token: bytes, member: str) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return independently spelled JSON bytes so decimal and exponent notation survive the HTTP exchange."""
    return raw_response(200, b'{"data":[{"id":"1"}],"' + member.encode() + b'":' + token + b"}", "application/json")


def _value_calls(harness: Harness, api: Any) -> Iterator[tuple[str, Any, dict[str, object]]]:
    """Yield initial integer and number arguments built from the review's Decimal and exact boundary values."""
    for name, value in (
        ("offset", Decimal("40.0")),
        ("position", Decimal("40.0")),
        ("position", 40.0),
        ("position", 40.5),
        ("position", Decimal("9007199254740993.0")),
    ):
        argument = harness.argument("listUsers", "query", name, value)
        helper = api.protocols.users.offsets if name == "offset" else api.protocols.users.positions
        yield f"{name} {value!r}", helper, {name: argument}


def pagination_count_values(package: ModuleType, lines: list[str]) -> None:
    """Keep exact numeric totals, reject the same invalid values, and accept integral Decimal starting positions."""
    harness = Harness(package)
    exchange = Exchange(lines)
    with exchange.client() as native, package.Client(http_client=native) as api:
        for token in (b"1.0", b"1e0"):
            exchange.respond(_total_response(token, "total"), _total_response(token, "total"))
            record(lines, f"ordinary total {token!r}", api.users.list_users)
            fetched(lines, f"offset total {token!r}", api.protocols.users.items.page)
        start = harness.argument("listUsers", "query", "offset", 9007199254740991)
        exchange.respond(
            _total_response(b"9007199254740993.0", "total"), _total_response(b"9007199254740993.0", "total")
        )
        drained(lines, "exact large total boundary", api.protocols.users.items.iterate(offset=start))
        for token in _TOTALS:
            exchange.respond(_total_response(token, "count"))
            fetched(lines, f"total {token!r}", api.protocols.users.counted.page)
        for label, helper, arguments in _value_calls(harness, api):
            if "40.5" not in label:
                exchange.respond(_answer("query", True))
            first = fetched(lines, label, lambda: helper.page(**arguments))
            if first is not None:
                exchange.respond(_answer("query", False))
                fetched(lines, f"{label} next", lambda: helper.next_page(first))
    run(lambda: _async_values(harness, lines))


async def _async_values(harness: Harness, lines: list[str]) -> None:
    """Repeat the numeric HTTP exchanges through the async client."""
    exchange = Exchange(lines)
    async with exchange.async_client() as native, harness.package.AsyncClient(http_client=native) as api:
        for token in (b"1.0", b"1e0"):
            exchange.respond(_total_response(token, "total"), _total_response(token, "total"))
            await arecord(lines, f"async ordinary total {token!r}", api.users.list_users)
            await afetched(lines, f"async offset total {token!r}", api.protocols.users.items.page)
        start = harness.argument("listUsers", "query", "offset", 9007199254740991)
        exchange.respond(
            _total_response(b"9007199254740993.0", "total"), _total_response(b"9007199254740993.0", "total")
        )
        await adrained(lines, "async exact large total boundary", api.protocols.users.items.iterate(offset=start))
        for token in _TOTALS:
            exchange.respond(_total_response(token, "count"))
            await afetched(lines, f"async total {token!r}", api.protocols.users.counted.page)
        for label, helper, arguments in _value_calls(harness, api):
            if "40.5" not in label:
                exchange.respond(_answer("query", True))
            first = await afetched(lines, f"async {label}", lambda: helper.page(**arguments))
            if first is not None:
                exchange.respond(_answer("query", False))
                await afetched(lines, f"async {label} next", lambda: helper.next_page(first))


def _answer(location: str, more: bool) -> Callable[[httpx2.Request], httpx2.Response]:
    """Answer with item ids computed from the position that actually arrived at the TLS server."""

    def answer(request: httpx2.Request) -> httpx2.Response:
        query = parse_qs(request.url.query.decode())
        position = (
            request.headers.get("X-Page", "0")
            if location == "header"
            else query.get("offset", query.get("position", ["0"]))[0]
        )
        start = int(Decimal(position))
        headers = {"X-Has-More": "true" if more else "false"} if location == "header" else {}
        return httpx2.Response(
            200,
            headers=headers,
            json={
                "data": [{"id": str(start + 1)}, {"id": str(start + 2)}],
                "has_more": more,
            },
        )

    return answer


def _default_calls(harness: Harness, api: Any) -> Iterator[tuple[str, str, Any, dict[str, object], bool]]:
    """Yield both targets with client, nested view, typed argument, call options, UNSET, and removal precedence."""
    options = harness.options
    for location, name, keyword, helper_name, first in (
        ("query", "offset", "offset", "offsets", 40),
        ("header", "X-Page", "x_page", "by_header", 5),
    ):
        field = "query" if location == "query" else "headers"
        helper = getattr(api.protocols.users, helper_name)
        view = api.with_options(options.RequestOptions(**{field: ((name.lower(), str(first + 10)),)}))
        nested = view.with_options(options.RequestOptions(**{field: ((name, str(first + 20)),)}))
        viewed, layered = (getattr(client.protocols.users, helper_name) for client in (view, nested))
        typed = harness.argument("listUsers", location, name, first + 30)
        call = options.RequestOptions(**{field: ((name, str(first + 40)),)})
        removed = options.RequestOptions(**{field: ((name, None),)})
        cleared = api.with_options(removed)
        for label, selected, arguments, sends in (
            ("client", helper, {}, True),
            ("view", viewed, {}, True),
            ("nested view", layered, {}, True),
            ("typed over views", layered, {keyword: typed}, True),
            ("UNSET over view", viewed, {keyword: options.UNSET}, True),
            ("rejected call over typed", layered, {keyword: typed, "options": call}, False),
            ("rejected call removes typed", layered, {keyword: typed, "options": removed}, False),
            ("view removes default", getattr(cleared.protocols.users, helper_name), {}, True),
            ("empty call options", helper, {"options": options.RequestOptions()}, True),
        ):
            yield f"{location} {label}", location, selected, arguments, sends
        for text in ("forty", "40.5", "40.00000000000000000001", "NaN", "Infinity"):
            invalid = api.with_options(options.RequestOptions(**{field: ((name, text),)}))
            yield f"{location} invalid {text}", location, getattr(invalid.protocols.users, helper_name), {}, False
        duplicate = api.with_options(options.RequestOptions(**{field: ((name, "40"), (name, "41"))}))
        yield f"{location} duplicate", location, getattr(duplicate.protocols.users, helper_name), {}, False


def pagination_count_defaults(package: ModuleType, lines: list[str]) -> None:
    """Continue from the effective first request without changing how the options layers choose that request."""
    harness = Harness(package)
    defaults = harness.options.ClientOptions(query=(("offset", "40"),), headers=(("X-Page", "5"),))
    exchange = Exchange(lines)
    with exchange.client() as native, package.Client(http_client=native, options=defaults) as api:
        for label, location, helper, arguments, sends in _default_calls(harness, api):
            if sends:
                exchange.respond(_answer(location, True))
            first = fetched(lines, label, lambda: helper.page(**arguments))
            if first is not None:
                exchange.respond(_answer(location, False))
                fetched(lines, f"{label} next", lambda: helper.next_page(first))
        exchange.respond(_answer("query", True), _answer("query", False))
        drained(lines, "client default offset items", api.protocols.users.offsets.iterate())
    run(lambda: _async_defaults(harness, defaults, lines))


async def _async_defaults(harness: Harness, defaults: Any, lines: list[str]) -> None:
    """Repeat all options layers through asyncio, including the review's first 40 then 42 request sequence."""
    exchange = Exchange(lines)
    async with (
        exchange.async_client() as native,
        harness.package.AsyncClient(http_client=native, options=defaults) as api,
    ):
        for label, location, helper, arguments, sends in _default_calls(harness, api):
            if sends:
                exchange.respond(_answer(location, True))
            first = await afetched(lines, f"async {label}", lambda: helper.page(**arguments))
            if first is not None:
                exchange.respond(_answer(location, False))
                await afetched(lines, f"async {label} next", lambda: helper.next_page(first))
        exchange.respond(_answer("query", True), _answer("query", False))
        await adrained(lines, "async client default offset items", api.protocols.users.offsets.iterate())
