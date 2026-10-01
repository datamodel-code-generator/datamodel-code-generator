"""Send items through generated batch helpers: chunks, limits, ordering, partial failures, and unknown deliveries."""

from __future__ import annotations

import asyncio
import importlib
import json
import threading
from typing import TYPE_CHECKING, Any, Final, get_origin, get_type_hints

import httpx2

from tests.data.python.client_pagination import Harness
from tests.data.python.client_runtime import Exchange, describe, failing, json_response, record, run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Iterator
    from types import ModuleType

_SECRET: Final = "private-batch-marker"


def _field(value: Any, name: str) -> Any:
    """Read a member of a decoded model or TypedDict."""
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _user(item: dict[str, Any]) -> dict[str, Any]:
    """Return the result of one user: an error for every fifth ID, the created user otherwise."""
    if int(item["id"][1:]) % 5 == 0:
        return {"id": item["id"], "error": {"code": "taken", "message": item["name"][:8]}}
    return {"id": item["id"], "user": {**item, "created": True}}


def _tag(item: dict[str, Any]) -> dict[str, Any]:
    """Return the result of one tag: an error for a tag without a color, the tag otherwise."""
    return {"tag": item} if "color" in item else {"error": {"code": "colorless"}}


def answer(request: httpx2.Request) -> httpx2.Response:
    """Answer a batch request from its body: users in reverse order by ID, tags in order."""
    body = json.loads(request.content)
    if isinstance(body, dict):
        return json_response(200, {"results": [_user(item) for item in reversed(body["users"])]})(request)
    return json_response(200, {"items": [_tag(item) for item in body]})(request)


def _summary(request: httpx2.Request) -> str:
    """Summarize a batch request by its first item, method, URL, item count, last item, and body bytes."""
    body = json.loads(request.content)
    items = body["users"] if isinstance(body, dict) else body
    names = [item.get("id") or item.get("label") for item in items]
    query = f"?{request.url.query.decode()}" if request.url.query else ""
    return (
        f"{names[0] if names else '-'} {request.method} {request.url.path}{query} items={len(items)} "
        f"last={names[-1] if names else '-'} bytes={len(request.content)}"
    )


class _Server(Exchange):
    """Answer batch requests with queued responders first and from their bodies after, summarizing each request.

    Requests sent in parallel arrive in any order, so their summaries are reported sorted by their first item.
    """

    def __init__(self, lines: list[str]) -> None:
        """Start with no request seen."""
        super().__init__(lines)
        self.summaries: list[str] = []
        self.guard = threading.Lock()

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        """Summarize a request and answer it with the next queued responder, or from its body."""
        request.read()
        with self.guard:
            self.summaries.append(_summary(request))
            responder = self.responders.pop(0) if self.responders else answer
        return responder(request)

    def report(self, label: str) -> None:
        """Report the requests seen since the last report, sorted."""
        with self.guard:
            summaries, self.summaries = sorted(self.summaries), []
        self.lines.append(f"  {label}: {len(summaries)} requests")
        self.lines.extend(f"    > {item}" for item in summaries)


class _Batches(Harness):
    """A generated batch package's public modules, its models, and the options its helpers take."""

    def __init__(self, package: ModuleType) -> None:
        """Import the modules and the models."""
        super().__init__(package)
        self.models = importlib.import_module(f"{package.__name__}_models")
        self.records = importlib.import_module(f"{package.__name__}.protocols.batches")

    def users(self, count: int, start: int = 0, name: str = "user") -> list[Any]:
        """Return new users with consecutive IDs."""
        return [self.models.NewUser(id=f"u{index:04d}", name=f"{name} {index}") for index in range(start, start + count)]

    def tags(self, *colors: str | None) -> list[Any]:
        """Return tags, each with its color or without one."""
        return [
            self.models.Tag(label=f"t{index:03d}", **({} if color is None else {"color": color}))
            for index, color in enumerate(colors)
        ]

    def batch(self, **settings: Any) -> Any:
        """Return batch options."""
        return self.protocols.BatchOptions(**settings)

    def client_options(self, **settings: Any) -> Any:
        """Return client options that retry at once and send each tags request alone by default."""
        protocols = self.protocols
        defaults = {"tags.put": protocols.ProtocolDefaults(options=protocols.BatchOptions(parallelism=1))}
        return super().client_options(protocols=self.options.ProtocolClientOptions(defaults=defaults), **settings)


