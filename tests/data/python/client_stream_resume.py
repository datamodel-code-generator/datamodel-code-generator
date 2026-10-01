"""Checkpoint, resume, and reconnect SSE and NDJSON streams through generated helpers that declare resumption."""

from __future__ import annotations

import base64
import importlib
import json
from datetime import datetime, timezone
from functools import partial
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.data.python.client_runtime import arecord, describe, record, run
from tests.data.python.client_streams import _AsyncEnds, _Ends, _event, _Harness, _Probed
from tests.data.python.client_transports import Adapter, AsyncAdapter, AsyncResponse, Response, Stop

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterable
    from types import ModuleType

_NDJSON: Final = "application/x-ndjson"
_EXPIRES: Final = "2999-01-01T00:00:00Z"
_PAST: Final = datetime(2000, 1, 1, tzinfo=timezone.utc)
_TRACKED: Final = (("X-Stream-Id", "s1"), ("X-Resume-Token", "t1"), ("X-Stream-Expires", _EXPIRES))


def _header(headers: Iterable[tuple[str, str]], name: str) -> str | None:
    return next((value for key, value in headers if key.lower() == name), None)


def _request(lines: list[str], request: Any, body: bytes | None) -> None:
    """Report a request's method, URL, Last-Event-ID, and any body."""
    sent = "" if body is None else f" body={body!r}"
    lines.append(
        f"  > {request.method} {request.url} last-event-id={_header(request.headers, 'last-event-id')!r}{sent}"
    )


class _Reopens(Adapter):
    """An adapter whose replies stream exact chunks, reporting each request's line, Last-Event-ID, and body."""

    def send(self, request: Any, context: Any) -> Any:
        _request(self.lines, request, None if request.body is None else b"".join(request.body.iter_bytes()))
        return self.replies.pop(0)(request, context)


class _AsyncReopens(AsyncAdapter):
    """The asyncio form of the reporting adapter."""

    async def send(self, request: Any, context: Any) -> Any:
        body = None if request.body is None else b"".join([chunk async for chunk in request.body.aiter_bytes()])
        _request(self.lines, request, body)
        return self.replies.pop(0)(request, context)


def _events(*frames: str) -> tuple[bytes, ...]:
    """Return each SSE frame as one chunk."""
    return tuple(frame.encode() for frame in frames)


def _saved(lines: list[str], label: str, state: Any) -> None:
    """Report what an exported state saved and its expiry."""
    envelope = json.loads(state.export())
    lines.append(f"  {label} saved {json.dumps(envelope['state'], sort_keys=True)} expires_at={envelope['expires_at']}")


def _kept(lines: list[str], error: BaseException) -> Any:
    """Report a stream's failure and whether it keeps a resume state, returning the state."""
    state = getattr(error, "resume_state", None)
    lines.append(f"    ! {describe(error)} resume_state={type(state).__name__}")
    return state


def _drained(lines: list[str], label: str, stream: Iterable[Any]) -> Any:
    """Report every event a stream yields, then its end or its failure, returning the failure's resume state."""
    lines.append(f"  {label}")
    try:
        for event in stream:
            lines.append(f"    {_event(event)}")
    except Exception as error:  # noqa: BLE001
        return _kept(lines, error)
    lines.append("    end")
    return None


async def _adrained(lines: list[str], label: str, stream: AsyncIterator[Any]) -> Any:
    """Report every event an asyncio stream yields, then its end or its failure, returning the failure's state."""
    lines.append(f"  {label}")
    try:
        async for event in stream:
            lines.append(f"    {_event(event)}")
    except Exception as error:  # noqa: BLE001
        return _kept(lines, error)
    lines.append("    end")
    return None


def _crafted(harness: _Harness, state: Any, saved: dict[str, Any] | None = None, **fields: Any) -> Any:
    """Return a state with another's fingerprints and a replaced protocol state or expiry."""
    envelope = json.loads(state.export())
    return harness.protocols.ResumeState(
        helper_fingerprint=envelope["helper_fingerprint"],
        security_fingerprint=envelope["security_fingerprint"],
        state=envelope["state"] if saved is None else saved,
        payload=fields.get("payload", base64.b64decode(envelope["payload"])),
        expires_at=fields.get("expires_at"),
    )


def _replaced(state: Any, **members: Any) -> dict[str, Any]:
    """Return the protocol state of an export with some members replaced."""
    return {**json.loads(state.export())["state"], **members}


