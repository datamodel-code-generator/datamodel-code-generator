"""Compress request bodies of declared operations through generated clients and helpers over real TLS."""

from __future__ import annotations

import gzip
import hashlib
import importlib
import io
from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING, Any

import httpx2

from tests.data.python.client_pagination import adrained, drained
from tests.data.python.client_runtime import Exchange, arecord, argument, json_response, raw_response, record, run
from tests.data.python.fixture_native import NativeFixture

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
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

    def settings(self, *compression: object, **options: Any) -> Any:
        options.setdefault("retry", self.options.RetryOptions(max_retries=1, initial_delay=0))
        return self.options.ClientOptions(**dict(zip(("compression",), compression, strict=False)), **options)

    @contextmanager
    def client(self, *compression: object, **options: Any) -> Iterator[Any]:
        """Yield a client that borrows a native client of the exchange, closing both afterwards."""
        with (
            self.exchange.client() as native,
            self.package.Client(http_client=native, options=self.settings(*compression, **options)) as api,
        ):
            yield api

    @asynccontextmanager
    async def async_client(self, *compression: object, **options: Any) -> AsyncIterator[Any]:
        """Yield an async client that borrows a native client of the exchange, closing both afterwards."""
        async with (
            self.exchange.async_client() as native,
            self.package.AsyncClient(http_client=native, options=self.settings(*compression, **options)) as api,
        ):
            yield api

    def call(self, **options: Any) -> Any:
        """Return request options that inherit the client's compression setting."""
        return self.options.RequestOptions(**options)

    def length(self, value: int) -> Any:
        """Return a typed Content-Length parameter whose value compression must replace."""
        return argument(self.package, "putBlob", "header", "Content-Length", value)

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


def _inherited(harness: _Harness, lines: list[str]) -> None:
    exchange = harness.exchange
    with harness.client() as api:
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
        record(lines, "file retried", lambda: api.items.put_blob(body=io.BytesIO(b"file body")))
        exchange.respond(raw_response(503))
        record(lines, "stream once", lambda: api.items.put_blob(body=iter((b"one", b"shot"))))
        exchange.respond(_created())
        record(lines, "raw view", lambda: api.items.with_raw_response.create_item(body=harness.item()).info.status_code)
        conflict = harness.call(headers=(("Content-Encoding", "br"),))
        record(lines, "header conflict", lambda: api.items.put_blob(body=b"x", options=conflict))
        exchange.respond(_stored())
        record(lines, "header without coding", lambda: api.items.create_note(body=harness.item(), options=conflict))
        moved = harness.call(follow_redirects=True)
        exchange.respond(raw_response(303, Location="https://api.example.com/items"), json_response(200, {"data": []}))
        record(
            lines,
            "redirect drops the body",
            lambda: api.items.with_raw_response.put_blob(body=b"x", options=moved).info.status_code,
        )
        exchange.respond(raw_response(307, Location="https://api.example.com/blobs?moved=1"), _stored())
        record(lines, "redirect keeps the body", lambda: api.items.put_blob(body=b"kept" * 50, options=moved))
    with harness.client(headers=(("Content-Encoding", "br"),)) as api:
        record(lines, "client header conflict", lambda: api.items.create_item(body=harness.item()))
    with harness.client(None) as api:
        exchange.respond(_stored())
        record(lines, "client disabled", lambda: api.items.put_blob(body=b"disabled"))


class _HeaderSigner:
    """Add a signature header without pre-reading the binary input."""

    def __init__(self, auth: ModuleType) -> None:
        self.auth = auth
        self.capabilities = auth.SignerCapabilities(
            allowed_origins=("https://api.example.com",),
            managed_headers=("x-signed",),
            managed_query=(),
        )

    def sign(self, request: Any) -> Any:
        return self.auth.SignatureFields(headers=(("x-signed", "yes"),), query=())


def _signed(harness: _Harness, lines: list[str]) -> None:
    exchange = harness.exchange
    auth = importlib.import_module(f"{harness.package.__name__}.auth")
    signer = _HeaderSigner(auth)

    def digested(request: httpx2.Request) -> httpx2.Response:
        lengths = request.headers.get_list("content-length")
        lines.append(f"    signed framing correct {lengths == [str(len(request.content))]}")
        return httpx2.Response(204)

    config = auth.AuthConfig(
        {}, send_on_anonymous=True, allowed_origins=("https://api.example.com",), signers=(signer,)
    )
    with harness.client("gzip", auth=config) as api:
        exchange.respond(digested)
        record(lines, "signed bytes", lambda: api.items.put_blob(body=b"signed", content_length=harness.length(6)))
        file = io.BytesIO(b"file")
        exchange.respond(raw_response(204))
        record(lines, "signed file", lambda: api.items.put_blob(body=file))
        lines.append(f"    signed caller file open {not file.closed}")
        file.close()