def _record(item: Any) -> str:
    """Describe one record by its outcome, index, ID, status, and payload."""
    status = None if item.response is None else item.response.status_code
    match item.outcome:
        case "success":
            payload = f"value={_field(item.value, 'id') or _field(item.value, 'label')}"
        case "error":
            payload = f"error={_field(item.error, 'code')}"
        case _:
            payload = "no payload"
    return f"{item.outcome} #{item.index} id={item.item_id!r} status={status} {payload}"


def _summarized(lines: list[str], label: str, records: list[Any], failure: BaseException | None) -> None:
    """Report records by outcome and order, the first and last ones, and the failure that ended them."""
    outcomes = {name: sum(item.outcome == name for item in records) for name in ("success", "error", "delivery_unknown")}
    ordered = [item.index for item in records] == list(range(len(records)))
    lines.append(f"  {label}: {len(records)} results in order {ordered} {outcomes}")
    lines.extend(f"    {_record(item)}" for item in records[:2] + records[2:][-2:])
    if failure is not None:
        lines.append(f"    ! {describe(failure)}")


def continued(lines: list[str], label: str, iterator: Iterator[Any]) -> None:
    """Consume a batch iterator through its failures, reporting each failure after the records before it."""
    records: list[Any] = []
    failures: list[str] = []
    while True:
        try:
            records.append(next(iterator))
        except StopIteration:
            break
        except Exception as error:  # noqa: BLE001
            failures.append(f"after {len(records)}: {describe(error)}")
    _summarized(lines, label, records, None)
    lines.extend(f"    ! {item}" for item in failures)


async def acontinued(lines: list[str], label: str, iterator: Any) -> None:
    """Consume an asyncio batch iterator through its failures, reporting each one after the records before it."""
    records: list[Any] = []
    failures: list[str] = []
    while True:
        try:
            records.append(await anext(iterator))
        except StopAsyncIteration:
            break
        except Exception as error:  # noqa: BLE001
            failures.append(f"after {len(records)}: {describe(error)}")
    _summarized(lines, label, records, None)
    lines.extend(f"    ! {item}" for item in failures)


def drained(lines: list[str], label: str, iterator: Iterator[Any]) -> list[Any]:
    """Consume a batch iterator, reporting its records and the failure that ended it."""
    records: list[Any] = []
    failure: BaseException | None = None
    try:
        records.extend(iterator)
    except Exception as error:  # noqa: BLE001
        failure = error
    _summarized(lines, label, records, failure)
    return records


async def adrained(lines: list[str], label: str, iterator: Any) -> list[Any]:
    """Consume an asyncio batch iterator, reporting its records and the failure that ended it."""
    records: list[Any] = []
    failure: BaseException | None = None
    try:
        async for item in iterator:
            records.append(item)
    except Exception as error:  # noqa: BLE001
        failure = error
    _summarized(lines, label, records, failure)
    return records


class _Counted:
    """A source that counts the items read, reporting how far reading ran ahead of the returned results."""

    def __init__(self, items: Iterable[Any]) -> None:
        """Keep the items; nothing is read yet."""
        self.items = list(items)
        self.read = 0

    def __iter__(self) -> Iterator[Any]:
        """Yield each item, counting it."""
        for item in self.items:
            self.read += 1
            yield item

    def ahead(self, iterator: Iterator[Any]) -> list[int]:
        """Consume the results, returning how many items were read but not returned at each result."""
        return [self.read - returned for returned, _ in enumerate(iterator, 1)]