class _Resumes:
    """A generated package's harness, its reporting adapter, and the options of a reconnecting stream."""

    def __init__(
        self, package: ModuleType, lines: list[str], adapter: Adapter, response: type[Response] = Response
    ) -> None:
        self.harness = harness = _Harness(package, lines)
        self.lines = lines
        self.adapter = adapter
        self.response = response
        self.reconnect = harness.protocols.StreamOptions(reconnect=True)

    def reply(self, *chunks: object, **settings: Any) -> None:
        """Queue a reply streaming the chunks, as the client's kind of response unless told otherwise."""
        self.adapter.replies.append(self.harness.reply(chunks, **{"response": self.response, **settings}))

    def redirect(self) -> None:
        """Queue a redirect whose follow-up the session has no send slot for."""
        self.reply(status=302, media=None, headers=(("location", "/events?moved=1"),))

    def redirects(self) -> Any:
        """Return call options that follow redirects."""
        options = self.harness.options
        return options.RequestOptions(redirects=options.RedirectOptions(enabled=True))

    def argument(self, location: str, name: str, wire: str, operation: str = "events.StreamEvents") -> object:
        """Return an argument of an operation, the events one unless told otherwise, for a wire value."""
        resource, codecs = operation.split(".")
        types = importlib.import_module(f"{self.harness.package.__name__}.types.{resource}")
        return getattr(types, f"{codecs}RequestCodecs").parameter(location=location, name=name).from_wire(wire)

    def client_options(self) -> Any:
        """Return client options whose reconnection backoff waits for nothing."""
        options = self.harness.options
        return options.ClientOptions(retry=options.RetryOptions(initial_delay=0.0, max_delay=0.0))


def stream_resume(package: ModuleType, lines: list[str]) -> None:
    """Checkpoint, resume, and reconnect streams through the synchronous and asyncio clients."""
    transports = importlib.import_module(f"{package.__name__}.transports")
    resumes = _Resumes(package, lines, _Reopens(transports, lines))
    with package.Client(transport_adapter=resumes.adapter, options=resumes.client_options()) as api:
        _cursors(resumes, api)
        _checkpoints(resumes, api)
        _reconnects(resumes, api)
        _ineligible(resumes, api)
        _budgets(resumes, api)
        _waits(resumes, api)
        _tracked(resumes, api)
        _rooms(resumes, api)
        _records(resumes, api)
        _ticks(resumes, api)
        _unencodable(resumes, api)
        _refusals(resumes, api)
    run(lambda: _async_resume(package, lines))


def _cursors(resumes: _Resumes, api: Any) -> None:
    """Track the event ID cursor of delivered events, checkpoint it, and reopen after it, or without it once cleared."""
    lines, helper = resumes.lines, api.protocols.events.live
    lines.append("event ID cursors")
    resumes.reply(
        *_events(
            'id: 1\ndata: {"text": "a"}\n\n', 'data: {"text": "b"}\n\n', 'retry: 2500\nid: 2\ndata: {"text": "c"}\n\n'
        )
    )
    stream = helper.open(
        topic=resumes.argument("query", "topic", "news"), last_event_id=resumes.argument("header", "Last-Event-ID", "0")
    )
    record(lines, "checkpoint before any event", stream.checkpoint)
    lines.append(f"  {_event(next(stream))}")
    _saved(lines, "after one event", stream.checkpoint())
    lines.append(f"  {_event(next(stream))}")
    _saved(lines, "after an event without an ID", stream.checkpoint())
    _drained(lines, "rest", stream)
    state = stream.checkpoint()
    _saved(lines, "after the end", state)
    lines.append(f"  state repr {state!r}")
    resumes.reply(*_events('data: {"text": "d"}\n\n', 'id\ndata: {"text": "cleared"}\n\n'))
    resumed = helper.resume(resumes.harness.protocols.import_state(state.export()))
    lines.append(f"  resumed response {resumed.response.status_code} progress {dict(resumed.progress)}")
    _drained(lines, "resumed", resumed)
    cleared = resumed.checkpoint()
    _saved(lines, "cleared", cleared)
    resumes.reply(*_events('id: 9\ndata: {"text": "fresh"}\n\n'))
    _drained(lines, "resumed without a cursor", helper.resume(cleared))


