"""Checkpoint, resume, and reconnect SSE and NDJSON streams through generated helpers that declare resumption."""

from __future__ import annotations

import importlib
import itertools
from contextlib import asynccontextmanager, contextmanager
import json
from datetime import datetime, timezone
from functools import partial
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.data.python.client_auth_options import _Signer
from tests.data.python.client_runtime import arecord, argument, describe, record, run
from tests.data.python.client_streams import _AsyncEnds, _Ends, _event, _Feed, _Harness

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterable, Iterator
    from types import ModuleType

_NDJSON: Final = "application/x-ndjson"
_EXPIRES: Final = "2999-01-01T00:00:00Z"
_PAST: Final = datetime(2000, 1, 1, tzinfo=timezone.utc)
_TRACKED: Final = (("X-Stream-Id", "s1"), ("X-Resume-Token", "t1"), ("X-Stream-Expires", _EXPIRES))


class _Stop(BaseException):
    """An interruption that is not an Exception, as KeyboardInterrupt is."""


class _Reopens(_Feed):
    """A transport whose replies stream exact chunks, reporting each request's line, Last-Event-ID, and body."""

    def report(self, request: httpx2.Request) -> None:
        """Report a request's method, URL, Last-Event-ID, and any body."""
        sent = f" body={body!r}" if (body := request.content) else ""
        lines = self.lines
        lines.append(f"  > {request.method} {request.url} last-event-id={request.headers.get('last-event-id')!r}{sent}")


def _events(*frames: str) -> tuple[bytes, ...]:
    """Return each SSE frame as one chunk."""
    return tuple(frame.encode() for frame in frames)


def _saved(lines: list[str], label: str, state: Any) -> None:
    """Report what an exported token saved, its expiry included."""
    lines.append(f"  {label} saved {json.dumps(json.loads(state.export())['state'], sort_keys=True)}")


def _kept(lines: list[str], error: BaseException) -> Any:
    """Report a stream's failure and whether it keeps a resume state, returning the state."""
    state = getattr(error, "resume_state", None)
    lines.append(f"    ! {describe(error)} resume_state={type(state).__name__}")
    return state


def _drained(lines: list[str], label: str, stream: Iterable[Any]) -> Any:
    """Report every event a stream yields, then its end or its failure, returning the failure's resume state."""
    lines.append(f"  {label}")
    try:
        lines.extend(f"    {_event(event)}" for event in stream)
    except Exception as error:  # ruff: ignore[blind-except]
        return _kept(lines, error)
    lines.append("    end")
    return None


async def _adrained(lines: list[str], label: str, stream: AsyncIterator[Any]) -> Any:
    """Report every event an asyncio stream yields, then its end or its failure, returning the failure's state."""
    lines.append(f"  {label}")
    try:
        async for event in stream:
            lines.append(f"    {_event(event)}")  # ruff: ignore[manual-list-comprehension] - Keep events delivered before an interruption.
    except Exception as error:  # ruff: ignore[blind-except]
        return _kept(lines, error)
    lines.append("    end")
    return None


def _crafted(harness: _Harness, state: Any, saved: dict[str, Any]) -> Any:
    """Return a token of another's helper with a replaced protocol state."""
    return harness.protocols.ResumeState(helper=json.loads(state.export())["helper"], state=saved)


def _replaced(state: Any, **members: Any) -> dict[str, Any]:
    """Return the protocol state of an export with some members replaced."""
    return {**json.loads(state.export())["state"], **members}


