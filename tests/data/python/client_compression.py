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
        self.codecs = importlib.import_module(f"{package.__name__}.types.items").PutBlobRequestCodecs

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

    def length(self, value: int) -> Any:
        """Return a typed Content-Length parameter whose value compression must replace."""
        return self.codecs.parameter(location="header", name="Content-Length").from_wire(value)

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
        record(lines, "declared without body", api.items.create_item)
        exchange.respond(_created())
        record(lines, "declared null", lambda: api.items.create_item(body=None))
        exchange.respond(_stored())
        record(lines, "undeclared body", lambda: api.items.create_note(body=harness.item("plain")))
        exchange.respond(json_response(200, {"data": []}))
        record(lines, "declared bodyless", api.items.list_items)
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
        lines.append(
            f"    signed digest of the sent bytes {hashlib.sha256(request.content).digest() == signer.digests[-1]}"
        )
        lengths = request.headers.get_list("content-length")
        lines.append(f"    signed framing correct {lengths == [str(len(request.content))]}")
        return httpx2.Response(204)

    config = auth.AuthConfig(
        {}, send_on_anonymous=True, allowed_origins=("https://api.example.com",), signers=(signer,)
    )
    with harness.client("gzip", auth=config) as api:
        exchange.respond(digested)
        record(lines, "signed bytes", lambda: api.items.put_blob(body=b"signed", content_length=harness.length(6)))
        file = harness.bodies.FileBody(io.BytesIO(b"file"))
        record(lines, "signed file", lambda: api.items.put_blob(body=file))


def _framing(harness: _Harness, lines: list[str]) -> None:
    """Replace a typed length after compression, keeping bytes sized and streamed bodies chunked."""
    exchange, bodies = harness.exchange, harness.bodies

    def sized(request: httpx2.Request) -> httpx2.Response:
        lengths = request.headers.get_list("content-length")
        lines.append(f"    compressed length correct {lengths == [str(len(request.content))]}")
        return httpx2.Response(204)

    def chunked(request: httpx2.Request) -> httpx2.Response:
        lengths = request.headers.get_list("content-length")
        lines.append(
            f"    streamed length absent {not lengths} chunked={request.headers.get('transfer-encoding') == 'chunked'}"
        )
        return httpx2.Response(204)

    length = harness.length(6)
    with harness.client("gzip") as api:
        exchange.respond(sized, chunked)
        record(lines, "typed bytes length", lambda: api.items.put_blob(body=b"framed", content_length=length))
        record(
            lines,
            "typed file length",
            lambda: api.items.put_blob(body=bodies.FileBody(io.BytesIO(b"framed")), content_length=length),
        )

    async def asynchronous() -> None:
        async with harness.async_client("gzip") as api:
            exchange.respond(sized, chunked)
            await arecord(
                lines, "async typed bytes length", lambda: api.items.put_blob(body=b"framed", content_length=length)
            )
            await arecord(
                lines,
                "async typed file length",
                lambda: api.items.put_blob(body=bodies.AsyncFileBody(io.BytesIO(b"framed")), content_length=length),
            )

    run(asynchronous)


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
        zero = protocols.PaginationOptions(max_items=0)
        record(
            lines, "zero next page", lambda: helpers.search_all.next_page(first, options=gzip, pagination_options=zero)
        )
        exchange.respond(*_results((["a"], "c2")))
        with helpers.search_all.iterate(body=harness.query()) as pager:
            next(pager)
            state = pager.checkpoint()
        record(lines, "zero resume", lambda: helpers.search_all.resume(state, options=gzip, pagination_options=zero))
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
        check = api.protocols.checks.run.start(options=gzip, poll_options=fast)
        state = check.checkpoint()
        check.close()
        resumed = api.protocols.checks.run.resume(state, options=gzip, poll_options=fast)
        record(lines, "check", resumed.wait)
        record(lines, "completed check resume", lambda: api.protocols.checks.run.resume(state, options=gzip))
        exchange.respond(json_response(200, {"id": "c1", "status": "done", "result": {"value": "ok"}}))
        record(lines, "completed check inherited resume", api.protocols.checks.run.resume(state).wait)
        exchange.respond(raw_response(200, b'data: {"text":"a"}\n\n', "text/event-stream"))
        with api.protocols.events.watch.open(body=harness.query(), options=gzip) as stream:
            lines.append(f"  events {[event.data for event in stream]}")
    with harness.client("gzip") as api:
        exchange.respond(*_results((["a"], None)))
        record(lines, "inherited bodyless pages", lambda: api.protocols.items.listing.page().items)


