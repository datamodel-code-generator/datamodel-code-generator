"""Read server-sent event streams through generated SSE helpers: framing, events, completions, limits, and lifetimes."""

from __future__ import annotations

import asyncio
import gzip
import importlib
import threading
import time
from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.data.python.client_runtime import (
    Exchange,
    aoutcome,
    arecord,
    chunked_response,
    describe,
    injected,
    json_response,
    outcome,
    raw_response,
    record,
    run,
)
from tests.data.python.client_transports import Adapter, AsyncAdapter, AsyncResponse, Response

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterable, Iterator
    from types import ModuleType

_STREAM: Final = "text/event-stream"
_FRAMES: Final = (
    b"\xef\xbb\xbf: a comment\r\n"
    b'data: {"text":\r\n'
    b'data:"multi"}\r\n'
    b"id: 7\r\n"
    b"\r\n"
    b"retry: 1500\r"
    b"event: note\r"
    b'data: {"text": "cr"}\r'
    b"\r"
    b"retry: soon\n"
    b"id: a\x00b\n"
    b"event: lonely\n"
    b"\n"
    b"unknown: field\n"
    b"data\n"
    b'data:{"text": "\xff"}\n'
    b"id\n"
    b"\n"
)
_TYPED: Final = (
    b'event: created\ndata: {"type": "created", "id": "1"}\n\n'
    b'event: renamed\ndata: {"id": "2"}\n\n'
    b'event: deleted\nid: 3\ndata: {"type": "deleted", "id": "3"}\n\n'
    b"event: done\ndata: {}\n\n"
    b'event: created\ndata: {"type": "created", "id": "never"}\n\n'
)
_TAGGED: Final = (
    b'data: {"type": "created", "id": "1"}\n\n'
    b'event: anything\ndata: {"type": "deleted", "id": "2"}\n\n'
    b"data: [DONE]\n\n"
    b'data: {"type": "created", "id": "never"}\n\n'
)


def _data(value: object) -> object:
    """Return an event's data as plain values, whatever its backend: a model, a dataclass, a Struct, or a dict."""
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if hasattr(value, "__struct_fields__"):
        return {name: getattr(value, name) for name in value.__struct_fields__}
    return value


def _event(event: Any) -> str:
    """Describe an event by its sequence, type, ID, reconnection time, data, and raw data."""
    return (
        f"{event.sequence} {event.event_type} id={event.event_id!r} retry={event.retry_ms} "
        f"data={type(event.data).__name__}{_data(event.data)!r} raw={event.raw_data!r}"
    )


def _drained(lines: list[str], label: str, events: Iterable[Any]) -> None:
    """Report every event a stream yields, then its end or the failure that stopped it."""
    lines.append(f"  {label}")
    try:
        for event in events:
            lines.append(f"    {_event(event)}")
    except Exception as error:  # noqa: BLE001
        lines.append(f"    ! {describe(error)}")
        return
    lines.append("    end")


async def _adrained(lines: list[str], label: str, events: AsyncIterator[Any]) -> None:
    """Report every event an asyncio stream yields, then its end or the failure that stopped it."""
    lines.append(f"  {label}")
    try:
        async for event in events:
            lines.append(f"    {_event(event)}")
    except Exception as error:  # noqa: BLE001
        lines.append(f"    ! {describe(error)}")
        return
    lines.append("    end")


def _header(headers: Iterable[tuple[str, str]], name: str) -> str | None:
    return next((value for key, value in headers if key.lower() == name), None)


def _pieces(content: bytes, size: int) -> tuple[bytes, ...]:
    return tuple(content[start : start + size] for start in range(0, len(content), size))


class _Feed(Adapter):
    """An adapter whose replies stream exact chunks, reporting only each request line."""

    def send(self, request: Any, context: Any) -> Any:
        self.lines.append(f"  > {request.method} {request.url} accept={_header(request.headers, 'accept')}")
        return self.replies.pop(0)(request, context)


class _AsyncFeed(AsyncAdapter):
    """The asyncio form of the exact-chunk adapter."""

    async def send(self, request: Any, context: Any) -> Any:
        self.lines.append(f"  > {request.method} {request.url} accept={_header(request.headers, 'accept')}")
        return self.replies.pop(0)(request, context)


class _Probed(Response):
    """A canned response whose chunks may also be probes, called between chunks to act while the stream reads."""

    def iter_raw_bytes(self) -> Iterator[object]:
        for chunk in self.chunks:
            if callable(chunk):
                chunk()
            elif isinstance(chunk, BaseException):
                raise chunk
            else:
                yield chunk


class _AsyncProbed(AsyncResponse):
    """The asyncio form of a probed response, whose probes are awaited."""

    async def iter_raw_bytes(self) -> AsyncIterator[object]:
        for chunk in self.chunks:
            if callable(chunk):
                await chunk()
            elif isinstance(chunk, BaseException):
                raise chunk
            else:
                yield chunk


