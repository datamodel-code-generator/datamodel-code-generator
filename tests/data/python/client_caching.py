"""Fetch through generated cache helpers: freshness, revalidation, Vary, credentials, and stores."""

from __future__ import annotations

import importlib
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Final

from tests.data.python.client_pagination import item_id
from tests.data.python.client_runtime import Exchange, argument, describe, json_response, raw_response, record, run

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    import httpx2

_PAST: Final = "Sun, 06 Nov 1994 08:49:37 GMT"
_FUTURE: Final = "Fri, 01 Jan 2100 00:00:00 GMT"
_LATER: Final = "Fri, 01 Jan 2100 01:00:00 GMT"
_MODIFIED: Final = "Wed, 21 Oct 2015 07:28:00 GMT"
_EPOCH: Final = datetime(2020, 1, 1, tzinfo=timezone.utc)
_WALL: Final = 1_000_000_000.0
_WALL_DATE: Final = "Sun, 09 Sep 2001 01:46:40 GMT"


def user(
    identifier: int, name: str = "cat", status: int = 200, **headers: str
) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a responder of one user with the given headers."""
    return json_response(status, {"id": identifier, "name": name}, **headers)


def not_modified(**headers: str) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a responder of a 304 with the given headers and no body."""
    return raw_response(304, **headers)


def _data(value: object) -> str:
    if isinstance(value, dict) and "data" in value:
        return f"[{','.join(item_id(item) for item in value['data'])}]"
    if hasattr(value, "data"):
        return f"[{','.join(item_id(item) for item in value.data)}]"
    return f"{item_id(value)}:{value['name'] if isinstance(value, dict) else value.name}"


def shown(result: Any) -> str:
    """Summarize a cache result: its source, data, statuses, the counters of its response, and its headers."""
    info = result.response
    return (
        f"{result.source} {_data(result.data)} status={info.status_code} network={result.network_status} "
        f"attempts={info.attempt_count} type={info.content_type} "
        f"request_id={info.request_id} headers={list(info.headers)}"
    )


def fetched(lines: list[str], label: str, call: Callable[[], Any]) -> Any:
    """Report one cache result, or the failure of the fetch."""
    try:
        result = call()
    except Exception as error:  # noqa: BLE001
        lines.append(f"  {label} ! {describe(error)}")
        return None
    lines.append(f"  {label} = {shown(result)}")
    return result


async def afetched(lines: list[str], label: str, call: Callable[[], Any]) -> Any:
    """Report one cache result of an asyncio fetch, or its failure."""
    try:
        result = await call()
    except Exception as error:  # noqa: BLE001
        lines.append(f"  {label} ! {describe(error)}")
        return None
    lines.append(f"  {label} = {shown(result)}")
    return result


def _entry(entry: Any) -> str:
    """Summarize a cache entry without its body, header values, or Vary values."""
    return (
        f"status={entry.status_code} vary={entry.vary} values={len(entry.vary_values)} "
        f"bytes={len(entry.body)} headers={[name for name, _ in entry.headers]} "
        f"fresh={entry.freshness_seconds > 0}"
    )


class Recording:
    """A cache store that records each call safely before passing it to a memory store, or fails as told."""

    def __init__(self, inner: Any, lines: list[str]) -> None:
        """Wrap a memory store; `faults` maps a method name to what it raises or returns instead."""
        self.inner = inner
        self.lines = lines
        self.faults: dict[str, object] = {}
        self.last: Any = None

    def _fault(self, name: str) -> object:
        if (fault := self.faults.pop(name, None)) is not None and isinstance(fault, Exception):
            raise fault
        return fault

    def get(self, key: bytes) -> Any:
        """Record a lookup without disclosing the key."""
        self.lines.append(f"    get key={len(key)}")
        if (fault := self._fault("get")) is not None:
            return fault
        return self.inner.get(key)

    def set(self, key: bytes, entry: Any) -> Any:
        """Record a replacement by the entry's safe fields."""
        self.lines.append(f"    set {_entry(entry)}")
        self.last = entry
        if (fault := self._fault("set")) is not None:
            return fault
        return self.inner.set(key, entry)

    def delete(self, key: bytes) -> Any:
        """Record an idempotent deletion."""
        self.lines.append("    delete")
        if (fault := self._fault("delete")) is not None:
            return fault
        return self.inner.delete(key)


class AsyncRecording(Recording):
    """The asynchronous store of the same recordings and faults."""

    async def get(self, key: bytes) -> Any:  # ty: ignore[invalid-method-override]
        """Record and pass on a lookup."""
        return Recording.get(self, key)

    async def set(self, key: bytes, entry: Any) -> Any:  # ty: ignore[invalid-method-override]
        """Record and pass on a replacement."""
        return Recording.set(self, key, entry)

    async def delete(self, key: bytes) -> Any:  # ty: ignore[invalid-method-override]
        """Record and pass on a deletion."""
        return Recording.delete(self, key)


class Caching:
    """A generated caching package's public modules and the typed argument values its helpers take."""

    def __init__(self, package: ModuleType) -> None:
        """Import the modules the scenarios use."""
        self.package = package
        self.options, self.protocols, self.errors, self.auth = (
            importlib.import_module(f"{package.__name__}.{name}") for name in ("options", "protocols", "errors", "auth")
        )

    def user_id(self, value: int) -> object:
        """Return the userId argument of a wire value, as its request codec builds it."""
        return argument(self.package, "getUser", "path", "userId", value)

    def language(self, value: str) -> object:
        """Return the Accept-Language argument of a wire value."""
        return argument(self.package, "getUser", "header", "Accept-Language", value)

    def cart(self, value: str) -> object:
        """Return the cart cookie argument of a wire value."""
        return argument(self.package, "getCurrentCart", "cookie", "cart", value)

    def page(self, value: int) -> object:
        """Return the page argument of a listing."""
        return argument(self.package, "listUsers", "query", "page", value)

    def stores(self, **stores: object) -> object:
        """Return client options lending the stores by helper name, with dots spelled as double underscores."""
        named = {name.replace("__", "."): store for name, store in stores.items()}
        return self.options.ClientOptions(protocols=self.options.ProtocolClientOptions(cache_stores=named))

    def headers(self, **values: str) -> object:
        """Return request options patching headers, with underscores spelled as hyphens."""
        return self.options.RequestOptions(
            headers=tuple((name.replace("_", "-"), value) for name, value in values.items())
        )


