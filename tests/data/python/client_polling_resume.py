"""Checkpoint and resume long-running operations, cancel them remotely, and bound checkpoints by a server's expiry."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Final

from tests.data.python.client_polling import Polling, astep, job, report, step
from tests.data.python.client_runtime import Exchange, json_response, raw_response, run

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_PAST: Final = datetime(2000, 1, 1, tzinfo=timezone.utc)
_EXPIRES: Final = "2999-01-01T00:00:00Z"


def _envelope(state: Any) -> dict[str, Any]:
    """Return the exported JSON of a token."""
    return json.loads(state.export())


def _saved(lines: list[str], label: str, state: Any) -> None:
    """Report what an exported token saved, never its helper identity."""
    lines.append(f"  {label} saved {json.dumps(_envelope(state)['state'], sort_keys=True)}")


def _crafted(harness: Polling, state: Any, saved: object) -> Any:
    """Return a token of another's helper with a replaced protocol state."""
    return harness.protocols.ResumeState(helper=_envelope(state)["helper"], state=saved)


def _replaced(state: Any, path: tuple[object, ...], value: object) -> dict[str, Any]:
    """Return the protocol state of an export with one member, reached through keys and indices, replaced."""
    saved = _envelope(state)["state"]
    target = saved
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return saved


def _failure(call: Callable[[], object]) -> Any:
    try:
        call()
    except Exception as error:  # ruff: ignore[blind-except]
        return error
    return None


def _kept(lines: list[str], label: str, error: Any) -> Any:
    """Report a failure and whether it keeps a resume state, returning the state."""
    state = getattr(error, "resume_state", None)
    lines.append(f"  {label} ! {type(error).__name__} resume_state={type(state).__name__}")
    return state


def _tracked(status: str, code: int = 200, job_id: str = "j5", **members: object) -> Any:
    """Return a responder of a tracked job in a state, expiring far in the future unless told otherwise."""
    return job(status, code, job_id, **{"expires": _EXPIRES, **members})


def polling_resume(package: ModuleType, lines: list[str]) -> None:
    """Checkpoint, resume, and cancel long-running operations through the synchronous and asyncio clients."""
    harness = Polling(package)
    exchange = Exchange(lines)
    with exchange.client() as native, package.Client(http_client=native, options=harness.client_options()) as api:
        _pending(harness, api, exchange, lines)
        _settled(harness, api, exchange, lines)
        _errors(harness, api, exchange, lines)
        _refusals(harness, api, exchange, lines)
        _malformed(harness, api, exchange, lines)
        _cancels(harness, api, exchange, lines)
        _expiries(harness, api, exchange, lines)
    _clocked(harness, lines)
    run(lambda: _async_resume(harness, lines))


class _Clock:
    """A clock source that moves only when a scenario sets it."""

    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _clocked(harness: Polling, lines: list[str]) -> None:
    """Read a server's expiry and refuse an expired token on the client's wall clock."""
    exchange = Exchange(lines)
    wall = _Clock(datetime(2060, 1, 1, tzinfo=timezone.utc).timestamp())
    settings = harness.client_options(clock=harness.options.Clock(time=wall))
    body = harness.body
    lines.append("checkpoints on the client clock")
    with exchange.client() as native, harness.package.Client(http_client=native, options=settings) as api:
        tracked = api.protocols.jobs.tracked
        exchange.respond(_tracked("queued", 202, expires="Thursday, 01-Jan-99 00:00:00 GMT"))
        state = tracked.start(body=body).checkpoint()
        expires_at = _envelope(state)["state"]["expires_at"]
        lines.append(f"  two-digit year by the client's wall clock expires_at={expires_at}")
        exchange.respond(_tracked("queued", 202, expires="2000-01-01T00:00:00Z"))
        state = tracked.start(body=body).checkpoint()
        for label, now in (("before", _PAST.timestamp() - 1), ("at", _PAST.timestamp())):
            wall.value = now
            step(lines, f"expiry {label} the client's wall clock", lambda state=state: tracked.resume(state).progress)