class _Harness:
    """A generated stream package's public modules, its models, and the replies its adapters give."""

    def __init__(self, package: ModuleType, lines: list[str]) -> None:
        self.package = package
        self.lines = lines
        self.options, self.protocols, self.errors, self.responses = (
            importlib.import_module(f"{package.__name__}.{name}")
            for name in ("options", "protocols", "errors", "responses")
        )
        self.models = importlib.import_module(f"{package.__name__}_models")

    def reply(
        self,
        chunks: tuple[object, ...],
        *,
        status: int = 200,
        media: str | None = _STREAM,
        response: type[Response] = Response,
        headers: tuple[tuple[str, str], ...] = (),
    ) -> Callable[[Any, Any], Any]:
        """Return a reply that streams the chunks with a status, a Content-Type, and other headers."""
        fields = self.responses.HeadersView([*headers, *([] if media is None else [("content-type", media)])])
        return lambda request, context: response(self.lines, status, fields, chunks)

    def interrupted(self) -> Any:
        """Return the transport failure of a connection broken while its body streams."""
        errors = self.errors
        return errors.TransportError(delivery_state=errors.DeliveryState.RESPONSE_STARTED, phase="read")

    def idle(self) -> Any:
        """Return the failure of a read that waited longer than its phase cap."""
        errors = self.errors
        return errors.PhaseTimeoutError(
            effective_timeout=1, phase="read", delivery_state=errors.DeliveryState.RESPONSE_STARTED
        )

    def topic(self, wire: str) -> object:
        """Return the topic argument of the events operation for a wire value."""
        types = importlib.import_module(f"{self.package.__name__}.types.events")
        return types.StreamEventsRequestCodecs.parameter(location="query", name="topic").from_wire(wire)

    def schema(self) -> Any:
        """Return call options that validate responses against their schemas."""
        options = self.options
        return options.RequestOptions(validation=options.ValidationOptions(response="schema"))


def streams(package: ModuleType, lines: list[str]) -> None:
    """Parse, route, decode, end, and limit SSE streams of exact chunks through the synchronous and asyncio clients."""
    harness = _Harness(package, lines)
    adapter = _Feed(importlib.import_module(f"{package.__name__}.transports"), lines)
    with package.Client(transport_adapter=adapter) as api:
        _framing(harness, api, adapter)
        _completions(harness, api, adapter)
        _routing(harness, api, adapter)
        _decoding(harness, api, adapter)
        _limits(harness, api, adapter)
        _opens(harness, api, adapter)
        _states(harness, api, adapter)
        _hooked(harness, api, adapter)
    run(lambda: _async_streams(package, lines))
    run(lambda: _async_hooked(harness))


class _Ends:
    """A hook reporting the outcome of each handed-over stream's end."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    def on_event(self, event: Any) -> None:
        if event.name == "stream_end":
            self.lines.append(f"    hook {event.name} outcome={event.outcome}")


class _AsyncEnds(_Ends):
    """The asyncio form of the stream end hook."""

    async def on_event(self, event: Any) -> None:
        super().on_event(event)


_ENDINGS: Final = (
    ("end at EOF", "messages", (b'data: {"text": "a"}\n\n',)),
    ("end at the sentinel", "feed", (b'data: {"text": "a"}\n\n', b"data: [DONE]\n\n", b'data: {"text": "b"}\n\n')),
    ("cut frame at EOF", "messages", (b'data: {"text": "a"}\n\n', b"data: x")),
    ("EOF before the sentinel", "feed", (b'data: {"text": "a"}\n\n',)),
    ("undecodable event", "messages", (b"data: {}\n\n",)),
)


def _hooked(harness: _Harness, api: Any, adapter: _Feed) -> None:
    """Report a stream's end to the hooks as it ended: at its completion, failing, or closed early."""
    lines = harness.lines
    hooked = api.with_options(harness.options.RequestOptions(hooks=(_Ends(lines),)))
    adapter.replies.extend(harness.reply(chunks) for _, _, chunks in _ENDINGS)
    adapter.replies.append(harness.reply((b'data: {"text": "a"}\n\n',) * 2))
    for label, helper, _ in _ENDINGS:
        stream = (
            hooked.protocols.events.messages.open()
            if helper == "messages"
            else hooked.protocols.feed.all.open(body=harness.models.FeedQuery())
        )
        _drained(lines, f"hooked {label}", stream)
    stream = hooked.protocols.events.messages.open()
    lines.append(f"  hooked early close after {_event(next(stream))}")
    stream.close()


async def _async_hooked(harness: _Harness) -> None:
    """Report an asyncio stream's end to the hooks as it ended."""
    lines, package = harness.lines, harness.package
    adapter = _AsyncFeed(importlib.import_module(f"{package.__name__}.transports"), lines)
    adapter.replies.extend(harness.reply(chunks, response=AsyncResponse) for _, _, chunks in _ENDINGS)
    adapter.replies.append(harness.reply((b'data: {"text": "a"}\n\n',) * 2, response=AsyncResponse))
    options = harness.options.RequestOptions(hooks=(_AsyncEnds(lines),))
    async with package.AsyncClient(transport_adapter=adapter) as api:
        hooked = api.with_options(options)
        for label, helper, _ in _ENDINGS:
            stream = await (
                hooked.protocols.events.messages.open()
                if helper == "messages"
                else hooked.protocols.feed.all.open(body=harness.models.FeedQuery())
            )
            await _adrained(lines, f"async hooked {label}", stream)
        stream = await hooked.protocols.events.messages.open()
        lines.append(f"  async hooked early close after {_event(await anext(stream))}")
        await stream.aclose()


def _framing(harness: _Harness, api: Any, adapter: _Feed) -> None:
    """Interpret one stream split at every byte and in one piece, and fields of every kind, as WHATWG does."""
    lines, helper = harness.lines, api.protocols.events.messages
    adapter.replies.extend((harness.reply(_pieces(_FRAMES, 1)), harness.reply((_FRAMES,))))
    with helper.open() as stream:
        _drained(lines, "one byte at a time", stream)
        lines.append(f"    response {stream.response.status_code} progress {dict(stream.progress)}")
    with helper.open() as stream:
        _drained(lines, "in one chunk", stream)
    adapter.replies.extend((
        harness.reply((b"\xef\xbb", b'\xbfdata: {"text": "bom"}\n\n')),
        harness.reply((b"\xef\xbbdata: {}\n\n",)),
        harness.reply((b"\xef",)),
        harness.reply(()),
    ))
    _drained(lines, "byte order mark split", helper.open(options=harness.schema()))
    _drained(lines, "partial byte order mark", helper.open())
    _drained(lines, "byte order mark prefix at the end", helper.open())
    _drained(lines, "empty body", helper.open())


