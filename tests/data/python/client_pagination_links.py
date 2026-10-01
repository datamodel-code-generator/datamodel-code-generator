"""Follow next URLs and RFC 8288 Link headers: resolution, refusals, origins, credentials, bodies, and cycles."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.data.python.client_pagination import Harness, adrained, afetched, drained, fetched
from tests.data.python.client_runtime import Exchange, json_response, run

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
    ("other host", f"{_OTHER}/v1/users"),
    ("plain HTTP", "http://api.example.com/v1/users"),
    ("other port", "https://api.example.com:8443/v1/users"),
    ("oversized", f"/v1/users?q={'a' * 8200}"),
)


def _page(*ids: str, **members: object) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a responder of one page of users with the given members."""
    return json_response(200, {"data": [{"id": value} for value in ids], **members})


def _headed(*ids: str, headers: tuple[tuple[str, str], ...], **members: object) -> Callable[[httpx2.Request], Any]:
    """Return a responder of one page of users with repeatable response headers."""
    payload = {"data": [{"id": value} for value in ids], **members}
    return lambda _: httpx2.Response(200, headers=list(headers), json=payload)


def _links(*values: str, ids: tuple[str, ...] = ("1",)) -> Callable[[httpx2.Request], Any]:
    """Return a responder of one page of users with one Link field per value."""
    return _headed(*ids, headers=tuple(("Link", value) for value in values))


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
    _origins(harness, exchange, lines)
    _credentials(harness, exchange, lines)
    _redirects(harness, exchange, lines)
    run(lambda: _async_links(harness, lines))