def _checkpoints(resumes: _Resumes, api: Any) -> None:
    """Checkpoint streams in each state, refuse one running a step, and never save credentials."""
    lines, harness, helper = resumes.lines, resumes.harness, api.protocols.events.live
    lines.append("checkpoints")
    held: list[Any] = []
    probe = lambda: record(lines, "checkpoint while receiving", held[0].checkpoint)  # noqa: E731
    resumes.reply(b'id: 1\ndata: {"text": "a"}\n\n', probe, b'data: {"text": "b"}\n\n', response=_Probed)
    held.append(stream := helper.open())
    _drained(lines, "probed", stream)
    resumes.reply(*_events('id: 1\ndata: {"text": "a"}\n\n', "data: {broken\n\n"))
    stream = helper.open()
    _drained(lines, "failing", stream)
    _saved(lines, "failed", stream.checkpoint())
    resumes.reply(*_events('id: 1\ndata: {"text": "a"}\n\n', 'data: {"text": "b"}\n\n'))
    stream = helper.open()
    next(stream)
    stream.close()
    _saved(lines, "closed", stream.checkpoint())
    resumes.reply(*_events('id: 1\ndata: {"text": "a"}\n\n'))
    stream = helper.open(session=resumes.argument("cookie", "session", "secret"))
    next(stream)
    record(lines, "checkpoint of a call giving a cookie", stream.checkpoint)
    stream.close()
    resumes.reply(*_events('id: 1\ndata: {"text": "a"}\n\n'))
    with api.protocols.events.plain.open() as plain:
        next(plain)
        record(lines, "checkpoint without resume metadata", plain.checkpoint)
        lines.append(f"  progress without resume metadata {dict(plain.progress)}")
    headers = harness.options.RequestOptions(headers=(("last-event-id", "7"),))
    record(lines, "open patching the cursor header", lambda: helper.open(options=headers))
    query = harness.options.RequestOptions(query=(("after", "7"),))
    record(lines, "open patching the cursor query", lambda: api.protocols.records.all.open(options=query))
    record(
        lines, "open through a view patching the cursor header", api.with_options(headers).protocols.events.live.open
    )
    with resumes_client(resumes, harness.options.ClientOptions(headers=(("Last-Event-ID", "7"),))) as patched:
        record(lines, "open on a client patching the cursor header", patched.protocols.events.live.open)
    fixed = harness.options.RequestOptions(idempotency_key=harness.options.IdempotencyKey.new())
    record(lines, "open fixing an idempotency key", lambda: helper.open(options=fixed))
    ok = harness.options.RequestOptions(headers=(("x-trace", "1"),), query=(("trace", "1"),))
    resumes.reply(*_events('id: 1\ndata: {"text": "a"}\n\n'))
    _drained(lines, "open patching other parameters", helper.open(options=ok))


def _reconnects(resumes: _Resumes, api: Any) -> None:
    """Reconnect after interruptions once a cursor was delivered, reporting each response's end to the hooks."""
    lines, harness, helper = resumes.lines, resumes.harness, api.protocols.events.live
    lines.append("reconnections")
    hooked = api.with_options(harness.options.RequestOptions(hooks=(_Ends(lines),))).protocols.events.live
    cut = harness.interrupted()
    resumes.reply(b'id: 1\ndata: {"text": "a"}\n\n', cut)
    resumes.reply(*_events('id: 1\ndata: {"text": "a"}\n\n', 'id: 2\ndata: {"text": "b"}\n\n'))
    stream = hooked.open(topic=resumes.argument("query", "topic", "news"), stream_options=resumes.reconnect)
    _drained(lines, "interrupted and reopened with a duplicate", stream)
    lines.append(f"    progress {dict(stream.progress)} response {stream.response.status_code}")
    resumes.reply(b'data: {"text": "no ID"}\n\n', cut)
    _drained(lines, "interrupted before any cursor", helper.open(stream_options=resumes.reconnect))
    resumes.reply(b'id: 1\ndata: {"text": "a"}\n\n', cut)
    state = _drained(
        lines, "interrupted without reconnecting", helper.open(topic=resumes.argument("query", "topic", "news"))
    )
    _saved(lines, "interruption", state)
    resumes.reply(*_events('id: 2\ndata: {"text": "b"}\n\n'))
    _drained(lines, "resumed after the interruption", helper.resume(state))
    resumes.reply(*_events('id: 1\ndata: {"text": "a"}\n\n', "data: cut"))
    _drained(lines, "cut frame without incomplete_eof", helper.open(stream_options=resumes.reconnect))
    resumes.reply(b'retry: 1\nid: 1\ndata: {"text": "a"}\n\n', cut)
    resumes.reply(*_events('id: 2\ndata: {"text": "b"}\n\n'))
    _drained(lines, "reopened after the reconnection time", helper.open(stream_options=resumes.reconnect))
    read = harness.options.RequestOptions(timeout=harness.options.TimeoutOptions(read=5.0))
    resumes.reply(b'id: 1\ndata: {"text": "a"}\n\n', harness.idle())
    resumes.reply(*_events('id: 2\ndata: {"text": "b"}\n\n'))
    stream = helper.open(options=read, stream_options=resumes.reconnect)
    _drained(lines, "reopened after the call's own read timeout", stream)