def _completions(harness: _Harness, api: Any, adapter: _Feed) -> None:
    """End streams at EOF, a sentinel, or an event type, and refuse an early or cut end."""
    lines, protocols = harness.lines, api.protocols
    message = b'data: {"text": "a"}\n\n'
    adapter.replies.extend((
        harness.reply((message, b"data: x")),
        harness.reply((message, b'data: {"text": "b"}\n')),
        harness.reply((message, b": only a comment\n")),
        harness.reply((message,)),
        harness.reply((message, b"data: [DONE]\n\n", b'data: {"text": "after"}\n\n')),
        harness.reply((b"data: [DONE]", b"\n\n")),
    ))
    _drained(lines, "cut line at EOF", protocols.events.messages.open())
    _drained(lines, "cut frame at EOF", protocols.events.messages.open())
    _drained(lines, "comment before EOF", protocols.events.messages.open())
    _drained(lines, "EOF before the sentinel", protocols.feed.all.open(body=harness.models.FeedQuery()))
    stream = protocols.feed.all.open(body=harness.models.FeedQuery(topic="a"))
    _drained(lines, "sentinel", stream)
    record(lines, "after the end", lambda: next(stream))
    stream.close()
    record(lines, "closed after the end", lambda: next(stream))
    _drained(lines, "sentinel at once", protocols.feed.all.open(body=harness.models.FeedQuery()))
    adapter.replies.append(harness.reply(_pieces(_TYPED, 7)))
    _drained(lines, "event type completion", protocols.events.typed.open())


def _routing(harness: _Harness, api: Any, adapter: _Feed) -> None:
    """Route events by their SSE type or a body member, keep unknown ones, and raise error events."""
    lines, protocols = harness.lines, api.protocols
    created = b'event: created\ndata: {"type": "created", "id": "1"}\n\n'
    error = b'event: error\nid: 9\ndata: {"message": "boom"}\n\n'
    adapter.replies.extend((
        harness.reply((created, error)),
        harness.reply((_TAGGED,)),
        harness.reply((b'data: {"type": "failed", "message": "nope"}\n\n',)),
        harness.reply((b'data: {"id": "1"}\n\n',)),
        harness.reply((b'data: {"type": null}\n\n',)),
        harness.reply((b'data: {"type": 5}\n\n',)),
        harness.reply((b'data: {"type": "renamed"}\n\n',)),
        harness.reply((b'data: {"type": "created"}\n\n',)),
    ))
    stream = protocols.events.typed.open()
    lines.append(f"  before the error {_event(next(stream))}")
    try:
        next(stream)
    except harness.errors.StreamRemoteError as failure:
        lines.append(
            f"  error event {failure.event_type} {_data(failure.data)!r} {failure.sequence} {describe(failure)}"
        )
    record(lines, "after the error", lambda: next(stream))
    tagged = protocols.events.tagged
    _drained(lines, "body discriminator", tagged.open())
    stream = tagged.open()
    try:
        next(stream)
    except harness.errors.StreamRemoteError as failure:
        lines.append(f"  body error event {failure.event_type} {_data(failure.data)!r} {failure.sequence}")
    for label in ("missing discriminator", "null discriminator", "number discriminator", "unknown discriminator"):
        _drained(lines, label, tagged.open())
    _drained(lines, "mapped event failing its schema", tagged.open(options=harness.schema()))


def _decoding(harness: _Harness, api: Any, adapter: _Feed) -> None:
    """Refuse event data that is not JSON or that its schema or type refuses, keeping a bounded raw prefix."""
    lines, helper = harness.lines, api.protocols.events.messages
    large = b'data: "' + b"x" * 70000 + b'"\n\n'
    adapter.replies.extend((
        harness.reply((b"data: {broken\n\n",)),
        harness.reply((b'data: {"text": 5}\n\n',)),
        harness.reply((b"data: {}\n\n",)),
        harness.reply((large,)),
        harness.reply((b'data: {"type": "created"}\n\n',)),
    ))
    for label, options in (
        ("not JSON", None),
        ("refused by its schema", harness.schema()),
        ("refused natively", None),
    ):
        _drained(lines, label, helper.open(options=options))
    try:
        next(helper.open())
    except harness.errors.StreamDecodeError as failure:
        lines.append(f"  large event raw prefix {len(failure.raw_prefix)} truncated={failure.truncated}")
    try:
        next(api.protocols.events.tagged.open(options=harness.schema()))
    except harness.errors.StreamDecodeError as failure:
        lines.append(f"  invalid mapped event cause {type(failure.cause).__name__} prefix {failure.raw_prefix!r}")


