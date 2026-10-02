"""Save calls through generated queue helpers and deliver them only by draining, against an independent orders server.

The server deduplicates orders by their idempotency keys and records each arrival; a store written against the public
queue types alone holds the entries, moves their instants back to let time pass, and injects store failures.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import re
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from functools import partial
from typing import TYPE_CHECKING, Any, Final

import httpx2

from tests.data.python.client_runtime import Exchange, describe, failing, injected, json_response, raw_response, run

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from types import ModuleType

_PARTITION: Final = "tenant-a"
_UUID: Final = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_TERMINAL: Final = ("succeeded", "dead", "delivery_unknown", "cancelled")


class _Crash(BaseException):
    """A process that stops at once, leaving whatever it saved."""


class _Labels:
    """Stable labels of random identifiers, in the order the report first meets them."""

    def __init__(self, prefix: str) -> None:
        """Start without labels."""
        self.prefix, self.labels = prefix, {}

    def __call__(self, value: str) -> str:
        """Return a value's label."""
        return self.labels.setdefault(value, f"{self.prefix}{len(self.labels) + 1}")


class _Orders:
    """An independent orders server: it deduplicates creates by key and records every arrival it answers."""

    def __init__(self) -> None:
        """Start without orders or arrivals."""
        self.keys = _Labels("k")
        self.orders: dict[str, dict[str, object]] = {}
        self.log: list[str] = []
        self.lock = threading.Lock()
        self.during: Callable[[], None] | None = None

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        """Answer a request by its method and path, recording it."""
        request.read()
        with self.lock:
            if (during := self.during) is not None:
                self.during = None
                during()
            path = request.url.path
            key = request.headers.get("Idempotency-Key")
            auth = request.headers.get("Authorization")
            facts = [
                *([f"key={self.keys(key)}"] if key else []),
                *([f"auth={auth}"] if auth else []),
                *([f"trace={trace}"] if (trace := request.headers.get("X-Trace")) else []),
                *([f"query={request.url.query.decode()}"] if request.url.query else []),
                *([f"body={request.content.decode()}"] if request.content else []),
            ]
            if request.method == "POST" and path == "/orders":
                duplicate = key in self.orders
                order = self.orders.setdefault(key or "", {"id": f"o{len(self.orders) + 1}", "item": "tea"})
                self.log.append(f"server POST {path} {' '.join(facts)} duplicate={duplicate}")
                return json_response(201, order)(request)
            self.log.append(f"server {request.method} {path} {' '.join(facts)}".rstrip())
            if path.startswith("/orders/"):
                return json_response(200, {"id": path.rsplit("/", 1)[1], "item": "tea"})(request)
            return json_response(200, {"name": "ada"})(request)

    def flush(self, lines: list[str], *, ordered: bool = True) -> None:
        """Report the arrivals since the last report, in arrival order or sorted when sends overlapped."""
        with self.lock:
            log, self.log = self.log, []
        lines.extend(f"  {line}" for line in (log if ordered else sorted(log)))

    def status(self, code: int, **headers: str) -> Callable[[httpx2.Request], httpx2.Response]:
        """Return a responder that records an arrival and answers with a status instead of processing it."""

        def respond(request: httpx2.Request) -> httpx2.Response:
            request.read()
            with self.lock:
                key = request.headers.get("Idempotency-Key")
                self.log.append(
                    (
                        f"server {request.method} {request.url.path} {'key=' + self.keys(key) if key else ''} -> {code}"
                    ).replace("  ", " ")
                )
            return raw_response(code, b"", None, **headers)(request)

        return respond

    def lost(self, request: httpx2.Request) -> httpx2.Response:
        """Process a request, then lose the connection before the response, so the client cannot know it arrived."""
        self(request)
        msg = "connection reset"
        raise httpx2.ReadError(msg, request=request)


class _Exchange(Exchange):
    """An exchange whose server answers every request without a queued responder, reporting nothing itself."""

    def __init__(self, server: _Orders) -> None:
        """Answer through the orders server by default."""
        super().__init__([])
        self.orders = server

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        """Answer with the next queued responder, or the orders server."""
        request.read()
        return (self.responders.pop(0) if self.responders else self.orders)(request)


class _Time:
    """Advance by microseconds until the scenario moves time, and draw the middle of the jitter range."""

    def __init__(self) -> None:
        """Start at a fixed instant."""
        self.offset = 0.0
        self.reads = 0
        self.lock = threading.Lock()

    def monotonic(self) -> float:
        """Return seconds on the clock's monotonic scale."""
        return 1000.0 + self.offset

    def time(self) -> float:
        """Return POSIX seconds with ordered microsecond increments."""
        with self.lock:
            self.reads += 1
            return 1_800_000_000.0 + self.offset + self.reads / 1_000_000

    @staticmethod
    def random() -> float:
        """Return the middle of the jitter range."""
        return 0.5

    def advance(self, seconds: float) -> None:
        """Let time pass."""
        with self.lock:
            self.offset += seconds

    def now(self) -> datetime:
        """Return the clock's instant in UTC."""
        return datetime.fromtimestamp(self.time(), timezone.utc)

    def http_date(self, seconds: float) -> str:
        """Return the HTTP date seconds after the clock's instant."""
        return format_datetime(datetime.fromtimestamp(self.time() + seconds, timezone.utc), usegmt=True)


class _Broken(httpx2.SyncByteStream):
    """A response body that breaks after its first bytes."""

    def __iter__(self) -> Iterator[bytes]:
        """Yield the start of a body, then lose the connection."""
        yield b'{"code":'
        msg = "connection reset"
        raise httpx2.ReadError(msg)


class _Cancelling(httpx2.SyncByteStream):
    """A response body whose reading cancels a call's token."""

    def __init__(self, token: Any) -> None:
        """Keep the token to cancel."""
        self.token = token

    def __iter__(self) -> Iterator[bytes]:
        """Yield the start of a body, cancel the token, then the rest."""
        yield b'{"code":'
        self.token.cancel()
        yield b'"x"}'


def _streamed(body: httpx2.SyncByteStream, request: httpx2.Request) -> httpx2.Response:
    """Answer 503 with a streamed JSON body."""
    request.read()
    return httpx2.Response(503, headers={"content-type": "application/json"}, stream=body)


class _Store:
    """A queue store written against the public queue types alone: entries in a dict, versions as counters.

    It fails, conflicts, or answers wrongly on request; time passes on the client's stepped clock.
    """

    def __init__(self, protocols: ModuleType) -> None:
        """Start empty."""
        self.protocols = protocols
        self.entries: dict[str, Any] = {}
        self.version = 0
        self.lock = threading.RLock()
        self.failures: dict[str, BaseException] = {}
        self.later: dict[str, tuple[int, BaseException]] = {}
        self.wrong: dict[str, object] = {}
        self.conflicts = 0
        self.claims = 0
        self.at_claim: dict[int, Callable[[], None]] = {}
        self.before: dict[str, Callable[[Any], None]] = {}
        self.writes = 0

    def _next(self, entry: Any) -> Any:
        self.version += 1
        return replace(entry, version=f"v{self.version}")

    def _hook(self, action: str, entry: Any = None) -> object:
        if (hook := self.before.pop(action, None)) is not None:
            hook(entry)
        if (failure := self.failures.pop(action, None)) is not None:
            raise failure
        if (later := self.later.pop(action, None)) is not None:
            if later[0]:
                self.later[action] = (later[0] - 1, later[1])
            else:
                raise later[1]
        return self.wrong.pop(action, _Store)

    def put(self, entry: Any) -> None:
        """Store a new entry."""
        with self.lock:
            self._hook("put", entry)
            self.entries[entry.entry_id] = self._next(entry)

    def get(self, entry_id: str) -> Any:
        """Return an entry or None."""
        with self.lock:
            if (wrong := self._hook("get")) is not _Store:
                return wrong
            return self.entries.get(entry_id)

    def claim(self, *, now: datetime, lease_until: datetime, limit: int) -> tuple[Any, ...]:
        """Recover expired leases, then lease ready pending entries in creation order."""
        with self.lock:
            self.claims += 1
            if (hook := self.at_claim.pop(self.claims, None)) is not None:
                hook()
            if (wrong := self._hook("claim")) is not _Store:
                return wrong  # type: ignore[return-value]
            for key, entry in list(self.entries.items()):
                if entry.state == "leased" and entry.lease_until <= now:
                    state, result = "pending", entry.result
                    if result is not None and result.category != "retryable":
                        state = {
                            "success": "succeeded",
                            "permanent": "dead",
                            "unknown": "delivery_unknown",
                            "cancelled": "delivery_unknown" if entry.send_intent else "cancelled",
                        }[result.category]
                    elif entry.cancel_requested:
                        state = "delivery_unknown" if entry.send_intent else "cancelled"
                        result = self.protocols.QueueOutcome(category="cancelled")
                    self.entries[key] = self._next(
                        replace(
                            entry,
                            state=state,
                            result=result,
                            send_intent=entry.send_intent if state == "pending" else False,
                            lease_id=None,
                            lease_until=None,
                        )
                    )
            ready = sorted(
                (entry for entry in self.entries.values() if entry.state == "pending" and entry.not_before <= now),
                key=lambda entry: (entry.created_at, entry.entry_id),
            )[:limit]
            leases = []
            for entry in ready:
                self.version += 1
                leased = self.entries[entry.entry_id] = self._next(
                    replace(entry, state="leased", lease_id=f"lease{self.version}", lease_until=lease_until)
                )
                leases.append(self.protocols.QueueLease(entry=leased, lease_id=leased.lease_id))
            return tuple(leases)

    def exchange(self, entry_id: str, expected_version: str, entry: Any) -> bool:
        """Replace an entry whose version is the one expected."""
        with self.lock:
            if (wrong := self._hook("compare_exchange", entry)) is not _Store:
                return wrong  # type: ignore[return-value]
            if self.conflicts:
                self.conflicts -= 1
                return False
            current = self.entries.get(entry_id)
            if current is None or current.version != expected_version:
                return False
            self.writes += 1
            self.entries[entry_id] = self._next(entry)
            return True

    compare_exchange = exchange

    def purge_terminal(self, before: datetime) -> tuple[Any, ...]:
        """Remove and return the ended entries created before an instant."""
        with self.lock:
            if (wrong := self._hook("purge_terminal")) is not _Store:
                return wrong  # type: ignore[return-value]
            purged = tuple(
                entry for entry in self.entries.values() if entry.state in _TERMINAL and entry.created_at < before
            )
            for entry in purged:
                del self.entries[entry.entry_id]
            return purged

    def tamper(self, entry_id: str, **fields: object) -> None:
        """Change an entry's fields as another writer would."""
        with self.lock:
            self.entries[entry_id] = self._next(replace(self.entries[entry_id], **fields))


