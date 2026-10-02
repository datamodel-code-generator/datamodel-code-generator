"""Replay crash journals through public queue APIs and observe independent server effects in both modes."""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from functools import partial
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any

from tests.data.python.client_queues import _AsyncStore, _Crash, _Queues, _Static, _Store, _Time
from tests.data.python.client_runtime import run

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_INPUT = Path(__file__).parents[1] / "generation_platform/client/queue-recovery-inputs.json"


class _Journal(_Store):
    """An explicit test restart adapter that writes public records to an external JSON journal."""

    def reopened(self, path: Path) -> _Journal:
        """Persist the public record fields and restore a fresh adapter without the old owner's live state."""
        rows = []
        for entry in self.entries.values():
            row = {name: getattr(entry, name) for name in entry.__dataclass_fields__}
            for name in ("created_at", "expires_at", "not_before", "lease_until"):
                row[name] = None if row[name] is None else row[name].isoformat()
            row["payload"] = row["payload"].hex()
            row["policy"] = asdict(entry.policy)
            row["result"] = None if entry.result is None else {"category": entry.result.category}
            rows.append(row)
        path.write_text(json.dumps(rows), encoding="utf-8")
        fresh = _Journal(self.protocols)
        for row in json.loads(path.read_text(encoding="utf-8")):
            for name in ("created_at", "expires_at", "not_before", "lease_until"):
                row[name] = None if row[name] is None else datetime.fromisoformat(row[name])
            row["payload"] = bytes.fromhex(row["payload"])
            row["policy"] = self.protocols.ResolvedQueueOptions(**row["policy"])
            row["result"] = None if row["result"] is None else self.protocols.QueueOutcome(**row["result"])
            fresh.entries[row["entry_id"]] = fresh._next(self.protocols.QueueEntry(**row))
        return fresh


async def _value(call: Callable[[], Any]) -> Any:
    result = call()
    return await result if inspect.isawaitable(result) else result


class _WallClock(_Time):
    """Keep monotonic time progressing while explicitly adjusting the injected UTC clock."""

    wall_shift = 0.0

    def time(self) -> float:
        return super().time() + self.wall_shift


class _Provider(_Static):
    """A public credential provider whose callback can expire or cancel the queued child."""

    def __init__(self, queue: _Queues, asynchronous: bool, during: Callable[[], None]) -> None:
        super().__init__(queue.auth, "refreshed-secret")
        self.during, self.calls = during, 0
        if asynchronous:
            self.get = self.aget

    def get(self, context: object) -> object:
        self.calls += 1
        self.during()
        return super().get(context)

    async def aget(self, context: object) -> object:
        self.calls += 1
        self.during()
        return super().get(context)


@asynccontextmanager
async def _native(queue: _Queues, asynchronous: bool) -> Any:
    if asynchronous:
        async with queue.exchange.async_client() as native:
            yield native
    else:
        with queue.exchange.client() as native:
            yield native