def _ineligible(resumes: _Resumes, api: Any) -> None:
    """Never reconnect after a decode, size, idle, or remote failure, a stop, a close, or a failed reopen."""
    lines, harness, helper = resumes.lines, resumes.harness, api.protocols.events.live
    lines.append("failures that never reconnect")
    first = b'id: 1\ndata: {"text": "a"}\n\n'
    resumes.reply(first, b"data: {broken\n\n")
    _drained(lines, "undecodable event", helper.open(stream_options=resumes.reconnect))
    small = harness.protocols.StreamOptions(reconnect=True, max_line_bytes=24)
    resumes.reply(first, b'data: {"text": "far too long for the line limit"}\n\n')
    _drained(lines, "line over its limit", helper.open(stream_options=small))
    resumes.reply(first, harness.idle())
    _drained(lines, "idle read", helper.open(stream_options=resumes.reconnect))
    level = harness.options.RequestOptions(timeout=harness.options.TimeoutOptions(read=60.0))
    resumes.reply(first, harness.idle())
    _drained(
        lines, "read timeout tied with the idle limit", helper.open(options=level, stream_options=resumes.reconnect)
    )
    errors = harness.errors
    undecodable = errors.TransportError(
        delivery_state=errors.DeliveryState.RESPONSE_STARTED, phase="read", cause=httpx2.DecodingError("bad coding")
    )
    resumes.reply(first, undecodable)
    _drained(lines, "read failure classified as not retryable", helper.open(stream_options=resumes.reconnect))
    resumes.reply(first, errors.TransportError(delivery_state=errors.DeliveryState.RESPONSE_STARTED, phase="write"))
    _drained(lines, "failure outside the read phase", helper.open(stream_options=resumes.reconnect))
    resumes.reply(first, Stop())
    stream = helper.open(stream_options=resumes.reconnect)
    next(stream)
    try:
        next(stream)
    except Stop:
        lines.append(f"  stopped ! Stop then {describe(_failure(lambda: next(stream)))}")
    resumes.reply(first, b'data: {"text": "b"}\n\n')
    stream = helper.open(stream_options=resumes.reconnect)
    next(stream)
    stream.close()
    lines.append(f"  closed {describe(_failure(lambda: next(stream)))}")
    resumes.reply(first, harness.interrupted())
    resumes.reply(b'{"detail": "gone"}', status=404, media="application/json")
    stream = helper.open(stream_options=resumes.reconnect)
    _drained(lines, "reopen answered 404", stream)
    _saved(lines, "after the failed reopen", stream.checkpoint())
    tracked = api.protocols.events.tracked
    resumes.reply(
        b'event: created\nid: 1\ndata: {"id": "1"}\n\n',
        b'event: error\ndata: {"message": "boom"}\n\n',
        headers=_TRACKED,
    )
    _drained(lines, "error event", tracked.open(stream_options=resumes.reconnect))


def _failure(call: Callable[[], object]) -> BaseException | None:
    try:
        call()
    except Exception as error:  # noqa: BLE001
        return error
    return None


def _budgets(resumes: _Resumes, api: Any) -> None:
    """Exhaust the reconnections or the session's sends, keeping a checkpoint, never ending as normal EOF."""
    lines, harness, helper = resumes.lines, resumes.harness, api.protocols.events.live
    lines.append("budgets")
    cut = harness.interrupted()
    once = harness.protocols.StreamOptions(reconnect=True, max_reconnects=1)
    resumes.reply(b'id: 1\ndata: {"text": "a"}\n\n', cut)
    resumes.reply(b'id: 2\ndata: {"text": "b"}\n\n', cut)
    stream = helper.open(stream_options=once)
    _drained(lines, "one reconnection allowed", stream)
    lines.append(f"    progress {dict(stream.progress)}")
    never = harness.protocols.StreamOptions(reconnect=True, max_reconnects=0)
    resumes.reply(b'id: 1\ndata: {"text": "a"}\n\n', cut)
    _drained(lines, "no reconnection allowed", helper.open(stream_options=never))
    unlimited = harness.protocols.StreamOptions(reconnect=True, max_reconnects=None)
    one_send = harness.options.SessionOptions(max_network_sends=1)
    resumes.reply(b'id: 1\ndata: {"text": "a"}\n\n', cut)
    _drained(lines, "no send slot left", helper.open(stream_options=unlimited, session_options=one_send))
    two_sends = harness.options.SessionOptions(max_network_sends=2)
    resumes.reply(b'id: 1\ndata: {"text": "a"}\n\n', cut)
    resumes.redirect()
    stream = helper.open(stream_options=resumes.reconnect, options=resumes.redirects(), session_options=two_sends)
    _drained(lines, "reopen redirected past the sends", stream)
    resumes.reply(b'event: created\nid: 1\ndata: {"id": "1"}\n\n', cut, headers=_TRACKED)
    resumes.redirect()
    tracked = api.protocols.events.tracked
    stream = tracked.open(stream_options=resumes.reconnect, options=resumes.redirects(), session_options=two_sends)
    _refused(lines, "another reopen operation redirected past the sends", stream)