class _Stubborn(_Store):
    """A store that ignores its leases: every claim returns the entries of its first one again."""

    def __init__(self, protocols: ModuleType) -> None:
        """Start empty, without a first claim."""
        super().__init__(protocols)
        self.first: tuple[Any, ...] | None = None

    def claim(self, *, now: datetime, lease_until: datetime, limit: int) -> tuple[Any, ...]:
        """Lease ready entries once, then return the same leases forever."""
        if self.first is None:
            self.first = super().claim(now=now, lease_until=lease_until, limit=limit)
        return self.first


class _Recancelling(_Store):
    """A store where a cancel is requested of an entry each time a claim returns it again."""

    def __init__(self, protocols: ModuleType) -> None:
        """Start empty, without claimed entries."""
        super().__init__(protocols)
        self.claimed: set[str] = set()

    def claim(self, *, now: datetime, lease_until: datetime, limit: int) -> tuple[Any, ...]:
        """Lease ready entries, requesting a cancel of any claimed before."""
        leases = super().claim(now=now, lease_until=lease_until, limit=limit)
        for lease in leases:
            if (entry_id := lease.entry.entry_id) in self.claimed:
                self.tamper(entry_id, cancel_requested=True)
            self.claimed.add(entry_id)
        return leases


class _AsyncStore:
    """The asynchronous face of a scenario store."""

    def __init__(self, store: _Store) -> None:
        """Share the synchronous store's entries."""
        self.store = store

    async def put(self, entry: Any) -> None:
        """Store a new entry."""
        self.store.put(entry)

    async def get(self, entry_id: str) -> Any:
        """Return an entry or None."""
        return self.store.get(entry_id)

    async def claim(self, *, now: datetime, lease_until: datetime, limit: int) -> tuple[Any, ...]:
        """Lease ready entries."""
        return self.store.claim(now=now, lease_until=lease_until, limit=limit)

    async def exchange(self, entry_id: str, expected_version: str, entry: Any) -> bool:
        """Replace an entry whose version is the one expected."""
        return self.store.exchange(entry_id, expected_version, entry)

    compare_exchange = exchange

    async def purge_terminal(self, before: datetime) -> tuple[Any, ...]:
        """Remove and return ended entries."""
        return self.store.purge_terminal(before)


class _Static:
    """A bearer credential provider whose token the scenario changes, or that is not ready."""

    def __init__(self, auth: ModuleType, token: str) -> None:
        """Give one token."""
        self.auth, self.ready = auth, True
        self.token(token)

    def token(self, value: str) -> None:
        """Give another token from now on."""
        self.current = self.auth.BearerCredential(self.auth.AccessToken(value), self.auth.TokenVersion())

    def get(self, context: object) -> object:
        """Return the current credential, or fail while the provider is not ready."""
        del context
        if not self.ready:
            msg = "provider offline"
            raise RuntimeError(msg)
        return self.current


class _Queues:
    """A generated queue package's public modules and the clients the scenarios use."""

    def __init__(self, package: ModuleType, lines: list[str]) -> None:
        """Import the modules and start the orders server."""
        self.package, self.lines = package, lines
        self.options, self.protocols, self.errors, self.auth = (
            importlib.import_module(f"{package.__name__}.{name}") for name in ("options", "protocols", "errors", "auth")
        )
        types = importlib.import_module(f"{package.__name__}.types.orders")
        self.order = types.CreateOrderRequestCodecs.body().from_wire({"item": "tea", "quantity": 2})
        self.codecs = types
        self.entries = _Labels("e")
        self.server = _Orders()
        self.exchange = _Exchange(self.server)
        self.time = _Time()
        self.clock = self.options.Clock(monotonic=self.time.monotonic, time=self.time.time, random=self.time.random)

    def stepping(self, code: int) -> Callable[[httpx2.Request], httpx2.Response]:
        """Return a responder that answers with a status after a second has passed."""
        refuse = self.server.status(code)

        def respond(request: httpx2.Request) -> httpx2.Response:
            self.time.advance(1)
            return refuse(request)

        return respond

    def argument(self, operation: str, location: str, name: str, wire: object) -> object:
        """Return an argument built from its wire value."""
        codecs = getattr(self.codecs, f"{operation}RequestCodecs")
        return codecs.parameter(location=location, name=name).from_wire(wire)

    def settings(self, store: object, *, security: bool = True, auth: object = None, **settings: Any) -> Any:
        """Return client options lending a store to both queues, without retries unless asked."""
        options = self.options
        protocols = options.ProtocolClientOptions(
            queue_stores={"orders.outbox": store, "account.offline": store},
            security=self.protocols.ProtocolSecurityContext(credential_partition=_PARTITION) if security else None,
            **settings,
        )
        extra = {} if auth is None else {"auth": self.auth.AuthConfig({"bearer": auth})}
        return options.ClientOptions(
            retry=options.RetryOptions(max_retries=0), protocols=protocols, clock=self.clock, **extra
        )

    def label(self, text: str) -> str:
        """Replace every entry identifier in a text with its label."""
        labels = self.entries.labels
        return _UUID.sub(lambda found: labels.get(found[0], found[0]), text)

    def entry(self, entry: Any) -> str:
        """Describe an entry by its state, deliveries, flags, lease, and last outcome; never its payload."""
        if entry is None:
            return "None"
        result = entry.result
        outcome = (
            "-"
            if result is None
            else (
                f"{result.category}/{result.error_code}/"
                f"{None if result.response is None else result.response.status_code}"
            )
        )
        return (
            f"{self.entries(entry.entry_id)} {entry.operation_alias} {entry.state} deliveries={entry.delivery_count} "
            f"intent={entry.send_intent} cancel={entry.cancel_requested} leased={entry.lease_id is not None} "
            f"outcome={outcome}"
        )

    def report(self, report: Any) -> str:
        """Describe a drain report by its labeled entries."""
        named = {name: [self.entries(item) for item in getattr(report, name)] for name in report.counts}
        return " ".join(f"{name}={items}" for name, items in named.items() if items) or "empty"

    def step(self, label: str, call: Callable[[], object]) -> object:
        """Report a step's result or failure, labeling entries."""
        try:
            result = call()
        except Exception as error:  # ruff: ignore[blind-except]
            self.lines.append(self.label(f"  {label} ! {self.failure(error)}"))
            return None
        self.lines.append(self.label(f"  {label} = {self.describe(result)}"))
        return result

    def raised(self, label: str, call: Callable[[], object]) -> None:
        """Report only the class of a step's failure, whose delivery state depends on when a cancellation landed."""
        try:
            result = call()
        except Exception as error:  # ruff: ignore[blind-except]
            self.lines.append(f"  {label} ! {type(error).__name__}")
            return
        self.lines.append(self.label(f"  {label} = {self.describe(result)}"))

    async def astep(self, label: str, call: Callable[[], Any]) -> object:
        """Report an asyncio step's result or failure, labeling entries."""
        try:
            result = await call()
        except Exception as error:  # ruff: ignore[blind-except]
            self.lines.append(self.label(f"  {label} ! {self.failure(error)}"))
            return None
        self.lines.append(self.label(f"  {label} = {self.describe(result)}"))
        return result

    def failure(self, error: BaseException) -> str:
        """Describe a failure with its queue fields and secondary errors."""
        extra = [
            f"{name}={getattr(error, name)!r}"
            for name in ("action", "fields", "entry_id", "observed")
            if getattr(error, name, None) is not None
        ]
        secondary = [type(item).__name__ for item in getattr(error, "secondary_errors", ())]
        return f"{describe(error)} {' '.join(extra)}{f' secondary={secondary}' if secondary else ''}".rstrip()

    def describe(self, value: object) -> str:
        """Describe a receipt, entry, report, tuple of entries, or anything else."""
        protocols = self.protocols
        if isinstance(value, protocols.QueueReceipt):
            return f"receipt {self.entries(value.entry_id)} {value.state} ttl={value.expires_at - value.created_at}"
        if isinstance(value, protocols.QueueEntry) or value is None:
            return self.entry(value)
        if isinstance(value, protocols.DrainReport):
            return self.report(value)
        if isinstance(value, tuple):
            return f"[{', '.join(self.entry(item) for item in value)}]"
        return repr(value)