def caching(package: ModuleType, lines: list[str]) -> None:
    """Fetch fresh, stale, and changed entries, with validators, Vary, directives, redirects, and invalidation."""
    cache = Caching(package)
    exchange = Exchange(lines)
    store = cache.protocols.MemoryCacheStore()
    options = cache.stores(users__profile=store, users__dated=cache.protocols.MemoryCacheStore(), users__listing=store)
    with exchange.client() as native, package.Client(http_client=native, options=options) as api:
        _fresh(cache, api, exchange, lines)
        _revalidated(cache, api, exchange, lines)
        _vary(cache, api, exchange, lines)
        _unstored(cache, api, exchange, lines)
        _directives(cache, api, exchange, lines)
        _validators(cache, api, exchange, lines)
    run(lambda: _async_caching(cache, lines))
    _clocked(cache, lines)


class _Clock:
    """A wall clock source that moves only when a scenario sets it."""

    def __init__(self) -> None:
        self.value = _WALL

    def __call__(self) -> float:
        return self.value


def _clocked_options(cache: Caching, clock: _Clock, store: object) -> object:
    """Return client options lending the profile helper a store, on a client whose wall clock is the given one."""
    options = cache.options
    stores = options.ProtocolClientOptions(cache_stores={"users.profile": store})
    return options.ClientOptions(protocols=stores, clock=options.Clock(time=clock))


_DATED: Final = {"cache-control": "max-age=60", "date": _WALL_DATE, "age": "30"}


def _clocked(cache: Caching, lines: list[str]) -> None:
    """Age a stored entry from its Date and Age on the client's wall clock, keeping it fresh until max-age."""
    exchange, clock = Exchange(lines), _Clock()
    lines.append("freshness on the client clock")
    with (
        exchange.client() as native,
        cache.package.Client(
            http_client=native, options=_clocked_options(cache, clock, cache.protocols.MemoryCacheStore())
        ) as api,
    ):
        helper, argument = api.protocols.users.profile, cache.user_id(50)
        exchange.respond(user(50, etag='"w"', **_DATED), not_modified(etag='"w"'))
        fetched(lines, "dated miss", lambda: helper.fetch(user_id=argument))
        clock.value += 29
        fetched(lines, "fresh until max-age less its age", lambda: helper.fetch(user_id=argument))
        clock.value += 2
        fetched(lines, "stale past max-age", lambda: helper.fetch(user_id=argument))
    run(lambda: _async_clocked(cache, lines))


async def _async_clocked(cache: Caching, lines: list[str]) -> None:
    """Age an asyncio fetch's stored entry on the client's wall clock."""
    exchange, clock = Exchange(lines), _Clock()
    async with (
        exchange.async_client() as native,
        cache.package.AsyncClient(
            http_client=native, options=_clocked_options(cache, clock, cache.protocols.AsyncMemoryCacheStore())
        ) as api,
    ):
        helper, argument = api.protocols.users.profile, cache.user_id(51)
        exchange.respond(user(51, etag='"x"', **_DATED), not_modified(etag='"x"'))
        await afetched(lines, "async dated miss", lambda: helper.fetch(user_id=argument))
        clock.value += 29
        await afetched(lines, "async fresh until max-age less its age", lambda: helper.fetch(user_id=argument))
        clock.value += 2
        await afetched(lines, "async stale past max-age", lambda: helper.fetch(user_id=argument))


