"""Follow next URLs and RFC 8288 Link headers: resolution, refusals, origins, credentials, bodies, and cycles."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.data.python.client_pagination import (
    Harness,
    adrained,
    afetched,
    drained,
    fetched,
    headed_page,
    user_page,
)
from tests.data.python.client_runtime import Exchange, arecord, record, request_body, run

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_SERVER: Final = "https://api.example.com/v1"
_OTHER: Final = "https://other.example.com"
_LINK_VECTORS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("absolute link", (f'<{_SERVER}/users?page=2>; rel="next"',)),
    ("token relation", ("</v1/users?page=2>; rel=next",)),
    ("relative link", ("<?page=2>; rel=next",)),
    ("relation list", ('<?page=9>; rel="last", <?page=2>; rel="prev next"',)),
    ("relation and parameter case", ('<?page=2>; REL="NEXT"',)),
    ("quoted comma and semicolon", ('<?page=2>; title="a, b; c"; rel="next", <?page=9>; rel="last"',)),
    ("escaped quote", ('<?page=2>; title="say \\"hi\\""; rel=next',)),
    ("separate fields", ('<?page=1>; rel="first"', '<?page=2>; rel="next"')),
    ("empty list elements", (" , <?page=2>; rel=next ,, ",)),
    ("whitespace around parameters", ('<?page=2> ;rel = "next"',)),
    ("first relation parameter only", ("<?page=2>; rel=last; rel=next",)),
    ("anchored link", ('<?page=8>; rel=next; anchor="#other", <?page=2>; rel=next',)),
    ("relation without value", ("<?page=2>; rel",)),
    ("no next link", ("<?page=1>; rel=prev",)),
    ("empty field", ("",)),
    ("two next links", ("<?page=2>; rel=next, <?page=3>; rel=next",)),
    ("two next fields", ("<?page=2>; rel=next", '<?page=3>; rel="next"')),
    ("unbracketed target", ("?page=2; rel=next",)),
    ("unterminated quote", ('<?page=2>; rel="next',)),
    ("missing comma", ("<?page=2>; rel=next <?page=3>; rel=last",)),
    ("parameter without name", ("<?page=2>; =next",)),
    ("space in target", ("</v1/users?page= 2>; rel=next",)),
    ("fragment in target", ("<?page=2#top>; rel=next",)),
    ("other origin", (f"<{_OTHER}/users?page=2>; rel=next",)),
)
_URL_REFUSALS: Final[tuple[tuple[str, object], ...]] = (
    ("fragment", f"{_SERVER}/users?cursor=2#top"),
    ("user information", "https://user:secret@api.example.com/v1/users"),
    ("empty user information", "//@api.example.com/v1/users"),
    ("ftp scheme", "ftp://api.example.com/v1/users"),
    ("mailto scheme", "mailto:users@example.com"),
    ("space", "/v1/users?q=a b"),
    ("non-ASCII", "/v1/üsers"),
    ("bad escape", "/v1/users?q=%zz"),
    ("bad port", "https://api.example.com:99999/v1/users"),
    ("bracket outside a host", "/v1/users?q=[1]"),
    ("IPv6 host", "https://[::1]/v1/users"),
    ("other host", f"{_OTHER}/v1/users"),
    ("plain HTTP", "http://api.example.com/v1/users"),
    ("other port", "https://api.example.com:8443/v1/users"),
)


def _links(*values: str, ids: tuple[str, ...] = ("1",)) -> Callable[[httpx2.Request], Any]:
    """Return a responder of one page of users with one Link field per value."""
    return headed_page(*ids, headers=tuple(("Link", value) for value in values))


def pagination_links(package: ModuleType, lines: list[str]) -> None:
    """Follow next URLs and Link headers through the synchronous and asyncio clients."""
    harness = Harness(package)
    exchange = Exchange(lines)
    with exchange.client() as native, package.Client(http_client=native) as api:
        _urls(harness, api, exchange, lines)
        _refusals(harness, api, exchange, lines)
        _vectors(harness, api, exchange, lines)
        _bodies(harness, api, exchange, lines)
        _cycles(api, exchange, lines)
        _pages(harness, api, exchange, lines)
    _guards(harness, exchange, lines)
    run(lambda: _async_links(harness, lines))


def _urls(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Send each later page to the URL the last page gave, resolved against the URL that returned it.

    The caller's query and its query patches are never laid over the URL; its headers and cookies stay.
    """
    users = api.protocols.users
    arguments = {
        "limit": harness.argument("listUsers", "query", "limit", 1),
        "x_trace": harness.argument("listUsers", "header", "X-Trace", "t"),
        "session": harness.argument("listUsers", "cookie", "session", "s"),
    }
    patched = harness.options.RequestOptions(extra_headers={"X-Client": "c"}, extra_query={"debug": "1"})
    exchange.respond(
        user_page("1", next=f"{_SERVER}/users?limit=2&cursor=b%2Fc"),
        user_page(next="?cursor=c"),
        user_page("2", next="../v1/./users/../users?cursor=d"),
        user_page("3", next="//api.example.com/v1/users?cursor=e"),
        user_page("4"),
    )
    drained(lines, "followed URLs", users.follow.iterate(**arguments, options=patched))
    exchange.respond(
        headed_page("1", headers=(("X-Next", "/v1/users?cursor=h"),)),
        user_page("2"),
    )
    drained(lines, "header URL", users.headed.iterate())
    for label, members in (("null URL", {"maybe_next": None}), ("empty URL", {"maybe_next": ""})):
        exchange.respond(user_page("1", **members))
        drained(lines, label, users.nullable.iterate())
    exchange.respond(user_page("1"))
    drained(lines, "missing URL without its end", users.nullable.iterate())
    for label, value in (("integer URL", 7), ("object URL", {"href": "/v1/users"})):
        exchange.respond(user_page("1", loose_next=value))
        drained(lines, label, users.loose.iterate())