def _helpers(api: Any) -> tuple[Any, Any]:
    return api.protocols.orders.outbox, api.protocols.account.offline


def queues(package: ModuleType, lines: list[str]) -> None:
    """Enqueue, drain, recover, cancel, and refuse queued calls through the synchronous and asyncio clients."""
    queue = _Queues(package, lines)
    with queue.exchange.client() as native:
        _enqueue(queue, native)
        _deliveries(queue, native)
        _outcomes(queue, native)
        _holding(queue, native)
        _waits(queue, native)
        _recovery(queue, native)
        _cancelling(queue, native)
        _bindings(queue, native)
        _malformed(queue, native)
        _store_failures(queue, native)
        _conflicts(queue, native)
        _limits(queue, native)
        _parallel(queue, native)
        _configuration(queue, native)
        _memory_stores(queue)
    run(lambda: _async_queues(queue))


def _client(queue: _Queues, native: httpx2.Client, store: object, **settings: Any) -> Any:
    return queue.package.Client(http_client=native, options=queue.settings(store, **settings))


def _enqueue(queue: _Queues, native: httpx2.Client) -> None:
    """Enqueue saves wire values and sends nothing; secrets, sizes, policy fields, and partitions are refused first."""
    lines, step = queue.lines, queue.step
    lines.append("enqueue")
    store = _Store(queue.protocols)
    trace = queue.argument("CreateOrder", "header", "X-Trace", "t1")
    with _client(queue, native, store) as api:
        outbox, account = _helpers(api)
        receipt = step("create", lambda: outbox.operations.create_order.enqueue(x_trace=trace, body=queue.order))
        step(
            "refresh",
            lambda: outbox.operations.refresh.enqueue(order_id=queue.argument("GetOrder", "path", "orderId", "o9")),
        )
        entry = store.entries[receipt.entry_id]
        payload = json.loads(entry.payload)
        payload["key"], payload["created_at"] = "<stable key>", "<creation instant>"
        lines.append(f"  saved payload {json.dumps(payload, sort_keys=True, separators=(',', ':'))}")
        lines.append(f"  saved key {entry.idempotency_key is not None} blob {entry.blob} owned {entry.blob_owned}")
        lines.append(f"  saved policy {entry.policy}")
        hidden = ("payload", "idempotency_key", "fingerprint", "version", "lease_id")
        lines.append(f"  saved repr hides {[name for name in hidden if name not in repr(entry)]}")
        queue.server.flush(lines)
        lines.append(f"  sends after enqueue {len(queue.server.log)}")
        session = queue.argument("CreateOrder", "cookie", "session", "s1")
        step("secret cookie", lambda: outbox.operations.create_order.enqueue(session=session, body=queue.order))
        small = queue.protocols.QueueOptions(max_entry_body_bytes=10)
        step("too large", lambda: outbox.operations.create_order.enqueue(body=queue.order, queue_options=small))
        drain_field = queue.protocols.QueueOptions(parallelism=2, max_entries=3)
        step("drain field", lambda: outbox.operations.create_order.enqueue(body=queue.order, queue_options=drain_field))
        step("options type", lambda: outbox.operations.create_order.enqueue(body=queue.order, queue_options="fast"))
        delays = queue.protocols.QueueOptions(retry_initial_delay=10, retry_max_delay=5)
        step("delays order", lambda: outbox.operations.create_order.enqueue(body=queue.order, queue_options=delays))
        ttl = queue.protocols.QueueOptions(entry_ttl=60)
        step("short ttl", lambda: outbox.operations.create_order.enqueue(body=queue.order, queue_options=ttl))
        step("account", account.operations.fetch.enqueue)
    with _client(queue, native, store, security=False) as api:
        step("anonymous account", _helpers(api)[1].operations.fetch.enqueue)
    lines.append(f"  entries {len(store.entries)}")