def _waits(resumes: _Resumes, api: Any) -> None:
    """Refuse a reconnection whose wait is longer than allowed or than the session has left, without waiting."""
    lines, harness, helper = resumes.lines, resumes.harness, api.protocols.events.live
    lines.append("waits")
    cut = harness.interrupted()
    resumes.reply(b'retry: 70000\nid: 1\ndata: {"text": "a"}\n\n', cut)
    _drained(lines, "reconnection time over the allowed wait", helper.open(stream_options=resumes.reconnect))
    patient = harness.protocols.StreamOptions(reconnect=True, max_reconnect_wait=None)
    short = harness.options.SessionOptions(total_timeout=30.0)
    resumes.reply(b'retry: 70000\nid: 1\ndata: {"text": "a"}\n\n', cut)
    _drained(lines, "reconnection time past the deadline", helper.open(stream_options=patient, session_options=short))
    options = harness.options
    slow = options.RequestOptions(retry=options.RetryOptions(initial_delay=10.0, max_delay=10.0))
    hasty = harness.protocols.StreamOptions(reconnect=True, max_reconnect_wait=5.0)
    resumes.reply(b'id: 1\ndata: {"text": "a"}\n\n', cut)
    _drained(lines, "backoff cap over the allowed wait", helper.open(options=slow, stream_options=hasty))


def _tracked(resumes: _Resumes, api: Any) -> None:
    """Reopen with another operation writing the bindings' values, after incomplete ends, and bound by an expiry."""
    lines, harness, helper = resumes.lines, resumes.harness, api.protocols.events.tracked
    lines.append("another reopen operation")
    resumes.reply(b'event: created\nid: 1\ndata: {"id": "1"}\n\n', b"data: cut", headers=_TRACKED)
    resumes.reply(b'event: renamed\nid: 2\ndata: {"id": "2"}\n\n', headers=(("X-Resume-Token", "t2"),))
    resumes.reply(b"event: done\ndata: {}\n\n", headers=(("X-Resume-Token", "t3"),))
    stream = helper.open(stream_options=resumes.reconnect)
    _drained(lines, "incomplete ends reopened", stream)
    lines.append(f"    progress {dict(stream.progress)}")
    state = stream.checkpoint()
    _saved(lines, "tracked", state)
    resumes.reply(b"event: done\ndata: {}\n\n", headers=(("X-Resume-Token", "t4"),))
    _drained(lines, "resumed with the bindings", helper.resume(state))
    expired = _crafted(harness, state, expires_at=_PAST)
    record(lines, "resume an expired state", lambda: helper.resume(expired))
    dotted = _crafted(harness, state, _replaced(state, bound=["..", "t1", "resume"]), expires_at=state_expiry(state))
    record(lines, "resume a dot segment", lambda: helper.resume(dotted))
    hooked = api.with_options(harness.options.RequestOptions(hooks=(_Ends(lines),))).protocols.events.tracked
    for label, headers in (
        ("open without the stream ID", (("X-Resume-Token", "t1"), ("X-Stream-Expires", _EXPIRES))),
        ("open with two stream IDs", (("X-Stream-Id", "a"), ("X-Stream-Id", "b"), *_TRACKED[1:])),
        ("open with a dot segment stream ID", (("X-Stream-Id", ".."), *_TRACKED[1:])),
        ("open without the expiry", _TRACKED[:2]),
        ("open with two expiries", (*_TRACKED, ("X-Stream-Expires", _EXPIRES))),
        ("open with an unreadable expiry", (*_TRACKED[:2], ("X-Stream-Expires", "soon"))),
    ):
        resumes.reply(b"event: done\ndata: {}\n\n", headers=headers)
        record(lines, label, hooked.open)
    resumes.reply(b'event: created\nid: 1\ndata: {"id": "1"}\n\n', harness.interrupted(), headers=_TRACKED)
    resumes.reply(b"event: done\ndata: {}\n\n")
    _drained(lines, "reopen without the token", hooked.open(stream_options=resumes.reconnect))