class _Resumes:
    """A generated package's harness, its reporting transport, and the options of a reconnecting stream."""

    def __init__(self, package: ModuleType, lines: list[str]) -> None:
        self.harness = harness = _Harness(package, lines)
        self.lines = lines
        self.feed = _Reopens(lines)
        self.reconnect = harness.protocols.StreamOptions(reconnect=True)

    def reply(self, *chunks: object, **settings: Any) -> None:
        """Queue a reply streaming the chunks."""
        self.feed.replies.append(self.harness.reply(chunks, **settings))

    @contextmanager
    def client(self, options: Any = None) -> Iterator[Any]:
        """Yield a client of the package sending through the reporting transport."""
        with self.feed.client() as http, self.harness.package.Client(http_client=http, options=options) as api:
            yield api

    @asynccontextmanager
    async def async_client(self, options: Any = None) -> AsyncIterator[Any]:
        """Yield an asyncio client of the package sending through the reporting transport."""
        async with (
            self.feed.async_client() as http,
            self.harness.package.AsyncClient(http_client=http, options=options) as api,
        ):
            yield api

    def redirect(self) -> None:
        """Queue a redirect whose follow-up the session has no send slot for."""
        self.reply(status=302, media=None, headers=(("location", "/events?moved=1"),))

    def redirects(self) -> Any:
        """Return call options that follow redirects."""
        options = self.harness.options
        return options.RequestOptions(redirects=options.RedirectOptions(enabled=True))

    def argument(self, location: str, name: str, wire: object, operation_id: str = "streamEvents") -> object:
        """Return an argument of an operation, the events one unless told otherwise, for a wire value."""
        return argument(self.harness.package, operation_id, location, name, wire)

    def client_options(self) -> Any:
        """Return client options whose reconnection backoff waits for nothing."""
        options = self.harness.options
        return options.ClientOptions(retry=options.RetryOptions(initial_delay=0.0, max_delay=0.0))


def stream_resume(package: ModuleType, lines: list[str]) -> None:
    """Checkpoint, resume, and reconnect streams through the synchronous and asyncio clients."""
    resumes = _Resumes(package, lines)
    with resumes.client(resumes.client_options()) as api:
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
        _exploded(resumes, api)
        _refusals(resumes, api)
    run(lambda: _async_resume(package, lines))
    _clocked(package, lines)
    run(lambda: _aclocked(package, lines))
    _write_guards(package, lines)
    run(lambda: _awrite_guards(package, lines))


def _clock_options(resumes: _Resumes) -> Any:
    """Advance past backoff and SSE retry waits on every clock read, with a wall clock before the server expiry."""
    options = resumes.harness.options
    ticks = itertools.count(100.0, 10.0)
    return options.ClientOptions(
        clock=options.Clock(monotonic=lambda: next(ticks), time=lambda: 0.0, random=lambda: 0.5),
        total_timeout=None,
        stream_idle_timeout=None,
        timeout=options.TimeoutOptions(connect=None, read=None, write=None, pool=None),
        retry=options.RetryOptions(initial_delay=8.0, max_delay=16.0),
    )


def _clock_replies(resumes: _Resumes) -> Any:
    """Queue an open, explicit resume, and automatic reconnect, whose six-second retry exceeds jittered backoff."""
    headers = (*_TRACKED[:2], ("X-Stream-Expires", "2000-01-01T00:00:00Z"))
    resumes.reply(b'event: created\nid: 1\ndata: {"id": "1"}\n\n', headers=headers)
    resumes.reply(
        b'retry: 6000\nevent: created\nid: 2\ndata: {"id": "2"}\n\n',
        resumes.harness.interrupted(),
        headers=headers,
    )
    resumes.reply(b'event: created\nid: 3\ndata: {"id": "3"}\n\nevent: done\ndata: {}\n\n', headers=headers)
    return resumes.harness.options.SessionOptions(total_timeout=10000.0)


def _clocked(package: ModuleType, lines: list[str]) -> None:
    """Checkpoint, resume, and reconnect on an injected stepped clock without real waits."""
    resumes = _Resumes(package, lines)
    lines.append("stepped client clock")
    session = _clock_replies(resumes)
    with resumes.client(_clock_options(resumes)) as api:
        helper = api.protocols.events.tracked
        stream = helper.open(session_options=session)
        lines.append(f"  {_event(next(stream))}")
        state = stream.checkpoint()
        stream.close()
        resumed = helper.resume(state, stream_options=resumes.reconnect, session_options=session)
        _drained(lines, "resumed and reconnected", resumed)
        _saved(lines, "clock checkpoint", resumed.checkpoint())
        lines.append(f"  clock progress {dict(resumed.progress)}")
        record(
            lines,
            "expired on the client wall clock",
            lambda: helper.resume(
                _crafted(resumes.harness, state, _replaced(state, expires_at="1960-01-01T00:00:00+00:00"))
            ),
        )