def _deliveries(queue: _Queues, native: httpx2.Client) -> None:
    """Drain in creation order, each keyed entry once with its key, the shared retries inside a delivery."""
    lines, step, server = queue.lines, queue.step, queue.server
    lines.append("deliveries")
    store = _Store(queue.protocols)
    with _client(queue, native, store) as api:
        outbox = _helpers(api)[0]
        first = step("first", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        step(
            "second",
            lambda: outbox.operations.refresh.enqueue(order_id=queue.argument("GetOrder", "path", "orderId", "o1")),
        )
        step("drain", outbox.drain)
        server.flush(lines)
        step("inspect", lambda: outbox.inspect(first.entry_id))
        step("drain again", outbox.drain)
        server.flush(lines)
        retried = step("retried", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        queue.exchange.respond(server.status(503))
        retry = queue.options.RetryOptions(max_retries=1, initial_delay=0, jitter="none")
        step("drain with a retry", lambda: outbox.drain(options=queue.options.RequestOptions(retry=retry)))
        server.flush(lines)
        step("inspect retried", lambda: outbox.inspect(retried.entry_id))
        refused = step("refused", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        queue.exchange.respond(server.status(422))
        step("drain refused", outbox.drain)
        server.flush(lines)
        step("inspect refused", lambda: outbox.inspect(refused.entry_id))
        lost = step("lost", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        queue.exchange.respond(injected(server.lost))
        step("drain lost", outbox.drain)
        server.flush(lines)
        step("inspect lost", lambda: outbox.inspect(lost.entry_id))
        step("drain leaves unknown", outbox.drain)
        step("retry unknown", lambda: outbox.retry_unknown(lost.entry_id))
        step("drain retried unknown", outbox.drain)
        server.flush(lines)
        step("retry settled", lambda: outbox.retry_unknown(lost.entry_id))
        step("retry missing", lambda: outbox.retry_unknown("missing"))
        unsent = step("unsent", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        queue.exchange.respond(failing(httpx2.ConnectTimeout))
        step("drain unproven timeout", outbox.drain)
        step("inspect unproven timeout", lambda: outbox.inspect(unsent.entry_id))
        later = queue.time.now() + timedelta(days=2)
        step("purge", lambda: outbox.purge_terminal(later))
        step("purge naive", lambda: outbox.purge_terminal(datetime(2030, 1, 1)))  # ruff: ignore[call-datetime-without-tzinfo]
        lines.append(f"  left {sorted(queue.entries(key) for key in store.entries)}")


class _Unsent:
    """A transport adapter that proves every request unsent."""

    def __init__(self, transports: ModuleType, errors: ModuleType) -> None:
        """Declare one send per attempt."""
        self.capabilities = transports.TransportCapabilities(
            internal_retry_limit=0, delivery_evidence=False, http_versions=("HTTP/1.1",)
        )
        self.errors = errors

    def send(self, request: Any, context: Any) -> Any:
        """Fail before sending anything."""
        del request, context
        raise self.errors.TransportError(delivery_state=self.errors.DeliveryState.NOT_SENT, phase="connect")

    def close(self) -> None:
        """Close nothing."""


def _outcomes(queue: _Queues, native: httpx2.Client) -> None:  # ruff: ignore[too-many-locals]
    """Classify each way a delivery ends: by its response, whether anything was sent, and how a failure arrived."""
    lines, step, server, options = queue.lines, queue.step, queue.server, queue.options
    lines.append("outcomes")
    store = _Store(queue.protocols)
    with _client(queue, native, store) as api:
        outbox = _helpers(api)[0]
        garbled = step("garbled success", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        queue.exchange.respond(raw_response(201, b"{", "application/json"))
        step("drain garbled", outbox.drain)
        step("inspect garbled", lambda: outbox.inspect(garbled.entry_id))
        odd = step("undeclared status", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        queue.exchange.respond(raw_response(304))
        step("drain undeclared", outbox.drain)
        step("inspect undeclared", lambda: outbox.inspect(odd.entry_id))
        dotted = step(
            "dot segment",
            lambda: outbox.operations.refresh.enqueue(order_id=queue.argument("GetOrder", "path", "orderId", "x")),
        )
        store.tamper(dotted.entry_id, payload=b'{"arguments":[["."],[]],"body":[],"version":1}')
        step("drain dot segment", outbox.drain)
        managed = step("managed key header", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        patched = options.RequestOptions(headers=(("Idempotency-Key", "mine"),))
        step("drain patching the key", lambda: outbox.drain(options=patched))
        step("inspect managed", lambda: outbox.inspect(managed.entry_id))
        step(
            "drain without a session deadline",
            lambda: outbox.drain(session_options=options.SessionOptions(total_timeout=None)),
        )
        failed = step("failed body", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        queue.exchange.respond(injected(partial(_streamed, _Broken())))
        step("drain failed body", outbox.drain)
        step("inspect failed body", lambda: outbox.inspect(failed.entry_id))
        token = options.CancelToken()
        sent = step("cancelled in a body", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        queue.exchange.respond(injected(partial(_streamed, _Cancelling(token))))
        queue.raised(
            "drain cancelled in a body", lambda: outbox.drain(options=options.RequestOptions(cancel_token=token))
        )
        step("inspect cancelled", lambda: outbox.inspect(sent.entry_id))
        interrupted = step("interrupted", lambda: outbox.operations.create_order.enqueue(body=queue.order))

        def interrupt(request: httpx2.Request) -> httpx2.Response:
            server(request)
            raise _Crash

        queue.exchange.respond(injected(interrupt))
        try:
            outbox.drain()
        except _Crash:
            lines.append("  interrupted while sending")
        step("inspect interrupted", lambda: outbox.inspect(interrupted.entry_id))
        step("retry interrupted", lambda: outbox.retry_unknown(interrupted.entry_id))
        step("drain interrupted again", outbox.drain)
        unsaved = step("interrupted unsaved", lambda: outbox.operations.create_order.enqueue(body=queue.order))

        def interrupt_unsaved(request: httpx2.Request) -> httpx2.Response:
            server(request)
            store.failures["compare_exchange"] = OSError("write")
            raise _Crash

        queue.exchange.respond(injected(interrupt_unsaved))
        try:
            outbox.drain()
        except _Crash as crash:
            lines.append(f"  interrupted with a failed save notes={len(getattr(crash, '__notes__', ()))}")
        step("inspect unsaved", lambda: outbox.inspect(unsaved.entry_id))
        store.entries.pop(unsaved.entry_id)
        lost = step("cancelled and taken", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        stop = options.CancelToken()

        def taken() -> None:
            store.tamper(lost.entry_id, lease_id="elsewhere")
            stop.cancel()

        server.during = taken
        queue.raised(
            "drain cancelled and taken", lambda: outbox.drain(options=options.RequestOptions(cancel_token=stop))
        )
        server.flush(lines)
    unsent = queue.package.Client(
        transport_adapter=_Unsent(importlib.import_module(f"{queue.package.__name__}.transports"), queue.errors),
        options=queue.settings(store),
    )
    with unsent:
        outbox = _helpers(unsent)[0]
        proven = step(
            "proven unsent",
            lambda: outbox.operations.refresh.enqueue(order_id=queue.argument("GetOrder", "path", "orderId", "u1")),
        )
        step("drain unsent", outbox.drain)
        step("inspect unsent", lambda: outbox.inspect(proven.entry_id))


def _holding(queue: _Queues, native: httpx2.Client) -> None:
    """Hold an entry ready again in the same drain until it ends, then return it to pending."""
    lines, step, server, protocols = queue.lines, queue.step, queue.server, queue.protocols
    lines.append("holding")
    store = _Store(protocols)
    soon = protocols.QueueOptions(retry_initial_delay=1e-9, retry_max_delay=1e-9)
    with _client(queue, native, store) as api:
        outbox = _helpers(api)[0]
        again = step(
            "ready again", lambda: outbox.operations.create_order.enqueue(body=queue.order, queue_options=soon)
        )
        queue.exchange.respond(queue.stepping(503))
        step("drain once", outbox.drain)
        step("inspect released", lambda: outbox.inspect(again.entry_id))
        broken = step(
            "broken",
            lambda: outbox.operations.refresh.enqueue(order_id=queue.argument("GetOrder", "path", "orderId", "h1")),
        )
        store.tamper(broken.entry_id, helper_fingerprint="0" * 64)
        queue.exchange.respond(queue.stepping(503))
        store.later["compare_exchange"] = (3, OSError("release"))
        step("release fails", outbox.drain)
        server.flush(lines)
        for key in list(store.entries):
            store.entries.pop(key)
        stuck = step(
            "held and stuck", lambda: outbox.operations.create_order.enqueue(body=queue.order, queue_options=soon)
        )
        queue.exchange.respond(queue.stepping(503))
        store.at_claim[store.claims + 3] = lambda: setattr(store, "conflicts", 100)
        step("release conflicts", outbox.drain)
        store.conflicts = 0
        step("inspect stuck", lambda: outbox.inspect(stuck.entry_id))
        store.entries.pop(stuck.entry_id)
    recancelling = _Recancelling(protocols)
    with _client(queue, native, recancelling) as api:
        outbox = _helpers(api)[0]
        held = step(
            "held and cancelled", lambda: outbox.operations.create_order.enqueue(body=queue.order, queue_options=soon)
        )
        queue.exchange.respond(queue.stepping(503))
        step("drain held", outbox.drain)
        step("inspect held", lambda: outbox.inspect(held.entry_id))
        server.flush(lines)


def _waits(queue: _Queues, native: httpx2.Client) -> None:  # ruff: ignore[too-many-locals]
    """Wait at least the server's Retry-After, end dead past the lifetime or deliveries, and never shorten a wait."""
    lines, step, server = queue.lines, queue.step, queue.server
    lines.append("waits")
    store = _Store(queue.protocols)
    with _client(queue, native, store) as api:
        outbox = _helpers(api)[0]
        waited = step("waited", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        queue.exchange.respond(server.status(503, **{"Retry-After": "120"}))
        retry = queue.options.RetryOptions(max_retries=2, max_retry_after=60, initial_delay=0, jitter="none")
        before = queue.time.now()
        step("drain past the child cap", lambda: outbox.drain(options=queue.options.RequestOptions(retry=retry)))
        server.flush(lines)
        entry = store.entries[waited.entry_id]
        lines.append(
            f"  waits {entry.not_before - before >= timedelta(seconds=120)} saved {entry.saved_wait_seconds >= 120} "
            f"retry_at {entry.result.retry_at == entry.not_before}"
        )
        step("not ready yet", outbox.drain)
        queue.time.advance(121)
        queue.exchange.respond(server.status(503, **{"Retry-After": "7200"}))
        step("wait past the key", outbox.drain)
        server.flush(lines)
        step("inspect", lambda: outbox.inspect(waited.entry_id))
        limited = queue.protocols.QueueOptions(max_deliveries=2, retry_initial_delay=1, retry_max_delay=1)
        twice = step("twice", lambda: outbox.operations.create_order.enqueue(body=queue.order, queue_options=limited))
        queue.exchange.respond(server.status(503), server.status(503))
        step("first delivery", outbox.drain)
        lines.append(f"  jitter wait {store.entries[twice.entry_id].saved_wait_seconds}")
        queue.time.advance(2)
        step("second delivery", outbox.drain)
        server.flush(lines)
        step("inspect twice", lambda: outbox.inspect(twice.entry_id))
        session = queue.options.SessionOptions(total_timeout=30)
        deadline = queue.options.RetryOptions(max_retries=1, max_retry_after=None, initial_delay=0, jitter="none")
        later = step("past the drain", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        step(
            "next",
            lambda: outbox.operations.refresh.enqueue(order_id=queue.argument("GetOrder", "path", "orderId", "o2")),
        )
        queue.exchange.respond(server.status(503, **{"Retry-After": "40"}))
        step(
            "drain shorter than the wait",
            lambda: outbox.drain(options=queue.options.RequestOptions(retry=deadline), session_options=session),
        )
        server.flush(lines)
        lines.append(f"  waits {store.entries[later.entry_id].saved_wait_seconds >= 40}")
        expired = step(
            "expired",
            lambda: outbox.operations.refresh.enqueue(order_id=queue.argument("GetOrder", "path", "orderId", "o3")),
        )
        queue.time.advance(86401)
        step("drain expired", outbox.drain)
        step("inspect expired", lambda: outbox.inspect(expired.entry_id))
        exhausted = step(
            "exhausted",
            lambda: outbox.operations.refresh.enqueue(order_id=queue.argument("GetOrder", "path", "orderId", "o4")),
        )
        store.tamper(exhausted.entry_id, delivery_count=5)
        step("drain exhausted", outbox.drain)
        step("inspect exhausted", lambda: outbox.inspect(exhausted.entry_id))
        dated = step("dated wait", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        queue.exchange.respond(server.status(503, **{"Retry-After": queue.time.http_date(120)}))
        step("drain dated", outbox.drain)
        wait = store.entries[dated.entry_id].saved_wait_seconds
        lines.append(f"  dated wait 119<wait<=120 {119 < wait <= 120}")
        slow = step("slow intent", lambda: outbox.operations.create_order.enqueue(body=queue.order))

        def stall(entry: Any) -> None:
            if entry.send_intent:
                queue.time.advance(3601)

        store.before["compare_exchange"] = stall
        step("drain past the key during the intent", outbox.drain)
        step("inspect slow", lambda: outbox.inspect(slow.entry_id))
        server.flush(lines)


def _recovery(queue: _Queues, native: httpx2.Client) -> None:
    """Recover outcome-free crash leases using the original key and request."""
    lines, step, server = queue.lines, queue.step, queue.server
    lines.append("recovery")
    store = _Store(queue.protocols)
    with _client(queue, native, store) as api:
        outbox = _helpers(api)[0]
        crashed = step("crash after intent", lambda: outbox.operations.create_order.enqueue(body=queue.order))

        def intent(entry: Any) -> None:
            if entry.send_intent:
                store.failures["get"] = _Crash()

        store.before["compare_exchange"] = intent
        try:
            outbox.drain()
        except _Crash:
            lines.append("  crashed before sending")
        server.flush(lines)
        step("inspect", lambda: outbox.inspect(crashed.entry_id))
        step("lease held", outbox.drain)
        queue.time.advance(400)
        step("after the lease", outbox.drain)
        server.flush(lines)
        step("inspect recovered", lambda: outbox.inspect(crashed.entry_id))
        answered = step("crash after response", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        server.during = lambda: store.failures.__setitem__("compare_exchange", _Crash())
        try:
            outbox.drain()
        except _Crash:
            lines.append("  crashed before saving the response")
        server.flush(lines)
        queue.time.advance(400)
        step("redelivered", outbox.drain)
        server.flush(lines)
        step("inspect redelivered", lambda: outbox.inspect(answered.entry_id))
        other = step(
            "held by another worker",
            lambda: outbox.operations.refresh.enqueue(order_id=queue.argument("GetOrder", "path", "orderId", "o5")),
        )
        now = queue.time.now()
        store.claim(now=now, lease_until=now + timedelta(seconds=60), limit=1)
        step("leased elsewhere", outbox.drain)
        queue.time.advance(61)
        step("after its lease", outbox.drain)
        server.flush(lines)
        step("inspect other", lambda: outbox.inspect(other.entry_id))


def _cancelling(queue: _Queues, native: httpx2.Client) -> None:  # ruff: ignore[too-many-locals]
    """Cancel pending entries at once and leased ones at the drain's next boundary, never resending a sent one."""
    lines, step, server = queue.lines, queue.step, queue.server
    lines.append("cancelling")
    store = _Store(queue.protocols)
    with _client(queue, native, store) as api:
        outbox = _helpers(api)[0]
        pending = step("pending", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        step("cancel pending", lambda: outbox.cancel(pending.entry_id))
        step("cancel again", lambda: outbox.cancel(pending.entry_id))
        step("retry cancelled", lambda: outbox.retry_unknown(pending.entry_id))
        step("cancel missing", lambda: outbox.cancel("missing"))
        maybe = step("maybe sent", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        store.tamper(maybe.entry_id, send_intent=True)
        step("cancel maybe sent", lambda: outbox.cancel(maybe.entry_id))
        leased = step("leased", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        now = queue.time.now()
        store.claim(now=now, lease_until=now + timedelta(seconds=60), limit=1)
        step("cancel leased", lambda: outbox.cancel(leased.entry_id))
        step("cancel requested", lambda: outbox.cancel(leased.entry_id))
        queue.time.advance(61)
        step("drain cancelled", outbox.drain)
        step("inspect", lambda: outbox.inspect(leased.entry_id))
        done = step(
            "done",
            lambda: outbox.operations.refresh.enqueue(order_id=queue.argument("GetOrder", "path", "orderId", "o6")),
        )
        step("drain done", outbox.drain)
        server.flush(lines)
        step("cancel done", lambda: outbox.cancel(done.entry_id))
        during = step("during a send", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        server.during = lambda: outbox.cancel(during.entry_id)
        step("drain while cancelled", outbox.drain)
        server.flush(lines)
        step("inspect sent", lambda: outbox.inspect(during.entry_id))
        failed = step("during a failure", lambda: outbox.operations.create_order.enqueue(body=queue.order))

        def refused(request: httpx2.Request) -> httpx2.Response:
            outbox.cancel(failed.entry_id)
            return server.status(503)(request)

        queue.exchange.respond(refused)
        step("drain refused while cancelled", outbox.drain)
        server.flush(lines)
        step("inspect refused", lambda: outbox.inspect(failed.entry_id))
        intent = step("before the send", lambda: outbox.operations.create_order.enqueue(body=queue.order))

        def requested(entry: Any) -> None:
            if entry.send_intent:
                store.before["get"] = lambda _: store.tamper(intent.entry_id, cancel_requested=True)

        store.before["compare_exchange"] = requested
        step("drain cancelled before the send", outbox.drain)
        server.flush(lines)
        step("inspect before the send", lambda: outbox.inspect(intent.entry_id))
        racing = step("cancelled during the intent", lambda: outbox.operations.create_order.enqueue(body=queue.order))

        def racing_cancel(entry: Any) -> None:
            if entry.send_intent:
                store.tamper(racing.entry_id, cancel_requested=True)

        store.before["compare_exchange"] = racing_cancel
        step("drain cancelled during the intent", outbox.drain)
        step("inspect during the intent", lambda: outbox.inspect(racing.entry_id))
        token = queue.options.CancelToken()
        token.cancel()
        stopped = step("token", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        step("drain cancelled token", lambda: outbox.drain(options=queue.options.RequestOptions(cancel_token=token)))
        step("inspect token", lambda: outbox.inspect(stopped.entry_id))
        server.flush(lines)


def _bindings(queue: _Queues, native: httpx2.Client) -> None:
    """Send under the current credentials; refuse entries of another contract, partition, or auth without migrating."""
    lines, step, server = queue.lines, queue.step, queue.server
    lines.append("bindings")
    store = _Store(queue.protocols)
    provider = _Static(queue.auth, "secret-one")
    with _client(queue, native, store, auth=provider) as api:
        account = _helpers(api)[1]
        fetched = step("fetch", account.operations.fetch.enqueue)
        name = importlib.import_module(f"{queue.package.__name__}.types.account").UpdateAccountRequestCodecs
        renamed = step(
            "rename", lambda: account.operations.rename.enqueue(body=name.body().from_wire({"name": "grace"}))
        )
        saved = b"".join(entry.payload for entry in store.entries.values())
        lines.append(f"  token saved {b'secret' in saved}")
        lines.append(
            f"  receipt ttl {store.entries[renamed.entry_id].expires_at - store.entries[renamed.entry_id].created_at}"
        )
        provider.token("secret-two")
        step("drain with a new token", account.drain)
        server.flush(lines)
        retry = step("retry", account.operations.fetch.enqueue)
        provider.ready = False
        step("provider not ready", account.drain)
        step("inspect deferred", lambda: account.inspect(retry.entry_id))
        provider.ready = True
        step("provider ready", account.drain)
        server.flush(lines)
        del fetched
    other = queue.package.Client(
        http_client=native,
        options=queue.options.ClientOptions(
            retry=queue.options.RetryOptions(max_retries=0),
            clock=queue.clock,
            auth=queue.auth.AuthConfig({"bearer": provider}),
            protocols=queue.options.ProtocolClientOptions(
                queue_stores={"account.offline": store},
                security=queue.protocols.ProtocolSecurityContext(credential_partition="tenant-b"),
            ),
        ),
    )
    with _client(queue, native, store, auth=provider) as api:
        moved = step("partition", _helpers(api)[1].operations.fetch.enqueue)
    with other:
        step("drain in another partition", _helpers(other)[1].drain)
        step("inspect", lambda: _helpers(other)[1].inspect(moved.entry_id))
    with _client(queue, native, store, auth=provider) as api:
        account = _helpers(api)[1]
        store.tamper(moved.entry_id, helper_fingerprint="0" * 64)
        step("changed contract", account.drain)
        store.tamper(moved.entry_id, operation_alias="gone")
        step("unknown alias", account.drain)
        store.tamper(
            moved.entry_id, operation_alias="fetch", helper_fingerprint=store.entries[retry.entry_id].helper_fingerprint
        )
        step("restored", account.drain)
        server.flush(lines)


def _malformed(queue: _Queues, native: httpx2.Client) -> None:
    """Entries whose saved request no longer restores end dead without a send."""
    lines, step, server = queue.lines, queue.step, queue.server
    lines.append("malformed")
    store = _Store(queue.protocols)
    with _client(queue, native, store) as api:
        outbox = _helpers(api)[0]
        cases = {
            "not json": b"{",
            "version": b'{"arguments":[[],[]],"body":[],"version":2}',
            "arguments": b'{"arguments":[[]],"body":[],"version":1}',
            "media": b'{"arguments":[[],[]],"body":[{"item":"tea","quantity":2},5,null],"version":1}',
            "value": b'{"arguments":[[],[]],"body":[{"item":"tea","quantity":0},"application/json",null],"version":1}',
            "secret": (
                b'{"arguments":[[],["s1"]],"body":[{"item":"tea","quantity":2},"application/json",null],"version":1}'
            ),
        }
        for label, corrupt in cases.items():
            payload = corrupt
            receipt = outbox.operations.create_order.enqueue(body=queue.order)
            if label != "not json":
                fields = json.loads(store.entries[receipt.entry_id].payload)
                fields.update(json.loads(payload))
                payload = json.dumps(fields).encode()
            store.tamper(receipt.entry_id, payload=payload)
            step(f"drain {label}", outbox.drain)
        bodiless = outbox.operations.create_order.enqueue(body=queue.order)
        store.tamper(bodiless.entry_id, payload=b'{"arguments":[[],[]],"body":[],"version":1}')
        step("drain without its body", outbox.drain)
        pathless = outbox.operations.refresh.enqueue(order_id=queue.argument("GetOrder", "path", "orderId", "o1"))
        store.tamper(pathless.entry_id, payload=b'{"arguments":[[],[]],"body":[],"version":1}')
        step("drain without its path", outbox.drain)
        step("inspect without its path", lambda: outbox.inspect(pathless.entry_id))
        keyed = outbox.operations.create_order.enqueue(body=queue.order)
        store.tamper(keyed.entry_id, idempotency_key=" bad key")
        step("drain bad key", outbox.drain)
        step("inspect bad key", lambda: outbox.inspect(keyed.entry_id))
        server.flush(lines)


def _store_failures(queue: _Queues, native: httpx2.Client) -> None:
    """Map store failures and wrong results to queue store errors; nothing is sent unless the intent is saved."""
    lines, step, server = queue.lines, queue.step, queue.server
    lines.append("store failures")
    store = _Store(queue.protocols)
    errors = queue.errors
    with _client(queue, native, store) as api:
        outbox = _helpers(api)[0]
        store.failures["put"] = OSError("disk full")
        step("put fails", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        store.failures["put"] = errors.QueueFullError(kind="entries", limit=1, observed=2)
        step("put full", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        entry = step("entry", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        store.wrong["get"] = "entry"
        step("get wrong", lambda: outbox.inspect(entry.entry_id))
        store.failures["get"] = OSError("read")
        step("get fails", lambda: outbox.inspect(entry.entry_id))
        store.wrong["claim"] = ["lease"]
        step("claim wrong", outbox.drain)
        store.failures["claim"] = OSError("lock")
        step("claim fails", outbox.drain)
        store.failures["compare_exchange"] = OSError("write")
        step("intent fails", outbox.drain)
        server.flush(lines)
        step("inspect", lambda: outbox.inspect(entry.entry_id))
        queue.time.advance(400)
        store.wrong["compare_exchange"] = "yes"
        step("exchange wrong", outbox.drain)
        queue.time.advance(400)
        store.wrong["purge_terminal"] = ["entry"]
        step("purge wrong", lambda: outbox.purge_terminal(queue.time.now()))
    stubborn = _Stubborn(queue.protocols)
    with _client(queue, native, stubborn) as api:
        outbox = _helpers(api)[0]
        step(
            "ignored leases",
            lambda: outbox.operations.refresh.enqueue(order_id=queue.argument("GetOrder", "path", "orderId", "s1")),
        )
        step("drain bounded", lambda: outbox.drain(queue_options=queue.protocols.QueueOptions(max_entries=2)))
        lines.append(f"  claims bounded {stubborn.version}")
        server.flush(lines)


def _conflicts(queue: _Queues, native: httpx2.Client) -> None:
    """Reload after a write another writer beat; a lease another worker took is left to it."""
    lines, step, server = queue.lines, queue.step, queue.server
    lines.append("conflicts")
    store = _Store(queue.protocols)
    with _client(queue, native, store) as api:
        outbox = _helpers(api)[0]
        entry = step("entry", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        store.conflicts = 1
        step("one conflict", outbox.drain)
        server.flush(lines)
        step("stuck", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        store.conflicts = 100
        step("endless conflicts", outbox.drain)
        store.conflicts = 0
        queue.time.advance(400)
        step("drain stuck", outbox.drain)
        server.flush(lines)
        settled = step("settled", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        server.during = lambda: setattr(store, "conflicts", 100)
        step("conflicts after the send", outbox.drain)
        store.conflicts = 0
        server.flush(lines)
        step("inspect settled", lambda: outbox.inspect(settled.entry_id))
        pending = step("pending", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        store.tamper(settled.entry_id, state="delivery_unknown", lease_id=None, lease_until=None)
        store.conflicts = 100
        step("cancel conflicts", lambda: outbox.cancel(pending.entry_id))
        step("retry conflicts", lambda: outbox.retry_unknown(settled.entry_id))
        store.conflicts = 0
        queue.time.advance(3601)
        step("retry expired", lambda: outbox.retry_unknown(settled.entry_id))
        exhausted = step("exhausted", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        store.tamper(exhausted.entry_id, state="delivery_unknown", delivery_count=5)
        step("retry exhausted", lambda: outbox.retry_unknown(exhausted.entry_id))
        store.entries.pop(pending.entry_id)
        after = step("taken after the intent", lambda: outbox.operations.create_order.enqueue(body=queue.order))

        def taken(entry: Any) -> None:
            if entry.send_intent:
                store.before["get"] = lambda _: store.tamper(after.entry_id, lease_id="elsewhere")

        store.before["compare_exchange"] = taken
        step("drain taken after the intent", outbox.drain)
        server.flush(lines)
        stolen = step("stolen", lambda: outbox.operations.create_order.enqueue(body=queue.order))

        def steal(_: Any) -> None:
            store.tamper(stolen.entry_id, lease_id="elsewhere")

        store.before["compare_exchange"] = steal
        step("lease taken before the send", outbox.drain)
        server.flush(lines)
        store.tamper(stolen.entry_id, state="pending", lease_id=None, lease_until=None)
        server.during = lambda: store.tamper(stolen.entry_id, lease_id="elsewhere")
        step("lease taken during the send", outbox.drain)
        server.flush(lines)
        step("inspect stolen", lambda: outbox.inspect(stolen.entry_id))
        del entry


def _limits(queue: _Queues, native: httpx2.Client) -> None:
    """End at the entry, send, or time limit, leaving the rest pending."""
    lines, step, server = queue.lines, queue.step, queue.server
    lines.append("limits")
    store = _Store(queue.protocols)
    with _client(queue, native, store) as api:
        outbox = _helpers(api)[0]
        for index in range(3):
            outbox.operations.refresh.enqueue(order_id=queue.argument("GetOrder", "path", "orderId", f"l{index}"))
        step("one entry", lambda: outbox.drain(queue_options=queue.protocols.QueueOptions(max_entries=1)))
        step("one send", lambda: outbox.drain(session_options=queue.options.SessionOptions(max_network_sends=1)))
        step("no time", lambda: outbox.drain(session_options=queue.options.SessionOptions(total_timeout=0)))
        step("policy field", lambda: outbox.drain(queue_options=queue.protocols.QueueOptions(max_deliveries=1)))
        step(
            "fixed key",
            lambda: outbox.drain(
                options=queue.options.RequestOptions(idempotency_key=queue.options.IdempotencyKey("k"))
            ),
        )
        step("options type", lambda: outbox.drain(options="fast"))
        step("session type", lambda: outbox.drain(session_options="fast"))
        step("entry type", lambda: outbox.inspect(5))
        step("rest", outbox.drain)
        server.flush(lines)
        late = [
            outbox.operations.refresh.enqueue(order_id=queue.argument("GetOrder", "path", "orderId", f"t{index}"))
            for index in range(2)
        ]
        server.during = lambda: queue.time.advance(400)
        step("session deadline passes", outbox.drain)
        step("inspect after the session", lambda: outbox.inspect(late[1].entry_id))
        server.flush(lines)


def _parallel(queue: _Queues, native: httpx2.Client) -> None:
    """Deliver waves of entries at once; a failed delivery is raised after the wave's others are saved."""
    lines, step, server = queue.lines, queue.step, queue.server
    lines.append("parallel")
    store = _Store(queue.protocols)
    defaults = queue.protocols.ProtocolDefaults(options=queue.protocols.QueueOptions(parallelism=2))
    with _client(queue, native, store, defaults={"orders.outbox": defaults}) as api:
        outbox = _helpers(api)[0]
        for index in range(3):
            outbox.operations.refresh.enqueue(order_id=queue.argument("GetOrder", "path", "orderId", f"p{index}"))
        step("waves", outbox.drain)
        server.flush(lines, ordered=False)
        broken = [
            outbox.operations.refresh.enqueue(order_id=queue.argument("GetOrder", "path", "orderId", f"b{index}"))
            for index in range(2)
        ]
        for receipt in broken:
            store.tamper(receipt.entry_id, helper_fingerprint="0" * 64)
        store.failures["compare_exchange"] = OSError("release")
        step("broken wave", outbox.drain)
        server.flush(lines, ordered=False)


def _configuration(queue: _Queues, native: httpx2.Client) -> None:
    """Refuse stores of other helpers or modes, and send nothing without a store."""
    lines, step = queue.lines, queue.step
    lines.append("configuration")
    options, protocols = queue.options, queue.protocols
    store = _Store(protocols)
    rows: dict[str, Callable[[], object]] = {
        "unknown helper": lambda: options.ProtocolClientOptions(queue_stores={"orders.unknown": store}),
        "not a mapping": lambda: options.ProtocolClientOptions(queue_stores=[store]),
        "bad name": lambda: options.ProtocolClientOptions(queue_stores={"1bad": store}),
        "missing methods": lambda: options.ProtocolClientOptions(queue_stores={"orders.outbox": object()}),
        "async store": lambda: options.ProtocolClientOptions(queue_stores={"orders.outbox": _AsyncStore(store)}),
        "kind defaults": lambda: options.ProtocolClientOptions(
            defaults={"orders.outbox": protocols.ProtocolDefaults(options=protocols.PollOptions())}
        ),
    }
    for label, build in rows.items():
        step(
            label,
            lambda build=build: queue.package.Client(
                http_client=native, options=options.ClientOptions(protocols=build())
            ),
        )
    with queue.package.Client(http_client=native) as api:
        step("no store", lambda: _helpers(api)[0].operations.create_order.enqueue(body=queue.order))
    rows = {
        "zero entries": lambda: protocols.QueueOptions(max_entries=0),
        "boolean deliveries": lambda: protocols.QueueOptions(max_deliveries=True),
        "long delivery": lambda: protocols.QueueOptions(max_delivery_timeout=301),
        "infinite ttl": lambda: protocols.QueueOptions(entry_ttl=float("inf")),
        "no fields": lambda: queue.errors.QueuePolicyConflictError(fields=()),
        "resolved zero": lambda: protocols.ResolvedQueueOptions(
            max_entries=0,
            parallelism=1,
            max_entry_body_bytes=1,
            max_deliveries=1,
            entry_ttl=1,
            retry_initial_delay=1,
            retry_max_delay=1,
            lease_min=1,
            lease_grace=1,
            max_delivery_timeout=1,
        ),
        "resolved long": lambda: protocols.ResolvedQueueOptions(
            max_entries=1,
            parallelism=1,
            max_entry_body_bytes=1,
            max_deliveries=1,
            entry_ttl=1,
            retry_initial_delay=1,
            retry_max_delay=1,
            lease_min=1,
            lease_grace=1,
            max_delivery_timeout=301,
        ),
        "resolved order": lambda: protocols.ResolvedQueueOptions(
            max_entries=1,
            parallelism=1,
            max_entry_body_bytes=1,
            max_deliveries=1,
            entry_ttl=1,
            retry_initial_delay=2,
            retry_max_delay=1,
            lease_min=1,
            lease_grace=1,
            max_delivery_timeout=1,
        ),
    }
    for label, build in rows.items():
        step(label, build)


def _memory_stores(queue: _Queues) -> None:
    """Lease ready entries in order, refuse when full, and purge only ended entries in builtin stores."""
    lines, step, protocols = queue.lines, queue.step, queue.protocols
    lines.append("memory stores")
    store = protocols.MemoryQueueStore(max_entries=2, max_bytes=64)
    now = datetime.now(timezone.utc)
    policy = protocols.ResolvedQueueOptions(
        max_entries=1,
        parallelism=1,
        max_entry_body_bytes=8,
        max_deliveries=1,
        entry_ttl=1,
        retry_initial_delay=1,
        retry_max_delay=1,
        lease_min=1,
        lease_grace=1,
        max_delivery_timeout=1,
    )

    def entry(entry_id: str, *, payload: bytes = b"{}", created: int = 0) -> Any:
        return protocols.QueueEntry(
            entry_id=entry_id,
            version="",
            operation_alias="refresh",
            helper_fingerprint="",
            security_fingerprint="",
            payload=payload,
            blob=None,
            blob_owned=False,
            idempotency_key=None,
            created_at=now + timedelta(seconds=created),
            expires_at=now + timedelta(days=1),
            not_before=now,
            saved_wait_seconds=0,
            state="pending",
            delivery_count=0,
            send_intent=False,
            cancel_requested=False,
            lease_id=None,
            lease_until=None,
            policy=policy,
            result=None,
        )

    step("put b", lambda: store.put(entry("b", created=1)))
    step("put a", lambda: store.put(entry("a")))
    step("put full", lambda: store.put(entry("c")))
    step("put again", lambda: store.put(entry("a")))
    step("put other", lambda: store.put("entry"))
    leases = store.claim(now=now, lease_until=now + timedelta(seconds=5), limit=5)
    lines.append(
        f"  claimed {[lease.entry.entry_id for lease in leases]} lease hidden {'lease_id' not in repr(leases[0])}"
    )
    step("claim none", lambda: store.claim(now=now, lease_until=now, limit=1))
    step("claim zero", lambda: store.claim(now=now, lease_until=now, limit=0))
    intended = store.get("b")
    step(
        "save b intent",
        lambda: store.compare_exchange("b", intended.version, replace(intended, send_intent=True, delivery_count=1)),
    )
    recovered = store.claim(now=now + timedelta(seconds=6), lease_until=now + timedelta(seconds=9), limit=1)
    lines.append(f"  recovered {[lease.entry.entry_id for lease in recovered]}")
    recovered_intent = store.get("b")
    lines.append(
        f"  recovered intent {recovered_intent.state} count={recovered_intent.delivery_count} "
        f"intent={recovered_intent.send_intent} lease={recovered_intent.lease_id}"
    )
    current = store.get("a")
    step("exchange stale", lambda: store.compare_exchange("a", "stale", current))
    step("exchange other lease", lambda: store.compare_exchange("a", current.version, replace(current, lease_id="x")))
    step("exchange mismatch", lambda: store.compare_exchange("b", current.version, current))
    step("exchange missing", lambda: store.compare_exchange("z", current.version, replace(current, entry_id="z")))
    done = replace(current, state="succeeded", lease_id=None, lease_until=None)
    step("exchange done", lambda: store.compare_exchange("a", current.version, done))
    step("purge", lambda: [item.entry_id for item in store.purge_terminal(now + timedelta(seconds=1))])
    step("get missing", lambda: store.get("a"))
    bounded = protocols.MemoryQueueStore(max_bytes=4)
    step("bytes full", lambda: bounded.put(entry("x", payload=b"12345")))
    step("bad limits", lambda: protocols.MemoryQueueStore(max_entries=0))
    rows = {
        "naive instant": lambda: replace(entry("n"), created_at=datetime(2030, 1, 1)),  # ruff: ignore[call-datetime-without-tzinfo]
        "lease pair": lambda: replace(entry("n"), lease_id="l"),
        "state": lambda: replace(entry("n"), state="lost"),
        "payload": lambda: replace(entry("n"), payload="{}"),
        "wait": lambda: replace(entry("n"), saved_wait_seconds=-1),
        "wait type": lambda: replace(entry("n"), saved_wait_seconds="0"),
        "count": lambda: replace(entry("n"), delivery_count=True),
        "outcome": lambda: protocols.QueueOutcome(category="later"),
        "outcome response": lambda: protocols.QueueOutcome(category="success", response="200"),
        "outcome instant": lambda: protocols.QueueOutcome(category="retryable", retry_at="soon"),
        "blob": lambda: protocols.BlobRef(size=1, sha256=b"1", key="k"),
        "blob key": lambda: protocols.BlobRef(size=1, sha256=bytes(32), key=""),
        "blob digest": lambda: protocols.BlobRef(size=1, sha256="digest", key="k"),
        "with blob": lambda: replace(entry("n"), blob=protocols.BlobRef(size=1, sha256=bytes(32), key="k")),
        "wrong blob": lambda: replace(entry("n"), blob="ref"),
        "wrong policy": lambda: replace(entry("n"), policy=None),
        "wrong result": lambda: replace(entry("n"), result="done"),
        "lease of pending": lambda: protocols.QueueLease(entry=entry("n"), lease_id="l"),
        "report": lambda: protocols.DrainReport(succeeded=["a"]),
        "receipt": lambda: protocols.QueueReceipt(entry_id="a", state="pending", created_at=now, expires_at="later"),
    }
    for label, build in rows.items():
        step(label, build)
    report = protocols.DrainReport(succeeded=("a",), dead=("b", "c"))
    lines.append(f"  report counts {dict(report.counts)}")


async def _async_queues(queue: _Queues) -> None:  # ruff: ignore[too-many-locals]
    """Enqueue, drain in parallel tasks, cancel a drain's task, and manage entries with asyncio."""
    lines, astep, server = queue.lines, queue.astep, queue.server
    lines.append("asyncio")
    store = _Store(queue.protocols)
    shared = _AsyncStore(store)
    defaults = queue.protocols.ProtocolDefaults(options=queue.protocols.QueueOptions(parallelism=2))
    async with (
        queue.exchange.async_client() as native,
        queue.package.AsyncClient(
            http_client=native, options=queue.settings(shared, defaults={"orders.outbox": defaults})
        ) as api,
    ):
        outbox = _helpers(api)[0]
        first = await astep("enqueue", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        for index in range(2):
            await outbox.operations.refresh.enqueue(order_id=queue.argument("GetOrder", "path", "orderId", f"a{index}"))
        await astep("drain", outbox.drain)
        server.flush(lines, ordered=False)
        await astep("inspect", lambda: outbox.inspect(first.entry_id))
        await astep("purge", lambda: outbox.purge_terminal(queue.time.now() + timedelta(seconds=1)))
        lost = await astep("lost", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        queue.exchange.respond(injected(server.lost))
        await astep("drain lost", outbox.drain)
        server.flush(lines)
        await astep("retry unknown", lambda: outbox.retry_unknown(lost.entry_id))
        await astep("cancel", lambda: outbox.cancel(lost.entry_id))
        broken = [
            await outbox.operations.refresh.enqueue(order_id=queue.argument("GetOrder", "path", "orderId", f"x{index}"))
            for index in range(2)
        ]
        for receipt in broken:
            store.tamper(receipt.entry_id, helper_fingerprint="0" * 64)
        await astep("broken wave", outbox.drain)
        for receipt in broken:
            store.entries.pop(receipt.entry_id)
        store.failures["put"] = OSError("disk full")
        await astep("put fails", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        held = await astep("held", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        arrived, released = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()

        def hold(request: httpx2.Request) -> httpx2.Response:
            loop.call_soon_threadsafe(arrived.set)
            released.wait(10)
            return server(request)

        queue.exchange.respond(hold)
        task = asyncio.ensure_future(outbox.drain())
        await arrived.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            lines.append("  drain cancelled while sending")
        released.set()
        await astep("inspect held", lambda: outbox.inspect(held.entry_id))
        await astep("retry held", lambda: outbox.retry_unknown(held.entry_id))
        queue.time.advance(400)
        await astep("redelivered", outbox.drain)
        server.flush(lines, ordered=False)
        unsaved = await astep("held unsaved", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        arrived, released = asyncio.Event(), threading.Event()
        queue.exchange.respond(hold)
        task = asyncio.ensure_future(outbox.drain())
        await arrived.wait()
        store.failures["compare_exchange"] = OSError("write")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError as cancelled:
            lines.append(f"  drain cancelled with a failed save notes={len(getattr(cancelled, '__notes__', ()))}")
        released.set()
        await astep("inspect unsaved", lambda: outbox.inspect(unsaved.entry_id))
        store.entries.pop(unsaved.entry_id)
        server.flush(lines, ordered=False)
        failing_entry = await astep("intent entry", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        store.failures["compare_exchange"] = OSError("write")
        await astep("intent fails", outbox.drain)
        await astep("entry type", lambda: outbox.inspect(5))
        store.entries.pop(failing_entry.entry_id)
        store.failures["put"] = queue.errors.QueueFullError(kind="bytes", limit=1, observed=2)
        await astep("put full", lambda: outbox.operations.create_order.enqueue(body=queue.order))
        store.wrong["claim"] = "leases"
        await astep("claim wrong", outbox.drain)
        soon = queue.protocols.QueueOptions(retry_initial_delay=1e-9, retry_max_delay=1e-9)
        await astep("ready again", lambda: outbox.operations.create_order.enqueue(body=queue.order, queue_options=soon))
        broken = await astep(
            "broken",
            lambda: outbox.operations.refresh.enqueue(order_id=queue.argument("GetOrder", "path", "orderId", "h2")),
        )
        store.tamper(broken.entry_id, helper_fingerprint="0" * 64)
        queue.exchange.respond(queue.stepping(503))
        store.later["compare_exchange"] = (3, OSError("release"))
        single = queue.protocols.QueueOptions(parallelism=1)
        await astep("release fails", lambda: outbox.drain(queue_options=single))
        server.flush(lines, ordered=False)
    stubborn = _Stubborn(queue.protocols)
    async with (
        queue.exchange.async_client() as native,
        queue.package.AsyncClient(http_client=native, options=queue.settings(_AsyncStore(stubborn))) as api,
    ):
        outbox = _helpers(api)[0]
        await astep(
            "ignored leases",
            lambda: outbox.operations.refresh.enqueue(order_id=queue.argument("GetOrder", "path", "orderId", "s2")),
        )
        await astep("drain bounded", lambda: outbox.drain(queue_options=queue.protocols.QueueOptions(max_entries=2)))
        server.flush(lines)
    memory = queue.protocols.AsyncMemoryQueueStore()
    async with (
        queue.exchange.async_client() as native,
        queue.package.AsyncClient(http_client=native, options=queue.settings(memory)) as api,
    ):
        outbox = _helpers(api)[0]
        entry = await astep(
            "memory enqueue",
            lambda: outbox.operations.refresh.enqueue(order_id=queue.argument("GetOrder", "path", "orderId", "m1")),
        )
        await astep("memory drain", outbox.drain)
        server.flush(lines)
        await astep("memory inspect", lambda: outbox.inspect(entry.entry_id))
        await astep("memory purge", lambda: outbox.purge_terminal(queue.time.now() + timedelta(seconds=1)))

        intended = await astep(
            "async recovery enqueue", lambda: outbox.operations.create_order.enqueue(body=queue.order)
        )
        now = queue.time.now()
        leases = await memory.claim(now=now, lease_until=now + timedelta(seconds=1), limit=1)
        leased = leases[0].entry
        await astep(
            "async recovery intent",
            lambda: memory.compare_exchange(
                intended.entry_id, leased.version, replace(leased, send_intent=True, delivery_count=1)
            ),
        )
        queue.time.advance(2)
        await astep("async expired intent drain", outbox.drain)
        await astep("async recovered intent", lambda: outbox.inspect(intended.entry_id))
        server.flush(lines)