def _limits(harness: _Harness, api: Any, adapter: _Feed) -> None:
    """Refuse lines and event data over their limits, from the call's options or the helper's defaults."""
    lines, protocols, options = harness.lines, api.protocols, harness.options
    limited = harness.protocols.StreamOptions(max_line_bytes=12, max_event_bytes=20)
    adapter.replies.extend((
        harness.reply((b'data: {"text": "a"}', b"\n\n")),
        harness.reply((b'data: {"text":\ndata: "abcdefghijk"}\n\n',)),
        harness.reply((b'data: {"text":\ndata: "abc"}\n\n',)),
        harness.reply((b": a comment over the line limit\n",)),
    ))
    _drained(lines, "line over the limit", protocols.events.messages.open(stream_options=limited))
    wide = harness.protocols.StreamOptions(max_line_bytes=64, max_event_bytes=20)
    _drained(lines, "event over the limit", protocols.events.messages.open(stream_options=wide))
    _drained(lines, "event at the limit", protocols.events.messages.open(stream_options=wide))
    defaults = harness.protocols.ProtocolDefaults(options=limited)
    client_options = options.ClientOptions(
        protocols=options.ProtocolClientOptions(defaults={"events.messages": defaults})
    )
    with harness.package.Client(transport_adapter=adapter, options=client_options) as limiting:
        _drained(lines, "comment over the default limit", limiting.protocols.events.messages.open())
    drip = b":" + b"x" * 62 + b"\n" + b'data: {"text": "drip"}\n\n'
    adapter.replies.extend((
        harness.reply((b"data: {", b'"text": "a"}\n\n')),
        harness.reply((b"data: {", b'"text"')),
        harness.reply(_pieces(drip, 1)),
    ))
    for label in ("line a later chunk ends over the limit", "line a later chunk extends over the limit"):
        _drained(lines, label, protocols.events.messages.open(stream_options=limited))
    _drained(
        lines,
        "line just under the limit, one byte at a time",
        protocols.events.messages.open(stream_options=harness.protocols.StreamOptions(max_line_bytes=64)),
    )
    for label, call in (
        (
            "stream options of another type",
            lambda: protocols.events.messages.open(stream_options=options.RequestOptions()),
        ),
        ("options of another type", lambda: protocols.events.messages.open(options=harness.protocols.StreamOptions())),
        ("session options of another type", lambda: protocols.events.messages.open(session_options={})),
        (
            "reconnecting",
            lambda: protocols.events.messages.open(stream_options=harness.protocols.StreamOptions(reconnect=True)),
        ),
        (
            "no send slot",
            lambda: protocols.events.messages.open(session_options=options.SessionOptions(max_network_sends=0)),
        ),
        (
            "call without a send slot",
            lambda: protocols.events.messages.open(options=options.RequestOptions(max_network_sends=0)),
        ),
    ):
        record(lines, label, call)
    adapter.replies.append(harness.reply((), status=302, media=None, headers=(("location", "/events?moved=1"),)))
    redirects = options.RequestOptions(redirects=options.RedirectOptions(enabled=True))
    record(
        lines,
        "redirect without a session send slot",
        lambda: protocols.events.messages.open(
            options=redirects, session_options=options.SessionOptions(max_network_sends=1)
        ),
    )


def _opens(harness: _Harness, api: Any, adapter: _Feed) -> None:
    """Return a stream only for a declared success of the event stream media, retrying its acquisition."""
    lines, helper, options = harness.lines, api.protocols.events.messages, harness.options
    adapter.replies.extend((
        harness.reply((b'{"detail": "missing"}',), status=404, media="application/json"),
        harness.reply((), status=204, media=None),
        harness.reply((b"data: {}\n\n",), status=201),
        harness.reply((b"data: {}\n\n",), media=None),
        harness.reply((b"busy",), status=503, media="text/plain"),
        harness.reply((b'data: {"text": "retried"}\n\n',)),
    ))
    for label in ("declared error status", "no content", "undeclared success status", "no media type"):
        record(lines, label, helper.open)
    retry = options.RequestOptions(retry=options.RetryOptions(initial_delay=0, jitter="none"))
    stream = helper.open(topic=harness.topic("news"), options=retry)
    lines.append(f"  retried {stream.response.status_code} sends={stream.response.network_send_count}")
    lines.append(f"    progress {dict(stream.progress)}")
    _drained(lines, "retried stream", stream)


def _states(harness: _Harness, api: Any, adapter: _Feed) -> None:
    """Close streams, refuse a concurrent step or close, and interrupt one whose connection breaks."""
    lines, helper = harness.lines, api.protocols.events.messages
    message = b'data: {"text": "a"}\n\n'
    stream: Any = None

    def concurrent() -> None:
        lines.append(f"    concurrent next: {outcome(lambda: next(stream))}")
        lines.append(f"    concurrent close: {outcome(stream.close)}")

    adapter.replies.extend((
        harness.reply((message, message)),
        harness.reply((message, concurrent, message), response=_Probed),
        harness.reply((message, harness.interrupted())),
        harness.reply((message, harness.idle())),
        harness.reply((b"[]",), media="application/json"),
    ))
    with helper.open() as stream:
        lines.append(f"  first {_event(next(stream))}")
    record(lines, "after close", lambda: next(stream))
    record(lines, "close again", stream.close)
    stream = helper.open()
    lines.append(f"  data {[_data(value) for value in stream.data()]!r}")
    _drained(lines, "broken connection", helper.open())
    _drained(lines, "read phase timeout", helper.open())
    record(lines, "another media type", helper.open)