async def _aclocked(package: ModuleType, lines: list[str]) -> None:
    """Resume and reconnect with asyncio on the same stepped-clock schedule."""
    resumes = _Resumes(package, lines)
    lines.append("async stepped client clock")
    session = _clock_replies(resumes)
    async with resumes.async_client(_clock_options(resumes)) as api:
        helper = api.protocols.events.tracked
        stream = await helper.open(session_options=session)
        lines.append(f"  {_event(await anext(stream))}")
        state = stream.checkpoint()
        await stream.aclose()
        resumed = await helper.resume(state, stream_options=resumes.reconnect, session_options=session)
        await _adrained(lines, "resumed and reconnected", resumed)
        _saved(lines, "clock checkpoint", resumed.checkpoint())
        lines.append(f"  clock progress {dict(resumed.progress)}")


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
    probe = lambda: record(lines, "checkpoint while receiving", held[0].checkpoint)  # ruff: ignore[lambda-assignment]
    resumes.reply(b'id: 1\ndata: {"text": "a"}\n\n', probe, b'data: {"text": "b"}\n\n')
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
    with resumes.client(harness.options.ClientOptions(headers=(("Last-Event-ID", "7"),))) as patched:
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
    resumes.reply(first, httpx2.DecodingError("bad coding"))
    _drained(lines, "read failure classified as not retryable", helper.open(stream_options=resumes.reconnect))
    resumes.reply(first, httpx2.WriteError("write failed"))
    _drained(lines, "failure outside the read phase", helper.open(stream_options=resumes.reconnect))
    resumes.reply(first, _Stop())
    stream = helper.open(stream_options=resumes.reconnect)
    next(stream)
    try:
        next(stream)
    except _Stop:
        lines.append(f"  stopped ! Stop then {describe(_failure(partial(next, stream)))}")
    resumes.reply(first, b'data: {"text": "b"}\n\n')
    stream = helper.open(stream_options=resumes.reconnect)
    next(stream)
    stream.close()
    lines.append(f"  closed {describe(_failure(partial(next, stream)))}")
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
    except Exception as error:  # ruff: ignore[blind-except]
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
    expired = _crafted(harness, state, _replaced(state, expires_at=_PAST.isoformat()))
    record(lines, "resume an expired state", lambda: helper.resume(expired))
    dotted = _crafted(harness, state, _replaced(state, bound=["..", "t1", "resume"]))
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
    room, shard = (partial(resumes.argument, "path", name, operation_id="streamRoom") for name in ("room", "shard"))
    stream = helper.open(room=room("r"), shard=shard("0"), stream_options=resumes.reconnect)
    _drained(lines, "reopened with the shard", stream)
    _saved(lines, "shard", stream.checkpoint())
    resumes.reply(b'id: 1\ndata: {"text": "a"}\n\n', headers=(("X-Shard", "."),))
    record(lines, "open with a shard making a dot segment", lambda: helper.open(room=room("."), shard=shard("x")))


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


def _exploded(resumes: _Resumes, api: Any) -> None:
    """Refuse a checkpoint whose exploded object query cursor contains a credential field."""
    lines, harness, helper = resumes.lines, resumes.harness, api.protocols.marks.scoped
    lines.append("cursors written as exploded query fields")
    resumes.reply(*_events('data: {"scope": {"after": "5"}}\n\n', 'data: {"scope": {"api_key": "k"}}\n\n'))
    stream = helper.open()
    lines.append(f"  {_event(next(stream))}")
    state = stream.checkpoint()
    _saved(lines, "scope", state)
    lines.append(f"  {_event(next(stream))}")
    record(lines, "checkpoint of a credential field", stream.checkpoint)
    stream.close()
    crafted = _crafted(harness, state, _replaced(state, cursor={"api_key": "k"}))
    record(lines, "resume a credential field", lambda: helper.resume(crafted))
    resumes.reply(*_events('data: {"scope": {"after": "6"}}\n\n'))
    _drained(lines, "resumed after the scope", helper.resume(state))


def _refused(lines: list[str], label: str, stream: Iterable[Any]) -> None:
    """Drain a stream whose reconnection is refused, reporting the refusal's operation, cause, and context."""
    lines.append(f"  {label}")
    try:
        lines.extend(f"    {_event(event)}" for event in stream)
    except Exception as error:  # ruff: ignore[blind-except]
        _origins(lines, error)


def _origins(lines: list[str], error: BaseException) -> None:
    """Report a failure, its operation, its cause, and its context."""
    _kept(lines, error)
    cause, context = type(getattr(error, "cause", None)).__name__, type(error.__context__).__name__
    lines.append(f"    operation {getattr(error, 'operation', None)!r} cause {cause} context {context}")


