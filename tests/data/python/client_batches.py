"""Send items through generated batch helpers: chunks, limits, ordering, partial failures, and unknown deliveries."""

from __future__ import annotations

import asyncio
import gzip
import importlib
import json
import threading
from contextlib import suppress
from functools import partial
from itertools import product
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, get_origin, get_type_hints

import httpx2

from tests.data.python.client_pagination import Harness
from tests.data.python.client_runtime import Exchange, describe, failing, json_response, record, run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterable, Iterator
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
        return [
            self.models.NewUser(id=f"u{index:04d}", name=f"{name} {index}") for index in range(start, start + count)
        ]

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
    outcomes = {
        name: sum(item.outcome == name for item in records) for name in ("success", "error", "delivery_unknown")
    }
    ordered = [item.index for item in records] == list(range(len(records)))
    lines.append(f"  {label}: {len(records)} results in order {ordered} {outcomes}")
    lines.extend(f"    {_record(item)}" for item in records[:2] + records[2:][-2:])
    if failure is not None:
        lines.append(f"    ! {describe(failure)}")


def continued(
    lines: list[str],
    label: str,
    iterator: Iterator[Any],
    *,
    failure_description: Callable[[Exception], str] = describe,
) -> None:
    """Consume a batch iterator through its failures, reporting each failure after the records before it."""
    records: list[Any] = []
    failures: list[str] = []
    while True:
        try:
            records.append(next(iterator))
        except StopIteration:  # noqa: PERF203 - Each step must report failures and continue to sibling outcomes.
            break
        except Exception as error:  # noqa: BLE001 - Report failures from public calls.
            failures.append(f"after {len(records)}: {failure_description(error)}")
    _summarized(lines, label, records, None)
    lines.extend(f"    ! {item}" for item in failures)


async def acontinued(
    lines: list[str], label: str, iterator: Any, *, failure_description: Callable[[Exception], str] = describe
) -> None:
    """Consume an asyncio batch iterator through its failures, reporting each one after the records before it."""
    records: list[Any] = []
    failures: list[str] = []
    while True:
        try:
            records.append(await anext(iterator))
        except StopAsyncIteration:  # noqa: PERF203 - Each step must report failures and continue to sibling outcomes.
            break
        except Exception as error:  # noqa: BLE001 - Report failures from public calls.
            failures.append(f"after {len(records)}: {failure_description(error)}")
    _summarized(lines, label, records, None)
    lines.extend(f"    ! {item}" for item in failures)


def drained(lines: list[str], label: str, iterator: Iterator[Any]) -> list[Any]:
    """Consume a batch iterator, reporting its records and the failure that ended it."""
    records: list[Any] = []
    failure: BaseException | None = None
    try:
        records.extend(iterator)
    except Exception as error:  # noqa: BLE001 - Report failures from public calls.
        failure = error
    _summarized(lines, label, records, failure)
    return records


async def adrained(lines: list[str], label: str, iterator: Any) -> list[Any]:
    """Consume an asyncio batch iterator, reporting its records and the failure that ended it."""
    records: list[Any] = []
    failure: BaseException | None = None
    try:
        async for item in iterator:
            records.append(item)  # noqa: PERF401 - Keep results consumed before the iterator raises.
    except Exception as error:  # noqa: BLE001 - Report failures from public calls.
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
    _boundary_controls(harness, lines)
    run(lambda: _async_boundary_controls(harness, lines))


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
        lines,
        "call sends",
        users.iterate(harness.users(3), options=harness.options.RequestOptions(max_network_sends=0)),
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
            [
                good,
                {"id": "u0002", "user": good["user"], "error": {"code": "x"}},
                {"id": "u0000", "error": {"code": "x"}},
            ],
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
    for label, responder in (
        (
            "success body invalid JSON",
            lambda _: httpx2.Response(200, content=b"{", headers={"Content-Type": "application/json"}),
        ),
        ("success body invalid model", json_response(200, {"results": 1})),
    ):
        server.respond(answer, responder, answer)
        drained(lines, label, users.iterate(harness.users(5), batch_options=single))
        server.report(label)


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
    states = {harness.errors.DeliveryState.MAYBE_SENT, harness.errors.DeliveryState.RESPONSE_STARTED}
    continued(
        lines,
        "cancelled while answered",
        users.iterate(harness.users(6), batch_options=single, options=options),
        failure_description=lambda error: (
            f"{type(error).__name__} delivery_unknown={error.delivery_state in states} "
            f"source={error.source} reason={error.reason_code}"
        ),
    )
    server.report("cancelled while answered")
    _deadline(harness, server, lines)
    server.report("deadline while answered")
    _interruption(harness, lines)