def batches(package: ModuleType, lines: list[str]) -> None:
    """Send users and tags in bounded requests through the synchronous and asyncio clients."""
    harness = _Batches(package)
    server = _Server(lines)
    with server.client() as native, package.Client(http_client=native, options=harness.client_options()) as api:
        _chunks(harness, api, server, lines)
        _bounds(harness, api, server, lines)
        _limits(harness, api, server, lines)
        _mismatches(harness, api, server, lines)
        _failures(harness, api, server, lines)
        _inputs(harness, api, server, lines)
        _states(harness, api, server, lines)
        _siblings(harness, api, server, lines)
        _records(harness, api, server, lines)
    run(lambda: _async_batches(harness, lines))
    _errors(harness, lines)


def _chunks(harness: _Batches, api: Any, server: _Server, lines: list[str]) -> None:
    """Split items by the server's count and byte limits and by repeated IDs, answering out of order."""
    users = api.protocols.users.create
    lines.append("chunks")
    iterator = users.iterate(harness.users(1000))
    lines.append(f"  created {iterator!r} {dict(iterator.progress)}")
    server.report("before iterating")
    drained(lines, "1000 users", iterator)
    lines.append(f"  progress {dict(iterator.progress)}")
    server.report("1000 users")
    dry_run = harness.argument("users", "CreateUsers", "query", "dryRun", True)
    drained(lines, "long names", users.iterate(harness.users(30, name="x" * 300), dry_run=dry_run))
    server.report("long names")
    repeated = [*harness.users(2), *harness.users(1)]
    drained(lines, "repeated ID", users.iterate(repeated))
    server.report("repeated ID")
    drained(lines, "tags", api.protocols.tags.put.iterate(harness.tags("red", None, "blue", "green", None)))
    server.report("tags")
    drained(lines, "no items", users.iterate([]))
    server.report("no items")


def _bounds(harness: _Batches, api: Any, server: _Server, lines: list[str]) -> None:
    """Read ahead only into free request slots and the prepared-input buffer."""
    users = api.protocols.users.create
    lines.append("read-ahead bounds")
    for label, settings in (
        ("batch 5 parallelism 2", {"batch_size": 5, "parallelism": 2}),
        ("buffer of one request", {"batch_size": 10, "parallelism": 3, "max_buffer_bytes": 400}),
    ):
        source = _Counted(harness.users(23))
        ahead = source.ahead(users.iterate(source, batch_options=harness.batch(**settings)))
        lines.append(f"  {label}: results {len(ahead)} read ahead {ahead}")
        server.report(label)


def _limits(harness: _Batches, api: Any, server: _Server, lines: list[str]) -> None:
    """Refuse items over the byte limit and the item limit, and requests over the session's sends."""
    users = api.protocols.users.create
    lines.append("limits")
    large = [*harness.users(2), *harness.users(1, start=2, name="y" * 5000), *harness.users(1, start=3)]
    drained(lines, "too large item", users.iterate(large))
    server.report("too large item")
    drained(lines, "item limit", users.iterate(harness.users(7), batch_options=harness.batch(max_items=5)))
    drained(lines, "zero item limit", users.iterate([], batch_options=harness.batch(max_items=0)))
    drained(lines, "no item limit", users.iterate(harness.users(3), batch_options=harness.batch(max_items=None)))
    server.report("item limits")
    drained(
        lines,
        "session sends",
        users.iterate(
            harness.users(20),
            batch_options=harness.batch(batch_size=5, parallelism=1),
            session_options=harness.options.SessionOptions(max_network_sends=2),
        ),
    )
    drained(
        lines, "call sends", users.iterate(harness.users(3), options=harness.options.RequestOptions(max_network_sends=0))
    )
    drained(
        lines,
        "call sends in parallel",
        users.iterate(
            harness.users(10),
            batch_options=harness.batch(batch_size=2, parallelism=2),
            options=harness.options.RequestOptions(max_network_sends=0),
        ),
    )
    server.report("send limits")