def _refusals(resumes: _Resumes, api: Any) -> None:
    """Refuse a state that is not one, another helper's, or that does not fit."""
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
    for label, saved in (
        ("missing members", {"cursor": "1"}),
        ("cursor not a string", _replaced(state, cursor=5)),
        ("empty cursor", _replaced(state, cursor="")),
        ("bound values of another count", _replaced(state, bound=["x"])),
        ("a saved cookie", _replaced(state, arguments=[[], [], ["c"]])),
        ("an expiry of another form", _replaced(state, expires_at="soon")),
        ("cursor the reopen cannot encode", _replaced(state, cursor="5 ")),
    ):
        record(lines, f"resume {label}", lambda saved=saved: helper.resume(_crafted(harness, state, saved)))
    record(
        lines,
        "object cursor",
        lambda: api.protocols.records.all.resume(_crafted(harness, other, _replaced(other, cursor={"a": 1}))),
    )
    _cursor_refusals(resumes, api)
    resumes.reply(*_events('id: 2\ndata: {"text": "b"}\n\n'))
    _drained(lines, "resume after the refusals", helper.resume(state))


def _cursor_refusals(resumes: _Resumes, api: Any) -> None:
    """Refuse checkpoint fields and body cursors that do not fit the reopen operation."""
    lines, harness = resumes.lines, resumes.harness
    resumes.reply(*_events('id: 1\ndata: {"text": "a"}\n\n'), headers=_TRACKED)
    tracked = api.protocols.events.tracked.open()
    next(tracked)
    saved = tracked.checkpoint()
    tracked.close()
    crafted = _crafted(harness, saved, _replaced(saved, arguments=[[]]))
    record(lines, "resume arguments of another operation", lambda: api.protocols.events.tracked.resume(crafted))
    query = harness.models.FeedQuery(topic="t")
    resumes.reply(*_events('event: tick\ndata: {"seq": 1}\n\n'))
    ticks = api.protocols.feed.ticks.open(body=query)
    next(ticks)
    query.topic = object()
    record(lines, "checkpoint of changed invalid body", ticks.checkpoint)
    query.topic = "t"
    tick = ticks.checkpoint()
    ticks.close()
    auth = importlib.import_module(f"{harness.package.__name__}.auth")
    signer = _Signer(auth.SignerCapabilities((), ("X-Signature",), ("sig",), False), auth.SignatureFields((), ()))
    signed = api.with_options(harness.options.RequestOptions(auth=auth.AuthConfig({}, signers=(signer,))))
    for label, client, criteria in (
        ("ordinary querystring", api, {"term": "news"}),
        ("credential querystring", api, {"api_key": "secret"}),
        ("signer querystring field", signed, {"sig": "echoed"}),
    ):
        resumes.reply(*_events('event: tick\ndata: {"seq": 1}\n\n'))
        value = resumes.argument("querystring", "criteria", criteria, "searchFeed")
        with client.protocols.searches.ticks.open(body=query, criteria=value) as searched:
            next(searched)
            record(lines, f"checkpoint of {label}", lambda: type(searched.checkpoint()).__name__)
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
    for label, body in (
        ("invalid saved body", [{"topic": []}, "application/json", None]),
        ("invalid saved media", [{"topic": "t"}, "application/json", "application/xml"]),
        ("mismatched saved media", [{"topic": "t"}, "application/other+json", "application/json"]),
    ):
        record(
            lines,
            label,
            lambda body=body: api.protocols.feed.ticks.resume(_crafted(harness, tick, _replaced(tick, body=body))),
        )


async def _async_resume(package: ModuleType, lines: list[str]) -> None:
    """Reconnect, checkpoint, and resume asyncio streams."""
    resumes = _Resumes(package, lines)
    harness = resumes.harness
    lines.append("asyncio")
    async with resumes.async_client(resumes.client_options()) as api:
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
        resumes.reply(b'id: 5 \ndata: {"text": "a"}\n\n', cut)
        stream = await helper.open(stream_options=resumes.reconnect)
        try:
            await anext(stream)
            await anext(stream)
        except Exception as error:  # ruff: ignore[blind-except]
            _origins(lines, error)
        resumes.reply(b'id: 1\ndata: {"text": "a"}\n\n', _Stop())
        stream = await helper.open(stream_options=resumes.reconnect)
        await anext(stream)
        try:
            await anext(stream)
        except _Stop:
            lines.append("  async stopped ! Stop")
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