async def _async_streams(package: ModuleType, lines: list[str]) -> None:
    """Parse, route, end, and close streams with the asyncio client."""
    harness = _Harness(package, lines)
    adapter = _AsyncFeed(importlib.import_module(f"{package.__name__}.transports"), lines)
    async with package.AsyncClient(transport_adapter=adapter) as api:
        protocols = api.protocols
        adapter.replies.extend((
            harness.reply(_pieces(_FRAMES, 1), response=AsyncResponse),
            harness.reply(_pieces(_TYPED, 5), response=AsyncResponse),
            harness.reply((_TAGGED,), response=AsyncResponse),
            harness.reply((b'data: {"text": "a"}\n\n', b"data: cut"), response=AsyncResponse),
            harness.reply((b'data: {"text": "a"}\n\n', harness.interrupted()), response=AsyncResponse),
            harness.reply((b'data: {"text": "a"}\n\n' * 2,), response=AsyncResponse),
        ))
        async with await protocols.events.messages.open() as stream:
            await _adrained(lines, "async one byte at a time", stream)
        await _adrained(lines, "async event type completion", await protocols.events.typed.open())
        await _adrained(lines, "async body discriminator", await protocols.events.tagged.open())
        await _adrained(lines, "async cut frame", await protocols.events.messages.open())
        await _adrained(lines, "async broken connection", await protocols.events.messages.open())
        stream = await protocols.events.messages.open()
        lines.append(f"  async data {[_data(value) async for value in stream.data()]!r}")
        await arecord(lines, "async after the end", lambda: anext(stream))
        await stream.aclose()
        await arecord(lines, "async options of another type", lambda: protocols.events.messages.open(options=1))
        options = harness.options
        adapter.replies.extend((
            harness.reply((b'data: {"text": "a"}\n\n' * 2,), response=AsyncResponse),
            harness.reply(
                (), status=302, media=None, headers=(("location", "/events?moved=1"),), response=AsyncResponse
            ),
        ))
        async with await protocols.events.messages.open() as stream:
            lines.append(f"  async left early {_event(await anext(stream))}")
        await arecord(
            lines,
            "async call without a send slot",
            lambda: protocols.events.messages.open(options=options.RequestOptions(max_network_sends=0)),
        )
        await arecord(
            lines,
            "async redirect without a session send slot",
            lambda: protocols.events.messages.open(
                options=options.RequestOptions(redirects=options.RedirectOptions(enabled=True)),
                session_options=options.SessionOptions(max_network_sends=1),
            ),
        )


class _Paced(httpx2.SyncByteStream):
    """A server body that sends its parts, waiting at each event among them until it is set."""

    def __init__(self, parts: tuple[bytes | threading.Event, ...]) -> None:
        self.parts = parts

    def __iter__(self) -> Iterator[bytes]:
        for part in self.parts:
            if isinstance(part, threading.Event):
                part.wait(10)
            else:
                yield part


def _paced(*parts: bytes | threading.Event, **headers: str) -> Callable[[httpx2.Request], httpx2.Response]:
    return lambda _: httpx2.Response(200, headers={"content-type": _STREAM, **headers}, stream=_Paced(parts))


def _expired(deadline: Any) -> Callable[[], None]:
    """Return a probe that returns only once a deadline has passed."""

    def wait() -> None:
        while deadline.remaining() > 0:
            time.sleep(0.01)

    return wait


def _aexpired(deadline: Any) -> Callable[[], Any]:
    """Return an asyncio probe that returns only once a deadline has passed."""

    async def wait() -> None:
        while deadline.remaining() > 0:
            await asyncio.sleep(0.01)

    return wait


def stream_lifetimes(package: ModuleType, lines: list[str]) -> None:
    """Bound streams by idle and deadline limits, release them, and keep a borrowed pool usable, in both modes."""
    harness = _Harness(package, lines)
    message = b'data: {"text": "a"}\n\n'
    exchange = Exchange(lines)
    options = harness.options
    with exchange.client(connections=1) as native:
        with package.Client(http_client=native) as api:
            helper = api.protocols.events.messages
            gate = threading.Event()
            exchange.respond(_paced(message, gate, message), _paced(message, message))
            try:
                stream = helper.open(stream_options=harness.protocols.StreamOptions(idle_timeout=1.0))
                lines.append(f"  idle first {_event(next(stream))}")
                record(lines, "idle while waiting for bytes", lambda: next(stream))
            finally:
                gate.set()
            stream = helper.open(stream_options=harness.protocols.StreamOptions(idle_timeout=1.0))
            lines.append(f"  paused first {_event(next(stream))}")
            time.sleep(1.1)
            _drained(lines, "after a pause longer than the idle limit", stream)
            exchange.respond(
                _paced(message, message),
                _paced(message),
                _paced(gzip.compress(message, mtime=0), **{"content-encoding": "gzip"}),
            )
            first = helper.open()
            lines.append(f"  pooled first {_event(next(first))}")
            first.close()
            _drained(lines, "pooled again", helper.open())
            _drained(lines, "content coded", helper.open())
        exchange.respond(json_response(200, {"text": "ok"}))
        with package.Client(http_client=native) as again:
            record(lines, "borrowed pool after the client closed", again.status.get_status)
    adapter = _Feed(importlib.import_module(f"{package.__name__}.transports"), lines)
    deadline = options.Deadline.after(1.0)
    adapter.replies.extend((
        harness.reply((_expired(deadline), message), response=_Probed),
        harness.reply((message + message,)),
        harness.reply((message, message)),
    ))
    api = package.Client(transport_adapter=adapter, options=options.ClientOptions(cleanup_timeout=0.05))
    helper = api.protocols.events.messages
    _drained(
        lines, "session deadline after handoff", helper.open(session_options=options.SessionOptions(deadline=deadline))
    )
    buffered = options.Deadline.after(1.0)
    stream = helper.open(session_options=options.SessionOptions(deadline=buffered))
    lines.append(f"  buffered first {_event(next(stream))}")
    _expired(buffered)()
    record(lines, "buffered event after the session deadline", lambda: next(stream))
    stream = helper.open()
    lines.append(f"  before the client closes {_event(next(stream))}")
    record(lines, "client close with an open stream", api.close)
    record(lines, "after the client closed", lambda: next(stream))
    run(lambda: _async_lifetimes(harness))


