"""Open, probe, close, and reset circuit breakers of grouped operations through generated clients over real TLS."""

from __future__ import annotations

import asyncio
import importlib
import io
import math
from collections import Counter
from typing import TYPE_CHECKING, Any

import httpx2

from tests.data.python.client_runtime import (
    Exchange,
    aoutcome,
    arecord,
    broken,
    failing,
    json_response,
    outcome,
    raw_response,
    record,
    run,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_UP = {"state": "up"}
_START = 1000.0


def _modules(package: ModuleType) -> tuple[Any, ...]:
    return tuple(
        importlib.import_module(f"{package.__name__}.{name}") for name in ("options", "protocols", "errors", "auth")
    )


def _down() -> Callable[[httpx2.Request], httpx2.Response]:
    return raw_response(503, b"down", "text/plain")


def _failures(lines: list[str], exchange: Exchange, call: Callable[[], object], label: str) -> None:
    """Fail a grouped call five times with 503 responses, reporting the classes of the failures."""
    outcomes = []
    for _ in range(5):
        exchange.respond(_down())
        outcomes.append(outcome(call).split(" ")[0])
    lines.append(f"  {label} {outcomes}")


def _snapshot(snapshot: Any) -> str:
    return (
        f"{snapshot.state} failures={snapshot.consecutive_failures} retry_at={snapshot.retry_at} "
        f"generation={snapshot.generation}"
    )


class _Clocked:
    """A borrowed circuit store at a clock the scenario sets, reporting each admission and outcome."""

    def __init__(self, store: Any, lines: list[str]) -> None:
        self.store = store
        self.lines = lines
        self.time = _START

    def admit(self, key: Any, *, now: float, options: Any) -> Any:
        del now
        permit = self.store.admit(key, now=self.time, options=options)
        self.lines.append(
            f"    admit {key.group} {key.credential_partition} {key.origin.host} "
            f"generation={permit.generation} probe={permit.probe} threshold={options.failure_threshold}"
        )
        return permit

    def record(self, permit: Any, outcome: str, *, now: float) -> None:
        del now
        self.lines.append(f"    record {outcome} generation={permit.generation} probe={permit.probe}")
        self.store.record(permit, outcome, now=self.time)

    def reset(self, key: Any) -> None:
        self.store.reset(key)

    def snapshot(self, key: Any) -> Any:
        return self.store.snapshot(key)


class _AsyncClocked:
    """The asynchronous counterpart of the clocked store."""

    def __init__(self, store: Any, lines: list[str]) -> None:
        self.store = store
        self.lines = lines
        self.time = _START

    async def admit(self, key: Any, *, now: float, options: Any) -> Any:
        del now
        permit = await self.store.admit(key, now=self.time, options=options)
        self.lines.append(f"    admit {key.group} generation={permit.generation} probe={permit.probe}")
        return permit

    async def record(self, permit: Any, outcome: str, *, now: float) -> None:
        del now
        self.lines.append(f"    record {outcome} generation={permit.generation} probe={permit.probe}")
        await self.store.record(permit, outcome, now=self.time)

    async def reset(self, key: Any) -> None:
        await self.store.reset(key)

    async def snapshot(self, key: Any) -> Any:
        return await self.store.snapshot(key)


class _Failing:
    """A circuit store whose chosen methods raise a failure or return a wrong value, wrapping a memory store."""

    def __init__(self, store: Any, failures: dict[str, BaseException | str]) -> None:
        self.store = store
        self.failures = failures

    def _failed(self, name: str) -> object:
        failure = self.failures.get(name)
        if isinstance(failure, BaseException):
            raise failure
        return failure

    def admit(self, key: Any, *, now: float, options: Any) -> Any:
        return self._failed("admit") or self.store.admit(key, now=now, options=options)

    def record(self, permit: Any, outcome: str, *, now: float) -> None:
        self._failed("record")
        self.store.record(permit, outcome, now=now)

    def reset(self, key: Any) -> None:
        self._failed("reset")
        self.store.reset(key)

    def snapshot(self, key: Any) -> Any:
        return self.store.snapshot(key)


class _AsyncFailing(_Failing):
    """The asynchronous counterpart of the failing store."""

    async def admit(self, key: Any, *, now: float, options: Any) -> Any:  # type: ignore[override]
        return self._failed("admit") or await self.store.admit(key, now=now, options=options)

    async def record(self, permit: Any, outcome: str, *, now: float) -> None:  # type: ignore[override]
        self._failed("record")
        await self.store.record(permit, outcome, now=now)

    async def reset(self, key: Any) -> None:  # type: ignore[override]
        self._failed("reset")
        await self.store.reset(key)

    async def snapshot(self, key: Any) -> Any:  # type: ignore[override]
        return await self.store.snapshot(key)


class _OnAttempt:
    """A hook that runs an action when a call's first attempt starts, after the call passed its circuit."""

    def __init__(self, action: Callable[[], object]) -> None:
        self.action = action

    def on_event(self, event: Any) -> None:
        if event.name == "attempt_start":
            self.action()


class _Blocking:
    """An asynchronous memory store whose record waits for the scenario, so a call can be cancelled while it records."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.entered = asyncio.Event()
        self.gate = asyncio.Event()
        self.recorded = asyncio.Event()

    async def admit(self, key: Any, *, now: float, options: Any) -> Any:
        return await self.store.admit(key, now=now, options=options)

    async def record(self, permit: Any, outcome: str, *, now: float) -> None:
        self.entered.set()
        await self.gate.wait()
        await self.store.record(permit, outcome, now=now)
        self.recorded.set()

    async def reset(self, key: Any) -> None:
        await self.store.reset(key)

    async def snapshot(self, key: Any) -> Any:
        return await self.store.snapshot(key)


class _Partial:
    """A circuit store without a snapshot method."""

    def admit(self, key: Any, *, now: float, options: Any) -> Any:
        raise NotImplementedError

    def record(self, permit: Any, outcome: str, *, now: float) -> None:
        raise NotImplementedError

    def reset(self, key: Any) -> None:
        raise NotImplementedError


class _Harness:
    """Build clients of one package whose breakers use given stores, partitions, and retries."""

    def __init__(self, package: ModuleType, exchange: Exchange) -> None:
        self.package = package
        self.exchange = exchange
        self.options, self.protocols, self.errors, self.auth = _modules(package)
        self.origin = self.protocols.Origin(scheme="https", host="api.example.com", port=443)

    def settings(
        self,
        store: object = None,
        *,
        partition: str | None = None,
        enabled: bool = True,
        retries: int = 0,
        threshold: int = 5,
        **options: Any,
    ) -> Any:
        protocols = self.protocols
        security = None if partition is None else protocols.ProtocolSecurityContext(credential_partition=partition)
        breaker = protocols.CircuitBreakerOptions(enabled=enabled, failure_threshold=threshold)
        return self.options.ClientOptions(
            retry=self.options.RetryOptions(max_retries=retries, initial_delay=0),
            protocols=self.options.ProtocolClientOptions(circuit=breaker, circuit_store=store, security=security),
            **options,
        )

    def client(self, store: object = None, **settings: Any) -> Any:
        return self.package.Client(
            http_client=self.exchange.client(), http_client_ownership="owned", options=self.settings(store, **settings)
        )

    def async_client(self, store: object = None, **settings: Any) -> Any:
        return self.package.AsyncClient(
            http_client=self.exchange.async_client(),
            http_client_ownership="owned",
            options=self.settings(store, **settings),
        )

    def key(self, group: str = "backend", partition: str = "anonymous") -> Any:
        return self.protocols.CircuitKey(origin=self.origin, credential_partition=partition, group=group)


def _values(harness: _Harness, lines: list[str]) -> None:
    protocols, options = harness.protocols, harness.options
    breaker = protocols.CircuitBreakerOptions
    for label, build in (
        ("default", breaker),
        ("enabled int", lambda: breaker(enabled=1)),
        ("threshold zero", lambda: breaker(failure_threshold=0)),
        ("threshold bool", lambda: breaker(failure_threshold=True)),
        ("cooldown zero", lambda: breaker(cooldown=0)),
        ("cooldown infinite", lambda: breaker(cooldown=math.inf)),
        ("cooldown text", lambda: breaker(cooldown="30")),
        ("resolved", lambda: breaker(enabled=True, failure_threshold=2, cooldown=5).resolved()),
        ("protocols circuit", lambda: options.ProtocolClientOptions(circuit="on")),
        ("key", harness.key),
        (
            "key origin",
            lambda: protocols.CircuitKey(origin="https://api.example.com", credential_partition="a", group="g"),
        ),
        ("key partition", lambda: protocols.CircuitKey(origin=harness.origin, credential_partition=1, group="g")),
        ("permit", lambda: protocols.CircuitPermit(key=harness.key(), generation=0, probe=False, permit_id="p")),
        (
            "permit generation",
            lambda: protocols.CircuitPermit(key=harness.key(), generation=-1, probe=False, permit_id="p"),
        ),
        ("permit probe", lambda: protocols.CircuitPermit(key=harness.key(), generation=0, probe=1, permit_id="p")),
        ("permit key", lambda: protocols.CircuitPermit(key="k", generation=0, probe=False, permit_id="p")),
        (
            "snapshot",
            lambda: protocols.CircuitSnapshot(state="open", consecutive_failures=5, retry_at=3.5, generation=1),
        ),
        (
            "snapshot state",
            lambda: protocols.CircuitSnapshot(state="ajar", consecutive_failures=0, retry_at=None, generation=0),
        ),
        (
            "snapshot retry_at",
            lambda: protocols.CircuitSnapshot(state="open", consecutive_failures=0, retry_at="soon", generation=0),
        ),
        (
            "snapshot counts",
            lambda: protocols.CircuitSnapshot(state="closed", consecutive_failures=-1, retry_at=None, generation=0),
        ),
        ("open error", lambda: harness.errors.CircuitOpenError(key=harness.key(), retry_at=2.5)),
        ("open error key", lambda: harness.errors.CircuitOpenError(key="backend", retry_at=2.5)),
        ("store error", lambda: harness.errors.CircuitStoreError(action="admit")),
    ):
        record(lines, f"value {label}", build)
    error = harness.errors.CircuitOpenError(key=harness.key(), retry_at=2.5)
    lines.append(f"  open error fields {error.key.group} {error.retry_at} {error.helper_id}")
    store = protocols.MemoryCircuitStore()
    once, probed = breaker(failure_threshold=1, cooldown=1).resolved(), protocols.MemoryCircuitStore()
    probed.record(probed.admit(harness.key(), now=0.0, options=once), "failure", now=0.0)
    probe = probed.admit(harness.key(), now=1.0, options=once)
    probed.record(probe, "neutral", now=1.0)
    probed.record(probe, "success", now=1.0)
    lines.append(f"  store repeated probe {_snapshot(probed.snapshot(harness.key()))}")
    running = probed.admit(harness.key(), now=2.0, options=once)
    record(lines, "store probe running", lambda: probed.admit(harness.key(), now=60.0, options=once).probe)
    probed.record(running, "success", now=60.0)
    lines.append(f"  store probe closed {_snapshot(probed.snapshot(harness.key()))}")
    for label, build in (
        ("store admit key", lambda: store.admit("k", now=0.0, options=breaker().resolved())),
        ("store record permit", lambda: store.record("p", "failure", now=0.0)),
        ("store reset key", lambda: store.reset("k")),
        ("store snapshot key", lambda: store.snapshot("k")),
        ("store unknown", lambda: _snapshot(store.snapshot(harness.key()))),
        (
            "store foreign permit",
            lambda: store.record(
                protocols.CircuitPermit(key=harness.key(), generation=0, probe=True, permit_id="x"), "failure", now=0.0
            ),
        ),
    ):
        record(lines, label, build)


def _construction(harness: _Harness, lines: list[str]) -> None:
    protocols = harness.protocols
    for label, store, asynchronous in (
        ("partial store", _Partial(), False),
        ("async store for sync client", protocols.AsyncMemoryCircuitStore(), False),
        ("sync store for async client", protocols.MemoryCircuitStore(), True),
    ):
        build = harness.async_client if asynchronous else harness.client
        record(lines, label, lambda build=build, store=store: build(store).close())
    disabled = _Clocked(protocols.MemoryCircuitStore(), lines)
    with harness.client(disabled, enabled=False) as api:
        for index in range(6):
            harness.exchange.respond(_down())
            record(lines, f"disabled {index}", lambda api=api: api.backend.get_status())
        record(lines, "disabled reset", lambda: api.reset_circuit("backend", origin=harness.origin))
        record(lines, "unknown group", lambda: api.reset_circuit("payments", origin=harness.origin))
        record(lines, "group type", lambda: api.reset_circuit(5, origin=harness.origin))
        record(lines, "origin type", lambda: api.reset_circuit("backend", origin="https://api.example.com"))


def _opening(harness: _Harness, lines: list[str]) -> None:
    exchange, protocols = harness.exchange, harness.protocols
    store = _Clocked(protocols.MemoryCircuitStore(), lines)
    with harness.client(store) as api:
        for index in range(5):
            exchange.respond(_down())
            record(lines, f"failure {index}", lambda api=api: api.backend.get_status())
        lines.append(f"  opened {_snapshot(store.snapshot(harness.key()))}")
        record(lines, "open", api.backend.get_status)
        try:
            api.backend.get_status()
        except harness.errors.CircuitOpenError as failure:
            lines.append(
                f"  open fields {failure.key.group} {failure.retry_at} sends={failure.network_send_count} "
                f"attempts={failure.resource_attempt_count}"
            )
        exchange.respond(json_response(200, _UP), json_response(200, _UP))
        record(lines, "ungrouped", api.backend.get_health)
        record(lines, "other group", api.search.search)
        exchange.respond(_down())
        record(lines, "raw request", lambda: api.request_raw("GET", "https://api.example.com/status").info.status_code)
        store.time = _START + 29.5
        record(lines, "cooling", api.backend.get_status)
        store.time = _START + 30
        exchange.respond(_down())
        record(lines, "probe failure", api.backend.get_status)
        lines.append(f"  reopened {_snapshot(store.snapshot(harness.key()))}")
        store.time = _START + 60
        exchange.respond(json_response(200, _UP))
        record(lines, "probe success", api.backend.get_status)
        lines.append(f"  closed {_snapshot(store.snapshot(harness.key()))}")


def _counting(harness: _Harness, lines: list[str]) -> None:
    exchange, protocols = harness.exchange, harness.protocols
    store = _Clocked(protocols.MemoryCircuitStore(), lines)
    with harness.client(store) as api:
        for index, responder in enumerate((*(_down(),) * 4, json_response(200, _UP), *(_down(),) * 4)):
            exchange.respond(responder)
            record(lines, f"reset count {index}", lambda api=api: api.backend.get_status())
        lines.append(f"  after reset {_snapshot(store.snapshot(harness.key()))}")
    store = _Clocked(protocols.MemoryCircuitStore(), lines)
    with harness.client(store, retries=2) as api:
        exchange.respond(_down(), _down(), _down())
        record(lines, "retried once", api.backend.get_status)
        lines.append(f"  retried {_snapshot(store.snapshot(harness.key()))}")
    store = _Clocked(protocols.MemoryCircuitStore(), lines)
    with harness.client(store, threshold=100) as api:
        for label, responder in (
            ("too many", raw_response(429, b"slow", "text/plain")),
            ("parse failure", raw_response(200, b"{", "application/json")),
            ("validation", json_response(200, {"state": 5})),
            ("missing", raw_response(404, b"gone", "text/plain")),
            ("bad gateway", raw_response(502, b"x", "text/plain")),
            ("internal", raw_response(500, b"x", "text/plain")),
            ("not implemented", raw_response(501, b"x", "text/plain")),
            ("gateway timeout", raw_response(504, b"x", "text/plain")),
            ("pool timeout", failing(httpx2.PoolTimeout)),
            ("connect error", failing(httpx2.ConnectError)),
            ("read error", failing(httpx2.ReadError)),
            ("broken body", broken),
        ):
            exchange.respond(responder)
            record(lines, f"classify {label}", lambda api=api: api.backend.get_status())
        exchange.respond(raw_response(503, b"down", "text/plain"))
        record(lines, "raw status", lambda: api.backend.with_raw_response.get_status().info.status_code)
        exchange.respond(json_response(200, _UP))
        with api.backend.with_streaming_response.get_status() as streamed:
            lines.append(f"  streaming status {streamed.info.status_code}")
        lines.append(f"  classified {_snapshot(store.snapshot(harness.key()))}")


def _resets(harness: _Harness, lines: list[str]) -> None:
    exchange, protocols, options = harness.exchange, harness.protocols, harness.options
    store = _Clocked(protocols.MemoryCircuitStore(), lines)
    with harness.client(store) as api:
        _failures(lines, exchange, api.backend.get_status, "opening")
        record(lines, "before reset", api.backend.get_status)
        api.reset_circuit("backend", origin=harness.origin)
        lines.append(f"  reset {_snapshot(store.snapshot(harness.key()))}")

        resetting = _OnAttempt(lambda: api.reset_circuit("backend", origin=harness.origin))
        exchange.respond(_down())
        view = api.with_options(options.RequestOptions(hooks=(resetting,)))
        record(lines, "stale failure", view.backend.get_status)
        lines.append(f"  stale {_snapshot(store.snapshot(harness.key()))}")
        _failures(lines, exchange, api.backend.get_status, "reopening")
        store.time = _START + 30
        token = options.CancelToken()
        hooks = (_OnAttempt(token.cancel),)
        cancelled = api.with_options(options.RequestOptions(hooks=hooks, cancel_token=token))
        record(lines, "cancelled probe", cancelled.backend.get_status)
        lines.append(f"  after cancel {_snapshot(store.snapshot(harness.key()))}")
        exchange.respond(json_response(200, _UP))
        record(lines, "next probe", api.backend.get_status)


def _partitions(harness: _Harness, lines: list[str]) -> None:
    exchange, protocols, auth = harness.exchange, harness.protocols, harness.auth
    shared = protocols.MemoryCircuitStore()
    with harness.client() as first, harness.client() as second, harness.client(shared) as third:
        _failures(lines, exchange, first.backend.get_status, "first")
        record(lines, "own store open", first.backend.get_status)
        exchange.respond(json_response(200, _UP))
        record(lines, "other client", second.backend.get_status)
        _failures(lines, exchange, third.backend.get_status, "third")
    with harness.client(shared) as fourth, harness.client(shared, partition="tenant-b") as fifth:
        record(lines, "shared store", fourth.backend.get_status)
        exchange.respond(json_response(200, _UP))
        record(lines, "other partition", fifth.backend.get_status)
    lines.append(
        f"  shared {shared.snapshot(harness.key()).state} {shared.snapshot(harness.key(partition='tenant-b')).state}"
    )
    token = auth.AuthConfig({"bearer": auth.StaticTokenProvider(auth.AccessToken("token-secret", scopes=None))})
    store = _Clocked(protocols.MemoryCircuitStore(), lines)
    with harness.client(store, auth=token) as anonymous:
        record(lines, "auth without partition", anonymous.backend.get_account)
        exchange.respond(json_response(200, _UP))
        record(lines, "ungrouped with auth", anonymous.backend.get_health)
    with harness.client(store, auth=token, partition="tenant-a") as partitioned:
        exchange.respond(json_response(200, _UP))
        record(lines, "auth with partition", partitioned.backend.get_account)
        other = auth.AuthConfig({"bearer": auth.StaticTokenProvider(auth.AccessToken("tenant-b", scopes=None))})
        foreign = partitioned.with_options(harness.options.RequestOptions(auth=other))
        record(lines, "other auth in a view", foreign.backend.get_account)
        exchange.respond(json_response(200, _UP))
        record(lines, "other auth ungrouped", foreign.backend.get_health)


def _store_failures(harness: _Harness, lines: list[str]) -> None:
    exchange, protocols, errors = harness.exchange, harness.protocols, harness.errors
    for label, failures, responder in (
        ("admit raises", {"admit": RuntimeError("store down")}, None),
        ("admit store error", {"admit": errors.CircuitStoreError(action="admit")}, None),
        ("admit wrong type", {"admit": "permit"}, None),
        ("record after success", {"record": RuntimeError("store down")}, json_response(200, _UP)),
        ("record after failure", {"record": RuntimeError("store down")}, _down()),
        ("record open", {"record": errors.CircuitOpenError(key=harness.key(), retry_at=1.0)}, json_response(200, _UP)),
    ):
        store = _Failing(protocols.MemoryCircuitStore(), failures)
        with harness.client(store) as api:
            if responder is not None:
                exchange.respond(responder)
            record(lines, label, lambda api=api: api.backend.get_status())
            try:
                exchange.respond(*(() if responder is None else (responder,)))
                api.backend.get_status()
            except errors.SDKError as error:
                lines.append(f"  {label} secondary {[type(item).__name__ for item in error.secondary_errors]}")
    store = _Failing(protocols.MemoryCircuitStore(), {"record": RuntimeError("store down")})
    with harness.client(store) as api:
        exchange.respond(json_response(200, _UP))
        record(lines, "raw record failure", api.backend.with_raw_response.get_status)
    store = _Failing(protocols.MemoryCircuitStore(), {"reset": RuntimeError("store down")})
    with harness.client(store) as api:
        record(lines, "reset failure", lambda: api.reset_circuit("backend", origin=harness.origin))


async def _cancelled_record(harness: _Harness, lines: list[str]) -> None:
    exchange = harness.exchange
    bodies = importlib.import_module(f"{harness.package.__name__}.bodies")
    store = _Blocking(harness.protocols.AsyncMemoryCircuitStore())
    file = io.BytesIO(b"upload")
    async with harness.async_client(store) as api:
        exchange.respond(_down())
        task = asyncio.create_task(api.backend.upload(body=bodies.AsyncFileBody(file, ownership="owned")))
        await store.entered.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            lines.append(f"  cancelled while recording, file closed {file.closed}")
        store.gate.set()
        await store.recorded.wait()
        lines.append(f"  recorded after the cancel {_snapshot(await store.snapshot(harness.key()))}")


async def _async_circuits(harness: _Harness, lines: list[str]) -> None:
    exchange, protocols = harness.exchange, harness.protocols
    store = _AsyncClocked(protocols.AsyncMemoryCircuitStore(), lines)
    async with harness.async_client(store) as api:
        for index in range(5):
            exchange.respond(_down())
            await arecord(lines, f"async failure {index}", api.backend.get_status)
        await arecord(lines, "async open", api.backend.get_status)
        store.time = _START + 30
        exchange.respond(json_response(200, _UP))
        results = await asyncio.gather(*(api.backend.get_status() for _ in range(20)), return_exceptions=True)
        lines.extend((
            f"  async probes {sorted(Counter(type(result).__name__ for result in results).items())}",
            f"  async closed {_snapshot(await store.snapshot(harness.key()))}",
        ))
        for _ in range(5):
            exchange.respond(_down())
            lines.append(f"  async reopen {await aoutcome(api.backend.get_status)}")
        await api.reset_circuit("backend", origin=harness.origin)
        lines.append(f"  async reset {_snapshot(await store.snapshot(harness.key()))}")
        exchange.respond(raw_response(503, b"down", "text/plain"))
        raw = await api.backend.with_raw_response.get_status()
        lines.append(f"  async raw {raw.info.status_code} {_snapshot(await store.snapshot(harness.key()))}")
    async with harness.async_client() as api:
        exchange.respond(json_response(200, _UP), json_response(200, _UP))
        await arecord(lines, "async default store", api.backend.get_status)
        await arecord(lines, "async ungrouped", api.backend.get_health)
        await api.reset_circuit("backend", origin=harness.origin)
    async with harness.async_client(enabled=False) as api:
        await arecord(lines, "async disabled reset", lambda: api.reset_circuit("backend", origin=harness.origin))
    for label, failures, responder in (
        ("async admit raises", {"admit": RuntimeError("store down")}, None),
        ("async admit wrong type", {"admit": "permit"}, None),
        ("async record after success", {"record": RuntimeError("store down")}, json_response(200, _UP)),
        ("async record after failure", {"record": RuntimeError("store down")}, _down()),
        ("async raw record failure", {"record": RuntimeError("store down")}, json_response(200, _UP)),
        ("async reset failure", {"reset": RuntimeError("store down")}, None),
    ):
        async with harness.async_client(_AsyncFailing(protocols.AsyncMemoryCircuitStore(), failures)) as api:
            if responder is not None:
                exchange.respond(responder)
            if label.endswith("reset failure"):
                call = lambda api=api: api.reset_circuit("backend", origin=harness.origin)  # ruff: ignore[lambda-assignment]
            elif "raw" in label:
                call = lambda api=api: api.backend.with_raw_response.get_status()  # ruff: ignore[lambda-assignment]
            else:
                call = lambda api=api: api.backend.get_status()  # ruff: ignore[lambda-assignment]
            await arecord(lines, label, call)
    store = _AsyncClocked(protocols.AsyncMemoryCircuitStore(), lines)
    async with harness.async_client(store) as api:
        for _ in range(5):
            exchange.respond(_down())
            lines.append(f"  async cancel setup {await aoutcome(api.backend.get_status)}")
        store.time = _START + 30
        token = harness.options.CancelToken()
        hooks = (_OnAttempt(token.cancel),)
        cancelled = api.with_options(harness.options.RequestOptions(hooks=hooks, cancel_token=token))
        await arecord(lines, "async cancelled probe", cancelled.backend.get_status)
        lines.append(f"  async after cancel {_snapshot(await store.snapshot(harness.key()))}")


def _clock(harness: _Harness, lines: list[str]) -> None:
    exchange, options = harness.exchange, harness.options
    times = [_START]
    clock = options.Clock(monotonic=lambda: times[0])
    with harness.client(threshold=1, clock=clock) as api:
        exchange.respond(_down())
        record(lines, "clocked failure", api.backend.get_status)
        try:
            api.backend.get_status()
        except harness.errors.CircuitOpenError as failure:
            lines.append(f"  clocked open retry_at={failure.retry_at}")
        times[0] = _START + 29.5
        record(lines, "clocked cooling", api.backend.get_status)
        times[0] = _START + 30
        exchange.respond(json_response(200, _UP))
        record(lines, "clocked probe", api.backend.get_status)


def circuits(package: ModuleType, lines: list[str]) -> None:
    """Drive circuit breakers through every state, outcome, store, and partition, then the asyncio client."""
    exchange = Exchange(lines)
    harness = _Harness(package, exchange)
    for step in (_values, _construction, _opening, _counting, _resets, _partitions, _store_failures, _clock):
        lines.append(f"# {step.__name__.strip('_')}")
        step(harness, lines)
    lines.append("# async")
    run(lambda: _async_circuits(harness, lines))
    run(lambda: _cancelled_record(harness, lines))