def _guard_failure(lines: list[str], error: Exception) -> None:
    """Report the public identity and cause of a refused stream operation."""
    operation = getattr(error, "operation", None)
    lines.append(
        f"  refused {type(error).__name__} condition={getattr(error, 'condition', None)!r} "
        f"path={getattr(error, 'field_path', ())!r} operation={getattr(operation, 'pointer', None)!r} "
        f"operation_id={getattr(error, 'operation_id', None)!r} "
        f"cause={type(getattr(error, 'cause', None)).__name__} context={type(error.__context__).__name__}"
    )
    if (progress := getattr(error, "progress", None)) is not None:
        lines.append(f"  admission progress {dict(progress)}")


def _guarded(lines: list[str], label: str, action: Callable[[], Any]) -> None:
    """Observe a refused public call and the requests its transport reports."""
    sends = sum(line.startswith("  >") for line in lines)
    lines.append(label)
    try:
        result = action()
    except Exception as error:  # ruff: ignore[blind-except]
        _guard_failure(lines, error)
    else:
        lines.append(f"  returned {type(result).__name__}")
    lines.append(f"  new sends={sum(line.startswith('  >') for line in lines) - sends}")


async def _aguarded(lines: list[str], label: str, action: Callable[[], Any]) -> None:
    """Observe the same refusals through asynchronous public calls."""
    sends = sum(line.startswith("  >") for line in lines)
    lines.append(label)
    try:
        result = await action()
    except Exception as error:  # ruff: ignore[blind-except]
        _guard_failure(lines, error)
    else:
        lines.append(f"  returned {type(result).__name__}")
    lines.append(f"  new sends={sum(line.startswith('  >') for line in lines) - sends}")


def _query_patches(resumes: _Resumes) -> Iterable[tuple[str, str, str, Any]]:
    """Give collisions at each options origin and both known and dynamic expanded property names."""
    options = resumes.harness.options
    for helper, names in (
        ("scoped", ("scope", "after", "page", "tag")),
        ("named", ("after",)),
        ("deep", ("scope[after]", "scope[page]", "scope[tag]")),
    ):
        for name in names:
            for origin in ("client", "view", "call"):
                kind = options.ClientOptions if origin == "client" else options.RequestOptions
                yield helper, name, origin, kind(query=((name, "STALE"), ("tag", "kept")))


def _guard_auth(resumes: _Resumes, *, asynchronous: bool, authenticated: bool) -> Any:
    """Configure the active query scheme explicitly when exercising the authenticated control."""
    options = resumes.harness.options
    settings = {"retry": options.RetryOptions(initial_delay=0, max_delay=0)}
    if authenticated:
        auth = importlib.import_module(f"{resumes.harness.package.__name__}.auth")
        kind = auth.AsyncStaticCredentialProvider if asynchronous else auth.StaticCredentialProvider
        settings["auth"] = auth.AuthConfig(
            credentials={"query_key": kind(auth.ApiKeyCredential("CLIENT_KEY"))},
            send_on_anonymous=True,
            anonymous_schemes=("query_key",),
        )
    return options.ClientOptions(**settings)