async def _async_lifetimes(harness: _Harness) -> None:
    """Bound asyncio streams by their idle limit and deadlines, cancel one, and close their client."""
    lines, package, options = harness.lines, harness.package, harness.options
    message = b'data: {"text": "a"}\n\n'
    adapter = _AsyncFeed(importlib.import_module(f"{package.__name__}.transports"), lines)
    never = asyncio.Event()
    reached = asyncio.Event()

    async def mark() -> None:
        reached.set()

    adapter.replies.extend((
        harness.reply((message, never.wait), response=_AsyncProbed),
        harness.reply((message, message), response=_AsyncProbed),
        *(harness.reply((never.wait,), response=_AsyncProbed) for _ in range(3)),
        harness.reply((message + message,), response=_AsyncProbed),
        harness.reply((message, mark, never.wait), response=_AsyncProbed),
        harness.reply((message, message), response=_AsyncProbed),
    ))
    api = package.AsyncClient(transport_adapter=adapter, options=options.ClientOptions(cleanup_timeout=0.05))
    helper = api.protocols.events.messages
    stream = await helper.open(stream_options=harness.protocols.StreamOptions(idle_timeout=0.1))
    lines.append(f"  async idle first {_event(await anext(stream))}")
    await arecord(lines, "async idle while waiting for bytes", lambda: anext(stream))
    stream = await helper.open(stream_options=harness.protocols.StreamOptions(idle_timeout=0.1))
    lines.append(f"  async paused first {_event(await anext(stream))}")
    await asyncio.sleep(0.2)
    await _adrained(lines, "async after a pause longer than the idle limit", stream)
    for label, session, request in (
        ("async session deadline", True, None),
        ("async session deadline before the stream total", True, 10.0),
        ("async stream total before the session deadline", False, 0.2),
    ):
        stream = await helper.open(
            stream_options=harness.protocols.StreamOptions(idle_timeout=5.0),
            options=options.RequestOptions(stream_total_timeout=request),
            session_options=options.SessionOptions(
                deadline=options.Deadline.after(1.0) if session else None, total_timeout=10.0
            ),
        )
        await _adrained(lines, label, stream)
    buffered = options.Deadline.after(1.0)
    stream = await helper.open(session_options=options.SessionOptions(deadline=buffered))
    lines.append(f"  async buffered first {_event(await anext(stream))}")
    await _aexpired(buffered)()
    await arecord(lines, "async buffered event after the session deadline", lambda: anext(stream))
    stream = await helper.open()
    lines.append(f"  async cancelled first {_event(await anext(stream))}")
    task = asyncio.create_task(anext(stream))
    await reached.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        lines.append("  async step cancelled")
    await arecord(lines, "async after the cancellation", lambda: anext(stream))
    stream = await helper.open()
    lines.append(f"  async before the client closes {_event(await anext(stream))}")
    await arecord(lines, "async client close with an open stream", api.aclose)
    await arecord(lines, "async after the client closed", lambda: anext(stream))
    lines.append(f"  async outcome of closing {await aoutcome(stream.aclose)}")
    await _inherited_idle(harness, adapter, never)


async def _inherited_idle(harness: _Harness, adapter: _AsyncFeed, never: asyncio.Event) -> None:
    """Take the client's stream idle timeout unless the stream options set one, None keeping the session deadline."""
    lines, options = harness.lines, harness.options
    adapter.replies.extend(harness.reply((never.wait,), response=_AsyncProbed) for _ in range(2))
    settings = options.ClientOptions(stream_idle_timeout=0.1, cleanup_timeout=0.05)
    async with harness.package.AsyncClient(transport_adapter=adapter, options=settings) as api:
        helper = api.protocols.events.messages
        stream = await helper.open(stream_options=harness.protocols.StreamOptions())
        await arecord(lines, "async idle the client sets", lambda: anext(stream))
        stream = await helper.open(
            stream_options=harness.protocols.StreamOptions(idle_timeout=None),
            session_options=options.SessionOptions(deadline=options.Deadline.after(1.0)),
        )
        await arecord(lines, "async no idle limit before the session deadline", lambda: anext(stream))


def stream_backends(package: ModuleType, lines: list[str]) -> None:
    """Decode the events and error events of every helper with each model backend."""
    harness = _Harness(package, lines)
    adapter = _Feed(importlib.import_module(f"{package.__name__}.transports"), lines)
    adapter.replies.extend((
        harness.reply((b'data: {"text": "a"}\n\n',)),
        harness.reply((_TYPED,)),
        harness.reply((_TAGGED,)),
        harness.reply((b'event: error\ndata: {"message": "boom"}\n\n',)),
    ))
    with package.Client(transport_adapter=adapter) as api:
        protocols = api.protocols
        _drained(lines, "messages", protocols.events.messages.open())
        _drained(lines, "typed", protocols.events.typed.open())
        _drained(lines, "tagged", protocols.events.tagged.open())
        try:
            next(protocols.events.typed.open())
        except harness.errors.StreamRemoteError as failure:
            lines.append(f"  error event {failure.event_type} {_data(failure.data)!r}")


_NDJSON: Final = "application/x-ndjson"
_RECORDS: Final = b'{"text": "a"}\n{"text": "crlf"}\r\n{"text": "caf\xc3\xa9"}\n  {"text": "spaced"}  \n'
_RECORD_ROUTES: Final = (
    b'{"type": "created", "id": "1"}\n'
    b'{"type": "renamed", "id": "2"}\n'
    b'{"type": "deleted", "id": "3"}\r\n'
    b"[DONE]\n"
    b'{"type": "created", "id": "never"}\n'
)


def _lines(harness: _Harness, chunks: tuple[object, ...], **settings: Any) -> Callable[[Any, Any], Any]:
    """Return a reply that streams NDJSON chunks."""
    return harness.reply(chunks, **{"media": _NDJSON, **settings})