def _stream_resume(harness: _Harness, lines: list[str]) -> None:
    """Admit only reachable compressed stream requests, and recheck the saved reopen on resume."""
    exchange, protocols = harness.exchange, harness.protocols
    gzip = harness.call("gzip")
    event = raw_response(200, b'id: c1\ndata: {"text":"a"}\n\n', "text/event-stream")
    reconnect = protocols.StreamOptions(reconnect=True)
    with harness.client() as api:
        for name in ("resumable", "tail"):
            helper = getattr(api.protocols.events, name)
            exchange.respond(event)
            with helper.open(body=harness.query(), options=gzip, stream_options=reconnect) as stream:
                lines.append(f"  {name} first {next(stream).data}")
                state = stream.checkpoint()
            if name == "tail":
                record(
                    lines,
                    "bodyless reopen compression",
                    lambda helper=helper, state=state: helper.resume(state, options=gzip),
                )
                exchange.respond(event)
                with helper.resume(state) as stream:
                    lines.append(f"  tail plain resume {next(stream).data}")
            else:
                exchange.respond(event)
                with helper.resume(state, options=gzip) as stream:
                    lines.append(f"  own compressed resume {next(stream).data}")
        helper = api.protocols.events.push
        record(lines, "disabled reconnect compression", lambda: helper.open(options=gzip))
        record(
            lines,
            "zero reconnect compression",
            lambda: helper.open(options=gzip, stream_options=protocols.StreamOptions(reconnect=True, max_reconnects=0)),
        )
        exchange.respond(event)
        with helper.open(options=gzip, stream_options=reconnect) as stream:
            lines.append(f"  bodyless open {next(stream).data}")
            state = stream.checkpoint()
        exchange.respond(event)
        with helper.resume(state, options=gzip) as stream:
            lines.append(f"  cursor body resume {next(stream).data}")


async def _async(harness: _Harness, lines: list[str]) -> None:
    exchange, bodies = harness.exchange, harness.bodies
    gzip = harness.call("gzip")

    async def chunks() -> AsyncIterator[bytes]:  # ruff: ignore[unused-async]
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
        exchange.respond(*_results((["a"], "c2")))
        first = await api.protocols.items.search_all.page(body=harness.query())
        zero = harness.protocols.PaginationOptions(max_items=0)
        await arecord(
            lines,
            "async zero next",
            lambda: api.protocols.items.search_all.next_page(first, options=gzip, pagination_options=zero),
        )
        exchange.respond(*_results((["a"], "c2")))
        async with api.protocols.items.search_all.iterate(body=harness.query()) as pager:
            await anext(pager)
            state = pager.checkpoint()
        await arecord(
            lines,
            "async zero resume",
            lambda: api.protocols.items.search_all.resume(state, options=gzip, pagination_options=zero),
        )
        exchange.respond(
            json_response(202, {"id": "c1", "status": "running"}),
            json_response(200, {"id": "c1", "status": "done", "result": {"value": "ok"}}),
        )
        check = await api.protocols.checks.run.start(
            options=gzip, poll_options=harness.protocols.PollOptions(interval=0.000001)
        )
        state = check.checkpoint()
        await arecord(lines, "async check", check.wait)
        record(lines, "async completed check resume", lambda: api.protocols.checks.run.resume(state, options=gzip))
        exchange.respond(json_response(200, {"id": "c1", "status": "done", "result": {"value": "ok"}}))
        resumed = api.protocols.checks.run.resume(state)
        await arecord(lines, "async inherited completed check", resumed.wait)

        await arecord(
            lines,
            "async helper header conflict",
            lambda: api.protocols.jobs.run.start(
                body=harness.query(), options=harness.call("gzip", headers=(("Content-Encoding", "br"),))
            ),
        )

    event = raw_response(200, b'id: c1\ndata: {"text":"a"}\n\n', "text/event-stream")
    async with harness.async_client() as api:
        for name in ("resumable", "tail"):
            helper = getattr(api.protocols.events, name)
            exchange.respond(event)
            async with await helper.open(body=harness.query(), options=gzip) as stream:
                lines.append(f"  async {name} first {(await anext(stream)).data}")
                state = stream.checkpoint()
            if name == "tail":
                await arecord(
                    lines,
                    "async bodyless reopen compression",
                    lambda helper=helper, state=state: helper.resume(state, options=gzip),
                )
            else:
                exchange.respond(event)
                async with await helper.resume(state, options=gzip) as stream:
                    lines.append(f"  async own compressed resume {(await anext(stream)).data}")
        exchange.respond(event)
        async with await api.protocols.events.push.open() as stream:
            lines.append(f"  async bodyless open {(await anext(stream)).data}")
            state = stream.checkpoint()
        exchange.respond(event)
        async with await api.protocols.events.push.resume(state, options=gzip) as stream:
            lines.append(f"  async cursor body resume {(await anext(stream)).data}")


def compression(package: ModuleType, lines: list[str]) -> None:
    """Select gzip on clients, views, calls, and helpers, and report which requests are sent compressed."""
    exchange = Exchange(lines)
    harness = _Harness(package, exchange)
    for step in (_values, _inherited, _signed, _framing, _explicit, _helpers, _stream_resume):
        lines.append(f"# {step.__name__.strip('_')}")
        step(harness, lines)
    lines.append("# async")
    run(lambda: _async(harness, lines))