def _mismatches(harness: _Batches, api: Any, server: _Server, lines: list[str]) -> None:
    """Refuse results that cannot be matched with their items, or that carry no single outcome."""
    users = api.protocols.users.create
    lines.append("mismatched results")
    single = harness.batch(parallelism=1)
    good = {"id": "u0001", "user": {"id": "u0001", "name": "a"}}
    for label, results in (
        ("duplicate ID", [good, good, {"id": "u0002", "error": {"code": "x"}}]),
        ("missing result", [good, {"id": "u0002", "error": {"code": "x"}}]),
        ("unknown ID", [good, {"id": "u0009", "error": {"code": "x"}}, {"id": "u0000", "error": {"code": "x"}}]),
        ("result without ID", [good, {"error": {"code": "x"}}, {"id": "u0000", "error": {"code": "x"}}]),
        (
            "both members",
            [good, {"id": "u0002", "user": good["user"], "error": {"code": "x"}}, {"id": "u0000", "error": {"code": "x"}}],
        ),
        ("neither member", [good, {"id": "u0002", "user": None}, {"id": "u0000", "error": {"code": "x"}}]),
    ):
        server.respond(json_response(200, {"results": results}))
        drained(lines, label, users.iterate(harness.users(3), batch_options=single))
    server.respond(json_response(200, {"results": None}))
    drained(lines, "null results", users.iterate(harness.users(2), batch_options=single))
    server.respond(json_response(200, {}))
    drained(lines, "missing tag results", api.protocols.tags.put.iterate(harness.tags("red")))
    server.respond(json_response(200, {"items": [{"tag": {"label": "t000"}}]}))
    drained(lines, "fewer tag results", api.protocols.tags.put.iterate(harness.tags("red", "blue")))
    server.respond(json_response(200, {"items": [{"color": "red", "tag": {"label": "t000", "color": "red"}}]}))
    drained(lines, "item without ID", api.protocols.tags.colors.iterate(harness.tags("red", None)))
    server.report("mismatched results")


def _failures(harness: _Batches, api: Any, server: _Server, lines: list[str]) -> None:
    """Raise a failed request in order, retry a whole request only when safe, and keep unknown deliveries."""
    users, tags = api.protocols.users.create, api.protocols.tags.put
    lines.append("failures")
    single = harness.batch(batch_size=2, parallelism=1)
    server.respond(answer, json_response(503, {"message": "busy"}))
    drained(lines, "unsafe request unavailable", users.iterate(harness.users(5), batch_options=single))
    server.report("unsafe request unavailable")
    server.respond(json_response(503, {"message": "busy"}), answer)
    drained(lines, "idempotent request retried", tags.iterate(harness.tags("red", "blue")))
    server.report("idempotent request retried")
    server.respond(answer, failing(httpx2.ReadError), answer)
    drained(lines, "unknown delivery", users.iterate(harness.users(5), batch_options=single))
    server.report("unknown delivery")
    server.respond(answer, failing(httpx2.ReadError))
    drained(
        lines,
        "unknown delivery raised",
        users.iterate(harness.users(5), batch_options=harness.batch(batch_size=2, parallelism=1, raise_on_error=True)),
    )
    server.report("unknown delivery raised")


def _head_fails(request: httpx2.Request) -> httpx2.Response:
    """Refuse the request holding the first user with a 503, and answer any other from its body."""
    if json.loads(request.content)["users"][0]["id"] == "u0000":
        return json_response(503, {"message": "busy"})(request)
    return answer(request)


class _Interrupt(BaseException):
    """An interruption a caller's source raises, as KeyboardInterrupt would."""


def _interrupted(items: list[Any]) -> Iterator[Any]:
    """Yield the items, then interrupt the iteration reading them."""
    yield from items
    raise _Interrupt


def _siblings(harness: _Batches, api: Any, server: _Server, lines: list[str]) -> None:
    """Return the records of requests in flight after a failure, and report deliveries a stop left unknown."""
    users = api.protocols.users.create
    lines.append("requests in flight after a failure")
    server.respond(_head_fails, _head_fails)
    pair = harness.batch(batch_size=2, parallelism=2)
    continued(lines, "head refused", users.iterate(harness.users(4), batch_options=pair))
    server.report("head refused")
    token = harness.options.CancelToken()

    def cancelling(request: httpx2.Request) -> httpx2.Response:
        response = answer(request)
        token.cancel()
        return response

    server.respond(answer, cancelling)
    single = harness.batch(batch_size=2, parallelism=1)
    options = harness.options.RequestOptions(cancel_token=token)
    continued(lines, "cancelled while answered", users.iterate(harness.users(6), batch_options=single, options=options))
    server.report("cancelled while answered")
    received, release = threading.Event(), threading.Event()

    def stalled(request: httpx2.Request) -> httpx2.Response:
        received.set()
        release.wait(30)
        return answer(request)

    server.respond(stalled)
    session = harness.options.SessionOptions(total_timeout=3)
    continued(lines, "deadline while answered", users.iterate(harness.users(2), session_options=session))
    release.set()
    lines.append(f"  request received {received.is_set()}")
    server.report("deadline while answered")
    _interruption(harness, lines)