def _fresh(cache: Caching, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Answer a fresh entry without sending, until its age, a cap, a Date, or an Expires makes it stale."""
    helper, three = api.protocols.users.profile, cache.user_id(3)
    exchange.respond(user(3, etag='"v1"', **{"cache-control": "max-age=60", "x-request-id": "r1"}))
    fetched(lines, "miss", lambda: helper.fetch(user_id=three))
    fetched(lines, "fresh hit", lambda: helper.fetch(user_id=three))
    small = cache.options.RequestOptions(max_response_bytes=5)
    fetched(lines, "fresh hit over the response limit", lambda: helper.fetch(user_id=three, options=small))
    for index, (label, headers, limits, stale) in enumerate((
        ("aged past max-age", {"cache-control": "max-age=60", "age": "100"}, None, True),
        ("aged past the cap", {"cache-control": "max-age=100000", "age": "400"}, None, True),
        ("aged within a raised cap", {"cache-control": "max-age=100000", "age": "400"}, 1000, False),
        ("old date", {"cache-control": "max-age=60", "date": _PAST}, None, True),
        ("expires after its date", {"expires": _LATER, "date": _FUTURE}, None, False),
        ("expires without a date", {"expires": _LATER}, None, False),
        ("invalid expires", {"expires": "0", "date": _FUTURE}, None, True),
        ("invalid age", {"cache-control": "max-age=60", "age": "soon"}, None, True),
        ("huge max-age", {"cache-control": f"max-age={'9' * 12}"}, None, False),
    )):
        argument = cache.user_id(30 + index)
        settings = None if limits is None else cache.protocols.CacheOptions(max_ttl=limits)
        exchange.respond(user(30 + index, etag='"e"', **headers), *((not_modified(etag='"e"'),) if stale else ()))
        fetched(
            lines, f"{label} stored", lambda argument=argument: helper.fetch(user_id=argument, cache_options=settings)
        )
        fetched(
            lines, f"{label} again", lambda argument=argument: helper.fetch(user_id=argument, cache_options=settings)
        )
    listing = api.protocols.users.listing
    exchange.respond(json_response(200, {"data": [{"id": 1, "name": "a"}]}, **{"cache-control": "max-age=60"}))
    fetched(lines, "list miss", listing.fetch)
    fetched(lines, "list hit", listing.fetch)
    dated = api.protocols.users.dated
    exchange.respond(user(4, status=203, **{"cache-control": "max-age=60"}))
    fetched(lines, "listed 203", lambda: dated.fetch(user_id=cache.user_id(4)))
    fetched(lines, "listed 203 again", lambda: dated.fetch(user_id=cache.user_id(4)))


def _revalidated(cache: Caching, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Revalidate stale entries with ETag or Last-Modified, merging a 304's headers, or replace changed ones."""
    helper, five = api.protocols.users.profile, cache.user_id(5)
    exchange.respond(
        user(5, etag='W/"a"', **{"cache-control": "no-cache", "x-old": "1", "connection": "keep-alive"}),
        not_modified(etag='"a"', **{"cache-control": "max-age=60", "x-old": "2", "content-type": "text/html"}),
    )
    fetched(lines, "no-cache stored", lambda: helper.fetch(user_id=five))
    fetched(lines, "revalidated", lambda: helper.fetch(user_id=five))
    fetched(lines, "revalidated then fresh", lambda: helper.fetch(user_id=five))
    six = cache.user_id(6)
    exchange.respond(
        user(6, etag='"b"', **{"cache-control": "max-age=0"}),
        user(6, "dog", etag='"c"', **{"cache-control": "max-age=0"}),
        not_modified(),
    )
    fetched(lines, "stale stored", lambda: helper.fetch(user_id=six))
    fetched(lines, "changed", lambda: helper.fetch(user_id=six))
    fetched(lines, "revalidated without validators", lambda: helper.fetch(user_id=six))
    dated, seven = api.protocols.users.dated, cache.user_id(7)
    exchange.respond(
        user(7, etag='"d"', **{"last-modified": _MODIFIED, "cache-control": "max-age=0"}),
        not_modified(**{"last-modified": _MODIFIED}),
        not_modified(**{"last-modified": _PAST}),
    )
    fetched(lines, "dated stored", lambda: dated.fetch(user_id=seven))
    fetched(lines, "dated revalidated", lambda: dated.fetch(user_id=seven))
    fetched(lines, "dated changed date", lambda: dated.fetch(user_id=seven))
    listing = api.protocols.users.listing
    exchange.respond(
        json_response(200, {"data": []}, **{"last-modified": _MODIFIED, "cache-control": "max-age=0"}),
        json_response(200, {"data": []}),
    )
    second = cache.page(2)
    fetched(lines, "etag helper without an etag", lambda: listing.fetch(page=second))
    fetched(lines, "etag helper sends unconditionally", lambda: listing.fetch(page=second))
    eight = cache.user_id(8)
    exchange.respond(
        user(8, etag='"e"', **{"cache-control": "max-age=0"}),
        not_modified(etag='"z"'),
        user(8, etag='"e"', **{"cache-control": "max-age=0"}),
        not_modified(etag='"e"', vary="accept-language"),
        user(8, status=404),
    )
    fetched(lines, "mismatch stored", lambda: helper.fetch(user_id=eight))
    fetched(lines, "304 of another validator", lambda: helper.fetch(user_id=eight))
    fetched(lines, "after a refused 304 the entry is gone", lambda: helper.fetch(user_id=eight))
    fetched(lines, "304 with another vary", lambda: helper.fetch(user_id=eight))
    fetched(lines, "after the vary change", lambda: helper.fetch(user_id=eight))
    aged = cache.user_id(25)
    exchange.respond(
        user(25, etag='"g"', **{"cache-control": "max-age=60", "age": "100"}),
        not_modified(etag='"g"'),
    )
    fetched(lines, "aged stored", lambda: helper.fetch(user_id=aged))
    fetched(lines, "aged revalidated by a 304 without Age", lambda: helper.fetch(user_id=aged))
    fetched(lines, "fresh after the 304", lambda: helper.fetch(user_id=aged))


def _vary(cache: Caching, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Select entries by the allowlisted headers their Vary names, refusing other names and `*`."""
    helper, nine = api.protocols.users.profile, cache.user_id(9)
    english, french = cache.language("en"), cache.language("fr")
    vary = {"cache-control": "max-age=60", "vary": "Accept-Language, accept-language"}
    exchange.respond(
        user(9, "english", **vary), user(9, "french", **vary), user(9, "english", **vary), user(9, "french", **vary)
    )
    fetched(lines, "english stored", lambda: helper.fetch(user_id=nine, accept_language=english))
    fetched(lines, "french stored", lambda: helper.fetch(user_id=nine, accept_language=french))
    fetched(lines, "english replacement", lambda: helper.fetch(user_id=nine, accept_language=english))
    fetched(lines, "french replacement", lambda: helper.fetch(user_id=nine, accept_language=french))
    fetched(lines, "no language", lambda: _miss(exchange, lambda: helper.fetch(user_id=nine)))
    for label, headers in (
        ("absent", ()),
        ("empty", (("Accept-Language", ""),)),
        ("repeated", (("Accept-Language", "en"), ("Accept-Language", "fr"))),
        ("reordered", (("Accept-Language", "fr"), ("Accept-Language", "en"))),
    ):
        options = cache.options.RequestOptions(headers=headers)
        exchange.respond(user(9, **vary))
        fetched(lines, f"Vary {label} replacement", lambda options=options: helper.fetch(user_id=nine, options=options))
        fetched(lines, f"Vary {label} hit", lambda options=options: helper.fetch(user_id=nine, options=options))
    keyed = cache.user_id(43)
    fresh = {"cache-control": "max-age=60"}
    exchange.respond(user(43, "alice", **fresh), user(43, "bob", **fresh))
    for label, key in (("alice key", "a"), ("bob key", "b"), ("alice key again", "a"), ("bob key again", "b")):
        fetched(lines, label, lambda key=key: helper.fetch(user_id=keyed, options=cache.headers(X_Api_Key=key)))
    for index, (label, header, stored) in enumerate((
        ("unlisted vary", "Accept-Encoding", False),
        ("star vary", "*", False),
        ("empty vary", " , ", True),
    )):
        argument = cache.user_id(40 + index)
        exchange.respond(user(1, vary=header, **{"cache-control": "max-age=60"}), *(() if stored else (user(1),)))
        fetched(lines, label, lambda: helper.fetch(user_id=argument))
        fetched(lines, f"{label} again", lambda: helper.fetch(user_id=argument))


def _miss(exchange: Exchange, call: Callable[[], Any]) -> Any:
    exchange.respond(user(9, "none"))
    return call()


def _unstored(cache: Caching, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Return responses that must not be stored, removing the entries they supersede, and keep entries on errors."""
    helper = api.protocols.users.profile
    for label, response, limits in (
        ("no-store", user(1, **{"cache-control": "no-store, max-age=60"}), None),
        ("set-cookie", user(1, **{"cache-control": "max-age=60", "set-cookie": "a=1"}), None),
        ("extension", user(1, **{"cache-control": "max-age=60, stale-while-revalidate=5"}), None),
        ("malformed", user(1, **{"cache-control": "max-age=soon"}), None),
        ("repeated", user(1, **{"cache-control": "max-age=1, max-age=2"}), None),
        ("valued public", user(1, **{"cache-control": "public=1, max-age=60"}), None),
        ("unquoted private", user(1, **{"cache-control": "private=x, max-age=60"}), None),
        ("quoted private", user(1, **{"cache-control": 'private="a, b", max-age=60'}), None),
        ("bad quoting", user(1, **{"cache-control": 'private="a'}), None),
        ("too large", user(1, **{"cache-control": "max-age=60"}), 5),
        ("unlisted status", user(1, status=203, **{"cache-control": "max-age=60"}), None),
        ("no freshness or validator", user(1), None),
    ):
        argument = cache.user_id(10)
        settings = None if limits is None else cache.protocols.CacheOptions(max_entry_bytes=limits)
        exchange.respond(user(10, etag='"s"', **{"cache-control": "max-age=0"}), response)
        fetched(lines, f"{label} after stale", lambda: helper.fetch(user_id=argument))
        fetched(lines, label, lambda argument=argument: helper.fetch(user_id=argument, cache_options=settings))
        exchange.respond(user(10))
        fetched(lines, f"{label} next", lambda argument=argument: helper.fetch(user_id=argument))
    eleven = cache.user_id(11)
    exchange.respond(
        user(11, **{"cache-control": "max-age=0"}, etag='"k"'),
        json_response(404, {"message": "gone"}),
        json_response(200, {"id": "x"}),
        not_modified(etag='"k"'),
    )
    fetched(lines, "kept stored", lambda: helper.fetch(user_id=eleven))
    fetched(lines, "error keeps the entry", lambda: helper.fetch(user_id=eleven))
    fetched(lines, "decode failure keeps the entry", lambda: helper.fetch(user_id=eleven))
    fetched(lines, "entry still revalidates", lambda: helper.fetch(user_id=eleven))
    twelve = cache.user_id(12)
    exchange.respond(
        raw_response(302, location="https://api.example.com/users/13"),
        user(13, **{"cache-control": "max-age=60"}),
        raw_response(302, location="https://api.example.com/users/13"),
        user(13, **{"cache-control": "max-age=60"}),
    )
    follow = cache.options.RequestOptions(redirects=cache.options.RedirectOptions(enabled=True))
    fetched(lines, "redirected", lambda: helper.fetch(user_id=twelve, options=follow))
    fetched(lines, "redirected again", lambda: helper.fetch(user_id=twelve, options=follow))
    fourteen = cache.user_id(14)
    exchange.respond(
        user(14, etag='"r"', **{"cache-control": "max-age=0"}),
        raw_response(302, location="https://api.example.com/users/15"),
        not_modified(etag='"r"'),
    )
    fetched(lines, "stored before a redirect", lambda: helper.fetch(user_id=fourteen))
    fetched(lines, "304 after a redirect", lambda: helper.fetch(user_id=fourteen, options=follow))


def _directives(cache: Caching, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Honor a fetch's own no-cache, no-store, and max-age, and refuse what a stored response cannot answer."""
    helper, sixteen = api.protocols.users.profile, cache.user_id(16)
    exchange.respond(user(16, etag='"f"', **{"cache-control": "max-age=60", "age": "30"}))
    fetched(lines, "directives stored", lambda: helper.fetch(user_id=sixteen))
    for label, headers, response in (
        ("request max-age=10", {"Cache_Control": "max-age=10"}, not_modified(etag='"f"')),
        ("request no-cache", {"Cache_Control": "no-cache"}, not_modified(etag='"f"')),
        ("request max-age=3600", {"Cache_Control": "max-age=3600"}, None),
        ("request no-store", {"Cache_Control": "no-store"}, user(16, "other", **{"cache-control": "max-age=60"})),
        ("request no-store 304", {"Cache_Control": "no-store", "If_None_Match": '"f"'}, not_modified(etag='"f"')),
        ("request only-if-cached", {"Cache_Control": "only-if-cached"}, None),
        ("request no-cache value", {"Cache_Control": "no-cache=x"}, None),
        ("request bad max-age", {"Cache_Control": "max-age=-1"}, None),
        ("request malformed", {"Cache_Control": "max-age=1 2"}, None),
        ("request range", {"Range": "bytes=0-1"}, None),
        ("request if-match", {"If_Match": '"f"'}, None),
    ):
        if response is not None:
            exchange.respond(response)
        fetched(lines, label, lambda: helper.fetch(user_id=sixteen, options=cache.headers(**headers)))
    fetched(lines, "still fresh", lambda: helper.fetch(user_id=sixteen))


def _validators(cache: Caching, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Send a caller's validator once when it matches the entry's, and refuse one that does not or a cold 304."""
    helper, seventeen = api.protocols.users.profile, cache.user_id(17)
    exchange.respond(
        user(17, etag='"g"', **{"cache-control": "max-age=0", "last-modified": _MODIFIED}),
        not_modified(etag='"g"'),
        not_modified(),
    )
    fetched(lines, "validators stored", lambda: helper.fetch(user_id=seventeen))
    matching = cache.headers(If_None_Match='"g"')
    fetched(lines, "matching caller validator", lambda: helper.fetch(user_id=seventeen, options=matching))
    dated = cache.headers(If_Modified_Since=_MODIFIED)
    fetched(lines, "matching caller date", lambda: helper.fetch(user_id=seventeen, options=dated))
    for label, headers in (
        ("other caller validator", {"If_None_Match": '"h"'}),
        ("other caller date", {"If_Modified_Since": _PAST}),
    ):
        fetched(lines, label, lambda: helper.fetch(user_id=seventeen, options=cache.headers(**headers)))
    twice = cache.options.RequestOptions(headers=(("If-None-Match", '"g"'), ("If-None-Match", '"h"')))
    fetched(lines, "caller validator twice", lambda: helper.fetch(user_id=seventeen, options=twice))
    cold = cache.user_id(18)
    exchange.respond(not_modified(etag='"g"'))
    fetched(lines, "cold 304", lambda: helper.fetch(user_id=cold, options=matching))


async def _async_caching(cache: Caching, lines: list[str]) -> None:
    """Fetch and revalidate with asyncio and an asynchronous memory store."""
    package = cache.package
    exchange = Exchange(lines)
    store = cache.protocols.AsyncMemoryCacheStore()
    options = cache.stores(users__profile=store, users__listing=store)
    async with exchange.async_client() as native, package.AsyncClient(http_client=native, options=options) as api:
        helper, twenty = api.protocols.users.profile, cache.user_id(20)
        exchange.respond(
            user(20, etag='"a"', **{"cache-control": "max-age=0"}),
            not_modified(etag='"a"', **{"cache-control": "max-age=60"}),
            user(20, **{"cache-control": "no-store"}),
            json_response(404, {"message": "no"}),
        )
        await afetched(lines, "async miss", lambda: helper.fetch(user_id=twenty))
        await afetched(lines, "async revalidated", lambda: helper.fetch(user_id=twenty))
        await afetched(lines, "async fresh", lambda: helper.fetch(user_id=twenty))
        await afetched(
            lines,
            "async removed",
            lambda: helper.fetch(user_id=twenty, options=cache.headers(Cache_Control="no-cache")),
        )
        await afetched(lines, "async error", lambda: helper.fetch(user_id=twenty))
        no_store = cache.headers(Cache_Control="no-store")
        exchange.respond(user(20))
        await afetched(lines, "async request no-store", lambda: helper.fetch(user_id=twenty, options=no_store))
        exchange.respond(user(21), user(21))
        twenty_one = cache.user_id(21)
        await afetched(lines, "async no validator or freshness", lambda: helper.fetch(user_id=twenty_one))
        await afetched(lines, "async uncacheable fetched again", lambda: helper.fetch(user_id=twenty_one))


class Events:
    """A hook that reports each event's name and status, and raises at the end of a call when told to."""

    def __init__(self, lines: list[str], *, failing: bool = False) -> None:
        """Keep the report and whether the hook fails at the end of each call."""
        self.lines = lines
        self.failing = failing

    def on_event(self, event: Any) -> None:
        """Report the event, then raise at a call's end when told to."""
        self.lines.append(f"    event {event.name} status={event.status}")
        if self.failing and event.name == "call_end":
            msg = "hook failed"
            raise RuntimeError(msg)


class AsyncEvents(Events):
    """The asynchronous hook of the same reports."""

    async def on_event(self, event: Any) -> None:  # ty: ignore[invalid-method-override]
        """Report the event."""
        Events.on_event(self, event)


class _Signer:
    def __init__(self, auth: ModuleType) -> None:
        self.capabilities = auth.SignerCapabilities(("https://api.example.com",), ("X-Signature",), (), False)
        self.fields = auth.SignatureFields((("X-Signature", "signed"),), ())

    def sign(self, request: object) -> object:
        del request
        return self.fields


def cache_stores(package: ModuleType, lines: list[str]) -> None:
    """Drive the store contract, memory stores, store failures, credentials, records, and client settings."""
    cache = Caching(package)
    _records(cache, lines)
    _memory(cache, lines)
    run(lambda: _async_memory(cache, lines))
    exchange = Exchange(lines)
    with exchange.client() as native:
        _settings(cache, native, exchange, lines)
        _recorded(cache, native, exchange, lines)
        _credentials(cache, native, exchange, lines)
    run(lambda: _async_stores(cache, lines))


def _stamp(**fields: object) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    return {
        "vary": (),
        "vary_values": (),
        "status_code": 200,
        "body": b"{}",
        "request_time": now,
        "response_time": now,
        "stored_at": now,
        "freshness_seconds": 3600,
        "initial_age_seconds": 0,
        "schema_fingerprint": "f",
        **fields,
    }


def _records(cache: Caching, lines: list[str]) -> None:
    """Construct cache records, options, and exceptions, refusing invalid fields."""
    protocols, errors, options = cache.protocols, cache.errors, cache.options
    responses = importlib.import_module(f"{cache.package.__name__}.responses")
    headers = responses.HeadersView((("etag", '"a"'),))

    def entry(**fields: object) -> object:
        return protocols.CacheEntry(headers=headers, **_stamp(**fields))

    record(lines, "entry", entry)
    for label, fields in (
        ("entry vary list", {"vary": ["a"]}),
        ("entry vary item", {"vary": (1,), "vary_values": (("",),)}),
        ("entry boolean status", {"status_code": True}),
        ("entry status", {"status_code": 99}),
        ("entry Vary values", {"vary": ("a",)}),
        ("entry naive time", {"stored_at": datetime(2020, 1, 1)}),  # noqa: DTZ001
        ("entry negative freshness", {"freshness_seconds": -1}),
        ("entry infinite age", {"initial_age_seconds": float("inf")}),
        ("entry text freshness", {"freshness_seconds": "1"}),
        ("entry text body", {"body": "{}"}),
    ):
        record(lines, label, lambda fields=fields: entry(**fields))
    record(lines, "entry headers", lambda: protocols.CacheEntry(headers={}, **_stamp()))
    info = responses.ResponseInfo(status_code=200, headers=headers, call_id="c", elapsed=0.0, content_type=None)
    record(lines, "result", lambda: protocols.CacheResult(data=1, source="network", response=info, network_status=200))
    record(
        lines, "result source", lambda: protocols.CacheResult(data=1, source="disk", response=info, network_status=None)
    )
    record(
        lines,
        "result response",
        lambda: protocols.CacheResult(data=1, source="network", response=1, network_status=None),
    )
    record(
        lines,
        "result status",
        lambda: protocols.CacheResult(data=1, source="network", response=info, network_status="1"),
    )
    record(lines, "options", lambda: protocols.CacheOptions(max_entry_bytes=1, max_ttl=0.5))
    record(lines, "options zero bytes", lambda: protocols.CacheOptions(max_entry_bytes=0))
    record(lines, "options boolean ttl", lambda: protocols.CacheOptions(max_ttl=True))
    record(lines, "defaults", lambda: protocols.ProtocolDefaults(options=protocols.CacheOptions(max_ttl=1)))
    record(lines, "stores list", lambda: options.ProtocolClientOptions(cache_stores=[]))
    record(lines, "stores name", lambda: options.ProtocolClientOptions(cache_stores={"no name": 1}))
    record(lines, "conflict", lambda: errors.CacheValidatorConflictError(header_name="If-None-Match"))
    record(lines, "conflict header", lambda: errors.CacheValidatorConflictError(header_name="ETag"))
    record(lines, "protocol error", lambda: errors.CacheProtocolError())
    record(lines, "store error", lambda: errors.CacheStoreError(action="get"))


def _memory(cache: Caching, lines: list[str]) -> None:
    """Replace one representation per key and evict the least recently used key by count."""
    protocols = cache.protocols
    view = importlib.import_module(f"{cache.package.__name__}.responses").HeadersView
    for label, limit in (("zero", 0), ("boolean", True), ("negative", -1)):
        record(lines, f"memory {label} entries", lambda limit=limit: protocols.MemoryCacheStore(max_entries=limit))
    store = protocols.MemoryCacheStore(max_entries=2)

    def entry(body: bytes) -> object:
        return protocols.CacheEntry(headers=view(), **_stamp(body=body))

    def found(label: str, key: bytes) -> None:
        hit = store.get(key)
        lines.append(f"  memory {label} {None if hit is None else hit.body!r}")

    found("missing", b"k")
    record(lines, "memory set", lambda: store.set(b"k", entry(b"en")))
    found("stored", b"k")
    record(lines, "memory overwrite", lambda: store.set(b"k", entry(b"fr")))
    found("replaced", b"k")
    store.set(b"x", entry(b"x"))
    found("mark recently used", b"k")
    store.set(b"y", entry(b"y"))
    found("evicted", b"x")
    found("kept", b"k")
    store.set(b"k", entry(b"en2"))
    found("replacement keeps count", b"y")
    record(lines, "memory delete", lambda: store.delete(b"k"))
    record(lines, "memory repeated delete", lambda: store.delete(b"k"))
    found("deleted", b"k")
    for label, call in (
        ("get key", lambda: store.get("k")),
        ("set key", lambda: store.set("k", entry(b"e"))),
        ("set entry", lambda: store.set(b"k", {})),
        ("delete key", lambda: store.delete("k")),
    ):
        record(lines, f"memory {label}", call)


async def _async_memory(cache: Caching, lines: list[str]) -> None:
    """Replace, look up, evict, and delete through asynchronous memory-store methods."""
    protocols = cache.protocols
    view = importlib.import_module(f"{cache.package.__name__}.responses").HeadersView
    store = protocols.AsyncMemoryCacheStore(max_entries=2)
    stored = protocols.CacheEntry(headers=view(), **_stamp())
    lines.append(f"  async memory missing {await store.get(b'k')}")
    lines.append(f"  async memory set {await store.set(b'k', stored)}")
    lines.append(f"  async memory get {(await store.get(b'k')) is stored}")
    await store.set(b"x", stored)
    await store.set(b"k", stored)
    await store.set(b"y", stored)
    lines.append(f"  async memory evicted {await store.get(b'x')}")
    lines.append(f"  async memory kept {(await store.get(b'k')) is stored}")
    lines.append(f"  async memory delete {await store.delete(b'k')}")
    lines.append(f"  async memory repeated delete {await store.delete(b'k')}")
    lines.append(f"  async memory deleted {await store.get(b'k')}")


def _settings(cache: Caching, native: Any, exchange: Exchange, lines: list[str]) -> None:
    """Refuse stores and defaults the package or the client's mode cannot use, and fetches without a store."""
    package, protocols, options = cache.package, cache.protocols, cache.options
    for label, client, settings in (
        ("unknown helper store", package.Client, {"users.unknown": protocols.MemoryCacheStore()}),
        ("pagination-free name", package.Client, {"users": protocols.MemoryCacheStore()}),
        ("object store", package.Client, {"users.profile": object()}),
        ("asynchronous store", package.Client, {"users.profile": protocols.AsyncMemoryCacheStore()}),
        ("synchronous store", package.AsyncClient, {"users.profile": protocols.MemoryCacheStore()}),
    ):
        protocol = options.ProtocolClientOptions(cache_stores=settings)
        record(
            lines,
            label,
            lambda client=client, protocol=protocol: client(options=options.ClientOptions(protocols=protocol)),
        )
    for label, defaults in (
        ("session defaults", protocols.ProtocolDefaults(session=options.SessionOptions(total_timeout=1))),
        ("pagination defaults", protocols.ProtocolDefaults(options=protocols.PaginationOptions(max_pages=1))),
    ):
        protocol = options.ProtocolClientOptions(defaults={"users.profile": defaults})
        record(
            lines, label, lambda protocol=protocol: package.Client(options=options.ClientOptions(protocols=protocol))
        )
    three = cache.user_id(3)
    with package.Client(http_client=native) as bare:
        helper = bare.protocols.users.profile
        fetched(lines, "no store", lambda: helper.fetch(user_id=three))
    recording = Recording(protocols.MemoryCacheStore(), lines)
    capped = options.ProtocolClientOptions(
        cache_stores={"users.profile": recording},
        defaults={"users.profile": protocols.ProtocolDefaults(options=protocols.CacheOptions(max_entry_bytes=5))},
        security=None,
    )
    client = package.Client(http_client=native, options=options.ClientOptions(protocols=capped))
    helper = client.protocols.users.profile
    exchange.respond(user(3, **{"cache-control": "max-age=60"}), user(3, **{"cache-control": "max-age=60"}))
    fetched(lines, "default entry limit", lambda: helper.fetch(user_id=three))
    raised = protocols.CacheOptions(max_entry_bytes=1000)
    fetched(lines, "call entry limit", lambda: helper.fetch(user_id=three, cache_options=raised))
    fetched(lines, "call entry limit hit", lambda: helper.fetch(user_id=three, cache_options=raised))
    fetched(
        lines, "pagination options", lambda: helper.fetch(user_id=three, cache_options=protocols.PaginationOptions())
    )
    fetched(lines, "text options", lambda: helper.fetch(user_id=three, options="x"))
    client.close()
    fetched(lines, "closed client", lambda: helper.fetch(user_id=three))


def _recorded(cache: Caching, native: Any, exchange: Exchange, lines: list[str]) -> None:
    """Call a custom store in contract order, map its failures, and replace entries without a transaction."""
    package, protocols, errors = cache.package, cache.protocols, cache.errors
    store = Recording(protocols.MemoryCacheStore(), lines)
    hooks = Events(lines)
    settings = cache.options.ClientOptions(
        hooks=(hooks,),
        protocols=cache.options.ProtocolClientOptions(cache_stores={"users.profile": store, "users.listing": store}),
    )
    with package.Client(http_client=native, options=settings) as api:
        helper, twenty = api.protocols.users.profile, cache.user_id(21)
        exchange.respond(user(21, etag='"a"', **{"cache-control": "max-age=0"}), not_modified(etag='"a"'), user(21))
        fetched(lines, "recorded miss", lambda: helper.fetch(user_id=twenty))
        fetched(lines, "recorded revalidation", lambda: helper.fetch(user_id=twenty))
        fetched(lines, "recorded unstored", lambda: helper.fetch(user_id=twenty))
        listing = api.protocols.users.listing
        exchange.respond(
            json_response(200, {"data": []}, etag='"l"', **{"cache-control": "max-age=0"}),
            json_response(200, {"data": []}, vary="Accept-Language", **{"cache-control": "max-age=60"}),
        )
        fetched(lines, "list without vary", listing.fetch)
        fetched(lines, "list in another vary slot", listing.fetch)
        exchange.respond(user(21, etag='"h"', **{"cache-control": "max-age=0"}), not_modified(etag='"x"'))
        fetched(lines, "healing stored", lambda: helper.fetch(user_id=twenty))
        for label, fault in (("a failed deletion", OSError("gone")), ("a deletion result", 1)):
            exchange.respond(*(() if label == "a failed deletion" else (not_modified(etag='"x"'),)))
            store.faults["delete"] = fault
            try:
                helper.fetch(user_id=twenty)
            except errors.CacheProtocolError as error:
                lines.append(
                    f"  refused 304 with {label} ! {describe(error)} "
                    f"{[describe(item) for item in error.secondary_errors]}"
                )
        cancelled = cache.options.CancelToken()
        cancelled.cancel()
        stopped = cache.options.RequestOptions(cancel_token=cancelled)
        fetched(lines, "cancelled before lookup", lambda: helper.fetch(user_id=twenty, options=stopped))
        for label, method, fault, responses in (
            ("lookup failure", "get", OSError("disk"), ()),
            ("lookup result", "get", "entry", ()),
            ("lookup store error", "get", errors.CacheStoreError(action="get"), ()),
            (
                "exchange failure",
                "set",
                RuntimeError("down"),
                (user(21, **{"cache-control": "max-age=60"}),),
            ),
            ("exchange result", "set", "yes", (user(21, **{"cache-control": "max-age=60"}),)),
        ):
            store.faults[method] = fault
            exchange.respond(*responses)
            fetched(lines, label, lambda: helper.fetch(user_id=twenty))
        exchange.respond(
            user(21, etag='"b"', **{"cache-control": "max-age=0"}),
            user(21),
            user(21, etag='"b"', **{"cache-control": "max-age=0"}),
            user(21),
        )
        fetched(lines, "deletable stored", lambda: helper.fetch(user_id=twenty))
        store.faults["delete"] = 1
        fetched(lines, "delete result", lambda: helper.fetch(user_id=twenty))
        fetched(lines, "deletable stored again", lambda: helper.fetch(user_id=twenty))
        store.faults["delete"] = OSError("gone")
        fetched(lines, "delete failure", lambda: helper.fetch(user_id=twenty))
    hooks.failing = True
    with package.Client(http_client=native, options=settings) as api:
        exchange.respond(user(23, **{"cache-control": "max-age=60"}), user(23, **{"cache-control": "max-age=60"}))
        fetched(
            lines, "hook failure stores nothing", lambda: api.protocols.users.profile.fetch(user_id=cache.user_id(23))
        )
        hooks.failing = False
        fetched(lines, "after a hook failure", lambda: api.protocols.users.profile.fetch(user_id=cache.user_id(23)))
        fetched(lines, "hit without events", lambda: api.protocols.users.profile.fetch(user_id=cache.user_id(23)))


def _credentials(cache: Caching, native: Any, exchange: Exchange, lines: list[str]) -> None:
    """Key entries by credential partition and auth, refusing authenticated use without a partition or a match."""
    package, protocols, options, auth = cache.package, cache.protocols, cache.options, cache.auth
    store = protocols.MemoryCacheStore()
    token = auth.AuthConfig({"bearer": auth.StaticTokenProvider(auth.AccessToken("token"))})

    def client(partition: str | None, config: object = token, **stores: object) -> Any:
        security = None if partition is None else protocols.ProtocolSecurityContext(credential_partition=partition)
        protocol = options.ProtocolClientOptions(
            security=security, cache_stores=stores or {"secure.profile": store, "users.profile": store}
        )
        return package.Client(http_client=native, options=options.ClientOptions(auth=config, protocols=protocol))

    secure_user = argument(package, "getSecureUser", "path", "userId", 1)
    with client(None) as anonymous:
        fetched(lines, "no partition", lambda: anonymous.protocols.secure.profile.fetch(user_id=secure_user))
    with client("tenant-a", None) as unauthenticated:
        fetched(lines, "no credentials", lambda: unauthenticated.protocols.secure.profile.fetch(user_id=secure_user))
    exchange.respond(
        user(1, etag='"t"', **{"cache-control": "max-age=60"}),
        user(1, "other"),
        user(1, "anonymous", **{"cache-control": "max-age=60"}),
        user(1, "b"),
    )
    with client("tenant-a") as first, client("tenant-a") as same, client("tenant-b") as other:
        fetched(lines, "tenant a stored", lambda: first.protocols.secure.profile.fetch(user_id=secure_user))
        fetched(lines, "tenant a shared", lambda: same.protocols.secure.profile.fetch(user_id=secure_user))
        fetched(lines, "tenant b isolated", lambda: other.protocols.secure.profile.fetch(user_id=secure_user))
        public = cache.user_id(1)
        fetched(lines, "anonymous helper", lambda: first.protocols.users.profile.fetch(user_id=public))
        bearer = cache.headers(Authorization="Bearer x")
        fetched(
            lines,
            "anonymous helper with a credential",
            lambda: first.protocols.users.profile.fetch(user_id=public, options=bearer),
        )
        fetched(lines, "anonymous helper same tenant", lambda: same.protocols.users.profile.fetch(user_id=public))
        fetched(lines, "anonymous helper other tenant", lambda: other.protocols.users.profile.fetch(user_id=public))
    alice, bob = (
        auth.AuthConfig({"bearer": auth.StaticTokenProvider(auth.AccessToken(name))}) for name in ("alice", "bob")
    )
    two = argument(package, "getSecureUser", "path", "userId", 2)
    varying = {"cache-control": "max-age=60", "vary": "Authorization"}
    exchange.respond(user(2, "alice", **varying), user(2, "bob", **varying), user(2, "alice again", **varying))
    with client("tenant-a", alice) as first, client("tenant-a", bob) as second:
        fetched(lines, "alice varying on authorization", lambda: first.protocols.secure.profile.fetch(user_id=two))
        fetched(lines, "bob in the same partition", lambda: second.protocols.secure.profile.fetch(user_id=two))
        fetched(lines, "alice again", lambda: first.protocols.secure.profile.fetch(user_id=two))
        other_auth = options.RequestOptions(auth=bob)
        view = first.with_options(other_auth)
        fetched(lines, "view with other auth", lambda: view.protocols.secure.profile.fetch(user_id=two))
        fetched(
            lines,
            "call with other auth",
            lambda: first.protocols.secure.profile.fetch(user_id=two, options=other_auth),
        )
        public = cache.user_id(1)
        anonymous_view = first.with_options(options.RequestOptions(auth=None))
        fetched(lines, "anonymous view", lambda: anonymous_view.protocols.users.profile.fetch(user_id=public))
    fresh = {"cache-control": "max-age=60"}
    four, five = (argument(package, "getSecureUser", "path", "userId", value) for value in (4, 5))
    exchange.respond(
        user(4, "u", **fresh), user(4, "service", **fresh), user(5, "service", **fresh), user(5, "u", **fresh)
    )
    with client("tenant-a") as service:
        helper = service.protocols.secure.profile
        delegated = service.with_options(cache.headers(X_On_Behalf_Of="user-u")).protocols.secure.profile
        fetched(lines, "on behalf of u", lambda: delegated.fetch(user_id=four))
        fetched(lines, "service after u", lambda: helper.fetch(user_id=four))
        fetched(lines, "on behalf of u again", lambda: delegated.fetch(user_id=four))
        fetched(lines, "service again", lambda: helper.fetch(user_id=four))
        fetched(lines, "service first", lambda: helper.fetch(user_id=five))
        fetched(lines, "on behalf of u after the service", lambda: delegated.fetch(user_id=five))
        forged = service.with_options(cache.headers(Authorization="Bearer mallory")).protocols.secure.profile
        fetched(lines, "view patching the bound authorization", lambda: forged.fetch(user_id=four))
    exchange.respond(user(44, "alice", **fresh), user(44, "bob", **fresh))
    with client("tenant-a", None, **{"carts.current": store}) as shopper:
        carts = shopper.protocols.carts.current
        for label, cart in (("alice cart", "alice"), ("bob cart", "bob"), ("alice cart again", "alice")):
            fetched(lines, label, lambda cart=cart: carts.fetch(cart=cache.cart(cart)))
    signed_vary = {"cache-control": "max-age=60", "vary": "X-Signature"}
    exchange.respond(user(3, "first", **signed_vary), user(3, "second", **signed_vary))
    three = argument(package, "getSecureUser", "path", "userId", 3)
    signing = auth.AuthConfig({"bearer": auth.StaticTokenProvider(auth.AccessToken("alice"))}, signers=(_Signer(auth),))
    with client("tenant-a", signing) as signer:
        fetched(lines, "vary on a signed header", lambda: signer.protocols.secure.profile.fetch(user_id=three))
        fetched(lines, "vary on a signed header again", lambda: signer.protocols.secure.profile.fetch(user_id=three))
    failing = Recording(protocols.MemoryCacheStore(), lines)
    secret = auth.StaticCredentialProvider(auth.ApiKeyCredential("secret"))
    with auth.ClientCredentialsProvider(
        "https://auth.example.com/token", client_id="c", client_secret=secret, audience="api"
    ) as oauth:
        signed = auth.AuthConfig({"bearer": oauth}, signers=(_Signer(auth),))
        with client("tenant-a", signed, **{"secure.profile": failing}) as granted:
            failing.faults["get"] = OSError("down")
            fetched(lines, "granted and signed", lambda: granted.protocols.secure.profile.fetch(user_id=secure_user))


async def _async_stores(cache: Caching, lines: list[str]) -> None:
    """Map an asynchronous store's failures and results as the synchronous ones are mapped."""
    package, protocols, options, auth = cache.package, cache.protocols, cache.options, cache.auth
    exchange = Exchange(lines)
    store = AsyncRecording(protocols.MemoryCacheStore(), lines)
    settings = options.ClientOptions(
        hooks=(AsyncEvents(lines),), protocols=options.ProtocolClientOptions(cache_stores={"users.profile": store})
    )
    async with exchange.async_client() as native, package.AsyncClient(http_client=native, options=settings) as api:
        helper, twenty = api.protocols.users.profile, cache.user_id(24)
        exchange.respond(
            user(24, etag='"a"', **{"cache-control": "max-age=0"}), user(24), json_response(404, {"message": "no"})
        )
        await afetched(lines, "async recorded stored", lambda: helper.fetch(user_id=twenty))
        await afetched(lines, "async recorded unstored", lambda: helper.fetch(user_id=twenty))
        await afetched(lines, "async recorded error", lambda: helper.fetch(user_id=twenty))
        for label, method, fault, responses in (
            ("async lookup failure", "get", OSError("disk"), ()),
            ("async lookup store error", "get", cache.errors.CacheStoreError(action="get"), ()),
            ("async exchange result", "set", "yes", (user(24, **{"cache-control": "max-age=60"}),)),
        ):
            store.faults[method] = fault
            exchange.respond(*responses)
            await afetched(lines, label, lambda: helper.fetch(user_id=twenty))
        exchange.respond(user(24, etag='"h"', **{"cache-control": "max-age=0"}), not_modified(etag='"x"'))
        await afetched(lines, "async healing stored", lambda: helper.fetch(user_id=twenty))
        for label, fault in (("a failed deletion", OSError("gone")), ("a deletion result", 1)):
            exchange.respond(*(() if label == "a failed deletion" else (not_modified(etag='"x"'),)))
            store.faults["delete"] = fault
            try:
                await helper.fetch(user_id=twenty)
            except cache.errors.CacheProtocolError as error:
                lines.append(
                    f"  async refused 304 with {label} ! {describe(error)} "
                    f"{[describe(item) for item in error.secondary_errors]}"
                )
        exchange.respond(user(24, etag='"b"', **{"cache-control": "max-age=0"}), user(24))
        await afetched(lines, "async deletable", lambda: helper.fetch(user_id=twenty))
        store.faults["delete"] = 1
        await afetched(lines, "async delete result", lambda: helper.fetch(user_id=twenty))
    fresh = {"cache-control": "max-age=60"}
    secure_user = argument(package, "getSecureUser", "path", "userId", 1)
    token = auth.AuthConfig({"bearer": auth.AsyncStaticTokenProvider(auth.AccessToken("token"))})
    partitioned = options.ProtocolClientOptions(
        security=protocols.ProtocolSecurityContext(credential_partition="tenant"),
        cache_stores={"secure.profile": protocols.AsyncMemoryCacheStore()},
    )
    exchange.respond(user(1, "service", **fresh), user(1, "u", **fresh))
    async with (
        exchange.async_client() as native,
        package.AsyncClient(
            http_client=native, options=options.ClientOptions(auth=token, protocols=partitioned)
        ) as service,
    ):
        delegated = service.with_options(cache.headers(X_On_Behalf_Of="user-u")).protocols.secure.profile
        await afetched(lines, "async service", lambda: service.protocols.secure.profile.fetch(user_id=secure_user))
        await afetched(lines, "async on behalf of u", lambda: delegated.fetch(user_id=secure_user))
        await afetched(lines, "async on behalf of u again", lambda: delegated.fetch(user_id=secure_user))
    failing = AsyncRecording(protocols.MemoryCacheStore(), lines)
    failing.faults["get"] = OSError("down")
    secret = auth.AsyncStaticCredentialProvider(auth.ApiKeyCredential("secret"))
    oauth = auth.AsyncClientCredentialsProvider("https://auth.example.com/token", client_id="c", client_secret=secret)
    protocol = options.ProtocolClientOptions(
        security=protocols.ProtocolSecurityContext(credential_partition="tenant"),
        cache_stores={"secure.profile": failing},
    )
    config = options.ClientOptions(auth=auth.AuthConfig({"bearer": oauth}), protocols=protocol)
    async with exchange.async_client() as native, package.AsyncClient(http_client=native, options=config) as granted:
        await afetched(lines, "async granted", lambda: granted.protocols.secure.profile.fetch(user_id=secure_user))
    await oauth.aclose()


def cache_backends(package: ModuleType, lines: list[str]) -> None:
    """Decode stored representations again with each backend: a miss, a revalidation, a fresh hit, and a list."""
    cache = Caching(package)
    exchange = Exchange(lines)
    store = cache.protocols.MemoryCacheStore()
    with (
        exchange.client() as native,
        package.Client(http_client=native, options=cache.stores(users__profile=store, users__listing=store)) as api,
    ):
        helper, one = api.protocols.users.profile, cache.user_id(1)
        exchange.respond(
            user(1, etag='"a"', **{"cache-control": "max-age=0"}),
            not_modified(etag='"a"', **{"cache-control": "max-age=60"}),
            json_response(
                200, {"data": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]}, **{"cache-control": "max-age=60"}
            ),
        )
        fetched(lines, "backend miss", lambda: helper.fetch(user_id=one))
        fetched(lines, "backend revalidated", lambda: helper.fetch(user_id=one))
        fetched(lines, "backend fresh", lambda: helper.fetch(user_id=one))
        fetched(lines, "backend list miss", api.protocols.users.listing.fetch)
        fetched(lines, "backend list fresh", api.protocols.users.listing.fetch)