def _decode_failure(lines: list[str], label: str, stream: Any) -> None:
    """Report the records a stream yields before its decode failure, with the failure's raw prefix and cause."""
    lines.append(f"  {label}")
    try:
        for event in stream:
            lines.append(f"    {_event(event)}")
    except Exception as error:  # noqa: BLE001
        lines.append(f"    ! {describe(error)}")
        lines.append(
            f"    raw prefix {error.raw_prefix!r} cause {type(error.cause).__name__} "
            f"context {type(error.__context__).__name__}"
        )


def ndjson(package: ModuleType, lines: list[str]) -> None:
    """Split, decode, route, end, and limit NDJSON streams of exact chunks through the synchronous and asyncio clients."""
    harness = _Harness(package, lines)
    adapter = _Feed(importlib.import_module(f"{package.__name__}.transports"), lines)
    with package.Client(transport_adapter=adapter) as api:
        _ndjson_framing(harness, api, adapter)
        _ndjson_endings(harness, api, adapter)
        _ndjson_routing(harness, api, adapter)
        _ndjson_limits(harness, api, adapter)
        _ndjson_states(harness, api, adapter)
    run(lambda: _async_ndjson(harness))


def _ndjson_framing(harness: _Harness, api: Any, adapter: _Feed) -> None:
    """Split records at LF or CRLF, one byte at a time and in one piece, and refuse lines that are not UTF-8 JSON."""
    lines, helper = harness.lines, api.protocols.records.all
    adapter.replies.extend((_lines(harness, _pieces(_RECORDS, 1)), _lines(harness, (_RECORDS,))))
    with helper.open() as stream:
        _drained(lines, "one byte at a time", stream)
        lines.append(f"    response {stream.response.status_code} progress {dict(stream.progress)}")
    _drained(lines, "in one chunk", helper.open())
    adapter.replies.extend(
        _lines(harness, (body,))
        for body in (
            b'{"text": "a"}\n\n{"text": "b"}\n',
            b" \t\n",
            b'{"text": "\xff"}\n',
            b'\xef\xbb\xbf{"text": "a"}\n',
            b'{"text": "a"}\r{"text": "b"}\n',
            b'{"text": "a"}\n{"text": 5}\n',
        )
    )
    for label in (
        "blank line",
        "whitespace line",
        "invalid UTF-8",
        "byte order mark",
        "CR without LF",
        "record refused by its schema",
    ):
        _decode_failure(lines, label, helper.open(options=harness.schema() if "schema" in label else None))


def _ndjson_endings(harness: _Harness, api: Any, adapter: _Feed) -> None:
    """End streams at EOF or a sentinel line, decoding a final line without a line end only where allowed."""
    lines, records, search = harness.lines, api.protocols.records, api.protocols.search.all
    adapter.replies.extend((
        _lines(harness, (b'{"text": "a"}\n{"text": "b"}',)),
        _lines(harness, (b'{"text": "a"}\n',)),
        _lines(harness, ()),
        _lines(harness, _pieces(b'{"text": "a"}\n{"text": "b"}', 5)),
        _lines(harness, (b'{"text": "a"}\r',)),
        _lines(harness, (b'{"text"',)),
        _lines(harness, (b"\xff",)),
    ))
    _drained(lines, "final line without a line end", records.all.open())
    _drained(lines, "end after a line end", records.all.open())
    _drained(lines, "empty body", records.all.open())
    stream = records.lenient.open()
    _drained(lines, "final line allowed at the end", stream)
    record(lines, "after the end", lambda: next(stream))
    _drained(lines, "final line with a CR", records.lenient.open())
    _decode_failure(lines, "invalid final line", records.lenient.open())
    _decode_failure(lines, "final line not UTF-8", records.lenient.open())
    media = "application/jsonl; charset=utf-8"
    adapter.replies.extend(
        _lines(harness, chunks, media=media)
        for chunks in (
            (b'{"text": "a"}\n[DONE]\n{"text": "after"}\n',),
            (b'{"text": "a"}\n', b"[DONE]"),
            (b"[DONE]\r\n",),
            (b'{"text": "a"}\n',),
        )
    )
    for label in ("sentinel", "sentinel as the final line", "sentinel with CRLF", "EOF before the sentinel"):
        _drained(lines, label, search.open(body=harness.models.SearchQuery(text="a")))
    adapter.replies.append(_lines(harness, (b"[DONE]",)))
    _drained(lines, "sentinel without a required line end", records.tagged.open())


def _ndjson_routing(harness: _Harness, api: Any, adapter: _Feed) -> None:
    """Route records by a body member, keep unknown ones, and raise error records."""
    lines, helper = harness.lines, api.protocols.records.tagged
    adapter.replies.extend((
        _lines(harness, _pieces(_RECORD_ROUTES, 9)),
        _lines(harness, (b'{"type": "created", "id": "1"}\n{"type": "failed", "message": "nope"}\n',)),
        _lines(harness, (b'{"id": "1"}\n',)),
        _lines(harness, (b'{"type": "deleted", "id": 5}\n',)),
    ))
    _drained(lines, "body discriminator", helper.open())
    stream = helper.open()
    lines.append(f"  before the error {_event(next(stream))}")
    try:
        next(stream)
    except harness.errors.StreamRemoteError as failure:
        lines.append(f"  error record {failure.event_type!r} {_data(failure.data)!r} {failure.sequence}")
    record(lines, "after the error", lambda: next(stream))
    _drained(lines, "missing discriminator", helper.open())
    _drained(lines, "mapped record failing its schema", helper.open(options=harness.schema()))


