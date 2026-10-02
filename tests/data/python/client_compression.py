"""Compress request bodies of declared operations through generated clients and helpers over real TLS."""

from __future__ import annotations

import hashlib
import importlib
import io
from typing import TYPE_CHECKING, Any

import httpx2

from tests.data.python.client_pagination import adrained, drained
from tests.data.python.client_runtime import (
    Exchange,
    arecord,
    json_response,
    raw_response,
    record,
    run,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from types import ModuleType


class _Harness:
    """The modules of one compression package and the clients its scenario uses."""

    def __init__(self, package: ModuleType, exchange: Exchange) -> None:
        self.package = package
        self.exchange = exchange
        self.options = importlib.import_module(f"{package.__name__}.options")
        self.bodies = importlib.import_module(f"{package.__name__}.bodies")
        self.protocols = importlib.import_module(f"{package.__name__}.protocols")
        self.models = importlib.import_module(f"{package.__name__}_models")

    def settings(self, compression: object = None, **options: Any) -> Any:
        options.setdefault("retry", self.options.RetryOptions(max_retries=1, initial_delay=0))
        return self.options.ClientOptions(compression=compression, **options)

    def client(self, compression: object = None, **options: Any) -> Any:
        return self.package.Client(
            http_client=self.exchange.client(),
            http_client_ownership="owned",
            options=self.settings(compression, **options),
        )

    def async_client(self, compression: object = None, **options: Any) -> Any:
        return self.package.AsyncClient(
            http_client=self.exchange.async_client(),
            http_client_ownership="owned",
            options=self.settings(compression, **options),
        )

    def call(self, *compression: object, **options: Any) -> Any:
        """Return call options, selecting a coding only when one is given."""
        return self.options.RequestOptions(**dict(zip(("compression",), compression, strict=False)), **options)

    def query(self, text: str = "q") -> Any:
        return self.models.Query(text=text)

    def item(self, name: str = "n") -> Any:
        return self.models.Item(name=name)


def _created() -> Any:
    return json_response(201, {"name": "n"})


def _stored() -> Any:
    return raw_response(204)


def _values(harness: _Harness, lines: list[str]) -> None:
    options = harness.options
    for label, value in (
        ("gzip", "gzip"),
        ("upper", "GZIP"),
        ("none", None),
        ("brotli", "br"),
        ("identity", "identity"),
        ("not a token", "g zip"),
        ("type", 5),
    ):
        record(lines, f"client {label}", lambda value=value: options.ClientOptions(compression=value).compression)
        record(lines, f"call {label}", lambda value=value: options.RequestOptions(compression=value).compression)


def _inherited(harness: _Harness, lines: list[str]) -> None:
    exchange, bodies = harness.exchange, harness.bodies
    with harness.client("gzip") as api:
        exchange.respond(_created())
        record(lines, "declared body", lambda: api.items.create_item(body=harness.item("compressed")))
        exchange.respond(_created())
        record(lines, "declared without body", lambda: api.items.create_item())
        exchange.respond(_created())
        record(lines, "declared null", lambda: api.items.create_item(body=None))
        exchange.respond(_stored())
        record(lines, "undeclared body", lambda: api.items.create_note(body=harness.item("plain")))
        exchange.respond(json_response(200, {"data": []}))
        record(lines, "declared bodyless", lambda: api.items.list_items())
        exchange.respond(_stored())
        record(
            lines,
            "raw request",
            lambda: api.request_raw("PUT", "https://api.example.com/blobs", body=b"raw").info.status_code,
        )
        exchange.respond(_stored())
        record(lines, "empty bytes", lambda: api.items.put_blob(body=b""))
        exchange.respond(raw_response(503), _stored())
        record(lines, "bytes retried", lambda: api.items.put_blob(body=b"a" * 70000))
        exchange.respond(raw_response(503), _stored())
        record(lines, "file retried", lambda: api.items.put_blob(body=bodies.FileBody(io.BytesIO(b"file body"))))
        exchange.respond(raw_response(503))
        record(lines, "stream once", lambda: api.items.put_blob(body=bodies.StreamBody(iter((b"one", b"shot")))))
        exchange.respond(_created())
        record(lines, "raw view", lambda: api.items.with_raw_response.create_item(body=harness.item()).info.status_code)
        exchange.respond(_created())
        record(lines, "call off", lambda: api.items.create_item(body=harness.item(), options=harness.call(None)))
        conflict = harness.call(headers=(("Content-Encoding", "br"),))
        record(lines, "header conflict", lambda: api.items.put_blob(body=b"x", options=conflict))
        exchange.respond(_stored())
        record(lines, "header without coding", lambda: api.items.create_note(body=harness.item(), options=conflict))
        moved = harness.call(redirects=harness.options.RedirectOptions(enabled=True, allow_303_to_get=True))
        exchange.respond(raw_response(303, Location="https://api.example.com/items"), json_response(200, {"data": []}))
        record(
            lines,
            "redirect drops the body",
            lambda: api.items.with_raw_response.put_blob(body=b"x", options=moved).info.status_code,
        )
        exchange.respond(raw_response(307, Location="https://api.example.com/blobs?moved=1"), _stored())
        record(lines, "redirect keeps the body", lambda: api.items.put_blob(body=b"kept" * 50, options=moved))
    with harness.client("gzip", headers=(("Content-Encoding", "br"),)) as api:
        record(lines, "client header conflict", lambda: api.items.create_item(body=harness.item()))


class _DigestSigner:
    """A signer that needs the body digest; it keeps each digest and adds a fixed header."""

    def __init__(self, auth: ModuleType) -> None:
        self.auth = auth
        self.digests: list[bytes] = []
        self.capabilities = auth.SignerCapabilities(
            allowed_origins=("https://api.example.com",),
            managed_headers=("x-signed",),
            managed_query=(),
            requires_body_digest=True,
        )

    def sign(self, request: Any) -> Any:
        self.digests.append(request.body_digest)
        return self.auth.SignatureFields(headers=(("x-signed", "yes"),), query=())


def _signed(harness: _Harness, lines: list[str]) -> None:
    exchange = harness.exchange
    auth = importlib.import_module(f"{harness.package.__name__}.auth")
    signer = _DigestSigner(auth)

    def digested(request: httpx2.Request) -> httpx2.Response:
        lines.append(f"    signed digest of the sent bytes {hashlib.sha256(request.content).digest() == signer.digests[-1]}")
        return httpx2.Response(204)

    config = auth.AuthConfig({}, send_on_anonymous=True, allowed_origins=("https://api.example.com",), signers=(signer,))
    with harness.client("gzip", auth=config) as api:
        exchange.respond(digested)
        record(lines, "signed bytes", lambda: api.items.put_blob(body=b"signed"))
        file = harness.bodies.FileBody(io.BytesIO(b"file"))
        record(lines, "signed file", lambda: api.items.put_blob(body=file))


def _explicit(harness: _Harness, lines: list[str]) -> None:
    exchange = harness.exchange
    gzip = harness.call("gzip")
    with harness.client() as api:
        exchange.respond(_created())
        record(lines, "call declared", lambda: api.items.create_item(body=harness.item(), options=gzip))
        record(lines, "call undeclared", lambda: api.items.create_note(body=harness.item(), options=gzip))
        record(lines, "call bodyless", lambda: api.items.list_items(options=gzip))
        record(lines, "call without body", lambda: api.items.create_item(options=gzip))
        record(
            lines,
            "call raw request",
            lambda: api.request_raw("PUT", "https://api.example.com/blobs", body=b"x", options=gzip),
        )
        record(
            lines, "call raw view", lambda: api.items.with_raw_response.create_note(body=harness.item(), options=gzip)
        )
        view = api.with_options(gzip)
        exchange.respond(_stored(), _created())
        record(lines, "view undeclared", lambda: view.items.create_note(body=harness.item()))
        record(lines, "view declared", lambda: view.items.create_item(body=harness.item()))


def _results(*pages: tuple[list[str], str | None]) -> list[Any]:
    return [
        json_response(200, {"data": [{"name": name} for name in names], "next_cursor": cursor})
        for names, cursor in pages
    ]


def _helpers(harness: _Harness, lines: list[str]) -> None:
    exchange, protocols = harness.exchange, harness.protocols
    gzip = harness.call("gzip")
    with harness.client() as api:
        helpers = api.protocols.items
        exchange.respond(*_results((["a"], "c2"), (["b"], None)))
        drained(lines, "cursor pages", helpers.search_all.iterate(body=harness.query(), options=gzip))
        exchange.respond(
            json_response(200, {"data": [{"name": "a"}], "next": "https://api.example.com/feed?page=2"}),
            json_response(200, {"data": [{"name": "b"}]}),
        )
        drained(lines, "followed pages", helpers.feed_all.iterate(body=harness.query(), options=gzip))
        record(lines, "bodyless pages", lambda: helpers.listing.page(options=gzip))
        record(
            lines,
            "no items",
            lambda: helpers.search_all.iterate(
                body=harness.query(), options=gzip, pagination_options=protocols.PaginationOptions(max_items=0)
            ),
        )
        exchange.respond(*_results((["a"], None)))
        last = helpers.search_all.page(body=harness.query())
        record(lines, "after the last page", lambda: helpers.search_all.next_page(last, options=gzip))
        exchange.respond(*_results((["a"], "c2")))
        first = helpers.search_all.page(body=harness.query())
        exchange.respond(*_results((["b"], None)))
        record(lines, "next page", lambda: helpers.search_all.next_page(first, options=gzip).items)
        exchange.respond(json_response(200, {"data": [{"name": "a"}], "next": "https://api.example.com/feed?page=2"}))
        followed = helpers.feed_all.page(body=harness.query())
        record(lines, "next followed page", lambda: helpers.feed_all.next_page(followed, options=gzip))
        exchange.respond(
            json_response(202, {"id": "j1", "status": "running"}),
            json_response(200, {"id": "j1", "status": "done", "result": {"value": "ok"}}),
        )
        fast = protocols.PollOptions(interval=0.000001)
        handle = api.protocols.jobs.run.start(body=harness.query(), options=gzip, poll_options=fast)
        record(lines, "job", handle.wait)
        exchange.respond(
            json_response(202, {"id": "c1", "status": "running"}),
            json_response(200, {"id": "c1", "status": "done", "result": {"value": "ok"}}),
        )
        record(lines, "check", api.protocols.checks.run.start(options=gzip, poll_options=fast).wait)
        exchange.respond(raw_response(200, b'data: {"text":"a"}\n\n', "text/event-stream"))
        with api.protocols.events.watch.open(body=harness.query(), options=gzip) as stream:
            lines.append(f"  events {[event.data for event in stream]}")
    with harness.client("gzip") as api:
        exchange.respond(*_results((["a"], None)))
        record(lines, "inherited bodyless pages", lambda: api.protocols.items.listing.page().items)


async def _async(harness: _Harness, lines: list[str]) -> None:
    exchange, bodies = harness.exchange, harness.bodies
    gzip = harness.call("gzip")

    async def chunks() -> AsyncIterator[bytes]:
        yield b"async "
        yield b"stream"

    async with harness.async_client("gzip") as api:
        exchange.respond(_created())
        await arecord(lines, "async declared", lambda: api.items.create_item(body=harness.item()))
        exchange.respond(raw_response(503), _stored())
        await arecord(
            lines, "async file retried", lambda: api.items.put_blob(body=bodies.AsyncFileBody(io.BytesIO(b"file")))
        )
        exchange.respond(_stored())
        await arecord(lines, "async stream", lambda: api.items.put_blob(body=bodies.AsyncStreamBody(chunks())))
        exchange.respond(_stored())
        await arecord(lines, "async undeclared", lambda: api.items.create_note(body=harness.item()))
        await arecord(lines, "async call undeclared", lambda: api.items.create_note(body=harness.item(), options=gzip))
        exchange.respond(*_results((["a"], "c2"), (["b"], None)))
        await adrained(
            lines, "async cursor pages", api.protocols.items.search_all.iterate(body=harness.query(), options=gzip)
        )
        await arecord(lines, "async bodyless pages", lambda: api.protocols.items.listing.page(options=gzip))
        await arecord(
            lines,
            "async helper header conflict",
            lambda: api.protocols.jobs.run.start(
                body=harness.query(), options=harness.call("gzip", headers=(("Content-Encoding", "br"),))
            ),
        )


def _cached(harness: _Harness, lines: list[str]) -> None:
    exchange, protocols = harness.exchange, harness.protocols
    gzip = harness.call("gzip")
    stores = harness.options.ProtocolClientOptions(cache_stores={"profiles.current": protocols.MemoryCacheStore()})
    with harness.client(protocols=stores) as api:
        cache = api.protocols.profiles.current
        exchange.respond(json_response(200, {"name": "p"}, ETag='"v1"', **{"Cache-Control": "max-age=60"}))
        record(lines, "cache fill", lambda: cache.fetch().source)
        record(lines, "cache hit with a coding", lambda: cache.fetch(options=gzip))
        record(lines, "cache hit", lambda: cache.fetch().source)
    empty = harness.options.ProtocolClientOptions(cache_stores={"profiles.current": protocols.MemoryCacheStore()})
    with harness.client(protocols=empty) as api:
        record(lines, "cache miss with a coding", lambda: api.protocols.profiles.current.fetch(options=gzip))


def compression(package: ModuleType, lines: list[str]) -> None:
    """Select gzip on clients, views, calls, and helpers, and report which requests are sent compressed."""
    exchange = Exchange(lines)
    harness = _Harness(package, exchange)
    for step in (_values, _inherited, _signed, _explicit, _helpers, _cached):
        lines.append(f"# {step.__name__.strip('_')}")
        step(harness, lines)
    lines.append("# async")
    run(lambda: _async(harness, lines))