def _interruption(harness: _Batches, lines: list[str]) -> None:
    """Stop at an interruption while reading, with a request in flight that may or may not be sent.

    Whether the cancelled request reaches the server depends on its thread, so it is sent to a server of its own.
    """
    with _Server([]).client() as native, harness.package.Client(http_client=native) as api:
        iterator = api.protocols.users.create.iterate(
            _interrupted(harness.users(3)), batch_options=harness.batch(batch_size=2, parallelism=2)
        )
        try:
            next(iterator)
        except _Interrupt:
            lines.append("  interrupted while reading")
        record(lines, "after the interruption", lambda: _record(next(iterator)))


def _failing_source(items: list[Any]) -> Iterator[Any]:
    """Yield the items, then fail as a caller's source may."""
    yield from items
    msg = "source failed"
    raise ValueError(msg)


def _inputs(harness: _Batches, api: Any, server: _Server, lines: list[str]) -> None:
    """Send the items read before an input failure, then raise it; refuse items and options that cannot be sent."""
    users = api.protocols.users.create
    lines.append("inputs")
    drained(lines, "source failure", users.iterate(_failing_source(harness.users(3))))
    drained(lines, "item of another type", users.iterate([*harness.users(1), 5]))
    server.report("inputs")
    for label, items in (("integer", 5), ("text", "u0001"), ("mapping", {"id": "u0001"})):
        record(lines, f"items {label}", lambda items=items: users.iterate(items))
    for label, settings in (
        ("batch options of another type", {"batch_options": harness.options.SessionOptions()}),
        ("request options of another type", {"options": harness.batch()}),
        ("session options of another type", {"session_options": harness.batch()}),
        ("fixed idempotency key", {"options": harness.options.RequestOptions(idempotency_key=harness.options.IdempotencyKey.new())}),
    ):
        record(lines, label, lambda settings=settings: users.iterate(harness.users(1), **settings))
    for label, settings in (
        ("zero batch size", {"batch_size": 0}),
        ("boolean parallelism", {"parallelism": True}),
        ("negative item limit", {"max_items": -1}),
        ("no item bytes", {"max_item_bytes": None}),
        ("zero buffer", {"max_buffer_bytes": 0}),
        ("raise on error text", {"raise_on_error": "yes"}),
    ):
        record(lines, label, lambda settings=settings: harness.batch(**settings))


def _states(harness: _Batches, api: Any, server: _Server, lines: list[str]) -> None:
    """Stop at close, refuse a nested step, and close at the end of a block."""
    users = api.protocols.users.create
    lines.append("states")
    iterator = users.iterate(harness.users(100), batch_options=harness.batch(batch_size=10, parallelism=1))
    record(lines, "first", lambda: _record(next(iterator)))
    iterator.close()
    iterator.close()
    record(lines, "after close", lambda: next(iterator))
    server.report("closed after one result")
    unused = users.iterate(harness.users(3))
    unused.close()
    server.report("closed before iterating")
    with users.iterate(harness.users(4)) as managed:
        record(lines, "in block", lambda: _record(next(managed)))
    record(lines, "after block", lambda: next(managed))
    server.report("block")
    holder: list[Any] = []

    def nested() -> Iterator[Any]:
        yield from harness.users(2)
        next(holder[0])

    holder.append(users.iterate(nested()))
    drained(lines, "nested step", holder[0])
    holder[0] = users.iterate(_closing(holder, harness.users(2)))
    drained(lines, "nested close", holder[0])
    server.report("nested")


def _closing(holder: list[Any], items: list[Any]) -> Iterator[Any]:
    """Yield the items, then close the iterator reading them."""
    yield from items
    holder[0].close()


