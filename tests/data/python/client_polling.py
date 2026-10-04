"""Poll long-running operations through generated helpers: creation, states, results, waits, limits, and failures."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.data.python.client_pagination import Harness
from tests.data.python.client_runtime import (
    Exchange,
    arecord,
    describe,
    failing,
    json_response,
    raw_response,
    request_body,
    run,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_HELPERS: Final = ("jobs.run", "jobs.inline", "jobs.report", "jobs.tracked", "exports.run", "exports.latest")
_PAUSE: Final = 0.2
_WALL: Final = 1_000_000_000.0
_WALL_DATE: Final = "Sun, 09 Sep 2001 01:48:40 GMT"


class _Clock:
    """A clock source that moves only when a scenario sets it."""

    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def job(
    status: str, code: int = 200, job: str = "j1", **members: object
) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a responder of a job in a state."""
    return json_response(code, {"id": job, "status": status, **members})


def _stated(state: str, **members: object) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a responder of a job whose state is the X-State header."""
    return json_response(200, {"id": "j2", "status": "any", **members}, **{"X-State": state})


def _export(state: object) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a responder of an export's state."""
    return json_response(200, {"state": state})


def report(rows: int) -> Callable[[httpx2.Request], httpx2.Response]:
    """Return a responder of a report."""
    return json_response(200, {"rows": rows})


def _snapshot(snapshot: Any) -> str:
    """Summarize a poll by its state, whether it is terminal, its status, and its decoded job's id."""
    data = snapshot.data
    return (
        f"PollSnapshot(state={snapshot.state!r}, terminal={snapshot.terminal}, "
        f"status={snapshot.response.status_code}, data={type(data).__name__}:{getattr(data, 'id', None)})"
    )


def _outcome(value: object) -> str:
    """Describe a step's result: a poll, a wait limit by its kind and thresholds, or anything else.

    An error also tells whether it names the helper's session.
    """
    if hasattr(value, "terminal"):
        return _snapshot(value)
    session = f" session={getattr(value, 'parent_session_id', None) is not None}"
    if hasattr(value, "required_wait"):
        error: Any = value
        if error.kind == "wait":
            facts = f"required>limit={error.required_wait > error.limit} limit={error.limit}"
        else:
            facts = f"required>=limit={error.required_wait >= error.limit}"
        return f"PollWaitLimitError kind={error.kind!r} {facts} {error.reason_code}{session}"
    if hasattr(value, "snapshot"):
        failure: Any = value
        return f"{type(failure).__name__}: {_snapshot(failure.snapshot)} {failure.reason_code}{session}"
    if hasattr(value, "response"):
        receipt: Any = value
        data = receipt.data
        return (
            f"CancelReceipt(status={receipt.response.status_code}, "
            f"data={type(data).__name__}:{getattr(data, 'status', None)})"
        )
    return describe(value) + (session if isinstance(value, BaseException) else "")


def measured(value: object) -> str:
    """Describe a step's result, giving a wait limit's exact required wait and limit."""
    if hasattr(value, "required_wait"):
        error: Any = value
        return f"PollWaitLimitError kind={error.kind!r} required_wait={error.required_wait} limit={error.limit}"
    return _outcome(value)


def step(
    lines: list[str], label: str, call: Callable[[], object], outcome: Callable[[object], str] = _outcome
) -> object:
    """Report a step's result or failure."""
    try:
        result = call()
    except Exception as error:  # noqa: BLE001
        lines.append(f"  {label} ! {outcome(error)}")
        return None
    lines.append(f"  {label} = {outcome(result)}")
    return result


async def astep(
    lines: list[str], label: str, call: Callable[[], Any], outcome: Callable[[object], str] = _outcome
) -> object:
    """Report an asyncio step's result or failure."""
    try:
        result = await call()
    except Exception as error:  # noqa: BLE001
        lines.append(f"  {label} ! {outcome(error)}")
        return None
    lines.append(f"  {label} = {outcome(result)}")
    return result