def _framing(harness: _Harness, lines: list[str]) -> None:
    """Replace a typed length after compression, keeping bytes sized and streamed bodies chunked."""
    exchange = harness.exchange

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
            lambda: api.items.put_blob(body=io.BytesIO(b"framed"), content_length=length),
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
                lambda: api.items.put_blob(body=io.BytesIO(b"framed"), content_length=length),
            )

    run(asynchronous)


def _results(*pages: tuple[list[str], str | None]) -> list[Any]:
    return [
        json_response(200, {"data": [{"name": name} for name in names], "next_cursor": cursor})
        for names, cursor in pages
    ]


def _helpers(harness: _Harness, lines: list[str]) -> None:
    exchange, protocols = harness.exchange, harness.protocols
    with harness.client() as api:
        helpers = api.protocols.items
        exchange.respond(*_results((["a"], "c2"), (["b"], None)))
        drained(lines, "cursor pages", helpers.search_all.iterate(body=harness.query()))
        exchange.respond(
            json_response(200, {"data": [{"name": "a"}], "next": "https://api.example.com/feed?page=2"}),
            json_response(200, {"data": [{"name": "b"}]}),
        )
        drained(lines, "followed pages", helpers.feed_all.iterate(body=harness.query()))
        exchange.respond(*_results((["a"], None)))
        record(lines, "bodyless pages", lambda: helpers.listing.page().items)
        exchange.respond(*_results((["a"], "c2")))
        first = helpers.search_all.page(body=harness.query())
        exchange.respond(*_results((["b"], None)))
        record(lines, "next page", lambda: helpers.search_all.next_page(first).items)
        exchange.respond(json_response(200, {"data": [{"name": "a"}], "next": "https://api.example.com/feed?page=2"}))
        followed = helpers.feed_all.page(body=harness.query())
        exchange.respond(json_response(200, {"data": [{"name": "b"}]}))
        record(lines, "next followed page", lambda: helpers.feed_all.next_page(followed).items)
        exchange.respond(
            json_response(202, {"id": "j1", "status": "running"}),
            json_response(200, {"id": "j1", "status": "done", "result": {"value": "ok"}}),
        )
        fast = protocols.PollOptions(interval=0.000001)
        handle = api.protocols.jobs.run.start(body=harness.query(), poll_options=fast)
        record(lines, "job", handle.wait)
        exchange.respond(
            json_response(202, {"id": "c1", "status": "running"}),
            json_response(200, {"id": "c1", "status": "done", "result": {"value": "ok"}}),
        )
        check = api.protocols.checks.run.start(poll_options=fast)
        state = check.checkpoint()
        check.close()
        resumed = api.protocols.checks.run.resume(state, poll_options=fast)
        record(lines, "check", resumed.wait)
        exchange.respond(json_response(200, {"id": "c1", "status": "done", "result": {"value": "ok"}}))
        record(lines, "completed check inherited resume", api.protocols.checks.run.resume(state).wait)
        exchange.respond(raw_response(200, b'data: {"text":"a"}\n\n', "text/event-stream"))
        with api.protocols.events.watch.open(body=harness.query()) as stream:
            lines.append(f"  events {[event.data for event in stream]}")
    with harness.client(None) as api:
        exchange.respond(*_results((["a"], None)))
        record(lines, "disabled helper body", lambda: api.protocols.items.search_all.page(body=harness.query()).items)


def _stream_resume(harness: _Harness, lines: list[str]) -> None:
    """Compress each stream open and resume according to that operation's declaration."""
    exchange, protocols = harness.exchange, harness.protocols
    event = raw_response(200, b'id: c1\ndata: {"text":"a"}\n\n', "text/event-stream")
    reconnect = protocols.StreamOptions(reconnect=True)
    with harness.client() as api:
        for name in ("resumable", "tail"):
            helper = getattr(api.protocols.events, name)
            exchange.respond(event)
            with helper.open(body=harness.query(), stream_options=reconnect) as stream:
                lines.append(f"  {name} first {next(stream).data}")
                state = stream.checkpoint()
            exchange.respond(event)
            with helper.resume(state, **({"body": harness.query()} if name == "resumable" else {})) as stream:
                lines.append(f"  {name} resume {next(stream).data}")
        helper = api.protocols.events.push
        exchange.respond(event)
        with helper.open(stream_options=reconnect) as stream:
            lines.append(f"  bodyless open {next(stream).data}")
            state = stream.checkpoint()
        exchange.respond(event)
        with helper.resume(state) as stream:
            lines.append(f"  cursor body resume {next(stream).data}")