def _urls(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Send each later page to the URL the last page gave, resolved against the URL that returned it.

    The caller's query and its query patches are never laid over the URL; its headers and cookies stay.
    """
    users = api.protocols.users
    arguments = {
        "limit": harness.argument("users", "ListUsers", "query", "limit", 1),
        "x_trace": harness.argument("users", "ListUsers", "header", "X-Trace", "t"),
        "session": harness.argument("users", "ListUsers", "cookie", "session", "s"),
    }
    patched = harness.options.RequestOptions(headers=(("X-Client", "c"),), query=(("debug", "1"),))
    exchange.respond(
        _page("1", next=f"{_SERVER}/users?limit=2&cursor=b%2Fc"),
        _page(next="?cursor=c"),
        _page("2", next="../v1/./users/../users?cursor=d"),
        _page("3", next="//api.example.com/v1/users?cursor=e"),
        _page("4"),
    )
    drained(lines, "followed URLs", users.follow.iterate(**arguments, options=patched))
    exchange.respond(
        _headed("1", headers=(("X-Next", "/v1/users?cursor=h"),)),
        _page("2"),
    )
    drained(lines, "header URL", users.headed.iterate())
    for label, members in (("null URL", {"maybe_next": None}), ("empty URL", {"maybe_next": ""})):
        exchange.respond(_page("1", **members))
        drained(lines, label, users.nullable.iterate())
    exchange.respond(_page("1"))
    drained(lines, "missing URL without its end", users.nullable.iterate())
    for label, value in (("integer URL", 7), ("object URL", {"href": "/v1/users"})):
        exchange.respond(_page("1", loose_next=value))
        drained(lines, label, users.loose.iterate())


def _refusals(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Refuse a URL with a fragment, user information, another scheme or origin, bad syntax, or too many bytes."""
    helper = api.protocols.users.follow
    for label, value in _URL_REFUSALS:
        exchange.respond(_page("1", next=value))
        drained(lines, label, helper.iterate())
    limits = harness.protocols.PaginationOptions(max_cursor_bytes=24)
    exchange.respond(_page("1", next="/v1/users?cursor=0123456789"))
    drained(lines, "URL over the cursor size", helper.iterate(pagination_options=limits))


def _vectors(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Read Link fields strictly: relation lists and case, quoted strings, parameters, and malformed fields."""
    users = api.protocols.users
    for label, values in _LINK_VECTORS:
        exchange.respond(_links(*values), _page("2"))
        drained(lines, label, users.linked.iterate())
        exchange.responders.clear()
    exchange.respond(_links('<?page=2>; rel="HTTPS://EXAMPLE.COM/REL/MORE"'), _page("2"))
    drained(lines, "extension relation", users.related.iterate())
    limits = harness.protocols.PaginationOptions(max_cursor_bytes=48)
    exchange.respond(_links("<?page=2>; rel=next", "<?page=9>; rel=last; title=long"))
    drained(lines, "Link fields over the cursor size", users.linked.iterate(pagination_options=limits))
    trace = harness.argument("users", "ListUsers", "header", "X-Trace", "mine")
    exchange.respond(
        _headed("1", headers=(("Link", "<?page=2>; rel=next"), ("X-Snapshot", "s1"))),
        _headed("2", headers=(("Link", "<?page=3>; rel=next"), ("X-Snapshot", "s2"))),
        _page("3"),
    )
    drained(lines, "snapshot kept from the first page", users.snapshot.iterate(x_trace=trace))


def _search(harness: Harness, wire: object) -> object:
    """Return the search request body of a wire value, as its body codec builds it."""
    types = importlib.import_module(f"{harness.package.__name__}.types.searches")
    return types.SearchRequestCodecs.body().from_wire(wire)


def _bodies(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Repeat the caller's body with the operation's method only when asked to, and GET the URL otherwise."""
    searches = api.protocols.searches
    body = _search(harness, {"query": "a"})
    exchange.respond(_page("1", next="?page=2", token="t1"), _page("2", next="?page=3", token="t2"), _page("3"))
    drained(lines, "repeated body", searches.repeated.iterate(body=body))
    exchange.respond(_page("1", next="?page=2"), _page("2"))
    drained(lines, "fetched without the body", searches.fetched.iterate(body=body))


def _cycles(api: Any, exchange: Exchange, lines: list[str]) -> None:
    """End with PaginationCycleError at a URL an earlier page gave, however the server spelled it."""
    helper = api.protocols.users.follow
    exchange.respond(_page("1", next="?cursor=a"), _page("2", next="?cursor=b"), _page("3", next="?cursor=a"))
    drained(lines, "repeated URL", helper.iterate())
    exchange.respond(_page("1", next="?cursor=a"), _page("2", next=f"{_SERVER}/users?cursor=a"))
    drained(lines, "repeated URL spelled absolutely", helper.iterate())


def _pages(harness: Harness, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Fetch followed pages one call at a time, refusing a URL of a server the call no longer selects."""
    helper = api.protocols.users.headed
    exchange.respond(_headed("1", headers=(("X-Next", "?cursor=2"),)), _page("2"))
    first = fetched(lines, "first followed page", helper.page)
    second = fetched(lines, "next followed page", lambda: helper.next_page(first))
    fetched(lines, "after the last followed page", lambda: helper.next_page(second))
    moved = harness.options.RequestOptions(base_url=f"{_OTHER}/v1")
    fetched(lines, "next followed page at another server", lambda: helper.next_page(first, options=moved))


def _secured(harness: Harness, *origins: Any, **settings: Any) -> Any:
    """Return client options whose protocol security context allows the origins, with any other settings."""
    protocols = harness.protocols
    context = protocols.ProtocolSecurityContext(credential_partition="tenant", allowed_origins=origins)
    return harness.options.ClientOptions(protocols=harness.options.ProtocolClientOptions(security=context), **settings)


def _origins(harness: Harness, exchange: Exchange, lines: list[str]) -> None:
    """Follow a URL to an allowed origin without the server's credential and cookie headers, and back with them."""
    other = harness.protocols.Origin(scheme="https", host="other.example.com", port=443)
    patched = harness.options.RequestOptions(headers=(("Authorization", "Basic c2VjcmV0"),))
    arguments = {
        "x_trace": harness.argument("users", "ListUsers", "header", "X-Trace", "t"),
        "session": harness.argument("users", "ListUsers", "cookie", "session", "s"),
        "options": patched,
    }
    with exchange.client() as native, harness.package.Client(http_client=native, options=_secured(harness, other)) as api:
        helper = api.protocols.users.follow
        exchange.respond(
            _page("1", next=f"{_OTHER}/v1/users?cursor=2"),
            _page("2", next="?cursor=3"),
            _page("3", next=f"{_SERVER}/users?cursor=4"),
            _page("4", next="https://third.example.com/v1/users"),
        )
        drained(lines, "allowed origin and back", helper.iterate(**arguments))


def _credentials(harness: Harness, exchange: Exchange, lines: list[str]) -> None:
    """Authenticate only at origins the auth allows, and place a query key once on a URL that repeats it."""
    auth = importlib.import_module(f"{harness.package.__name__}.auth")
    other = harness.protocols.Origin(scheme="https", host="other.example.com", port=443)
    token = auth.StaticTokenProvider(auth.AccessToken("token"))
    for label, config in (
        ("same origin bearer", auth.AuthConfig({"bearer": token})),
        ("bearer the auth keeps from another origin", auth.AuthConfig({"bearer": token})),
        (
            "bearer the auth allows at another origin",
            auth.AuthConfig({"bearer": token}, allowed_origins=("https://api.example.com", _OTHER)),
        ),
    ):
        target = "?page=2" if label.startswith("same") else f"{_OTHER}/secure/users?page=2"
        settings = _secured(harness, other, auth=config)
        with exchange.client() as native, harness.package.Client(http_client=native, options=settings) as api:
            exchange.respond(_links(f"<{target}>; rel=next"), _page("2"))
            drained(lines, label, api.protocols.secure.users.iterate())
            exchange.responders.clear()
    keyed = auth.AuthConfig({"query_key": auth.StaticCredentialProvider(auth.ApiKeyCredential("key"))})
    settings = harness.options.ClientOptions(auth=keyed)
    with exchange.client() as native, harness.package.Client(http_client=native, options=settings) as api:
        exchange.respond(_page("1", next="/v1/keyed/users?api_key=key&cursor=2"), _page("2"))
        drained(lines, "query key repeated by the URL", api.protocols.keyed.users.iterate())


def _redirects(harness: Harness, exchange: Exchange, lines: list[str]) -> None:
    """Resolve a relative URL against the hop a redirect reached, not the URL first requested."""
    options = harness.options
    settings = options.ClientOptions(redirects=options.RedirectOptions(enabled=True))
    with exchange.client() as native, harness.package.Client(http_client=native, options=settings) as api:
        exchange.respond(
            lambda _: httpx2.Response(302, headers={"Location": "/v2/people"}),
            _page("1", next="?cursor=2"),
            _page("2"),
        )
        drained(lines, "relative URL after a redirect", api.protocols.users.follow.iterate())


async def _async_links(harness: Harness, lines: list[str]) -> None:
    """Follow next URLs and Link headers with asyncio, in items and in single pages."""
    package = harness.package
    exchange = Exchange(lines)
    async with exchange.async_client() as native, package.AsyncClient(http_client=native) as api:
        users = api.protocols.users
        exchange.respond(_page("1", next="?cursor=2"), _page(next="/v1/users?cursor=3"), _page("2"))
        await adrained(lines, "async followed URLs", users.follow.iterate())
        exchange.respond(_links("<?page=2>; rel=next"), _links("<?page=2>; rel=next", ids=("2",)))
        await adrained(lines, "async repeated link", users.linked.iterate())
        exchange.respond(_links("<?page=2>; rel=next"), _page("2"))
        first = await afetched(lines, "async first linked page", users.linked.page)
        second = await afetched(lines, "async next linked page", lambda: users.linked.next_page(first))
        await afetched(lines, "async after the last linked page", lambda: users.linked.next_page(second))