def _pending(harness: Polling, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Resume a pending operation without creating it again: its bindings, polls, and result fetch carry over."""
    helper = api.protocols.jobs.run
    body = harness.body
    lines.append("pending operations")
    exchange.respond(job("queued", 202), job("running"))
    handle = helper.start(body=body)
    step(lines, "status", handle.status)
    state = handle.checkpoint()
    _saved(lines, "pending", state)
    lines.append(f"  state repr {state!r}")
    resumed = helper.resume(harness.protocols.import_state(state.export()))
    lines.append(f"  resumed {resumed!r} progress {dict(resumed.progress)!r}")
    exchange.respond(job("done"), report(3))
    step(lines, "resumed wait", resumed.wait)
    lines.append(f"  resumed progress {dict(resumed.progress)!r}")
    exchange.respond(job("queued", 202))
    fresh = helper.start(body=body).checkpoint()
    _saved(lines, "before any poll", fresh)
    exchange.respond(job("done"), report(4))
    step(lines, "resumed before any poll", helper.resume(fresh).wait)
    reports = api.protocols.jobs.report
    exchange.respond(job("queued", 202))
    posted = reports.start(body=body).checkpoint()
    _saved(lines, "fetch written from the final poll", posted)
    exchange.respond(job("done"), report(9))
    step(lines, "resumed fetch written from the final poll", reports.resume(posted).wait)
    exports = api.protocols.exports.run
    exchange.respond(raw_response(202, **{"Operation-Id": "e1"}))
    queried = exports.start().checkpoint()
    _saved(lines, "querystring", queried)
    exchange.respond(json_response(200, {"state": 1}))
    step(lines, "resumed querystring", exports.resume(queried).wait)
    exchange.respond(job("queued", 202))
    with helper.start(body=body) as closed:
        pass
    _saved(lines, "closed", closed.checkpoint())
    exchange.respond(job("done"), report(5))
    step(lines, "resumed after close", helper.resume(closed.checkpoint()).wait)


def _settled(harness: Polling, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Refuse checkpoints of settled operations, and resume a success whose result fetch is due by fetching it."""
    helper = api.protocols.jobs.run
    body = harness.body
    lines.append("settled operations")
    exchange.respond(job("queued", 202), job("done"), report(6))
    handle = helper.start(body=body)
    step(lines, "wait", handle.wait)
    step(lines, "checkpoint of a fetched result", handle.checkpoint)
    for status in ("failed", "cancelled"):
        exchange.respond(job("queued", 202), job(status))
        handle = helper.start(body=body)
        step(lines, f"{status} wait", handle.wait)
        step(lines, f"{status} checkpoint", handle.checkpoint)
    once = harness.request(retry=harness.options.RetryOptions(max_retries=0))
    exchange.respond(job("queued", 202), job("done"), json_response(500, {}))
    handle = helper.start(body=body, options=once)
    step(lines, "failed fetch", handle.wait)
    state = handle.checkpoint()
    _saved(lines, "fetch due", state)
    resumed = helper.resume(state)
    step(lines, "resumed fetch status", resumed.status)
    exchange.respond(report(7))
    step(lines, "resumed fetch", resumed.wait)
    inline = api.protocols.jobs.inline
    exchange.respond(job("done", 200, "j3", result={"rows": 1}))
    immediate = inline.start(body=body)
    step(lines, "immediate checkpoint", immediate.checkpoint)
    exchange.respond(
        job("queued", 202, "j2"),
        json_response(200, {"id": "j2", "status": "any", "result": {"rows": 8}}, **{"X-State": "done"}),
    )
    handle = inline.start(body=body)
    step(lines, "inline wait", handle.wait)
    step(lines, "inline checkpoint", handle.checkpoint)
    exports = api.protocols.exports.run
    exchange.respond(raw_response(202, **{"Operation-Id": "e1"}), json_response(200, {"state": 1}))
    handle = exports.start()
    step(lines, "wait without a result", handle.wait)
    step(lines, "checkpoint without a result", handle.checkpoint)


def _errors(harness: Polling, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Keep a checkpoint on wait and session limits; polls count on, while the session starts afresh."""
    helper = api.protocols.jobs.run
    body = harness.body
    lines.append("errors that keep a checkpoint")
    exchange.respond(json_response(202, {"id": "j1", "status": "queued"}, **{"Retry-After": "120"}))
    handle = helper.start(body=body)
    state = _kept(lines, "long server delay", _failure(handle.status))
    _saved(lines, "long server delay", state)
    exchange.respond(job("running"))
    step(lines, "resumed polls at once", helper.resume(state).status)
    huge = "1" + "0" * 308
    exchange.respond(json_response(202, {"id": "j1", "status": "queued"}, **{"Retry-After": huge}))
    handle = helper.start(body=body)
    _kept(lines, "status after a huge server delay", _failure(handle.status))
    state = _kept(lines, "wait after a huge server delay", _failure(handle.wait))
    _saved(lines, "huge server delay", state)
    _saved(lines, "checkpoint after a huge server delay", handle.checkpoint())
    exchange.respond(job("running"))
    step(lines, "resumed after the huge delay polls at once", helper.resume(state).status)
    exchange.respond(job("queued", 202), job("queued"))
    limited = helper.start(body=body, poll_options=harness.polls(max_polls=1))
    step(lines, "first poll", limited.status)
    state = _kept(lines, "poll limit", _failure(limited.status))
    exchange.respond(job("running"))
    again = helper.resume(state, poll_options=harness.polls(max_polls=1))
    step(lines, "resumed at the same limit polls afresh", again.status)
    lines.append(f"  resumed progress {dict(again.progress)!r}")
    exchange.respond(job("queued", 202), job("queued"))
    budget = helper.start(body=body, session_options=harness.session(max_network_sends=2))
    step(lines, "poll", budget.status)
    state = _kept(lines, "send limit", _failure(budget.status))
    exchange.respond(job("done"), report(8))
    step(lines, "resumed in a new session", helper.resume(state).wait)
    _kept(
        lines,
        "create without a send slot",
        _failure(lambda: helper.start(body=body, session_options=harness.session(max_network_sends=0))),
    )


def _refusals(harness: Polling, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Refuse other helpers' states, expired ones, and the resumed call's own bad options, sending nothing."""
    helper = api.protocols.jobs.run
    lines.append("refused states")
    exchange.respond(job("queued", 202))
    state = helper.start(body=harness.body).checkpoint()
    step(lines, "not a state", lambda: helper.resume(b"state"))
    step(lines, "another helper's state", lambda: api.protocols.jobs.report.resume(state))
    expired = _replaced(state, ("expires_at",), _PAST.isoformat())
    step(lines, "expired state", lambda: helper.resume(_crafted(harness, state, expired)))
    step(
        lines,
        "resumed with a patched written header",
        lambda: helper.resume(state, options=harness.request(headers=[("X-Trace", "mine")])),
    )
    step(lines, "resumed with poll options of another type", lambda: helper.resume(state, poll_options=1))


def _malformed(harness: Polling, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Refuse a state or saved body that does not fit the helper as malformed, and saved dot segments as data errors."""
    helper = api.protocols.jobs.run
    body = harness.body
    lines.append("malformed states")
    exchange.respond(job("queued", 202))
    pending = helper.start(body=body).checkpoint()
    for label, saved in (
        ("unknown member", {**_envelope(pending)["state"], "extra": 1}),
        ("unknown phase", _replaced(pending, ("phase",), "settled")),
        ("fetch with values of the poll", _replaced(pending, ("phase",), "fetch")),
        ("fetch with values of the create response", {**_envelope(pending)["state"], "phase": "fetch", "bound": [1]}),
        ("fetch value of another type", _replaced(pending, ("seed", 0), 5)),
        ("null fetch value", _replaced(pending, ("seed", 0), None)),
        ("object fetch value", _replaced(pending, ("seed", 0), {"x": 1})),
        ("short bindings", _replaced(pending, ("bound",), [])),
        ("cancel values without a remote cancel", _replaced(pending, ("cancel",), ["j1"])),
        ("header with a line break", _replaced(pending, ("bound", 1), "a\r\nX-Injected: 1")),
        ("expiry of another form", _replaced(pending, ("expires_at",), "soon")),
        ("expiry without an offset", _replaced(pending, ("expires_at",), "2999-01-01T00:00:00")),
    ):
        step(lines, label, lambda saved=saved: helper.resume(_crafted(harness, pending, saved)))
    inline = api.protocols.jobs.inline
    exchange.respond(job("queued", 202, "j2"))
    queued = inline.start(body=body).checkpoint()
    fetchless = {**_envelope(queued)["state"], "phase": "fetch", "bound": []}
    step(lines, "fetch of a helper without one", lambda: inline.resume(_crafted(harness, queued, fetchless)))
    for label, path in (("dot segment for a poll", ("bound", 0)), ("dot segment for a fetch", ("seed", 0))):
        step(
            lines,
            label,
            lambda path=path: helper.resume(_crafted(harness, pending, _replaced(pending, path, ".."))),
        )


def _cancels(harness: Polling, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Cancel an operation remotely while it is pending; the handle keeps its last poll until it polls again."""
    helper = api.protocols.jobs.tracked
    body = harness.body
    lines.append("remote cancellation")
    exchange.respond(_tracked("queued", 202), _tracked("running", job_id="j9"))
    handle = step(lines, "start", lambda: helper.start(body=body))
    lines.append(f"  handle class {type(handle).__name__}")
    step(lines, "status", handle.status)
    exchange.respond(_tracked("cancelled", 202, job_id="j9"))
    step(lines, "cancel", handle.cancel_remote)
    lines.append(f"  after the cancel {handle!r} progress {dict(handle.progress)!r}")
    exchange.respond(raw_response(204))
    step(lines, "cancel again", handle.cancel_remote)
    exchange.respond(json_response(409, {"message": "conflict"}))
    step(lines, "cancel refused", handle.cancel_remote)
    state = handle.checkpoint()
    _saved(lines, "tracked", state)
    resumed = helper.resume(state)
    lines.append(f"  resumed class {type(resumed).__name__}")
    exchange.respond(raw_response(204))
    step(lines, "resumed cancel", resumed.cancel_remote)
    exchange.respond(_tracked("cancelled", job_id="j9"))
    step(lines, "status after the cancel", handle.status)
    step(lines, "cancel once cancelled", handle.cancel_remote)
    step(lines, "wait", handle.wait)
    exchange.respond(_tracked("queued", 202))
    closed = helper.start(body=body)
    step(lines, "close", closed.close)
    step(lines, "cancel after close", closed.cancel_remote)
    exchange.respond(_tracked("queued", 202), json_response(200, {"status": "running"}))
    missing = helper.start(body=body)
    step(lines, "poll without a cancel value", missing.status)
    lines.append(f"  progress {dict(missing.progress)!r}")
    exchange.respond(_tracked("queued", 202))
    unbudgeted = helper.start(body=body, session_options=harness.session(max_network_sends=1))
    _kept(lines, "cancel without a send slot", _failure(unbudgeted.cancel_remote))
    exports = api.protocols.exports.run
    exchange.respond(raw_response(202, **{"Operation-Id": "e1"}), raw_response(204))
    handle = exports.start()
    lines.append(f"  handle class {type(handle).__name__}")
    step(lines, "cancel without bindings", handle.cancel_remote)


def _expiries(harness: Polling, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Read the server's expiry from the create response; checkpoints expire then, while polling goes on."""
    helper = api.protocols.jobs.tracked
    body = harness.body
    lines.append("server expiries")
    for label, value in (
        ("offset date-time", "2999-01-01T09:00:00.25+09:00"),
        ("lowercase date-time", "2999-01-01t00:00:00z"),
        ("fraction in UTC", "2999-01-01T00:00:00.1Z"),
        ("fraction past microseconds", "2999-01-01T00:00:00.123456789Z"),
        ("HTTP date", "Tue, 01 Jan 2999 00:00:00 GMT"),
        ("leap second", "2998-12-31T23:59:60Z"),
        ("leap second with a fraction and an offset", "2999-01-01T08:59:60.5+09:00"),
    ):
        exchange.respond(_tracked("queued", 202, expires=value))
        lines.append(f"  {label} expires_at={_envelope(helper.start(body=body).checkpoint())['state']['expires_at']}")
    exchange.respond(_tracked("queued", 202, expires="2000-01-01T00:00:00Z"), _tracked("done", result={"rows": 4}))
    expired = helper.start(body=body)
    state = expired.checkpoint()
    step(lines, "resume past the expiry", lambda: helper.resume(state))
    step(lines, "import past the expiry", lambda: harness.protocols.import_state(state.export()))
    step(lines, "polling past the expiry", expired.wait)
    for label, responder in (
        ("missing expiry", job("queued", 202, "j5")),
        ("null expiry", job("queued", 202, "j5", expires=None)),
        ("numeric expiry", job("queued", 202, "j5", expires=1)),
        ("unparsable expiry", job("queued", 202, "j5", expires="soon")),
        ("impossible date", job("queued", 202, "j5", expires="2999-02-30T00:00:00Z")),
        ("date past the last year", job("queued", 202, "j5", expires="9999-12-31T23:59:59-01:00")),
    ):
        exchange.respond(responder)
        step(lines, label, lambda: helper.start(body=body))


async def _async_resume(harness: Polling, lines: list[str]) -> None:
    """Checkpoint, resume, and cancel with asyncio: resume is not awaited; checkpoints and cancels run in a wait."""
    exchange = Exchange(lines)
    async with (
        exchange.async_client() as native,
        harness.package.AsyncClient(http_client=native, options=harness.client_options()) as api,
    ):
        helper = api.protocols.jobs.run
        body = harness.body
        lines.append("async resume")
        exchange.respond(job("queued", 202), job("running"))
        handle = await helper.start(body=body)
        await astep(lines, "status", handle.status)
        state = handle.checkpoint()
        resumed = helper.resume(state)
        lines.append(f"  resumed {resumed!r}")
        exchange.respond(job("done"), report(3))
        await astep(lines, "resumed wait", resumed.wait)
        once = harness.request(retry=harness.options.RetryOptions(max_retries=0))
        exchange.respond(job("queued", 202), job("done"), json_response(500, {}))
        handle = await helper.start(body=body, options=once)
        await astep(lines, "failed fetch", handle.wait)
        exchange.respond(report(4))
        await astep(lines, "resumed fetch", helper.resume(handle.checkpoint()).wait)
        exchange.respond(json_response(202, {"id": "j1", "status": "queued"}, **{"Retry-After": "120"}))
        handle = await helper.start(body=body)
        try:
            await handle.status()
        except Exception as error:  # ruff: ignore[blind-except]
            _kept(lines, "async long server delay", error)
        lines.append("async remote cancellation")
        tracked = api.protocols.jobs.tracked
        exchange.respond(_tracked("queued", 202))
        handle = await tracked.start(body=body, poll_options=harness.polls(interval=30))
        lines.append(f"  handle class {type(handle).__name__}")
        waiting = asyncio.create_task(handle.wait())
        await asyncio.sleep(0)
        _saved(lines, "checkpoint while waiting", handle.checkpoint())
        exchange.respond(_tracked("cancelled", 202))
        await astep(lines, "cancel while waiting", handle.cancel_remote)
        lines.append(f"  waiting after the cancel {not waiting.done()} {handle!r}")
        waiting.cancel()
        try:
            await waiting
        except asyncio.CancelledError:
            lines.append("  wait cancelled")
        exchange.respond(_tracked("cancelled", 202))
        await astep(lines, "cancel", handle.cancel_remote)
        resumed = tracked.resume(handle.checkpoint())
        lines.append(f"  resumed class {type(resumed).__name__}")
        exchange.respond(raw_response(204))
        await astep(lines, "resumed cancel", resumed.cancel_remote)
        await astep(lines, "close", handle.aclose)
        await astep(lines, "cancel after close", handle.cancel_remote)
        exchange.respond(_tracked("queued", 202))
        unbudgeted = await tracked.start(body=body, session_options=harness.session(max_network_sends=1))
        try:
            await unbudgeted.cancel_remote()
        except Exception as error:  # ruff: ignore[blind-except]
            _kept(lines, "async cancel without a send slot", error)