async def _matrix(package: ModuleType, lines: list[str], asynchronous: bool, path: Path) -> None:  # ruff: ignore[too-many-branches, too-many-locals]
    queue = _Queues(package, lines)
    inputs = json.loads(_INPUT.read_text(encoding="utf-8"))
    lines.append("async" if asynchronous else "sync")
    client = package.AsyncClient if asynchronous else package.Client

    def api(store: _Store, **settings: Any) -> Any:
        options = queue.settings(_AsyncStore(store) if asynchronous else store)
        return client(http_client=entered, options=replace(options, **settings))

    async def observed(label: str, call: Callable[[], Any]) -> Any:
        try:
            value = await _value(call)
        except Exception as error:  # ruff: ignore[blind-except]
            lines.append(f"  {label}: {type(error).__name__}")
            return None
        if isinstance(value, queue.protocols.DrainReport):
            shown = {name: count for name, count in value.counts.items() if count}
        elif isinstance(value, queue.protocols.QueueEntry):
            outcome = value.result
            shown = f"{value.state} count={value.delivery_count} intent={value.send_intent} outcome=" + (
                "none" if outcome is None else f"{outcome.category}/{outcome.error_code}"
            )
        else:
            shown = value
        lines.append(f"  {label}: {shown}")
        return value

    async def shut(instance: Any) -> None:
        await _value(instance.aclose if asynchronous else instance.close)

    async with _native(queue, asynchronous) as entered:
        for mode in inputs["crashes"]:
            store = _Journal(queue.protocols)
            instance = api(store)
            outbox = instance.protocols.orders.outbox
            receipt = await _value(partial(outbox.operations.create_order.enqueue, body=queue.order))
            original = store.entries[receipt.entry_id]
            effects = len(queue.server.orders)
            if mode == "before_intent":
                now = queue.time.now()
                store.claim(now=now, lease_until=now + timedelta(seconds=30), limit=1)
            else:

                def crash(entry: Any, store: _Store = store) -> None:
                    if entry.send_intent:
                        store.failures["get"] = _Crash()

                if mode == "after_intent":
                    store.before["compare_exchange"] = crash
                else:
                    queue.server.during = partial(store.failures.__setitem__, "compare_exchange", _Crash())
                try:
                    await _value(outbox.drain)
                except _Crash:
                    lines.append(f"  {mode}: interrupted")
            stale = store.entries[receipt.entry_id]
            await shut(instance)
            fresh = store.reopened(path)
            queue.time.advance(inputs["lease_seconds"])
            instance = api(fresh)
            outbox = instance.protocols.orders.outbox
            await observed(f"{mode} drain", outbox.drain)
            recovered = await observed(f"{mode} entry", partial(outbox.inspect, receipt.entry_id))
            lines.extend((
                (
                    f"  {mode} identity: key={recovered.idempotency_key == original.idempotency_key} "
                    f"payload={recovered.payload == original.payload} "
                    f"time={recovered.created_at == original.created_at}"
                ),
                f"  {mode} effects: {len(queue.server.orders) - effects}",
                f"  {mode} stale CAS: {fresh.compare_exchange(receipt.entry_id, stale.version, stale)}",
            ))
            await observed(f"{mode} recorded success drain", outbox.drain)
            queue.server.flush(lines)
            await shut(instance)

        store = _Journal(queue.protocols)
        instance = api(store)
        outbox = instance.protocols.orders.outbox
        receipt = await _value(
            partial(outbox.operations.refresh.enqueue, order_id=queue.argument("GetOrder", "path", "orderId", "safe"))
        )
        original = store.entries[receipt.entry_id]
        now = queue.time.now()
        lease = store.claim(now=now, lease_until=now + timedelta(seconds=30), limit=1)[0]
        store.compare_exchange(
            receipt.entry_id, lease.entry.version, replace(lease.entry, send_intent=True, delivery_count=1)
        )
        await shut(instance)
        fresh = store.reopened(path)
        queue.time.advance(inputs["lease_seconds"])
        instance = api(fresh)
        outbox = instance.protocols.orders.outbox
        await observed("unkeyed crash drain", outbox.drain)
        recovered = await observed("unkeyed crash entry", partial(outbox.inspect, receipt.entry_id))
        lines.append(
            f"  unkeyed identity: key={recovered.idempotency_key} "
            f"payload={recovered.payload == original.payload} time={recovered.created_at == original.created_at}"
        )
        queue.server.flush(lines)
        await shut(instance)

        for mode in inputs["stops"]:
            store = _Store(queue.protocols)
            instance = api(store)
            outbox = instance.protocols.orders.outbox
            receipt = await _value(partial(outbox.operations.create_order.enqueue, body=queue.order))
            store.tamper(receipt.entry_id, send_intent=True, delivery_count=1)
            entry = store.entries[receipt.entry_id]
            if mode == "expiry":
                queue.time.advance(3601)
            elif mode == "cap":
                store.tamper(receipt.entry_id, delivery_count=entry.policy.max_deliveries)
            elif mode == "payload":
                store.tamper(receipt.entry_id, payload=b"{")
            elif mode == "missing_key":
                store.tamper(receipt.entry_id, idempotency_key=None)
            elif mode == "changed_key":
                store.tamper(receipt.entry_id, idempotency_key="replacement-key")
            elif mode == "future":
                store.tamper(receipt.entry_id, created_at=queue.time.now() + timedelta(seconds=100))
            else:
                store.tamper(receipt.entry_id, cancel_requested=True)
            await observed(f"{mode} stop", outbox.drain)
            await observed(f"{mode} entry", partial(outbox.inspect, receipt.entry_id))
            queue.server.flush(lines)
            await shut(instance)

        for mode in inputs["patches"]:
            store = _Store(queue.protocols)
            instance = api(store)
            outbox = instance.protocols.orders.outbox
            receipt = await _value(partial(outbox.operations.create_order.enqueue, body=queue.order))
            await shut(instance)
            settings = {"base_url": "https://api.example.com/other"} if mode == "basepath" else {}
            instance = api(store, **settings)
            outbox = instance.protocols.orders.outbox
            options = None
            if mode == "header":
                options = queue.options.RequestOptions(headers=(("X-Unsafe", "changed"),))
            elif mode == "query":
                options = queue.options.RequestOptions(query=(("unsafe", "changed"),))
            await observed(f"{mode} mismatch", partial(outbox.drain, options=options))
            await observed(f"{mode} retained", partial(outbox.inspect, receipt.entry_id))
            queue.server.flush(lines)
            await shut(instance)

        store = _Store(queue.protocols)
        instance = api(store)
        outbox = instance.protocols.orders.outbox
        receipt = await _value(partial(outbox.operations.create_order.enqueue, body=queue.order))
        queue.exchange.respond(queue.server.lost)
        await observed("record unknown", outbox.drain)
        await observed("recorded unknown no resend", outbox.drain)
        await observed("explicit retry", lambda: outbox.retry_unknown(receipt.entry_id))
        await observed("cancel explicit retry", lambda: outbox.cancel(receipt.entry_id))
        queue.server.flush(lines)
        await shut(instance)

        for mode in inputs["patches"]:
            store = _Store(queue.protocols)
            provider = _Provider(queue, asynchronous, lambda: None)
            auth = queue.auth.AuthConfig({"bearer": provider})
            instance = api(store, auth=auth)
            account = instance.protocols.account.offline
            receipt = await _value(account.operations.fetch.enqueue)
            await shut(instance)
            settings = {"base_url": "https://api.example.com/other"} if mode == "basepath" else {}
            instance = api(store, auth=auth, **settings)
            account = instance.protocols.account.offline
            options = None
            if mode == "header":
                options = queue.options.RequestOptions(headers=(("X-Unsafe", "changed"),))
            elif mode == "query":
                options = queue.options.RequestOptions(query=(("unsafe", "changed"),))
            await observed(f"auth {mode} mismatch", partial(account.drain, options=options))
            await observed(f"auth {mode} retained", partial(account.inspect, receipt.entry_id))
            lines.append(f"  provider calls: {provider.calls}")
            queue.server.flush(lines)
            await shut(instance)

        for historical in (False, True):
            store = _Store(queue.protocols)
            provider = _Provider(queue, asynchronous, lambda: queue.time.advance(inputs["ttl_seconds"] + 1))
            instance = api(store, auth=queue.auth.AuthConfig({"bearer": provider}))
            account = instance.protocols.account.offline
            receipt = await _value(
                lambda account=account: account.operations.fetch.enqueue(
                    queue_options=queue.protocols.QueueOptions(entry_ttl=inputs["ttl_seconds"])
                )
            )
            if historical:
                store.tamper(receipt.entry_id, send_intent=True, delivery_count=1)
            await observed(f"auth TTL prior={historical}", account.drain)
            await observed("auth TTL entry", partial(account.inspect, receipt.entry_id))
            lines.append(f"  provider calls: {provider.calls}")
            queue.server.flush(lines)
            await shut(instance)

        store = _Store(queue.protocols)
        instance = api(store)
        outbox = instance.protocols.orders.outbox
        receipt = await _value(partial(outbox.operations.create_order.enqueue, body=queue.order))
        effects = len(queue.server.orders)
        arrived, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()

        def paused(request: Any) -> Any:
            response = queue.server(request)
            loop.call_soon_threadsafe(arrived.set)
            release.wait(10)
            return response

        queue.exchange.respond(paused)
        first = asyncio.create_task(_value(outbox.drain) if asynchronous else asyncio.to_thread(outbox.drain))
        await arrived.wait()
        queue.time.advance(400)
        await observed("concurrent new owner", outbox.drain)
        release.set()
        old = await first
        lines.append(f"  concurrent old owner: {dict(old.counts)}")
        await observed("concurrent settled", partial(outbox.inspect, receipt.entry_id))
        lines.append(f"  concurrent effects: {len(queue.server.orders) - effects}")
        queue.server.flush(lines)
        await shut(instance)

        for kind in ("unkeyed_key", "missing_key", "invalid_key", "oversize", "blob", "invalid_wait", "future_retry"):
            store = _Store(queue.protocols)
            instance = api(store)
            outbox = instance.protocols.orders.outbox
            enqueue = (
                partial(
                    outbox.operations.refresh.enqueue, order_id=queue.argument("GetOrder", "path", "orderId", "safe")
                )
                if kind == "unkeyed_key"
                else partial(outbox.operations.create_order.enqueue, body=queue.order)
            )
            receipt = await _value(enqueue)
            entry = store.entries[receipt.entry_id]
            fields = json.loads(entry.payload)
            if kind in {"unkeyed_key", "missing_key", "invalid_key"}:
                key = None if kind == "missing_key" else "invalid\nkey" if kind == "invalid_key" else "extra"
                fields["key"] = key
                store.tamper(receipt.entry_id, idempotency_key=key, payload=json.dumps(fields).encode())
            elif kind == "oversize":
                store.tamper(receipt.entry_id, policy=replace(entry.policy, max_entry_body_bytes=1))
            elif kind == "invalid_wait":
                store.tamper(receipt.entry_id, saved_wait_seconds=120)
            elif kind == "blob":
                store.tamper(receipt.entry_id, blob=queue.protocols.BlobRef(size=1, sha256=bytes(32), key="blob"))
            else:
                store.tamper(
                    receipt.entry_id, state="delivery_unknown", created_at=queue.time.now() + timedelta(seconds=100)
                )
                await observed("future explicit retry", partial(outbox.retry_unknown, receipt.entry_id))
            await observed(f"corrupt {kind}", outbox.drain)
            await observed(f"corrupt {kind} entry", partial(outbox.inspect, receipt.entry_id))
            queue.server.flush(lines)
            await shut(instance)

        store = _Store(queue.protocols)
        provider = _Provider(queue, asynchronous, lambda: store.tamper(receipt.entry_id, cancel_requested=True))
        instance = api(store, auth=queue.auth.AuthConfig({"bearer": provider}))
        account = instance.protocols.account.offline
        receipt = await _value(account.operations.fetch.enqueue)
        store.tamper(receipt.entry_id, send_intent=True, delivery_count=1)
        await observed("cancel during auth", account.drain)
        await observed("cancel during auth entry", partial(account.inspect, receipt.entry_id))
        queue.server.flush(lines)
        await shut(instance)

        for mutation in ("lease", "identity"):
            store = _Store(queue.protocols)

            def changed(mutation: str = mutation, store: _Store = store) -> None:
                if mutation == "lease":
                    store.tamper(next(iter(store.entries)), lease_id="other-owner")
                else:
                    store.tamper(next(iter(store.entries)), payload=b"changed")

            provider = _Provider(queue, asynchronous, changed)
            instance = api(store, auth=queue.auth.AuthConfig({"bearer": provider}))
            account = instance.protocols.account.offline
            receipt = await _value(account.operations.fetch.enqueue)
            await observed(f"auth changed {mutation}", account.drain)
            await observed(f"changed {mutation} entry", partial(account.inspect, receipt.entry_id))
            queue.server.flush(lines)
            await shut(instance)

        queue.time = _WallClock()
        queue.clock = queue.options.Clock(
            monotonic=queue.time.monotonic, time=queue.time.time, random=queue.time.random
        )
        store = _Store(queue.protocols)
        instance = api(store)
        outbox = instance.protocols.orders.outbox
        receipt = await _value(partial(outbox.operations.create_order.enqueue, body=queue.order))
        queue.time.advance(600)
        queue.exchange.respond(queue.server.status(503, **{"Retry-After": "120"}))
        await observed("saved server wait", outbox.drain)
        queue.time.advance(121)
        store.at_claim[store.claims + 1] = lambda: setattr(queue.time, "wall_shift", -122)
        await observed("rollback after ready claim", outbox.drain)
        entry = await _value(partial(outbox.inspect, receipt.entry_id))
        lines.append(
            f"  rollback saved wait: {entry.saved_wait_seconds} "
            f"outcome_matches={entry.result.retry_at == entry.not_before}"
        )
        queue.server.flush(lines)
        await shut(instance)

        store = queue.protocols.MemoryQueueStore()
        instance = api(store)
        outbox = instance.protocols.orders.outbox
        for intent in (False, True):
            receipt = await _value(partial(outbox.operations.create_order.enqueue, body=queue.order))
            now = queue.time.now()
            leased = store.claim(now=now, lease_until=now + timedelta(seconds=1), limit=1)[0].entry
            store.compare_exchange(
                receipt.entry_id, leased.version, replace(leased, send_intent=intent, cancel_requested=True)
            )
            queue.time.advance(2)
            await observed(f"cancelled expired lease intent={intent}", outbox.drain)
            await observed("cancelled lease entry", partial(outbox.inspect, receipt.entry_id))
        for category in inputs["records"]:
            receipt = await _value(partial(outbox.operations.create_order.enqueue, body=queue.order))
            entry = store.get(receipt.entry_id)
            now = queue.time.now()
            leased = store.claim(now=now, lease_until=now + timedelta(seconds=1), limit=1)[0].entry
            store.compare_exchange(
                receipt.entry_id,
                leased.version,
                replace(leased, result=queue.protocols.QueueOutcome(category=category)),
            )
            queue.time.advance(2)
            await observed(f"record {category} no reclaim", outbox.drain)
            await observed(f"record {category} entry", partial(outbox.inspect, entry.entry_id))
        queue.server.flush(lines)
        await shut(instance)