class _Stepped:
    """A monotonic clock source the server steps past a session's deadline while it answers."""

    def __init__(self) -> None:
        """Start at a fixed instant."""
        self.value = 100.0

    def __call__(self) -> float:
        """Return the current instant."""
        return self.value


def _deadline(harness: _Batches, server: _Server, lines: list[str]) -> None:
    """Report a request answered after the session's deadline as unknown deliveries, then raise the deadline.

    The client's clock is stepped by the server while it answers, so the outcome never depends on real time.
    """
    stepped = _Stepped()
    states = {harness.errors.DeliveryState.MAYBE_SENT, harness.errors.DeliveryState.RESPONSE_STARTED}

    def late(request: httpx2.Request) -> httpx2.Response:
        stepped.value += 10.0
        return answer(request)

    clock = harness.options.Clock(monotonic=stepped)
    server.respond(late)
    with (
        server.client() as native,
        harness.package.Client(http_client=native, options=harness.client_options(clock=clock)) as api,
    ):
        session = harness.options.SessionOptions(total_timeout=1)
        users = api.protocols.users.create
        continued(
            lines,
            "deadline while answered",
            users.iterate(harness.users(2), session_options=session),
            failure_description=lambda error: (
                f"{type(error).__name__} delivery_unknown="
                f"{error.delivery_state in states} "
                f"phase={error.phase} reason={error.reason_code}"
            ),
        )


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
        records = 0
        try:
            while True:
                next(iterator)
                records += 1
        except Exception as error:  # noqa: BLE001 - Report failures from public calls.
            lines.append(f"  after the interruption, at most 2 records {records <= 2} ! {describe(error)}")
        closed = api.protocols.users.create.iterate(
            harness.users(6), batch_options=harness.batch(batch_size=2, parallelism=2)
        )
        lines.append(f"  first before closing {_record(next(closed))}")
        closed.close()
        record(lines, "after closing", lambda: next(closed))


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
        (
            "fixed idempotency key",
            {"options": harness.options.RequestOptions(idempotency_key=harness.options.IdempotencyKey.new())},
        ),
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
        (
            f"  success {type(success).__name__} outcome={success.outcome} id={success.item_id} "
            f"token={success.retry_token} value={_field(success.value, 'name')}"
        ),
        f"  failure {type(failure).__name__} outcome={failure.outcome} error={_field(failure.error, 'code')}",
        f"  repr hides values {'user 5' not in repr(success)} {'taken' not in repr(failure)} {success!r}".replace(
            repr(success.response), "<response>"
        ),
        f"  exported {sorted(name for name in records.__all__ if name.startswith('Users'))}",
    ))
    record(lines, "frozen", lambda: setattr(success, "index", 9))
    record(
        lines,
        "outcome fixed",
        lambda: records.UsersCreateSuccess(index=0, item_id="a", response=None, value=1, outcome="error"),
    )
    unknown = records.TagsPutDeliveryUnknown(index=1, item_id=None, response=None)
    lines.append(f"  unknown {unknown!r}")
    server.report("records")