def _rooms(resumes: _Resumes, api: Any) -> None:
    """Reopen the caller's own path with the shard the last response gave, refusing one that makes a dot segment."""
    lines, harness, helper = resumes.lines, resumes.harness, api.protocols.rooms.live
    lines.append("bound path values")
    resumes.reply(b'id: 1\ndata: {"text": "a"}\n\n', harness.interrupted(), headers=(("X-Shard", "1"),))
    resumes.reply(b'id: 2\ndata: {"text": "b"}\n\n', headers=(("X-Shard", "2"),))
    room, shard = (partial(resumes.argument, "path", name, operation="rooms.StreamRoom") for name in ("room", "shard"))
    stream = helper.open(room=room("r"), shard=shard("0"), stream_options=resumes.reconnect)
    _drained(lines, "reopened with the shard", stream)
    _saved(lines, "shard", stream.checkpoint())
    resumes.reply(b'id: 1\ndata: {"text": "a"}\n\n', headers=(("X-Shard", "."),))
    record(lines, "open with a shard making a dot segment", lambda: helper.open(room=room("."), shard=shard("x")))


def state_expiry(state: Any) -> datetime:
    """Return the expiry an exported state carries."""
    return datetime.fromisoformat(json.loads(state.export())["expires_at"])


def _records(resumes: _Resumes, api: Any) -> None:
    """Track an NDJSON body cursor: inherited when missing, cleared by null, and written to a query parameter."""
    lines, harness, helper = resumes.lines, resumes.harness, api.protocols.records.all
    lines.append("NDJSON body cursors")
    records = (
        b'{"id": "r1", "text": "a"}\n',
        b'{"text": "no ID"}\n',
        b'{"id": null, "text": "cleared"}\n',
        b'{"id": "r2", "text": "b"}\n',
    )
    resumes.reply(*records, harness.interrupted(), media=_NDJSON)
    resumes.reply(b'{"id": "r3", "text": "c"}\n', media=_NDJSON)
    stream = helper.open(stream_options=resumes.reconnect)
    for _ in range(3):
        lines.append(f"  {_event(next(stream))}")
        _saved(lines, "record", stream.checkpoint())
    _drained(lines, "rest", stream)
    resumes.reply(*records[:3], media=_NDJSON)
    stream = helper.open()
    _drained(lines, "ending cleared", stream)
    resumes.reply(b'{"id": "r4", "text": "d"}\n', media=_NDJSON)
    _drained(lines, "resumed without a cursor", helper.resume(stream.checkpoint()))


def _ticks(resumes: _Resumes, api: Any) -> None:
    """Track an SSE body cursor that may be neither missing nor null, written into the caller's JSON body."""
    lines, harness, helper = resumes.lines, resumes.harness, api.protocols.feed.ticks
    lines.append("SSE body cursors")
    query = harness.models.FeedQuery(topic="t")
    resumes.reply(*_events('event: tick\ndata: {"seq": 1}\n\n', 'event: other\ndata: {"seq": 2}\n\n'))
    resumes.reply(*_events('event: tick\ndata: {"seq": 3}\n\n', "data: [DONE]\n\n"))
    stream = helper.open(body=query, stream_options=resumes.reconnect)
    _drained(lines, "EOF before the sentinel reopened", stream)
    resumes.reply(b'event: tick\ndata: {"seq": 1}\n\n', harness.interrupted())
    state = _drained(
        lines, "transport interruption not declared", helper.open(body=query, stream_options=resumes.reconnect)
    )
    _saved(lines, "ticks", state)
    resumes.reply(*_events("data: [DONE]\n\n"))
    _drained(lines, "resumed writing the body", helper.resume(state))
    for label, frame in (
        ("missing cursor", "event: tick\ndata: {}\n\n"),
        ("null cursor", 'event: tick\ndata: {"seq": null}\n\n'),
        ("unknown event that is not JSON", "event: other\ndata: plain\n\n"),
    ):
        resumes.reply(*_events(frame))
        _drained(lines, label, helper.open(body=query))


def _unencodable(resumes: _Resumes, api: Any) -> None:
    """Refuse a reconnection after, and a checkpoint of, a cursor the reopen request cannot encode, keeping no state."""
    lines, harness = resumes.lines, resumes.harness
    lines.append("cursors a reopen cannot encode")
    resumes.reply(b'id: 5 \ndata: {"text": "a"}\n\n', harness.interrupted())
    stream = api.protocols.events.live.open(stream_options=resumes.reconnect)
    _refused(lines, "event ID ending in a space", stream)
    record(lines, "checkpoint", stream.checkpoint)
    resumes.reply(b'event: other\ndata: {"id": {"a": 1}}\n\n', harness.interrupted())
    stream = api.protocols.topics.marks.open(stream_options=resumes.reconnect)
    _refused(lines, "object body cursor written to a query parameter", stream)
    record(lines, "checkpoint", stream.checkpoint)