class Polling(Harness):
    """A generated polling package's public modules, its request body, and the handles its helpers start."""

    def __init__(self, package: ModuleType) -> None:
        """Import the modules and the create body codec."""
        super().__init__(package)
        self.body = request_body(package, "createJob", None, {"name": "nightly"})

    def client_options(self, **settings: Any) -> Any:
        """Return client options that retry at once and poll each helper without a noticeable interval."""
        protocols = self.protocols
        fast = protocols.ProtocolDefaults(options=protocols.PollOptions(interval=0.000001))
        defaults = self.options.ProtocolClientOptions(defaults=dict.fromkeys(_HELPERS, fast))
        return super().client_options(protocols=defaults, **settings)

    def polls(self, **settings: Any) -> Any:
        """Return poll options."""
        return self.protocols.PollOptions(**settings)

    def session(self, **settings: Any) -> Any:
        """Return session options."""
        return self.options.SessionOptions(**settings)

    def request(self, **settings: Any) -> Any:
        """Return request options."""
        return self.options.RequestOptions(**settings)


def polling(package: ModuleType, lines: list[str]) -> None:
    """Create, poll, wait for, and limit long-running operations through the synchronous and asyncio clients."""
    harness = Polling(package)
    exchange = Exchange(lines)
    with exchange.client() as native, package.Client(http_client=native, options=harness.client_options()) as api:
        _lifecycle(harness, api, exchange, lines)
        _terminal(harness, api, exchange, lines)
        _states(harness, api, exchange, lines)
        _creates(harness, api, exchange, lines)
        _inline(harness, api, exchange, lines)
        _others(harness, api, exchange, lines)
        _waits(harness, api, exchange, lines)
        _limits(harness, api, exchange, lines)
        _failures(harness, api, exchange, lines)
    _clocked(harness, lines)
    run(lambda: _async_polling(harness, lines))