async def _async_batches(harness: _Batches, lines: list[str]) -> None:
    """Send items with the asyncio client, in the same order, from synchronous and asynchronous sources."""
    lines.append("asyncio")
    server = _Server(lines)
    async with (
        server.async_client() as native,
        harness.package.AsyncClient(http_client=native, options=harness.client_options()) as api,
    ):
        users = api.protocols.users.create
        await adrained(lines, "1000 users", users.iterate(harness.users(1000)))
        server.report("1000 users")
        await adrained(lines, "asynchronous source", users.iterate(_source(harness.users(40))))
        await adrained(lines, "asynchronous source failure", users.iterate(_source(harness.users(3), fail=True)))
        await adrained(lines, "tags", api.protocols.tags.put.iterate(harness.tags("red", None, "blue", "green")))
        server.report("sources")
        server.respond(answer, failing(httpx2.ReadError))
        await adrained(
            lines,
            "unknown delivery",
            users.iterate(harness.users(4), batch_options=harness.batch(batch_size=2, parallelism=1)),
        )
        for label, responder in (
            (
                "success body invalid JSON",
                lambda _: httpx2.Response(200, content=b"{", headers={"Content-Type": "application/json"}),
            ),
            ("success body invalid model", json_response(200, {"results": 1})),
        ):
            server.respond(answer, responder, answer)
            await adrained(
                lines, label, users.iterate(harness.users(5), batch_options=harness.batch(batch_size=2, parallelism=1))
            )
            server.report(label)
        server.respond(json_response(503, {"message": "busy"}))
        await adrained(
            lines,
            "unavailable",
            users.iterate(harness.users(4), batch_options=harness.batch(batch_size=2, parallelism=1)),
        )
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
        except Exception as error:  # noqa: BLE001 - Report failures from public calls.
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
    info = responses.ResponseInfo(
        status_code=200, headers=responses.HeadersView(()), call_id="c", elapsed=0.1, content_type=None
    )
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
            (
                f"  {name}: base={error_type.__bases__[0].__name__} reason={error.reason_code} "
                f"chain={[item.__name__ for item in error_type.__mro__ if issubclass(item, errors.SDKError)]}"
            ),
            (
                f"    retained={all(getattr(error, key) == value for key, value in fields.items())} "
                f"secret={secret in text} str={error}"
            ),
        ))
    unknown = errors.BatchDeliveryUnknownError(partial_results=(1,), batch_indices=(), delivery_state=sent)
    parameter = errors.BatchDeliveryUnknownError.__parameters__[0]
    hint = get_type_hints(errors.BatchDeliveryUnknownError.partial_results.fget)["return"]
    lines.append(
        f"  partial results {unknown.partial_results} message={unknown.message_id} "
        f"kind={errors.BatchItemTooLargeError(index=0, limit=0, observed=1).kind} "
        f"covariance={parameter.__covariant__} "
        f"subscripted={get_origin(errors.BatchDeliveryUnknownError[int]) is errors.BatchDeliveryUnknownError} "
        f"hint={get_origin(hint) is tuple}"
    )
    not_sent = errors.DeliveryState.NOT_SENT
    for label, create in (
        ("indices list", lambda: errors.BatchProtocolError(indices=[1])),
        ("indices negative", lambda: errors.BatchProtocolError(indices=(-1,))),
        ("fixed condition", lambda: errors.BatchProtocolError(indices=(), condition="value")),
        ("index negative", lambda: errors.BatchItemTooLargeError(index=-1, limit=0, observed=1)),
        ("fixed kind", lambda: errors.BatchItemTooLargeError(index=0, limit=0, observed=1, kind="page")),
        (
            "partial results list",
            lambda: errors.BatchDeliveryUnknownError(partial_results=[], batch_indices=(), delivery_state=sent),
        ),
        (
            "batch indices bool",
            lambda: errors.BatchDeliveryUnknownError(partial_results=(), batch_indices=(True,), delivery_state=sent),
        ),
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
            except Exception as error:  # noqa: BLE001, PERF203
                cause = type(error.__cause__ or getattr(error, "cause", None)).__name__
                lines.append(f"  {label} ! {type(error).__name__} location={error.location} cause={cause}")
        dry_run = harness.argument("users", "CreateUsers", "query", "dryRun", True)
        drained(lines, "valid shared argument", users.iterate(harness.users(3), dry_run=dry_run))
        server.report("arguments")


class _BoundaryResponse:
    """A public transport response retaining its release and content-decoding observations."""

    def __init__(self, owner: Any, items: list[Any]) -> None:
        self.owner = owner
        case = owner.case if items[0]["id"] == "u0000" or not owner.case.get("first_only") else {}
        self.status_code = case.get("status", 200)
        self.headers = owner.headers(
            (("content-type", "application/json"),)
            + ((("content-encoding", case["coding"]),) if case.get("coding") else ())
        )
        content = case.get("content")
        self.content = (
            gzip.compress(b"x" * case["expanded_bytes"], mtime=0)
            if "expanded_bytes" in case
            else content.encode()
            if content is not None
            else json.dumps({"results": [{"id": item["id"], "user": item} for item in items]}).encode()
        )
        self.closed = False

    def iter_raw_bytes(self) -> Iterator[bytes]:
        yield self.content

    def close(self) -> None:
        self.closed = True
        self.owner.completed.set()

    async def aclose(self) -> None:
        self.close()


class _AsyncBoundaryResponse(_BoundaryResponse):
    """The same response with native asynchronous bytes."""

    async def iter_raw_bytes(self) -> AsyncIterator[bytes]:
        yield self.content


class _BoundaryTransport:
    """A public transport adapter driving transmission, response faults, and event gates."""

    def __init__(self, harness: _Batches, case: dict[str, Any], *, asynchronous: bool = False) -> None:
        transports = importlib.import_module(f"{harness.package.__name__}.transports")
        responses = importlib.import_module(f"{harness.package.__name__}.responses")
        self.capabilities = transports.TransportCapabilities(
            internal_retry_limit=0, delivery_evidence=True, http_versions=("HTTP/1.1",)
        )
        self.headers = responses.HeadersView
        self.case = case
        event = asyncio.Event if asynchronous else threading.Event
        self.started, self.release, self.completed, self.ended = event(), event(), event(), event()
        self.requests: list[list[Any]] = []
        self.responses: list[Any] = []
        self.guard = threading.Lock()
        self.native = (
            asyncio.CancelledError("worker interruption") if asynchronous else KeyboardInterrupt("worker interruption")
        )
        if case.get("native_other"):
            self.native = _Interrupt("worker interruption")

    def _transmitted(self, content: bytes, context: Any) -> list[Any]:
        items = json.loads(content)["users"]
        with self.guard:
            self.requests.append(items)
        context.trace.request_headers_started()
        context.trace.wire_send()
        self.started.set()
        return items

    def _response(self, items: list[Any], context: Any, kind: type[_BoundaryResponse]) -> Any:
        response = kind(self, items)
        self.responses.append(response)
        context.trace.response_headers_received(
            http_version="HTTP/1.1", status_code=response.status_code, headers=response.headers
        )
        return response

    def on_event(self, event: Any) -> None:
        """Interrupt a public call-start hook when the control asks for failure before admission."""
        if event.name == "call_end":
            self.ended.set()
        if self.case.get("before_call") and event.name == "call_start":
            raise self.native

    def send(self, request: Any, context: Any) -> Any:
        if self.case.get("before_send"):
            raise self.native
        items = self._transmitted(b"".join(request.body.iter_bytes()), context)
        if self.case.get("gate") or self.case.get("source_gate"):
            self.release.wait(30)
        if self.case.get("native") and items[0]["id"] == "u0000":
            if self.case.get("sibling"):
                self.completed.wait(30)
            raise self.native
        return self._response(items, context, _BoundaryResponse)

    def close(self) -> None:
        """Leave this borrowed adapter with its caller."""


class _AsyncBoundaryTransport(_BoundaryTransport):
    """Drive the same controls with native async sends and event waits."""

    async def send(self, request: Any, context: Any) -> Any:
        if self.case.get("before_send"):
            raise self.native
        items = self._transmitted(b"".join([part async for part in request.body.aiter_bytes()]), context)
        if self.case.get("gate") or self.case.get("source_gate"):
            await self.release.wait()
        if self.case.get("native") and items[0]["id"] == "u0000":
            if self.case.get("sibling"):
                await self.completed.wait()
            raise self.native
        return self._response(items, context, _AsyncBoundaryResponse)

    async def aclose(self) -> None:
        """Leave this borrowed adapter with its caller."""


def _boundary_cases() -> dict[str, Any]:
    return json.loads((Path(__file__).parents[1] / "generation_platform/client/batch-boundaries.json").read_text())


def _boundary_failure(error: Exception) -> str:
    partial = [(item.index, item.outcome) for item in getattr(error, "partial_results", ())]
    cause = getattr(error, "cause", None)
    return (
        f"{type(error).__name__} state={getattr(error, 'delivery_state', None)} "
        f"partial={partial} cause={type(cause).__name__ if cause is not None else None} "
        f"nested={type(cause.cause).__name__ if getattr(cause, 'cause', None) is not None else None}"
    )


def _boundary_report(lines: list[str], transport: Any) -> None:
    lines.append(f"    sends={len(transport.requests)} closed={[reply.closed for reply in transport.responses]}")


def _submission_source(items: list[Any], transport: Any) -> Iterator[Any]:
    """Release a worker interruption while acquiring lookahead, then return the already-entered source item."""
    yield items[0]
    transport.release.set()
    transport.ended.wait(30)
    yield items[1]
    yield from items[2:]


async def _async_submission_source(items: list[Any], transport: Any) -> AsyncIterator[Any]:
    """Release the async worker at source entry, and return lookahead only after its interruption is published."""
    yield items[0]
    transport.release.set()
    await transport.ended.wait()
    for item in items[1:]:
        yield item


class _PresendClock:
    """Raise an unrelated SDK error from a public clock after the source has been entered."""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.failing = False

    def __call__(self) -> float:
        if self.failing:
            raise self.error
        return 100.0


def _presend_source(items: list[Any], clock: _PresendClock) -> Iterator[Any]:
    clock.failing = True
    yield from items


def _presend_error(
    harness: _Batches, transport: Any, control: dict[str, Any] | None = None
) -> tuple[_PresendClock, Any]:
    responses = importlib.import_module(f"{harness.package.__name__}.responses")
    info = responses.ResponseInfo(
        status_code=200, headers=transport.headers(()), call_id="earlier-call", elapsed=0, content_type=None
    )
    selected = control or {"name": "ResponseDecodeError", "arguments": {}}
    arguments = dict(selected["arguments"])
    if selected["name"] == "ResponseDecodeError":
        arguments["info"] = info
    if "delivery_state" in arguments:
        arguments["delivery_state"] = harness.errors.DeliveryState(arguments["delivery_state"])
    clock = _PresendClock(getattr(harness.errors, selected["name"])(**arguments))
    return clock, harness.options.Clock(monotonic=clock)


def _boundary_controls(harness: _Batches, lines: list[str]) -> None:
    cases = _boundary_cases()
    lines.append("sync boundary controls")
    _worker_source_stops(harness, lines)
    for case in cases["decoding"]:
        for raising in (False, True):
            transport = _BoundaryTransport(harness, case)
            with harness.package.Client(transport_adapter=transport) as api:
                iterator = api.protocols.users.create.iterate(
                    harness.users(4), batch_options=harness.batch(batch_size=2, parallelism=2, raise_on_error=raising)
                )
                continued(lines, f"{case['label']} raising={raising}", iterator, failure_description=_boundary_failure)
                iterator.close()
            _boundary_report(lines, transport)
    for case in cases["interruptions"]:
        for raising in (False, True):
            transport = _BoundaryTransport(harness, case)
            with harness.package.Client(transport_adapter=transport) as api:
                iterator = api.protocols.users.create.iterate(
                    _submission_source(harness.users(6), transport) if case.get("source_gate") else harness.users(6),
                    options=harness.options.RequestOptions(hooks=(transport,)),
                    batch_options=harness.batch(
                        batch_size=1 if case.get("source_gate") else 2,
                        parallelism=2 if case.get("sibling") or case.get("source_gate") else 1,
                        raise_on_error=raising,
                    ),
                )
                try:
                    next(iterator)
                except (KeyboardInterrupt, _Interrupt) as error:
                    lines.append(f"  {case['label']} raising={raising}: original={error is transport.native}")
                continued(lines, "retained after interruption", iterator, failure_description=_boundary_failure)
                iterator.close()
            _boundary_report(lines, transport)
    for control, raising in product(cases["presend_errors"], cases["presend_sdk"]):
        transport = _BoundaryTransport(harness, {})
        clock, option = _presend_error(harness, transport, control)
        with harness.package.Client(transport_adapter=transport, options=harness.client_options(clock=option)) as api:
            iterator = api.protocols.users.create.iterate(
                _presend_source(harness.users(2), clock),
                batch_options=harness.batch(parallelism=1, raise_on_error=raising),
            )
            continued(
                lines,
                f"pre-send {control['name']} raising={raising}",
                iterator,
                failure_description=_boundary_failure,
            )
            iterator.close()
        _boundary_report(lines, transport)
    for active in (False, True):
        for raising in (False, True):
            transport = _BoundaryTransport(harness, {"gate": active})
            api = harness.package.Client(
                transport_adapter=transport, options=harness.client_options(cleanup_timeout=0.05)
            )
            iterator = api.protocols.users.create.iterate(
                harness.users(2), batch_options=harness.batch(parallelism=1, raise_on_error=raising)
            )
            records: list[str] = []
            worker = threading.Thread(
                target=partial(continued, records, "closed", iterator, failure_description=_boundary_failure)
            )
            if active:
                worker.start()
                transport.started.wait(30)
            with suppress(harness.errors.CleanupError):
                api.close()
            transport.release.set()
            if active:
                worker.join(30)
            else:
                continued(records, "closed", iterator, failure_description=_boundary_failure)
            lines.append(f"  client close active={active} raising={raising}")
            lines.extend(records)
            iterator.close()
            api.close()
            _boundary_report(lines, transport)
    transport = _BoundaryTransport(harness, {"gate": True})
    with harness.package.Client(transport_adapter=transport) as api:
        source = _Counted(harness.users(2, name="x" * 7))
        iterator = api.protocols.users.create.iterate(source, batch_options=harness.batch(**cases["buffer"]))
        result: list[Any] = []
        worker = threading.Thread(target=lambda: result.append(next(iterator)))
        worker.start()
        transport.started.wait(30)
        held = sum(
            len(json.dumps(item, separators=(",", ":")).encode()) for batch in transport.requests for item in batch
        )
        lines.append(f"  gated buffer before return: read={source.read} sends={len(transport.requests)} bytes={held}")
        transport.release.set()
        worker.join(30)
        result.extend(iterator)
        lines.append(f"  gated buffer results={[(item.index, item.outcome) for item in result]}")
        iterator.close()
    _boundary_report(lines, transport)


async def _async_boundary_controls(harness: _Batches, lines: list[str]) -> None:
    cases = _boundary_cases()
    lines.append("async boundary controls")
    for case in cases["decoding"]:
        for raising in (False, True):
            transport = _AsyncBoundaryTransport(harness, case, asynchronous=True)
            async with harness.package.AsyncClient(transport_adapter=transport) as api:
                iterator = api.protocols.users.create.iterate(
                    harness.users(4), batch_options=harness.batch(batch_size=2, parallelism=2, raise_on_error=raising)
                )
                await acontinued(
                    lines, f"{case['label']} raising={raising}", iterator, failure_description=_boundary_failure
                )
                await iterator.aclose()
            _boundary_report(lines, transport)
    for case in cases["interruptions"]:
        for raising in (False, True):
            transport = _AsyncBoundaryTransport(harness, case, asynchronous=True)
            async with harness.package.AsyncClient(transport_adapter=transport) as api:
                iterator = api.protocols.users.create.iterate(
                    _async_submission_source(harness.users(6), transport)
                    if case.get("source_gate")
                    else harness.users(6),
                    options=harness.options.RequestOptions(hooks=(transport,)),
                    batch_options=harness.batch(
                        batch_size=1 if case.get("source_gate") else 2,
                        parallelism=2 if case.get("sibling") or case.get("source_gate") else 1,
                        raise_on_error=raising,
                    ),
                )
                try:
                    await anext(iterator)
                except (asyncio.CancelledError, _Interrupt) as error:
                    lines.append(f"  {case['label']} raising={raising}: original={error is transport.native}")
                await acontinued(lines, "retained after interruption", iterator, failure_description=_boundary_failure)
                await iterator.aclose()
            _boundary_report(lines, transport)
    for control, raising in product(cases["presend_errors"], cases["presend_sdk"]):
        transport = _AsyncBoundaryTransport(harness, {}, asynchronous=True)
        clock, option = _presend_error(harness, transport, control)
        async with harness.package.AsyncClient(
            transport_adapter=transport, options=harness.client_options(clock=option)
        ) as api:
            iterator = api.protocols.users.create.iterate(
                _presend_source(harness.users(2), clock),
                batch_options=harness.batch(parallelism=1, raise_on_error=raising),
            )
            await acontinued(
                lines,
                f"pre-send {control['name']} raising={raising}",
                iterator,
                failure_description=_boundary_failure,
            )
            await iterator.aclose()
        _boundary_report(lines, transport)
    for active in (False, True):
        for raising in (False, True):
            transport = _AsyncBoundaryTransport(harness, {"gate": active}, asynchronous=True)
            api = harness.package.AsyncClient(transport_adapter=transport)
            iterator = api.protocols.users.create.iterate(
                harness.users(2), batch_options=harness.batch(parallelism=1, raise_on_error=raising)
            )
            records: list[str] = []
            if active:
                worker = asyncio.create_task(
                    acontinued(records, "closed", iterator, failure_description=_boundary_failure)
                )
                await transport.started.wait()
            await api.aclose()
            if active:
                await worker
            else:
                await acontinued(records, "closed", iterator, failure_description=_boundary_failure)
            lines.append(f"  client close active={active} raising={raising}")
            lines.extend(records)
            await iterator.aclose()
            _boundary_report(lines, transport)
    await _caller_controls(harness, lines, cases["caller_cancellation"])
    transport = _AsyncBoundaryTransport(harness, {"gate": True}, asynchronous=True)
    async with harness.package.AsyncClient(transport_adapter=transport) as api:
        source = _Counted(harness.users(2, name="x" * 7))
        iterator = api.protocols.users.create.iterate(source, batch_options=harness.batch(**cases["buffer"]))
        step = asyncio.create_task(anext(iterator))
        await transport.started.wait()
        held = sum(
            len(json.dumps(item, separators=(",", ":")).encode()) for batch in transport.requests for item in batch
        )
        lines.append(f"  gated buffer before return: read={source.read} sends={len(transport.requests)} bytes={held}")
        transport.release.set()
        result = [await step]
        result.extend([item async for item in iterator])
        lines.append(f"  gated buffer results={[(item.index, item.outcome) for item in result]}")
        await iterator.aclose()
    _boundary_report(lines, transport)
    await _source_controls(harness, lines, cases["source_controls"])
    await _async_worker_source_stops(harness, lines, cases["source_stops"])


async def _caller_controls(harness: _Batches, lines: list[str], controls: list[bool]) -> None:
    """Cancel a caller's step after transmission, then drain the retained outcomes without sending more."""
    for raising in controls:
        transport = _AsyncBoundaryTransport(harness, {"gate": True}, asynchronous=True)
        async with harness.package.AsyncClient(transport_adapter=transport) as api:
            iterator = api.protocols.users.create.iterate(
                harness.users(6), batch_options=harness.batch(batch_size=2, parallelism=1, raise_on_error=raising)
            )
            step = asyncio.create_task(anext(iterator))
            await transport.started.wait()
            step.cancel("caller interruption")
            try:
                await step
            except asyncio.CancelledError as error:
                lines.append(f"  caller cancelled raising={raising}: {type(error).__name__} args={error.args}")
            await acontinued(
                lines, "retained after caller cancellation", iterator, failure_description=_boundary_failure
            )
            await iterator.aclose()
        _boundary_report(lines, transport)


class _BlockedSource:
    """A source whose event wait records entry and release on interruption."""

    def __init__(self, stepped: Any) -> None:
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.exited = False
        self.stepped = stepped

    def __aiter__(self) -> _BlockedSource:
        return self

    async def __anext__(self) -> Any:
        self.entered.set()
        try:
            if self.stepped is not None:
                self.stepped.value += 10
            await self.release.wait()
        finally:
            self.exited = True
        raise StopAsyncIteration


async def _source_controls(harness: _Batches, lines: list[str], controls: list[str]) -> None:
    for reason in controls:
        for during in (False, True):
            stepped = _Stepped()
            clock = harness.options.Clock(monotonic=stepped)
            token = harness.options.CancelToken()
            transport = _AsyncBoundaryTransport(harness, {}, asynchronous=True)
            api = harness.package.AsyncClient(transport_adapter=transport, options=harness.client_options(clock=clock))
            source = _BlockedSource(stepped if during and reason == "deadline" else None)
            iterator = api.protocols.users.create.iterate(
                source,
                session_options=harness.options.SessionOptions(total_timeout=1),
                options=harness.options.RequestOptions(cancel_token=token),
            )
            if not during:
                if reason == "deadline":
                    stepped.value += 10
                elif reason == "token":
                    token.cancel()
                else:
                    await api.aclose()
            task = asyncio.create_task(anext(iterator))
            if during:
                await source.entered.wait()
                if reason == "token":
                    token.cancel()
                elif reason == "close":
                    await api.aclose()
            result = await asyncio.gather(task, return_exceptions=True)
            lines.append(
                f"  source {reason} during={during}: entered={source.entered.is_set()} exited={source.exited} "
                f"result={[type(item).__name__ for item in result]} sends={len(transport.requests)}"
            )
            await iterator.aclose()
            await api.aclose()


def _worker_stop_source(items: list[Any], transport: Any, read: list[int]) -> Iterator[Any]:
    """Publish a worker stop from an already-entered synchronous read, then detect any later acquisition."""
    for index, item in enumerate(items):
        if index == 2:
            transport.release.set()
            if not transport.ended.wait(30):
                msg = "worker stop watchdog"
                raise RuntimeError(msg)
        read.append(index)
        yield item


def _worker_source_stops(harness: _Batches, lines: list[str]) -> None:
    for raising in (False, True):
        transport = _BoundaryTransport(harness, {"native": True, "source_gate": True})
        read: list[int] = []
        with harness.package.Client(transport_adapter=transport) as api:
            iterator = api.protocols.users.create.iterate(
                _worker_stop_source(harness.users(6), transport, read),
                options=harness.options.RequestOptions(hooks=(transport,)),
                batch_options=harness.batch(batch_size=2, parallelism=2, raise_on_error=raising),
            )
            try:
                next(iterator)
            except KeyboardInterrupt as error:
                lines.append(
                    f"  worker source stop raising={raising}: original={error is transport.native} read={read}"
                )
            continued(lines, "retained source stop", iterator, failure_description=_boundary_failure)
            iterator.close()
        _boundary_report(lines, transport)


async def _worker_stop_async_source(
    items: list[Any], transport: Any, read: list[int], pending: bool, gate: asyncio.Event, exited: asyncio.Event
) -> AsyncIterator[Any]:
    """Keep an input await owned by the iterator until the worker publishes its native stop."""
    try:
        for index, item in enumerate(items):
            read.append(index)
            if index == 2:
                transport.release.set()
                if pending:
                    await gate.wait()
                else:
                    await transport.ended.wait()
            yield item
    finally:
        exited.set()


async def _async_worker_source_stops(harness: _Batches, lines: list[str], controls: list[bool]) -> None:
    for pending, raising in product(controls, (False, True)):
        transport = _AsyncBoundaryTransport(harness, {"native": True, "source_gate": True}, asynchronous=True)
        read: list[int] = []
        gate, exited = asyncio.Event(), asyncio.Event()
        async with harness.package.AsyncClient(transport_adapter=transport) as api:
            iterator = api.protocols.users.create.iterate(
                _worker_stop_async_source(harness.users(6), transport, read, pending, gate, exited),
                options=harness.options.RequestOptions(hooks=(transport,)),
                session_options=harness.options.SessionOptions(total_timeout=None),
                batch_options=harness.batch(batch_size=2, parallelism=2, raise_on_error=raising),
            )
            try:
                await asyncio.wait_for(anext(iterator), timeout=30)
            except asyncio.CancelledError as error:
                lines.append(
                    f"  worker source stop pending={pending} raising={raising}: "
                    f"original={error is transport.native} read={read} "
                    f"cleanup={exited.is_set()} gate={gate.is_set()}"
                )
            await acontinued(lines, "retained source stop", iterator, failure_description=_boundary_failure)
            await iterator.aclose()
        _boundary_report(lines, transport)