def _refused(lines: list[str], label: str, stream: Iterable[Any]) -> None:
    """Drain a stream whose reconnection is refused, reporting the refusal's operation, cause, and context."""
    lines.append(f"  {label}")
    try:
        for event in stream:
            lines.append(f"    {_event(event)}")
    except Exception as error:  # noqa: BLE001
        _origins(lines, error)


def _origins(lines: list[str], error: BaseException) -> None:
    """Report a failure, its operation, its cause, and its context."""
    _kept(lines, error)
    cause, context = type(getattr(error, "cause", None)).__name__, type(error.__context__).__name__
    lines.append(f"    operation {getattr(error, 'operation', None)!r} cause {cause} context {context}")


def _refusals(resumes: _Resumes, api: Any) -> None:
    """Refuse a state that is not one, another helper's, made under other security, or that does not fit."""
    lines, harness, helper = resumes.lines, resumes.harness, api.protocols.events.live
    lines.append("refusals")
    resumes.reply(*_events('id: 1\ndata: {"text": "a"}\n\n'))
    stream = helper.open(topic=resumes.argument("query", "topic", "news"))
    next(stream)
    state = stream.checkpoint()
    stream.close()
    resumes.reply(*_events('{"id": "r1", "text": "a"}\n'), media=_NDJSON)
    records = api.protocols.records.all.open()
    next(records)
    other = records.checkpoint()
    records.close()
    record(lines, "resume a string", lambda: helper.resume("state"))
    record(lines, "resume another helper's state", lambda: helper.resume(other))
    options = harness.options
    context = options.ProtocolClientOptions(
        security=harness.protocols.ProtocolSecurityContext(credential_partition="p")
    )
    with resumes_client(resumes, options.ClientOptions(protocols=context)) as partitioned:
        record(lines, "resume under other security", lambda: partitioned.protocols.events.live.resume(state))
    for label, saved in (
        ("missing members", {"cursor": "1"}),
        ("cursor not a string", _replaced(state, cursor=5)),
        ("empty cursor", _replaced(state, cursor="")),
        ("bound values of another count", _replaced(state, bound=["x"])),
        ("a saved cookie", _replaced(state, arguments=[[], [], ["c"]])),
        ("negative sequence", _replaced(state, sequence=-1)),
        ("retry time over its limit", _replaced(state, retry_ms=10**18)),
    ):
        record(lines, f"resume {label}", lambda saved=saved: helper.resume(_crafted(harness, state, saved)))
    record(lines, "resume a payload", lambda: helper.resume(_crafted(harness, state, payload=b"x")))
    record(
        lines,
        "retry of NDJSON",
        lambda: api.protocols.records.all.resume(_crafted(harness, other, _replaced(other, retry_ms=5))),
    )
    record(
        lines,
        "object cursor",
        lambda: api.protocols.records.all.resume(_crafted(harness, other, _replaced(other, cursor={"a": 1}))),
    )
    resumes.reply(*_events('id: 1\ndata: {"text": "a"}\n\n'), headers=_TRACKED)
    tracked = api.protocols.events.tracked.open()
    next(tracked)
    saved = tracked.checkpoint()
    tracked.close()
    expires_at = state_expiry(saved)
    crafted = _crafted(harness, saved, _replaced(saved, arguments=[[]]), expires_at=expires_at)
    record(lines, "resume arguments of another operation", lambda: api.protocols.events.tracked.resume(crafted))
    query = harness.models.FeedQuery(topic="t")
    resumes.reply(*_events('event: tick\ndata: {"seq": 1}\n\n'))
    ticks = api.protocols.feed.ticks.open(body=query)
    next(ticks)
    tick = ticks.checkpoint()
    ticks.close()
    record(
        lines,
        "ticks cleared cursor",
        lambda: api.protocols.feed.ticks.resume(_crafted(harness, tick, _replaced(tick, cursor=None))),
    )
    record(
        lines,
        "ticks cursor replaced by an object",
        lambda: api.protocols.feed.ticks.resume(_crafted(harness, tick, _replaced(tick, cursor={"$gt": 0}))),
    )
    nothing = options.SessionOptions(max_network_sends=0)
    record(lines, "resume without send slots", lambda: helper.resume(state, session_options=nothing))
    one = options.SessionOptions(max_network_sends=1)
    resumes.redirect()
    record(
        lines,
        "resume redirected past the sends",
        lambda: helper.resume(state, options=resumes.redirects(), session_options=one),
    )
    resumes.reply(*_events('id: 2\ndata: {"text": "b"}\n\n'))
    _drained(lines, "resume after the refusals", helper.resume(state))