def _refusals(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Refuse a URL with a fragment, user information, another scheme or origin, bad syntax, or too many bytes."""
    helper = api.protocols.users.follow
    for label, value in _URL_REFUSALS:
        exchange.respond(user_page("1", next=value))
        drained(lines, label, helper.iterate())


def _vectors(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Read Link fields strictly: relation lists and case, quoted strings, parameters, and malformed fields."""
    users = api.protocols.users
    for label, values in _LINK_VECTORS:
        exchange.respond(_links(*values), user_page("2"))
        drained(lines, label, users.linked.iterate())
        exchange.responders.clear()
    exchange.respond(_links('<?page=2>; rel="HTTPS://EXAMPLE.COM/REL/MORE"'), user_page("2"))
    drained(lines, "extension relation", users.related.iterate())
    trace = harness.argument("listUsers", "header", "X-Trace", "mine")
    exchange.respond(
        headed_page("1", headers=(("Link", "<?page=2>; rel=next"), ("X-Snapshot", "s1"))),
        headed_page("2", headers=(("Link", "<?page=3>; rel=next"), ("X-Snapshot", "s2"))),
        user_page("3"),
    )
    drained(lines, "snapshot kept from the first page", users.snapshot.iterate(x_trace=trace))


def _search(harness: Harness, wire: object) -> object:
    """Return the search request body of a wire value, as its body codec builds it."""
    return request_body(harness.package, "search", None, wire)


def _bodies(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Repeat the caller's body with the operation's method only when asked to, and GET the URL otherwise."""
    searches = api.protocols.searches
    body = _search(harness, {"query": "a"})
    exchange.respond(
        user_page("1", next="?page=2", token="t1"), user_page("2", next="?page=3", token="t2"), user_page("3")
    )
    drained(lines, "repeated body", searches.repeated.iterate(body=body))
    exchange.respond(user_page("2", next="?page=3", token="t2"), user_page("3"))
    drained(lines, "resumed with a body binding", searches.repeated.resume(f"{_SERVER}/searches?page=2", body=body))
    exchange.respond(user_page("1", next="?page=2"), user_page("2"))
    drained(lines, "fetched without the body", searches.fetched.iterate(body=body))


def _cycles(api: Any, exchange: Exchange, lines: list[str]) -> None:
    """End with a pagination cycle at a URL an earlier page gave, however the server spelled it."""
    helper = api.protocols.users.follow
    exchange.respond(
        user_page("1", next="?cursor=a"), user_page("2", next="?cursor=b"), user_page("3", next="?cursor=a")
    )
    drained(lines, "repeated URL", helper.iterate())
    exchange.respond(user_page("1", next="?cursor=a"), user_page("2", next=f"{_SERVER}/users?cursor=a"))
    drained(lines, "repeated URL spelled absolutely", helper.iterate())
    for label, value in (("own URL", f"{_SERVER}/users"), ("empty reference", "")):
        exchange.respond(user_page("1", next=value))
        drained(lines, label, helper.iterate())


def _pages(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Fetch followed pages one call at a time, refusing a URL of a server the call no longer selects."""
    helper = api.protocols.users.headed
    exchange.respond(headed_page("1", headers=(("X-Next", "?cursor=2"),)), user_page("2"))
    first = fetched(lines, "first followed page", helper.page)
    second = fetched(lines, "next followed page", lambda: helper.next_page(first))
    fetched(lines, "after the last followed page", lambda: helper.next_page(second))
    moved = harness.options.RequestOptions(base_url=f"{_OTHER}/v1")
    fetched(lines, "next followed page at another server", lambda: helper.next_page(first, options=moved))


def _allowed(*origins: Any, **settings: Any) -> dict[str, Any]:
    """Return client keywords that allow the origins beyond the server's, with any other settings."""
    return {"allowed_origins": origins, **settings}


def _guarded(harness: Harness) -> tuple[tuple[str, Any, tuple[Any, ...], Any, dict[str, str]], ...]:
    """Return rows of client keywords, responders, the start of a traversal or an ordinary call, and credentials.

    They follow URLs to allowed origins and back, without the server's credentials, cookies, or the positions of the
    package's security schemes at any origin, authenticate only at the server's origin, and redirect.
    """
    options = harness.options
    other = harness.protocols.Origin(scheme="https", host="other.example.com", port=443)
    bearer = {"bearer": "token"}
    arguments = {
        "x_trace": harness.argument("listUsers", "header", "X-Trace", "t"),
        "session": harness.argument("listUsers", "cookie", "session", "s"),
        "options": options.RequestOptions(extra_headers={"Authorization": "Basic c2VjcmV0"}),
    }
    patched = {"X-Api-Key": "k", "Authorization": "Basic c2VjcmV0"}
    secure = f"<{_OTHER}/secure/users?page=2>; rel=next"
    return (
        (
            "allowed origin and back",
            _allowed(other),
            (
                user_page("1", next=f"{_OTHER}/v1/users?cursor=2"),
                user_page("2", next="?cursor=3"),
                user_page("3", next=f"{_SERVER}/users?cursor=4"),
                user_page("4", next="https://third.example.com/v1/users"),
            ),
            lambda api: api.protocols.users.follow.iterate(**arguments),
            {},
        ),
        (
            "scheme credentials kept from another origin",
            _allowed(other, default_headers=patched),
            (
                user_page("1", next=f"{_OTHER}/v1/users?api_key=k&cursor=2"),
                user_page("2", next=f"{_SERVER}/users?api_key=k&cursor=3"),
                user_page("3"),
            ),
            lambda api: api.protocols.users.follow.iterate(),
            {},
        ),
        (
            "same origin bearer",
            _allowed(other),
            (_links("<?page=2>; rel=next"), user_page("2")),
            lambda api: api.protocols.secure.users.iterate(),
            bearer,
        ),
        (
            "bearer kept from another origin",
            _allowed(other),
            (_links(secure), user_page("2")),
            lambda api: api.protocols.secure.users.iterate(),
            bearer,
        ),
        (
            "other scheme's query key at the same origin",
            _allowed(other),
            (_links("<?api_key=leak&page=2>; rel=next"), user_page("2")),
            lambda api: api.protocols.secure.users.iterate(),
            bearer,
        ),
        (
            "scheme query key without auth at the same origin",
            {},
            (user_page("1", next="/v1/users?cursor=2&api_key=leak"), user_page("2")),
            lambda api: api.protocols.users.follow.iterate(),
            {},
        ),
        (
            "query key repeated by the URL",
            {},
            (user_page("1", next="/v1/keyed/users?api_key=key&cursor=2"), user_page("2")),
            lambda api: api.protocols.keyed.users.iterate(),
            {"query_key": "key"},
        ),
        (
            "relative URL after a redirect",
            {"follow_redirects": True},
            (
                lambda _: httpx2.Response(302, headers={"Location": "/v2/people"}),
                user_page("1", next="?cursor=2"),
                user_page("2"),
            ),
            lambda api: api.protocols.users.follow.iterate(),
            {},
        ),
        (
            "a scheme key header is never redirected",
            {"default_headers": patched, "follow_redirects": True},
            (
                lambda _: httpx2.Response(302, headers={"Location": f"{_OTHER}/v1/users?api_key=k&page=1"}),
                user_page("1"),
            ),
            lambda api: api.users.with_response.list_users,
            {},
        ),
    )


def _guards(harness: Harness, exchange: Exchange, lines: list[str]) -> None:
    """Run the guarded rows synchronously."""
    for label, settings, responders, start, credentials in _guarded(harness):
        with (
            exchange.client() as native,
            harness.package.Client(http_client=native, **settings, **credentials) as api,
        ):
            exchange.respond(*responders)
            started = start(api)
            if callable(started):
                record(lines, label, lambda started=started: started().info.status_code)
            else:
                drained(lines, label, started)
            exchange.responders.clear()


async def _aguards(harness: Harness, exchange: Exchange, lines: list[str]) -> None:
    """Run the guarded rows with asyncio."""
    for label, settings, responders, start, credentials in _guarded(harness):
        async with (
            exchange.async_client() as native,
            harness.package.AsyncClient(http_client=native, **settings, **credentials) as api,
        ):
            exchange.respond(*responders)
            started = start(api)
            if callable(started):

                async def status(started: Any = started) -> int:
                    return (await started()).info.status_code

                await arecord(lines, f"async {label}", status)
            else:
                await adrained(lines, f"async {label}", started)
            exchange.responders.clear()


async def _async_links(harness: Harness, lines: list[str]) -> None:
    """Follow next URLs and Link headers with asyncio, in items and in single pages."""
    package = harness.package
    exchange = Exchange(lines)
    async with exchange.async_client() as native, package.AsyncClient(http_client=native) as api:
        users = api.protocols.users
        exchange.respond(user_page("1", next="?cursor=2"), user_page(next="/v1/users?cursor=3"), user_page("2"))
        await adrained(lines, "async followed URLs", users.follow.iterate())
        exchange.respond(_links("<?page=2>; rel=next"), _links("<?page=2>; rel=next", ids=("2",)))
        await adrained(lines, "async repeated link", users.linked.iterate())
        exchange.respond(_links("<?page=2>; rel=next"), user_page("2"))
        first = await afetched(lines, "async first linked page", users.linked.page)
        second = await afetched(lines, "async next linked page", lambda: users.linked.next_page(first))
        await afetched(lines, "async after the last linked page", lambda: users.linked.next_page(second))
    await _aguards(harness, exchange, lines)