async def _async(harness: _Harness, lines: list[str]) -> None:
    exchange = harness.exchange

    async def chunks() -> AsyncIterator[bytes]:  # ruff: ignore[unused-async]
        yield b"async "
        yield b"stream"

    async with harness.async_client() as api:
        exchange.respond(_created())
        await arecord(lines, "async declared", lambda: api.items.create_item(body=harness.item()))
        exchange.respond(raw_response(503), _stored())
        await arecord(lines, "async file retried", lambda: api.items.put_blob(body=io.BytesIO(b"file")))
        exchange.respond(_stored())
        await arecord(lines, "async stream", lambda: api.items.put_blob(body=chunks()))
        exchange.respond(_stored())
        await arecord(lines, "async undeclared", lambda: api.items.create_note(body=harness.item()))
        exchange.respond(*_results((["a"], "c2"), (["b"], None)))
        await adrained(lines, "async cursor pages", api.protocols.items.search_all.iterate(body=harness.query()))
        exchange.respond(*_results((["a"], None)))
        page = await api.protocols.items.listing.page()
        record(lines, "async bodyless pages", lambda: page.items)
        exchange.respond(
            json_response(202, {"id": "c1", "status": "running"}),
            json_response(200, {"id": "c1", "status": "done", "result": {"value": "ok"}}),
        )
        check = await api.protocols.checks.run.start(poll_options=harness.protocols.PollOptions(interval=0.000001))
        state = check.checkpoint()
        await arecord(lines, "async check", check.wait)
        exchange.respond(json_response(200, {"id": "c1", "status": "done", "result": {"value": "ok"}}))
        resumed = api.protocols.checks.run.resume(state)
        await arecord(lines, "async inherited completed check", resumed.wait)
        await arecord(
            lines,
            "async helper header conflict",
            lambda: api.protocols.jobs.run.start(
                body=harness.query(), options=harness.call(headers=(("Content-Encoding", "br"),))
            ),
        )
    async with harness.async_client(None) as api:
        exchange.respond(_stored())
        await arecord(lines, "async disabled", lambda: api.items.put_blob(body=b"disabled"))
    event = raw_response(200, b'id: c1\ndata: {"text":"a"}\n\n', "text/event-stream")
    async with harness.async_client() as api:
        for name in ("resumable", "tail"):
            helper = getattr(api.protocols.events, name)
            exchange.respond(event)
            async with await helper.open(body=harness.query()) as stream:
                lines.append(f"  async {name} first {(await anext(stream)).data}")
                state = stream.checkpoint()
            exchange.respond(event)
            async with await helper.resume(
                state, **({"body": harness.query()} if name == "resumable" else {})
            ) as stream:
                lines.append(f"  async {name} resume {(await anext(stream)).data}")
        exchange.respond(event)
        async with await api.protocols.events.push.open() as stream:
            lines.append(f"  async bodyless open {(await anext(stream)).data}")
            state = stream.checkpoint()
        exchange.respond(event)
        async with await api.protocols.events.push.resume(state) as stream:
            lines.append(f"  async cursor body resume {(await anext(stream)).data}")


def _native(harness: _Harness, lines: list[str]) -> None:
    """Observe default gzip and client disable over the SDK's native sync and async transports."""
    options = harness.options
    for mode in ("sync", "async"):
        for label, selection in (("default", {}), ("disabled", {"compression": None})):
            server = NativeFixture()
            server.status, server.body = 204, b""
            settings = options.ClientOptions(
                base_url=server.url,
                transport=options.TransportOptions(ssl_context=server.verify),
                retry=options.RetryOptions(max_retries=0),
                **selection,
            )
            try:
                if mode == "sync":
                    with harness.package.Client(options=settings) as api:
                        record(
                            lines, f"native {mode} {label}", lambda: api.items.put_blob(body=b"native declared body")
                        )
                else:

                    async def send(settings: Any = settings, label: str = label, mode: str = mode) -> None:
                        async with harness.package.AsyncClient(options=settings) as api:
                            await arecord(
                                lines,
                                f"native {mode} {label}",
                                lambda: api.items.put_blob(body=b"native declared body"),
                            )

                    run(send)
                for (method, path, body), fields in zip(server.requests, server.request_headers, strict=True):
                    headers = {name.lower(): value for name, value in fields}
                    encoded = headers.get(b"content-encoding")
                    decoded = gzip.decompress(body) if encoded == b"gzip" else body
                    lines.append(
                        f"    arrivals={len(server.requests)} method={method!r} path={path!r} encoding={encoded!r} "
                        f"length={headers.get(b'content-length')!r} bytes={len(body)} "
                        f"sha256={hashlib.sha256(decoded).hexdigest()} "
                        f"mtime={int.from_bytes(body[4:8], 'little') if encoded == b'gzip' else None}"
                    )
            finally:
                server.stop()


def compression(package: ModuleType, lines: list[str]) -> None:
    """Report declared gzip and client disable on ordinary calls, helpers, retries, and native transports."""
    exchange = Exchange(lines)
    harness = _Harness(package, exchange)
    for step in (_values, _inherited, _signed, _framing, _helpers, _stream_resume, _native):
        lines.append(f"# {step.__name__.strip('_')}")
        step(harness, lines)
    lines.append("# async")
    run(lambda: _async(harness, lines))