def _write_guards(package: ModuleType, lines: list[str]) -> None:
    """Refuse credential writes and query defaults before reopening, preserving independent query fields."""
    resumes = _Resumes(package, lines)
    options = resumes.harness.options
    lines.append("stream write guards")
    for authenticated in (False, True):
        with resumes.client(_guard_auth(resumes, asynchronous=False, authenticated=authenticated)) as api:
            for name in ("scoped", "bound", "deep", "deepbound"):
                resumes.reply(b'id: 1\ndata: {"scope": {"api_key": "SERVER_KEY"}}\n\n', resumes.harness.interrupted())
                stream = getattr(api.protocols.marks, name).open(stream_options=resumes.reconnect)
                next(stream)
                _guarded(lines, f"credential reconnect {name} authenticated={authenticated}", partial(next, stream))
                _guarded(lines, "credential checkpoint", stream.checkpoint)
                stream.close()
            if authenticated:
                lines.append("safe authenticated reconnect")
                resumes.reply(b'data: {"scope": {"after": "5"}}\n\n', resumes.harness.interrupted())
                resumes.reply(b'data: {"scope": {"after": "6"}}\n\n')
                with api.protocols.marks.scoped.open(stream_options=resumes.reconnect) as stream:
                    next(stream)
                    lines.append(f"  delivered sequence={next(stream).sequence}")
                scope = resumes.argument("query", "scope", {"api_key": "SERVER_KEY"}, "streamMarks")
                _guarded(lines, "active auth collision on open", partial(api.protocols.marks.scoped.open, scope=scope))
    for name, field, origin, patch in _query_patches(resumes):
        settings = patch if origin == "client" else resumes.client_options()
        with resumes.client(settings) as api:
            owner = api.with_options(patch) if origin == "view" else api
            helper = getattr(owner.protocols.marks, name)
            _guarded(
                lines,
                f"query patch {name} {field} {origin}",
                partial(helper.open, options=patch if origin == "call" else None),
            )
    with resumes.client(resumes.client_options()) as api:
        _cleared_guards(resumes, api)
        resumes.reply(b'event: tick\ndata: {"seq": 1}\n\n', b"data: [DONE]\n\n")
        query = resumes.harness.models.FeedQuery(topic="t")
        with api.protocols.feed.ticks.open(
            body=query, options=options.RequestOptions(query=(("tag", "kept"),))
        ) as stream:
            next(stream)
            state = stream.checkpoint()
        resumes.reply(b"data: [DONE]\n\n")
        api.protocols.feed.ticks.resume(state, options=options.RequestOptions(query=(("tag", "kept"),))).close()
        patch = options.RequestOptions(query=(("tag", "kept"),))
        resumes.reply(b'{"id": "r1", "text": "a"}\n', media=_NDJSON)
        with api.protocols.records.all.open(options=patch) as stream:
            next(stream)
            state = stream.checkpoint()
        resumes.reply(media=_NDJSON)
        api.protocols.records.all.resume(state, options=patch).close()


def _cleared_guards(resumes: _Resumes, api: Any) -> None:
    """Clear known and dynamic cursor fields on explicit and automatic reopens while keeping another parameter."""
    lines, options = resumes.lines, resumes.harness.options
    for name in ("scoped", "named", "deep"):
        owner = api if name == "scoped" else api.with_options(options.RequestOptions(query=(("tag", "kept"),)))
        helper = getattr(owner.protocols.marks, name)
        cursor = {"after": "5"} if name == "named" else {"after": "5", "page": "dynamic"}
        resumes.reply(f"data: {json.dumps({'scope': cursor})}\n\n".encode())
        given = {"tag": resumes.argument("query", "tag", "kept", "streamMarks")} if name == "scoped" else {}
        stream = helper.open(**given)
        next(stream)
        state = stream.checkpoint()
        stream.close()
        resumes.reply(b'data: {"scope": null}\n\n', resumes.harness.interrupted())
        resumed = helper.resume(state, stream_options=resumes.reconnect)
        next(resumed)
        cleared = resumed.checkpoint()
        lines.append(f"  {name} saved cleared cursor={json.loads(cleared.export())['state']['cursor']!r}")
        resumes.reply(b'data: {"scope": {"after": "6"}}\n\n')
        next(resumed)
        resumed.close()
        resumes.reply()
        helper.resume(cleared).close()
        for field in ("after", "page") if name == "scoped" else ("scope[after]",) if name == "deep" else ("after",):
            _guarded(
                lines,
                f"cleared resume patch {name} {field}",
                partial(helper.resume, cleared, options=options.RequestOptions(query=((field, "STALE"),))),
            )
    for name, field in (("named", "page"), ("named", "scope"), ("deep", "scope")):
        helper = getattr(api.protocols.marks, name)
        patch = options.RequestOptions(query=((field, "unrelated"),))
        resumes.reply(b'data: {"scope": {"after": "5"}}\n\n')
        stream = helper.open(options=patch)
        next(stream)
        state = stream.checkpoint()
        stream.close()
        resumes.reply()
        helper.resume(state, options=patch).close()
    helper = api.protocols.marks.deep
    for field in ("scope[after", "other"):
        resumes.reply(b'data: {"scope": {"after": "5"}}\n\n')
        stream = helper.open(options=options.RequestOptions(query=((field, "unrelated"),)))
        next(stream)
        stream.checkpoint()
        stream.close()