def _ndjson_limits(harness: _Harness, api: Any, adapter: _Feed) -> None:
    """Refuse a line over the smaller of the line and record limits, before it is kept or once it is found."""
    lines, helper = harness.lines, api.protocols.records.all
    narrow = harness.protocols.StreamOptions(max_line_bytes=16)
    adapter.replies.extend((
        _lines(harness, (b'{"text": "a"}\n{"text": "abcdef"}\n',)),
        _lines(harness, (b'{"text": ', b'"abcdefgh"}\n')),
        _lines(harness, (b'{"text": "a"}\n{"text": "abcdefghij"',)),
        _lines(harness, (b'{"text": "abcd"}\n',)),
        _lines(harness, (b'{"text": "abcd"}\r\n{"text": "efgh"}\r', b"\n")),
        _lines(harness, (b'{"text": "abcd"}\r', b'{"text": "e"}\n')),
    ))
    for label in (
        "line over the limit in one chunk",
        "line a later chunk extends over the limit",
        "unended line after a line end over the limit",
        "line at the limit",
        "CRLF line at the limit",
        "CR inside a line counted",
    ):
        _drained(lines, label, helper.open(stream_options=narrow))
    adapter.replies.append(_lines(harness, (b'{"text": "abcde"}\n',)))
    _drained(
        lines,
        "record over a record limit under the line limit",
        helper.open(stream_options=harness.protocols.StreamOptions(max_line_bytes=64, max_event_bytes=16)),
    )


def _ndjson_states(harness: _Harness, api: Any, adapter: _Feed) -> None:
    """Close streams early, report their ends to hooks, interrupt one, and check the response before returning."""
    lines, helper = harness.lines, api.protocols.records.all
    record_line = b'{"text": "a"}\n'
    adapter.replies.extend((
        _lines(harness, (record_line, record_line)),
        _lines(harness, (record_line * 2,)),
        _lines(harness, (record_line, b'{"te', harness.interrupted())),
        _lines(harness, (b'{"detail": "missing"}',), status=404, media="application/json"),
        _lines(harness, (record_line,), media=_STREAM),
        _lines(harness, (record_line,), media=f"{_NDJSON}; charset=utf-8"),
    ))
    with helper.open() as stream:
        lines.append(f"  first {_event(next(stream))}")
    record(lines, "after close", lambda: next(stream))
    lines.append(f"  data {[_data(value) for value in helper.open().data()]!r}")
    _drained(lines, "broken connection", helper.open())
    for label in ("declared error status", "event stream media type"):
        record(lines, label, helper.open)
    _drained(lines, "media type with parameters", helper.open())
    hooked = api.with_options(harness.options.RequestOptions(hooks=(_Ends(lines),)))
    adapter.replies.extend((
        _lines(harness, (record_line,)),
        _lines(harness, (b"{}\n",)),
        _lines(harness, (record_line, record_line)),
    ))
    _drained(lines, "hooked end at EOF", hooked.protocols.records.all.open())
    _drained(lines, "hooked undecodable record", hooked.protocols.records.all.open())
    stream = hooked.protocols.records.all.open()
    lines.append(f"  hooked early close after {_event(next(stream))}")
    stream.close()


async def _async_ndjson(harness: _Harness) -> None:
    """Split, end, route, and close NDJSON streams with the asyncio client."""
    lines, package = harness.lines, harness.package
    adapter = _AsyncFeed(importlib.import_module(f"{package.__name__}.transports"), lines)
    record_line = b'{"text": "a"}\n'
    adapter.replies.extend(
        _lines(harness, chunks, response=AsyncResponse)
        for chunks in (
            _pieces(_RECORDS, 1),
            _pieces(b'{"text": "a"}\n{"text": "b"}', 4),
            (b'{"text": "\xff"}\n',),
            _pieces(_RECORD_ROUTES, 11),
            (b'{"type": "created", "id": "1"}\n{"type": "failed", "message": "nope"}\n',),
            (record_line, b'{"te', harness.interrupted()),
            (record_line * 2,),
            (record_line * 2,),
        )
    )
    async with package.AsyncClient(transport_adapter=adapter) as api:
        records = api.protocols.records
        async with await records.all.open() as stream:
            await _adrained(lines, "async one byte at a time", stream)
        await _adrained(lines, "async final line allowed at the end", await records.lenient.open())
        await _adrained(lines, "async invalid UTF-8", await records.all.open())
        await _adrained(lines, "async body discriminator", await records.tagged.open())
        await _adrained(lines, "async error record", await records.tagged.open())
        await _adrained(lines, "async broken connection", await records.all.open())
        stream = await records.all.open()
        lines.append(f"  async data {[_data(value) async for value in stream.data()]!r}")
        stream = await records.all.open()
        lines.append(f"  async first {_event(await anext(stream))}")
        await stream.aclose()
        await arecord(lines, "async after close", lambda: anext(stream))


def ndjson_backends(package: ModuleType, lines: list[str]) -> None:
    """Decode the records and error records of every NDJSON helper with each model backend."""
    harness = _Harness(package, lines)
    adapter = _Feed(importlib.import_module(f"{package.__name__}.transports"), lines)
    adapter.replies.extend((
        _lines(harness, (b'{"text": "a"}\n',)),
        _lines(harness, (_RECORD_ROUTES,)),
        _lines(harness, (b'{"type": "failed", "message": "nope"}\n',)),
        _lines(harness, (b'{"text": "b"}\n[DONE]\n',), media="application/jsonl"),
    ))
    with package.Client(transport_adapter=adapter) as api:
        protocols = api.protocols
        _drained(lines, "records", protocols.records.all.open())
        _drained(lines, "tagged", protocols.records.tagged.open())
        try:
            next(protocols.records.tagged.open())
        except harness.errors.StreamRemoteError as failure:
            lines.append(f"  error record {failure.event_type!r} {_data(failure.data)!r}")
        _drained(lines, "search", protocols.search.all.open(body=harness.models.SearchQuery(text="a")))