def queue_recovery(package: ModuleType, lines: list[str]) -> None:
    """Run the literal acceptance matrix after recreating adapters from disk journals."""
    with TemporaryDirectory() as root:
        path = Path(root) / "queue.json"
        run(lambda: _matrix(package, lines, False, path))
        run(lambda: _matrix(package, lines, True, path))


async def _restoration(package: ModuleType, lines: list[str], asynchronous: bool) -> None:
    from tests.data.python import queue_recovery_adapter

    queue = _Queues(package, lines)
    lines.append("async restoration" if asynchronous else "sync restoration")
    async with _native(queue, asynchronous) as native:
        client = package.AsyncClient if asynchronous else package.Client
        for prior in (False, True):
            store = _Store(queue.protocols)
            instance = client(http_client=native, options=queue.settings(_AsyncStore(store) if asynchronous else store))
            outbox = instance.protocols.orders.outbox
            receipt = await _value(
                partial(
                    outbox.operations.create_order.enqueue,
                    body=queue.order,
                    queue_options=queue.protocols.QueueOptions(entry_ttl=3),
                )
            )
            store.tamper(receipt.entry_id, send_intent=prior, delivery_count=int(prior))
            queue_recovery_adapter.restoring = partial(queue.time.advance, 4)
            try:
                report = await _value(outbox.drain)
            finally:
                queue_recovery_adapter.restoring = None
            entry = await _value(partial(outbox.inspect, receipt.entry_id))
            lines.extend((
                (
                    f"  prior={prior}: {dict(report.counts)} {entry.state} "
                    f"{entry.result.category}/{entry.result.error_code} count={entry.delivery_count}"
                ),
                f"  sends: {len(queue.server.log)}",
            ))
            await _value(instance.aclose if asynchronous else instance.close)
        store = _Store(queue.protocols)
        instance = client(http_client=native, options=queue.settings(_AsyncStore(store) if asynchronous else store))
        outbox = instance.protocols.orders.outbox
        trace = queue.argument("CreateOrder", "header", "X-Trace", "original")
        receipt = await _value(partial(outbox.operations.create_order.enqueue, body=queue.order, x_trace=trace))
        calls = 0

        def altered() -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                queue_recovery_adapter.changed_trace = True

        queue_recovery_adapter.encoding = altered
        try:
            try:
                await _value(outbox.drain)
            except Exception as error:  # ruff: ignore[blind-except]
                lines.append(f"  actual preparation changed: {type(error).__name__}")
        finally:
            queue_recovery_adapter.encoding = None
            queue_recovery_adapter.changed_trace = False
        entry = await _value(partial(outbox.inspect, receipt.entry_id))
        lines.extend((
            f"  preparation retained: {entry.state} count={entry.delivery_count}",
            f"  sends: {len(queue.server.log)}",
        ))
        await _value(instance.aclose if asynchronous else instance.close)

        public = import_module(package.__name__ + ".model_codecs")
        for prior in (False, True):
            store = _Store(queue.protocols)
            instance = client(http_client=native, options=queue.settings(_AsyncStore(store) if asynchronous else store))
            outbox = instance.protocols.orders.outbox
            receipt = await _value(partial(outbox.operations.create_order.enqueue, body=queue.order, x_trace=trace))
            store.tamper(receipt.entry_id, send_intent=prior, delivery_count=int(prior))
            calls = 0

            def refused() -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    message = "Controlled public adapter refusal"
                    raise public.ParameterEncodingError(message)

            queue_recovery_adapter.encoding = refused
            try:
                report = await _value(outbox.drain)
            finally:
                queue_recovery_adapter.encoding = None
            entry = await _value(partial(outbox.inspect, receipt.entry_id))
            lines.extend((
                f"  prior={prior} preparation refused: {dict(report.counts)} {entry.state} {entry.result.category}",
                f"  sends: {len(queue.server.log)}",
            ))
            await _value(instance.aclose if asynchronous else instance.close)