def _records(harness: _Batches, api: Any, server: _Server, lines: list[str]) -> None:
    """Keep records frozen and their values out of their representation."""
    lines.append("records")
    success, failure = list(api.protocols.users.create.iterate(harness.users(2, start=4)))
    records = harness.records
    lines.extend((
        f"  success {type(success).__name__} outcome={success.outcome} id={success.item_id} "
        f"token={success.retry_token} value={_field(success.value, 'name')}",
        f"  failure {type(failure).__name__} outcome={failure.outcome} error={_field(failure.error, 'code')}",
        f"  repr hides values {'user 5' not in repr(success)} {'taken' not in repr(failure)} {success!r}"
        .replace(repr(success.response), "<response>"),
        f"  exported {sorted(name for name in records.__all__ if name.startswith('Users'))}",
    ))
    record(lines, "frozen", lambda: setattr(success, "index", 9))
    record(lines, "outcome fixed", lambda: records.UsersCreateSuccess(index=0, item_id="a", response=None, value=1, outcome="error"))
    unknown = records.TagsPutDeliveryUnknown(index=1, item_id=None, response=None)
    lines.append(f"  unknown {unknown!r}")
    server.report("records")


async def _async_batches(harness: _Batches, lines: list[str]) -> None:
    """Send items with the asyncio client, in the same order, from synchronous and asynchronous sources."""
    lines.append("asyncio")
    server = _Server(lines)
    async with server.async_client() as native, harness.package.AsyncClient(
        http_client=native, options=harness.client_options()
    ) as api:
        users = api.protocols.users.create
        await adrained(lines, "1000 users", users.iterate(harness.users(1000)))
        server.report("1000 users")
        await adrained(lines, "asynchronous source", users.iterate(_source(harness.users(40))))
        await adrained(lines, "asynchronous source failure", users.iterate(_source(harness.users(3), fail=True)))
        await adrained(lines, "tags", api.protocols.tags.put.iterate(harness.tags("red", None, "blue", "green")))
        server.report("sources")
        server.respond(answer, failing(httpx2.ReadError))
        await adrained(lines, "unknown delivery", users.iterate(harness.users(4), batch_options=harness.batch(batch_size=2, parallelism=1)))
        server.respond(json_response(503, {"message": "busy"}))
        await adrained(lines, "unavailable", users.iterate(harness.users(4), batch_options=harness.batch(batch_size=2, parallelism=1)))
        await adrained(
            lines,
            "call sends in parallel",
            users.iterate(
                harness.users(10),
                batch_options=harness.batch(batch_size=2, parallelism=2),
                options=harness.options.RequestOptions(max_network_sends=0),
            ),
        )
        await adrained(lines, "item limit", users.iterate(harness.users(3), batch_options=harness.batch(max_items=2)))
        server.respond(_head_fails, _head_fails)
        pair = harness.batch(batch_size=2, parallelism=2)
        await acontinued(lines, "head refused", users.iterate(harness.users(4), batch_options=pair))
        server.report("failures")
        await _async_states(harness, api, server, lines)


async def _source(items: list[Any], *, fail: bool = False) -> AsyncIterator[Any]:
    """Yield the items asynchronously, then fail when asked."""
    for item in items:
        await asyncio.sleep(0)
        yield item
    if fail:
        msg = "source failed"
        raise ValueError(msg)


async def _async_states(harness: _Batches, api: Any, server: _Server, lines: list[str]) -> None:
    """Stop at close and at the end of a block, then refuse a concurrent step and stop at cancellation."""
    users = api.protocols.users.create
    iterator = users.iterate(harness.users(30), batch_options=harness.batch(batch_size=5, parallelism=1))
    lines.append(f"  first {_record(await anext(iterator))}")
    await iterator.aclose()
    await iterator.aclose()
    try:
        await anext(iterator)
    except StopAsyncIteration:
        lines.append("  after close stops")
    async with users.iterate(harness.users(3)) as managed:
        lines.append(f"  in block {_record(await anext(managed))}")
    try:
        await anext(managed)
    except StopAsyncIteration:
        lines.append("  after block stops")
    server.report("states")
    await _cancelled(harness, lines)
    await _cancelled_reading(harness, lines, raise_on_error=False)
    await _cancelled_reading(harness, lines, raise_on_error=True)