def resumes_client(resumes: _Resumes, options: Any) -> Any:
    """Return another client of the package over the same adapter."""
    return resumes.harness.package.Client(transport_adapter=resumes.adapter, options=options)


async def _async_resume(package: ModuleType, lines: list[str]) -> None:
    """Reconnect, checkpoint, and resume asyncio streams."""
    transports = importlib.import_module(f"{package.__name__}.transports")
    resumes = _Resumes(package, lines, _AsyncReopens(transports, lines), AsyncResponse)
    harness = resumes.harness
    lines.append("asyncio")
    async with package.AsyncClient(transport_adapter=resumes.adapter, options=resumes.client_options()) as api:
        helper = api.protocols.events.live
        hooked = api.with_options(harness.options.RequestOptions(hooks=(_AsyncEnds(lines),))).protocols.events.live
        cut = harness.interrupted()
        resumes.reply(b'retry: 1\nid: 1\ndata: {"text": "a"}\n\n', cut)
        resumes.reply(*_events('id: 2\ndata: {"text": "b"}\n\n'))
        stream = await hooked.open(topic=resumes.argument("query", "topic", "news"), stream_options=resumes.reconnect)
        await _adrained(lines, "async reopened", stream)
        state = stream.checkpoint()
        _saved(lines, "async", state)
        resumes.reply(*_events('id: 3\ndata: {"text": "c"}\n\n'))
        await _adrained(lines, "async resumed", await helper.resume(state))
        resumes.reply(b'id: 1\ndata: {"text": "a"}\n\n', cut)
        never = harness.protocols.StreamOptions(reconnect=True, max_reconnects=0)
        await _adrained(lines, "async no reconnection allowed", await helper.open(stream_options=never))
        patient = harness.protocols.StreamOptions(reconnect=True, max_reconnect_wait=None)
        short = harness.options.SessionOptions(total_timeout=30.0)
        resumes.reply(b'retry: 70000\nid: 1\ndata: {"text": "a"}\n\n', cut)
        stream = await helper.open(stream_options=patient, session_options=short)
        await _adrained(lines, "async past the deadline", stream)
        two_sends = harness.options.SessionOptions(max_network_sends=2)
        resumes.reply(b'id: 1\ndata: {"text": "a"}\n\n', cut)
        resumes.redirect()
        redirects = resumes.redirects()
        stream = await helper.open(stream_options=resumes.reconnect, options=redirects, session_options=two_sends)
        await _adrained(lines, "async reopen redirected past the sends", stream)
        resumes.reply(b'id: 5 \ndata: {"text": "a"}\n\n', cut)
        stream = await helper.open(stream_options=resumes.reconnect)
        try:
            await anext(stream)
            await anext(stream)
        except Exception as error:  # noqa: BLE001
            _origins(lines, error)
        resumes.reply(b'id: 1\ndata: {"text": "a"}\n\n', Stop())
        stream = await helper.open(stream_options=resumes.reconnect)
        await anext(stream)
        try:
            await anext(stream)
        except Stop:
            lines.append("  async stopped ! Stop")
        one = harness.options.SessionOptions(max_network_sends=1)
        resumes.redirect()
        await arecord(
            lines,
            "async resume redirected past the sends",
            lambda: helper.resume(state, options=redirects, session_options=one),
        )
        await _async_tracked(resumes, api.protocols.events.tracked)


async def _async_tracked(resumes: _Resumes, tracked: Any) -> None:
    """Refuse asyncio opens, reopens, and resumes whose responses give no binding value."""
    lines, cut = resumes.lines, resumes.harness.interrupted()
    resumes.reply(b"event: done\ndata: {}\n\n", headers=_TRACKED[1:])
    await arecord(lines, "async open without the stream ID", tracked.open)
    resumes.reply(b'event: created\nid: 1\ndata: {"id": "1"}\n\n', cut, headers=_TRACKED)
    resumes.reply(b"event: done\ndata: {}\n\n")
    await _adrained(lines, "async reopen without the token", await tracked.open(stream_options=resumes.reconnect))
    resumes.reply(b'event: created\nid: 1\ndata: {"id": "1"}\n\n', headers=_TRACKED)
    opened = await tracked.open()
    await anext(opened)
    state = opened.checkpoint()
    await opened.aclose()
    resumes.reply(b"event: done\ndata: {}\n\n")
    await arecord(lines, "async resume without the token", lambda: tracked.resume(state))