def queue_restoration(package: ModuleType, lines: list[str]) -> None:
    """Consume the entry deadline in a registered restoration adapter before any resource/provider admission."""
    run(lambda: _restoration(package, lines, False))
    run(lambda: _restoration(package, lines, True))


def queue_scope(package: ModuleType, lines: list[str]) -> None:
    """Generate a changed declared key scope and refuse the old queue record before sending."""
    from tests.data.python.client_runtime import generated

    queue = _Queues(package, lines)
    store = _Journal(queue.protocols)
    with (
        queue.exchange.client() as native,
        package.Client(http_client=native, options=queue.settings(store)) as instance,
    ):
        receipt = instance.protocols.orders.outbox.operations.create_order.enqueue(body=queue.order)
    with TemporaryDirectory() as root:
        path = Path(root)

        def changed(second: ModuleType, nested: list[str]) -> None:
            other = _Queues(second, nested)
            store.protocols = other.protocols
            fresh = store.reopened(path / "journal.json")
            with (
                other.exchange.client() as native,
                second.Client(http_client=native, options=other.settings(fresh)) as instance,
            ):
                try:
                    instance.protocols.orders.outbox.drain()
                except Exception as error:  # ruff: ignore[blind-except]
                    nested.append(f"scope mismatch: {type(error).__name__}")
                same_key = (
                    fresh.entries[receipt.entry_id].idempotency_key == store.entries[receipt.entry_id].idempotency_key
                )
                nested.extend((f"sends: {len(other.server.log)}", f"key retained: {same_key}"))

        lines.append(generated("queues-scope", "pydantic_v2.BaseModel", path / "generated", changed).rstrip())