async def _awrite_guards(package: ModuleType, lines: list[str]) -> None:
    """Exercise identical credential, patch, clearing, and child admission contracts with asyncio."""
    resumes = _Resumes(package, lines)
    options = resumes.harness.options
    lines.append("async stream write guards")
    for authenticated in (False, True):
        async with resumes.async_client(_guard_auth(resumes, asynchronous=True, authenticated=authenticated)) as api:
            for name in ("scoped", "bound", "deep", "deepbound"):
                resumes.reply(b'id: 1\ndata: {"scope": {"api_key": "SERVER_KEY"}}\n\n', resumes.harness.interrupted())
                stream = await getattr(api.protocols.marks, name).open(stream_options=resumes.reconnect)
                await anext(stream)
                await _aguarded(
                    lines, f"credential reconnect {name} authenticated={authenticated}", partial(anext, stream)
                )
                _guarded(lines, "credential checkpoint", stream.checkpoint)
                await stream.aclose()
            if authenticated:
                lines.append("safe authenticated reconnect")
                resumes.reply(b'data: {"scope": {"after": "5"}}\n\n', resumes.harness.interrupted())
                resumes.reply(b'data: {"scope": {"after": "6"}}\n\n')
                async with await api.protocols.marks.scoped.open(stream_options=resumes.reconnect) as stream:
                    await anext(stream)
                    lines.append(f"  delivered sequence={(await anext(stream)).sequence}")
                scope = resumes.argument("query", "scope", {"api_key": "SERVER_KEY"}, "streamMarks")
                await _aguarded(
                    lines, "active auth collision on open", partial(api.protocols.marks.scoped.open, scope=scope)
                )
    for name, field, origin, patch in _query_patches(resumes):
        settings = patch if origin == "client" else resumes.client_options()
        async with resumes.async_client(settings) as api:
            owner = api.with_options(patch) if origin == "view" else api
            helper = getattr(owner.protocols.marks, name)
            await _aguarded(
                lines,
                f"query patch {name} {field} {origin}",
                partial(helper.open, options=patch if origin == "call" else None),
            )
    async with resumes.async_client(resumes.client_options()) as api:
        for name in ("scoped", "named", "deep"):
            owner = api if name == "scoped" else api.with_options(options.RequestOptions(query=(("tag", "kept"),)))
            helper = getattr(owner.protocols.marks, name)
            cursor = {"after": "5"} if name == "named" else {"after": "5", "page": "dynamic"}
            resumes.reply(f"data: {json.dumps({'scope': cursor})}\n\n".encode())
            given = {"tag": resumes.argument("query", "tag", "kept", "streamMarks")} if name == "scoped" else {}
            stream = await helper.open(**given)
            await anext(stream)
            state = stream.checkpoint()
            await stream.aclose()
            resumes.reply(b'data: {"scope": null}\n\n', resumes.harness.interrupted())
            resumed = await helper.resume(state, stream_options=resumes.reconnect)
            await anext(resumed)
            cleared = resumed.checkpoint()
            lines.append(f"  {name} saved cleared cursor={json.loads(cleared.export())['state']['cursor']!r}")
            resumes.reply(b'data: {"scope": {"after": "6"}}\n\n')
            await anext(resumed)
            await resumed.aclose()
            resumes.reply()
            await (await helper.resume(cleared)).aclose()
            for field in ("after", "page") if name == "scoped" else ("scope[after]",) if name == "deep" else ("after",):
                await _aguarded(
                    lines,
                    f"cleared resume patch {name} {field}",
                    partial(helper.resume, cleared, options=options.RequestOptions(query=((field, "STALE"),))),
                )
        for name, field in (("named", "page"), ("named", "scope"), ("deep", "scope")):
            helper = getattr(api.protocols.marks, name)
            patch = options.RequestOptions(query=((field, "unrelated"),))
            resumes.reply(b'data: {"scope": {"after": "5"}}\n\n')
            stream = await helper.open(options=patch)
            await anext(stream)
            state = stream.checkpoint()
            await stream.aclose()
            resumes.reply()
            await (await helper.resume(state, options=patch)).aclose()
        patch = options.RequestOptions(query=(("tag", "kept"),))
        resumes.reply(b'{"id": "r1", "text": "a"}\n', media=_NDJSON)
        async with await api.protocols.records.all.open(options=patch) as stream:
            await anext(stream)
            state = stream.checkpoint()
        resumes.reply(media=_NDJSON)
        await (await api.protocols.records.all.resume(state, options=patch)).aclose()