def _lifecycle(harness: Polling, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Create once, poll through pending states, fetch the result once, and keep it."""
    helper = api.protocols.jobs.run
    lines.append("wait through pending states")
    exchange.respond(job("queued", 202), job("queued"), job("running"), job("done"), report(3))
    handle = step(lines, "start", lambda: helper.start(body=harness.body))
    step(lines, "wait", handle.wait)
    step(lines, "wait again", handle.wait)
    step(lines, "status after success", handle.status)
    lines.append(f"  progress {dict(handle.progress)!r}")
    lines.append("poll one at a time")
    exchange.respond(job("queued", 202), job("running"), job("done"), report(4))
    with helper.start(body=harness.body) as handle:
        step(lines, "status", handle.status)
        step(lines, "status", handle.status)
        step(lines, "wait", handle.wait)
    step(lines, "status after the block", handle.status)
    step(lines, "close again", handle.close)
    lines.append("sleep through a short interval")
    exchange.respond(job("queued", 202), job("done"), report(5))
    paused = helper.start(body=harness.body, poll_options=harness.polls(interval=_PAUSE))
    step(lines, "wait", paused.wait)


def _terminal(harness: Polling, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Raise a failed or cancelled operation with its last poll, on every later wait too, without sending again."""
    helper = api.protocols.jobs.run
    lines.append("failed and cancelled operations")
    exchange.respond(job("queued", 202), job("failed"))
    handle = helper.start(body=harness.body)
    step(lines, "wait", handle.wait)
    step(lines, "wait again", handle.wait)
    step(lines, "status", handle.status)
    exchange.respond(job("queued", 202), job("cancelled"))
    handle = helper.start(body=harness.body)
    step(lines, "status", handle.status)
    step(lines, "wait", handle.wait)


def _states(harness: Polling, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Refuse a state no set declares, by value or JSON type, and a missing one; the handle polls again after them."""
    helper = api.protocols.jobs.run
    lines.append("unknown and missing states")
    exchange.respond(
        job("queued", 202),
        job("exploded"),
        json_response(200, {"id": "j1", "status": 5}),
        json_response(200, {"id": "j1"}),
        job("queued"),
    )
    handle = helper.start(body=harness.body)
    for label in ("unknown state", "state of another type", "missing state", "status after them"):
        step(lines, label, handle.status)


def _creates(harness: Polling, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Refuse a create response the helper did not declare, bad binding values, and options it cannot honor."""
    helper = api.protocols.jobs.run
    body = harness.body
    lines.append("create responses")
    for label, responder in (
        ("unaccepted success status", job("done", 200)),
        ("missing operation id", json_response(202, {"status": "queued"})),
        ("dot segment operation id", job("queued", 202, job="..")),
        ("create error", json_response(500, {"message": "down"})),
    ):
        exchange.respond(responder)
        step(lines, label, lambda: helper.start(body=body))
    lines.append("options")
    for label, settings in (
        ("no session send slot", {"session_options": harness.session(max_network_sends=0)}),
        ("no call send slot", {"options": harness.request(max_network_sends=0)}),
        ("poll options of another type", {"poll_options": harness.session()}),
        ("fixed idempotency key", {"options": harness.request(idempotency_key=harness.options.IdempotencyKey.new())}),
        ("patched written header", {"options": harness.request(headers=[("x-trace", "mine")])}),
    ):
        step(lines, label, lambda settings=settings: helper.start(body=body, **settings))
    step(
        lines,
        "patched written query",
        lambda: api.protocols.jobs.inline.start(body=body, options=harness.request(query=[("verbose", "false")])),
    )


def _inline(harness: Polling, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Read a result from the final poll or from an immediate create response, refusing a missing one."""
    helper = api.protocols.jobs.inline
    body = harness.body
    lines.append("inline results")
    exchange.respond(job("queued", 202, job="j2"), _stated("pending"), _stated("done", result={"rows": 7}))
    step(lines, "wait", helper.start(body=body).wait)
    exchange.respond(job("queued", 202, job="j2"), _stated("done"), _stated("done", result={"rows": 8}))
    handle = helper.start(body=body)
    step(lines, "wait without a result", handle.wait)
    step(lines, "wait again", handle.wait)
    repeated = httpx2.Response(
        200,
        headers=[("content-type", "application/json"), ("X-State", "done"), ("X-State", "pending")],
        content=b'{"id":"j2"}',
    )
    exchange.respond(job("queued", 202, job="j2"), lambda _: repeated)
    step(lines, "repeated state header", helper.start(body=body).status)
    lines.append("immediate results")
    exchange.respond(job("done", 200, job="j3", result={"rows": 1}))
    handle = step(lines, "start", lambda: helper.start(body=body))
    step(lines, "wait", handle.wait)
    step(lines, "status", handle.status)
    lines.append(f"  progress {dict(handle.progress)!r}")
    exchange.respond(job("done", 200, job="j3", result=None))
    step(lines, "immediate null result", lambda: helper.start(body=body))


def _others(harness: Polling, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Fetch a result with a JSON body, poll with a querystring, and finish operations without a result."""
    lines.append("fetch with a body and poll with the previous status")
    body = harness.body
    exchange.respond(job("queued", 202, job="j4"), job("queued", job="j4"), job("done", job="j4"), report(6))
    step(lines, "wait", api.protocols.jobs.report.start(body=body).wait)
    lines.append("querystring polls")
    exports = api.protocols.exports
    created = raw_response(202, **{"Operation-Id": "e1"})
    exchange.respond(created, _export(0), _export(1))
    step(lines, "wait without a result", exports.run.start().wait)
    exchange.respond(created, _export(2))
    step(lines, "failed export", exports.run.start().wait)
    exchange.respond(created, _export(1), report(9))
    step(lines, "fetch without bindings", exports.latest.start().wait)


def _waits(harness: Polling, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Wait out declared server delays and intervals, refusing a wait over the limit or the remaining deadline."""
    helper = api.protocols.jobs.run
    body = harness.body
    lines.append("server delays")
    exchange.respond(json_response(202, {"id": "j1", "status": "queued"}, **{"Retry-After": "120"}))
    handle = helper.start(body=body)
    step(lines, "status after a long create delay", handle.status)
    lines.append(f"  progress {dict(handle.progress)!r}")
    exchange.respond(
        job("queued", 202),
        json_response(503, {"message": "busy"}, **{"Retry-After": "0"}),
        json_response(200, {"id": "j1", "status": "queued"}, **{"Retry-After": "120"}),
    )
    handle = helper.start(body=body)
    step(lines, "status after a retried poll", handle.status)
    step(lines, "status after its delay", handle.status)
    exchange.respond(json_response(202, {"id": "j1", "status": "queued"}, **{"Retry-After": "soon"}), job("done"))
    step(lines, "invalid delay", helper.start(body=body).status)
    exchange.respond(
        json_response(202, {"id": "j2", "status": "queued"}, **{"Retry-After": "120"}),
        _stated("done", result={"rows": 2}),
    )
    step(lines, "undeclared delay header", api.protocols.jobs.inline.start(body=body).status)
    lines.append("intervals and deadlines")
    for label, polls in (
        ("interval past the session", harness.polls(interval=30)),
        ("interval past the session without a wait limit", harness.polls(interval=30, max_wait=None)),
        ("interval past the wait limit", harness.polls(interval=30, max_wait=10)),
    ):
        session = harness.session(total_timeout=5)
        step(
            lines,
            label,
            lambda polls=polls, session=session: helper.start(body=body, poll_options=polls, session_options=session),
        )
    deadline = harness.session(deadline=harness.options.Deadline.after(0.5))
    step(
        lines,
        "interval past the session deadline",
        lambda: helper.start(body=body, poll_options=harness.polls(interval=1), session_options=deadline),
    )
    exchange.respond(json_response(202, {"id": "j1", "status": "queued"}, **{"Retry-After": "120"}))
    handle = helper.start(
        body=body, poll_options=harness.polls(max_wait=None), session_options=harness.session(total_timeout=60)
    )
    step(lines, "server delay past the session", handle.status)
    exchange.respond(job("queued", 202), job("done"))
    handle = helper.start(
        body=body,
        poll_options=harness.polls(interval=_PAUSE),
        options=harness.request(headers=[("X-Client", "tests")], query=[("lang", "en")]),
        session_options=harness.session(total_timeout=None),
    )
    step(lines, "session without a deadline, with other patches", handle.status)


def _limits(harness: Polling, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Stop at the poll limit and the session's send slots, before the next poll or the result fetch."""
    helper = api.protocols.jobs.run
    body = harness.body
    lines.append("session limits")
    exchange.respond(job("queued", 202), job("queued"))
    handle = helper.start(body=body, poll_options=harness.polls(max_polls=1))
    step(lines, "first poll", handle.status)
    step(lines, "poll past the limit", handle.status)
    step(lines, "wait past the limit", handle.wait)
    exchange.respond(job("queued", 202), job("queued"))
    handle = helper.start(body=body, session_options=harness.session(max_network_sends=2))
    step(lines, "poll", handle.status)
    step(lines, "poll without a send slot", handle.status)
    exchange.respond(job("queued", 202), job("done"))
    handle = helper.start(body=body, session_options=harness.session(max_network_sends=2))
    step(lines, "success", handle.status)
    step(lines, "fetch without a send slot", handle.wait)


def _failures(harness: Polling, api: Any, exchange: Exchange, lines: list[str]) -> None:
    """Keep a handle pending through transport errors and cancellation, and retry only a failed result fetch."""
    helper = api.protocols.jobs.run
    body = harness.body
    once = harness.request(retry=harness.options.RetryOptions(max_retries=0))
    lines.append("failures that settle nothing")
    exchange.respond(job("queued", 202), failing(httpx2.ConnectError), job("done"), json_response(500, {}), report(2))
    handle = helper.start(body=body, options=once)
    step(lines, "poll that fails to connect", handle.status)
    step(lines, "poll again", handle.status)
    step(lines, "failed fetch", handle.wait)
    step(lines, "fetch again", handle.wait)
    lines.append(f"  progress {dict(handle.progress)!r}")
    token = harness.options.CancelToken()
    exchange.respond(job("queued", 202))
    handle = helper.start(
        body=body, options=harness.request(cancel_token=token), poll_options=harness.polls(interval=30)
    )
    token.cancel()
    step(lines, "cancelled wait", handle.status)
    lines.append(f"  progress {dict(handle.progress)!r}")
    token = harness.options.CancelToken()
    exchange.respond(job("queued", 202))
    handle = helper.start(body=body, options=harness.request(cancel_token=token))
    token.cancel()
    step(lines, "poll refused before sending", handle.status)
    lines.append(f"  progress {dict(handle.progress)!r}")
    lines.append("server delay of a failed result fetch")
    exchange.respond(job("queued", 202), job("done"), json_response(503, {"message": "busy"}, **{"Retry-After": "30"}))
    handle = helper.start(body=body, options=once, poll_options=harness.polls(max_wait=10))
    step(lines, "fetch answered with a server delay", handle.wait)
    step(lines, "fetch before the delay", handle.wait)
    lines.append("server delay of a failed poll")
    exchange.respond(job("queued", 202), json_response(503, {"message": "busy"}, **{"Retry-After": "30"}))
    handle = helper.start(body=body, options=once, poll_options=harness.polls(max_wait=10))
    step(lines, "poll answered with a server delay", handle.status)
    step(lines, "poll before the delay", handle.status)
    lines.append(f"  progress {dict(handle.progress)!r}")


def _clocked(harness: Polling, lines: list[str]) -> None:
    """Measure poll waits, server delays, and the session on the client's clock, waiting in real time when frozen."""
    exchange = Exchange(lines)
    clock = _Clock(1000.0)
    settings = harness.client_options(clock=harness.options.Clock(monotonic=clock, time=lambda: _WALL))
    body = harness.body
    lines.append("waits and limits on the client clock")
    with exchange.client() as native, harness.package.Client(http_client=native, options=settings) as api:
        helper = api.protocols.jobs.run
        exchange.respond(json_response(202, {"id": "j1", "status": "queued"}, **{"Retry-After": "120"}))
        handle = helper.start(body=body)
        clock.value += 30
        step(lines, "server delay less the clock's move", handle.status, measured)
        exchange.respond(json_response(202, {"id": "j1", "status": "queued"}, **{"Retry-After": _WALL_DATE}))
        handle = helper.start(body=body)
        step(lines, "date delay on the client's wall clock", handle.status, measured)
        clock.value = 1000.0
        delayed = json_response(200, {"id": "j1", "status": "queued"}, **{"Retry-After": "30"})
        exchange.respond(json_response(202, {"id": "j1", "status": "queued"}, **{"Retry-After": "30"}), delayed)
        handle = helper.start(body=body, session_options=harness.session(total_timeout=60))
        clock.value += 31
        step(lines, "poll once the clock passes the delay", handle.status)
        step(lines, "delay past the session", handle.status, measured)
        exchange.respond(job("queued", 202), job("done"))
        handle = helper.start(body=body, poll_options=harness.polls(interval=_PAUSE))
        step(lines, "interval on a frozen clock", handle.status)
    run(lambda: _async_clocked(harness, lines))


async def _async_clocked(harness: Polling, lines: list[str]) -> None:
    """Measure asyncio poll waits and the session on the client's clock, waiting in real time when frozen."""
    exchange = Exchange(lines)
    clock = _Clock(1000.0)
    settings = harness.client_options(clock=harness.options.Clock(monotonic=clock))
    body = harness.body
    async with (
        exchange.async_client() as native,
        harness.package.AsyncClient(http_client=native, options=settings) as api,
    ):
        helper = api.protocols.jobs.run
        delayed = json_response(200, {"id": "j1", "status": "queued"}, **{"Retry-After": "30"})
        exchange.respond(json_response(202, {"id": "j1", "status": "queued"}, **{"Retry-After": "30"}), delayed)
        handle = await helper.start(body=body, session_options=harness.session(total_timeout=60))
        clock.value += 31
        await astep(lines, "async poll once the clock passes the delay", handle.status)
        await astep(lines, "async delay past the session", handle.status, measured)
        exchange.respond(job("queued", 202), job("done"))
        handle = await helper.start(body=body, poll_options=harness.polls(interval=_PAUSE))
        await astep(lines, "async interval on a frozen clock", handle.status)


async def _async_polling(harness: Polling, lines: list[str]) -> None:
    """Create, poll, and wait with asyncio, refusing a concurrent step and keeping a cancelled wait pending."""
    package = harness.package
    exchange = Exchange(lines)
    async with (
        exchange.async_client() as native,
        package.AsyncClient(http_client=native, options=harness.client_options()) as api,
    ):
        helper = api.protocols.jobs.run
        body = harness.body
        lines.append("async lifecycle")
        exchange.respond(job("queued", 202), job("running"), job("done"), report(3))
        handle = await helper.start(body=body)
        await astep(lines, "status", handle.status)
        await astep(lines, "wait", handle.wait)
        await astep(lines, "status after success", handle.status)
        exchange.respond(job("queued", 202), job("done"), report(5))
        paused = await helper.start(body=body, poll_options=harness.polls(interval=_PAUSE))
        await astep(lines, "wait through a short interval", paused.wait)
        exchange.respond(job("done", 200, job="j3", result={"rows": 1}))
        async with await api.protocols.jobs.inline.start(body=body) as immediate:
            await astep(lines, "immediate wait", immediate.wait)
        await astep(lines, "wait after the block", immediate.wait)
        unbudgeted = harness.session(max_network_sends=0)
        await arecord(
            lines, "start without a session send slot", lambda: helper.start(body=body, session_options=unbudgeted)
        )
        lines.append("async concurrent steps")
        exchange.respond(job("queued", 202))
        handle = await helper.start(body=body, poll_options=harness.polls(interval=30))
        waiting = asyncio.create_task(handle.wait())
        await asyncio.sleep(0)
        await astep(lines, "status while waiting", handle.status)
        await astep(lines, "close while waiting", handle.aclose)
        waiting.cancel()
        try:
            await waiting
        except asyncio.CancelledError:
            lines.append("  wait cancelled")
        lines.append(f"  progress {dict(handle.progress)!r}")
        await astep(lines, "close", handle.aclose)
        await astep(lines, "status after close", handle.status)
        lines.append("async block error while another step runs")
        exchange.respond(job("queued", 202))
        handle = await helper.start(body=body, poll_options=harness.polls(interval=30))
        waiting = asyncio.create_task(handle.wait())
        await asyncio.sleep(0)
        try:
            async with handle:
                raise LookupError("block")
        except LookupError as error:
            lines.append(f"  block error kept: {error!r}")
        waiting.cancel()
        try:
            await waiting
        except asyncio.CancelledError:
            lines.append("  wait cancelled")
        lines.append(f"  progress {dict(handle.progress)!r}")
        await astep(lines, "close the handle the block left open", handle.aclose)
        await astep(lines, "status after its close", handle.status)
        lines.append("async server delay of a failed result fetch")
        once = harness.request(retry=harness.options.RetryOptions(max_retries=0))
        exchange.respond(
            job("queued", 202), job("done"), json_response(503, {"message": "busy"}, **{"Retry-After": "30"})
        )
        handle = await helper.start(body=body, options=once, poll_options=harness.polls(max_wait=10))
        await astep(lines, "fetch answered with a server delay", handle.wait)
        await astep(lines, "fetch before the delay", handle.wait)
        lines.append("async server delay of a failed poll")
        exchange.respond(job("queued", 202), json_response(503, {"message": "busy"}, **{"Retry-After": "30"}))
        handle = await helper.start(body=body, options=once, poll_options=harness.polls(max_wait=10))
        await astep(lines, "poll answered with a server delay", handle.status)
        await astep(lines, "poll before the delay", handle.status)
    await _async_closing(harness, lines)


async def _async_closing(harness: Polling, lines: list[str]) -> None:
    """Stop a wait when the client closes, naming the helper's session, and refuse later polls of its handles."""
    exchange = Exchange(lines)
    lines.append("async client closing during a wait")
    async with exchange.async_client() as native:
        api = harness.package.AsyncClient(http_client=native, options=harness.client_options())
        exchange.respond(job("queued", 202))
        handle = await api.protocols.jobs.run.start(body=harness.body, poll_options=harness.polls(interval=30))
        waiting = asyncio.create_task(handle.status())
        await asyncio.sleep(0)
        await api.aclose()
        await astep(lines, "wait while the client closes", lambda: waiting)
        await astep(lines, "status after the client closed", handle.status)
        lines.append(f"  progress {dict(handle.progress)!r}")