class _Gated:
    """An asynchronous source that waits before one item until it is released."""

    def __init__(self, items: list[Any], gate: int) -> None:
        """Keep the items and the position of the gate."""
        self.items = items
        self.position = 0
        self.gate = gate
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()

    def __aiter__(self) -> _Gated:
        """Return this source."""
        return self

    async def __anext__(self) -> Any:
        """Return the next item, waiting at the gate until released."""
        if self.position >= len(self.items):
            raise StopAsyncIteration
        if self.position == self.gate:
            self.waiting.set()
            await self.release.wait()
        self.position += 1
        return self.items[self.position - 1]


async def _cancelled_reading(harness: _Batches, lines: list[str], *, raise_on_error: bool) -> None:
    """Cancel a step while it reads the source with a request in flight: nothing more is read or sent.

    The request's records become unknown deliveries, or its unknown delivery is raised, and a ProtocolStateError of
    state `cancelled` then ends the iteration, so the item read but not sent is never dropped silently.
    """
    received, release = threading.Event(), threading.Event()

    def stalled(request: httpx2.Request) -> httpx2.Response:
        received.set()
        release.wait(30)
        return answer(request)

    server = _Server(lines)
    server.respond(stalled)
    async with server.async_client() as native, harness.package.AsyncClient(http_client=native) as api:
        source = _Gated(harness.users(6), gate=3)
        settings = harness.batch(batch_size=2, parallelism=2, raise_on_error=raise_on_error)
        iterator = api.protocols.users.create.iterate(source, batch_options=settings)
        step = asyncio.ensure_future(anext(iterator))
        await source.waiting.wait()
        await asyncio.to_thread(received.wait, 30)
        step.cancel()
        cancelled = await asyncio.gather(step, return_exceptions=True)
        release.set()
        source.release.set()
        lines.append(f"  cancelled while reading {[type(item).__name__ for item in cancelled]}")
        await acontinued(lines, f"after the cancellation, raising {raise_on_error}", iterator)
        server.report("cancelled while reading")


async def _cancelled(harness: _Batches, lines: list[str]) -> None:
    """Refuse a step while another waits, and stop when the waiting step is cancelled.

    The cancelled requests may or may not reach the server, so they are sent to a server of their own.
    """
    async with _Server([]).async_client() as native, harness.package.AsyncClient(http_client=native) as api:
        iterator = api.protocols.users.create.iterate(
            harness.users(30), batch_options=harness.batch(batch_size=5, parallelism=2)
        )
        first = asyncio.ensure_future(anext(iterator))
        await asyncio.sleep(0)
        try:
            await anext(iterator)
        except Exception as error:  # noqa: BLE001
            lines.append(f"  concurrent step ! {describe(error)}")
        first.cancel()
        cancelled = await asyncio.gather(first, return_exceptions=True)
        lines.append(f"  cancelled step {[type(item).__name__ for item in cancelled]}")
        await iterator.aclose()
        try:
            await anext(iterator)
        except StopAsyncIteration:
            lines.append("  after cancellation stops")


def _errors(harness: _Batches, lines: list[str]) -> None:
    """Report the batch errors: their bases, retained fields, safe messages, and rejected fields."""
    errors, protocols, responses = harness.errors, harness.protocols, harness.package.responses
    lines.append("errors")
    secret = _SECRET
    operation = protocols.OperationRef(pointer=f"/paths/{secret}/post")
    info = responses.ResponseInfo(status_code=200, headers=responses.HeadersView(()), call_id="c", elapsed=0.1, content_type=None)
    sent = errors.DeliveryState.MAYBE_SENT
    for name, fields in (
        ("BatchProtocolError", {"indices": (1, 2), "location": protocols.BodySelector(pointer=f"/{secret}")}),
        ("BatchItemTooLargeError", {"index": 3, "limit": 10, "observed": 11}),
        ("BatchDeliveryUnknownError", {"partial_results": (secret,), "batch_indices": (4,), "delivery_state": sent}),
        ("DeliveryUnknownError", {"delivery_state": sent, "message_id": secret}),
    ):
        error_type = getattr(errors, name)
        error = error_type(**fields, helper_id=secret, operation=operation, info=info)
        text = f"{error} {error!r}"
        lines.extend((
            f"  {name}: base={error_type.__bases__[0].__name__} reason={error.reason_code} "
            f"chain={[item.__name__ for item in error_type.__mro__ if issubclass(item, errors.SDKError)]}",
            f"    retained={all(getattr(error, key) == value for key, value in fields.items())} "
            f"secret={secret in text} str={error}",
        ))
    unknown = errors.BatchDeliveryUnknownError(partial_results=(1,), batch_indices=(), delivery_state=sent)
    parameter = errors.BatchDeliveryUnknownError.__parameters__[0]
    hint = get_type_hints(errors.BatchDeliveryUnknownError.partial_results.fget)["return"]
    lines.append(
        f"  partial results {unknown.partial_results} message={unknown.message_id} kind={errors.BatchItemTooLargeError(index=0, limit=0, observed=1).kind} "
        f"covariance={parameter.__covariant__} subscripted={get_origin(errors.BatchDeliveryUnknownError[int]) is errors.BatchDeliveryUnknownError} "
        f"hint={get_origin(hint) is tuple}"
    )
    not_sent = errors.DeliveryState.NOT_SENT
    for label, create in (
        ("indices list", lambda: errors.BatchProtocolError(indices=[1])),
        ("indices negative", lambda: errors.BatchProtocolError(indices=(-1,))),
        ("fixed condition", lambda: errors.BatchProtocolError(indices=(), condition="value")),
        ("index negative", lambda: errors.BatchItemTooLargeError(index=-1, limit=0, observed=1)),
        ("fixed kind", lambda: errors.BatchItemTooLargeError(index=0, limit=0, observed=1, kind="page")),
        ("partial results list", lambda: errors.BatchDeliveryUnknownError(
            partial_results=[], batch_indices=(), delivery_state=sent
        )),
        ("batch indices bool", lambda: errors.BatchDeliveryUnknownError(
            partial_results=(), batch_indices=(True,), delivery_state=sent
        )),
        ("not sent", lambda: errors.DeliveryUnknownError(delivery_state=not_sent)),
        ("message id type", lambda: errors.DeliveryUnknownError(delivery_state=sent, message_id=1)),
        ("resume state type", lambda: errors.DeliveryUnknownError(delivery_state=sent, resume_state={})),
        ("read-only partial results", lambda: setattr(unknown, "partial_results", ())),
    ):
        record(lines, label, create)


def batch_backends(package: ModuleType, lines: list[str]) -> None:
    """Send users and tags, and read their typed results, through every model backend."""
    harness = _Batches(package)
    server = _Server(lines)
    with server.client() as native, package.Client(http_client=native, options=harness.client_options()) as api:
        drained(lines, "users", api.protocols.users.create.iterate(harness.users(40)))
        drained(lines, "tags", api.protocols.tags.put.iterate(harness.tags("red", None, "blue", "green")))
        server.report("requests")



def batch_arguments(package: ModuleType, lines: list[str]) -> None:
    """Check the shared arguments with Pydantic once, before any request, when the package validates arguments."""
    harness = _Batches(package)
    server = _Server(lines)
    with server.client() as native, package.Client(http_client=native, options=harness.client_options()) as api:
        users = api.protocols.users.create
        iterator = users.iterate(harness.users(3), dry_run=[1])
        for label in ("invalid shared argument", "again"):
            try:
                next(iterator)
            except Exception as error:  # noqa: BLE001
                cause = type(error.__cause__ or getattr(error, "cause", None)).__name__
                lines.append(f"  {label} ! {type(error).__name__} location={error.location} cause={cause}")
        dry_run = harness.argument("users", "CreateUsers", "query", "dryRun", True)
        drained(lines, "valid shared argument", users.iterate(harness.users(3), dry_run=dry_run))
        server.report("arguments")
