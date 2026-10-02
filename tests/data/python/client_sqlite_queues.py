"""Exercise only generated public queue adapters with literal records and independent stdlib SQLite fixtures."""

from __future__ import annotations

import asyncio
import base64
import importlib
import inspect
import json
import sqlite3
import subprocess
import sys
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from tests.data.python.client_runtime import run

if TYPE_CHECKING:
    from types import ModuleType

_SOURCE: Final = Path(__file__).parents[1] / "generation_platform/client/sqlite-queues.json"
_NOW: Final = datetime(2025, 2, 3, 5, tzinfo=timezone.utc)
_TIMES: Final = ("created_at", "expires_at", "not_before", "lease_until")


def _record(protocols: ModuleType, responses: ModuleType, values: dict[str, Any]) -> Any:
    value = dict(values)
    value["payload"] = base64.b64decode(value["payload"])
    for name in _TIMES:
        value[name] = None if value[name] is None else datetime.fromisoformat(value[name])
    value["policy"] = protocols.ResolvedQueueOptions(**value["policy"])
    if (blob := value["blob"]) is not None:
        value["blob"] = protocols.BlobRef(**{**blob, "sha256": base64.b64decode(blob["sha256"])})
    if (outcome := value["result"]) is not None:
        outcome = dict(outcome)
        if (response := outcome["response"]) is not None:
            outcome["response"] = responses.ResponseInfo(**{
                **response,
                "headers": responses.HeadersView(tuple(tuple(pair) for pair in response["headers"])),
                "auth_refresh_ids": tuple(response["auth_refresh_ids"]),
            })
        outcome["retry_at"] = None if outcome["retry_at"] is None else datetime.fromisoformat(outcome["retry_at"])
        value["result"] = protocols.QueueOutcome(**outcome)
    return protocols.QueueEntry(**value)


def _metadata(entry: Any) -> dict[str, Any]:
    blob = entry.blob
    outcome = entry.result
    response = None if outcome is None else outcome.response
    return {
        "entry_id": entry.entry_id,
        "operation_alias": entry.operation_alias,
        "helper_fingerprint": entry.helper_fingerprint,
        "security_fingerprint": entry.security_fingerprint,
        "payload": entry.payload.hex(),
        "blob": None if blob is None else [blob.size, blob.sha256.hex(), blob.key],
        "blob_owned": entry.blob_owned,
        "idempotency_key": entry.idempotency_key,
        "created_at": entry.created_at.isoformat(),
        "expires_at": entry.expires_at.isoformat(),
        "not_before": entry.not_before.isoformat(),
        "saved_wait_seconds": entry.saved_wait_seconds,
        "state": entry.state,
        "delivery_count": entry.delivery_count,
        "send_intent": entry.send_intent,
        "cancel_requested": entry.cancel_requested,
        "lease_id": entry.lease_id,
        "lease_until": None if entry.lease_until is None else entry.lease_until.isoformat(),
        "policy": {
            name: getattr(entry.policy, name)
            for name in (
                "max_entries",
                "parallelism",
                "max_entry_body_bytes",
                "max_deliveries",
                "entry_ttl",
                "retry_initial_delay",
                "retry_max_delay",
                "lease_min",
                "lease_grace",
                "max_delivery_timeout",
            )
        },
        "result": None
        if outcome is None
        else {
            "category": outcome.category,
            "retry_at": None if outcome.retry_at is None else outcome.retry_at.isoformat(),
            "error_code": outcome.error_code,
            "response": None
            if response is None
            else {
                "status_code": response.status_code,
                "headers": list(response.headers.items()),
                "call_id": response.call_id,
                "elapsed": response.elapsed,
                "content_type": response.content_type,
                "request_id": response.request_id,
                "resource_attempt_count": response.resource_attempt_count,
                "redirect_count": response.redirect_count,
                "auth_exchange_count": response.auth_exchange_count,
                "network_send_count": response.network_send_count,
                "network_send_budget_used": response.network_send_budget_used,
                "auth_exchange_budget_used": response.auth_exchange_budget_used,
                "auth_refresh_ids": list(response.auth_refresh_ids),
                "auth_refresh_pending": response.auth_refresh_pending,
                "wire_send_count": response.wire_send_count,
            },
        },
    }


async def _call(store: Any, method: str, *arguments: Any, **keywords: Any) -> Any:
    operation = getattr(store, method)
    return (
        await operation(*arguments, **keywords)
        if inspect.iscoroutinefunction(operation)
        else operation(*arguments, **keywords)
    )


async def _close(store: Any) -> None:
    if hasattr(store, "aclose"):
        await store.aclose()
    elif hasattr(store, "close"):
        store.close()


async def _error(store: Any, method: str, *arguments: Any, **keywords: Any) -> str:
    try:
        await _call(store, method, *arguments, **keywords)
    except Exception as error:  # ruff: ignore[blind-except] - Report public SDK exceptions from generated packages.
        cause = error.cause if hasattr(error, "cause") else None
        return (
            f"{type(error).__name__}:{getattr(error, 'action', '-')}/"
            f"{getattr(error, 'entry_id', '-')}/{type(cause).__name__}"
        )
    return "returned"


async def _parity(
    protocols: ModuleType, entry: Any, name: str, root: Path, values: dict[str, Any], lines: list[str]
) -> None:
    constructor = getattr(protocols, name)
    arguments = (root / f"{name}.db",) if "SQLite" in name else ()
    store = constructor(*arguments)
    try:
        await _call(store, "put", entry)
        saved = await _call(store, "get", entry.entry_id)
        lines.extend((
            f"{name} metadata {json.dumps(_metadata(saved), sort_keys=True, separators=(',', ':'))}",
            f"{name} put-version {saved.version != entry.version} missing {await _call(store, 'get', 'missing')}",
            f"{name} duplicate {await _error(store, 'put', entry)}",
        ))
        await _close(store)
        if "SQLite" in name:
            store = constructor(*arguments)
            reopened = await _call(store, "get", entry.entry_id)
            lines.append(f"{name} restart {reopened == saved}")
        else:
            store = constructor()
        for index, (label, category, intent, cancelled) in enumerate(values["recovery"]):
            candidate = replace(
                entry,
                entry_id=label,
                created_at=_NOW - timedelta(seconds=100 - index),
                not_before=_NOW,
                lease_until=_NOW - timedelta(seconds=1),
                send_intent=intent,
                cancel_requested=cancelled,
                result=None if category is None else protocols.QueueOutcome(category=category),
            )
            await _call(store, "put", candidate)
        claimed = await _call(store, "claim", now=_NOW, lease_until=_NOW + timedelta(seconds=90), limit=100)
        lines.append(f"{name} recovered-claimed {[lease.entry.entry_id for lease in claimed]}")
        for label, *_ in values["recovery"]:
            recovered = await _call(store, "get", label)
            lines.append(
                f"{name} recovery {label} {recovered.state}/{recovered.send_intent}/{recovered.delivery_count}/"
                f"{None if recovered.result is None else recovered.result.category}"
            )
        lease = claimed[0]
        released = replace(lease.entry, state="pending", lease_id=None, lease_until=None)
        arguments = (lease.entry.entry_id, lease.entry.version)
        wrong = await _call(store, "compare_exchange", *arguments, replace(lease.entry, lease_id="wrong-lease"))
        changed = await _call(store, "compare_exchange", *arguments, released)
        stale = await _call(store, "compare_exchange", *arguments, released)
        lines.extend((f"{name} wrong-lease {wrong}", f"{name} release {changed} stale {stale}"))
        pending = await _call(store, "get", lease.entry.entry_id)
        immutable = [
            pending.idempotency_key,
            pending.created_at.isoformat(),
            pending.delivery_count,
            pending.payload.hex(),
            pending.policy == entry.policy,
        ]
        lines.append(f"{name} immutable {immutable}")
    finally:
        await _close(store)


async def _capacity(protocols: ModuleType, entry: Any, name: str, root: Path, lines: list[str]) -> None:
    constructor = getattr(protocols, name)
    store = constructor(*((root / f"capacity-{name}.db",) if "SQLite" in name else ()), max_entries=2, max_bytes=6)
    first = replace(entry, entry_id="a", payload=b"abcd", state="succeeded", lease_id=None, lease_until=None)
    second = replace(first, entry_id="b", payload=b"ef", created_at=first.created_at + timedelta(seconds=1))
    try:
        await _call(store, "put", first)
        await _call(store, "put", second)
        lines.append(
            f"{name} terminal-capacity {await _error(store, 'put', replace(first, entry_id='c', payload=b''))}"
        )
        saved = await _call(store, "get", "a")
        stale = await _call(store, "compare_exchange", "a", "stale", replace(saved, payload=b"abcdefg"))
        growth = await _error(store, "compare_exchange", "a", saved.version, replace(saved, payload=b"abcde"))
        same = await _call(store, "get", "a") == saved
        mismatch = await _error(store, "compare_exchange", "a", saved.version, replace(saved, entry_id="other"))
        boundary = await _call(store, "purge_terminal", first.created_at)
        lines.extend((
            f"{name} stale-growth {stale}",
            f"{name} growth {growth}",
            f"{name} growth-unchanged {same}",
            f"{name} mismatched-id {mismatch}",
            f"{name} purge-boundary {[item.entry_id for item in boundary]}",
        ))
        purged = await _call(store, "purge_terminal", second.created_at)
        lines.append(f"{name} purge-strict {[item.entry_id for item in purged]} metadata {purged == (saved,)}")
        await _call(store, "put", replace(first, entry_id="c"))
        missing = await _call(store, "get", "a")
        final = await _call(store, "purge_terminal", _NOW)
        lines.append(f"{name} reclaimed-capacity {missing} {[item.entry_id for item in final]}")
    finally:
        await _close(store)


async def _schema(protocols: ModuleType, root: Path, lines: list[str]) -> None:
    for label, sql in (
        ("unknown-version", "PRAGMA user_version = 2"),
        ("unrelated", "CREATE TABLE unrelated(value TEXT)"),
        ("malformed", "CREATE TABLE queue_entries(entry_id TEXT); PRAGMA user_version = 1"),
    ):
        for name in ("SQLiteQueueStore", "AsyncSQLiteQueueStore"):
            path = root / f"{label}-{name}.db"
            with sqlite3.connect(path) as connection:
                connection.executescript(sql)
            original = sha256(path.read_bytes()).digest()
            store = getattr(protocols, name)(path)
            try:
                lines.append(f"{name} {label} {await _error(store, 'get', 'missing')}")
            finally:
                await _close(store)
            lines.append(f"{name} {label}-unchanged {sha256(path.read_bytes()).digest() == original}")


async def _corruption(protocols: ModuleType, entry: Any, root: Path, values: dict[str, Any], lines: list[str]) -> None:
    for name in ("SQLiteQueueStore", "AsyncSQLiteQueueStore"):
        path = root / f"corruption-{name}.db"
        store = getattr(protocols, name)(path)
        healthy = replace(entry, entry_id="healthy", state="pending", lease_id=None, lease_until=None, not_before=_NOW)
        corrupted = replace(entry, entry_id="corrupted")
        try:
            await _call(store, "put", healthy)
            await _call(store, "put", corrupted)
            with sqlite3.connect(path) as connection:
                original = connection.execute(
                    "SELECT record FROM queue_entries WHERE entry_id = 'corrupted'"
                ).fetchone()[0]
                good = connection.execute("SELECT record FROM queue_entries WHERE entry_id = 'healthy'").fetchone()[0]
            for label, target, value in values["corruptions"]:
                record = json.loads(original)
                if target == "raw":
                    changed = value
                else:
                    owner = record if target in {"schema_version", "envelope-extra"} else record["entry"]
                    path_items = target.split(".")
                    for item in path_items[:-1]:
                        owner = owner[item]
                    owner[path_items[-1]] = value
                    changed = json.dumps(record)
                with sqlite3.connect(path) as connection:
                    connection.execute("UPDATE queue_entries SET record = ? WHERE entry_id = 'corrupted'", (changed,))
                outcome = await _error(store, "claim", now=_NOW, lease_until=_NOW + timedelta(seconds=90), limit=100)
                with sqlite3.connect(path) as connection:
                    retained = connection.execute(
                        "SELECT record FROM queue_entries WHERE entry_id = 'healthy'"
                    ).fetchone()[0]
                lines.append(f"{name} corrupt {label} {outcome} atomic {retained == good}")
            with sqlite3.connect(path) as connection:
                connection.execute("UPDATE queue_entries SET record = ? WHERE entry_id = 'corrupted'", (original,))
            claimed = await _call(store, "claim", now=_NOW, lease_until=_NOW + timedelta(seconds=90), limit=100)
            lines.append(f"{name} valid-after-corruption {[lease.entry.entry_id for lease in claimed]}")
        finally:
            await _close(store)


def _processes(package: ModuleType, protocols: ModuleType, entry: Any, root: Path, lines: list[str]) -> None:
    """Use pipe barriers for two independent first opens and two disjoint ordered claims."""
    origin = Path(package.__file__).resolve()
    for populated in (False, True):
        path = root / f"processes-{populated}.db"
        before: dict[str, Any] = {}
        store = protocols.SQLiteQueueStore(path)
        if populated:
            for key, delta in (("b", 0), ("a", 0), ("d", 1), ("c", 1)):
                store.put(
                    replace(
                        entry,
                        entry_id=key,
                        state="pending",
                        created_at=_NOW - timedelta(seconds=2 - delta),
                        not_before=_NOW,
                        lease_id=None,
                        lease_until=None,
                        result=None,
                        cancel_requested=False,
                    )
                )
                before[key] = store.get(key)
        store.close()
        arguments = [
            sys.executable,
            str(Path(__file__).with_name("sqlite_queue_process.py")),
            str(origin.parents[1]),
            str(origin.parents[3]),
            package.__name__,
            str(path),
            _NOW.isoformat(),
            (_NOW + timedelta(seconds=90)).isoformat(),
        ]
        processes = [
            subprocess.Popen(
                arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            for _ in range(2)
        ]
        try:
            readiness = [process.stdout.readline().strip() for process in processes]
            for process in processes:
                process.stdin.write("claim\n")
                process.stdin.flush()
            batches = [json.loads(process.stdout.readline()) for process in processes]
            exits = [process.wait(timeout=30) for process in processes]
            labels = sorted(sorted(item[0] for item in batch) for batch in batches)
            lines.append(f"processes populated={populated} ready={readiness} batches={labels} exits={exits}")
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=30)
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
        with sqlite3.connect(path) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
            metadata = connection.execute("SELECT COUNT(*), MIN(last_lease) FROM store_meta").fetchone()
            lines.append(f"processes schema {version}/{journal}/{metadata}")
        reopened = protocols.SQLiteQueueStore(path)
        try:
            stale = [
                reopened.compare_exchange(key, saved.version, replace(saved, state="dead"))
                for key, saved in sorted(before.items())
            ]
            lines.append(f"processes stale={stale}")
        finally:
            reopened.close()


async def _worker(protocols: ModuleType, entry: Any, root: Path, lines: list[str]) -> None:
    """Observe FIFO settlement and cancellation through an independent SQLite writer lock and public records."""
    path = root / "worker.db"
    store = protocols.AsyncSQLiteQueueStore(path)
    await store.get("missing")
    blocker = sqlite3.connect(path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    entered = [asyncio.Event() for _ in range(68)]

    async def put(index: int) -> str:
        entered[index].set()
        try:
            await store.put(
                replace(
                    entry, entry_id=f"fifo-{index:02}", state="pending", lease_id=None, lease_until=None, result=None
                )
            )
        except asyncio.CancelledError as error:
            return f"native-cancel:{error.args == ('cancel-pending',)}"
        except Exception as error:  # ruff: ignore[blind-except] - Report public generated rejection classes.
            return f"{type(error).__name__}:{getattr(error, 'action', '-')}/{type(error.cause).__name__}"
        return "stored"

    tasks = [asyncio.create_task(put(index)) for index in range(68)]
    try:
        await asyncio.gather(*(event.wait() for event in entered))
        tasks[30].cancel("cancel-pending")
        cancelled = await tasks[30]
        closing = asyncio.create_task(store.aclose())
        admission = asyncio.Event()

        async def close() -> None:
            admission.set()
            await closing

        wrapped = asyncio.create_task(close())
        await admission.wait()
        wrapped.cancel("cancel-close")
        try:
            await wrapped
        except asyncio.CancelledError as error:
            lines.append(f"worker close-native {error.args == ('cancel-close',)}")
        blocker.rollback()
        outcomes = await asyncio.gather(*tasks)
        await store.aclose()
        await store.aclose()
        lines.append(f"worker pending-cancel {cancelled}")
        saved = [index for index, outcome in enumerate(outcomes) if outcome == "stored"]
        refused = [index for index, outcome in enumerate(outcomes) if outcome.startswith("QueueStoreError")]
        lines.extend((
            (
                f"worker bounded {len(saved) in {64, 65}} rejected={len(refused) in {2, 3}} "
                f"cancellation-excluded={30 not in saved}"
            ),
            f"worker retained-close {await _error(store, 'get', 'missing')}",
        ))
        with sqlite3.connect(path) as connection:
            rows = [row[0] for row in connection.execute("SELECT entry_id FROM queue_entries ORDER BY rowid")]
        lines.append(f"worker FIFO {rows == [f'fifo-{index:02}' for index in saved]}")
    finally:
        blocker.close()
        await store.aclose()


def _closed_loop(protocols: ModuleType, entry: Any, root: Path, lines: list[str]) -> None:
    """Close an originating loop before notification, then reuse and close the public store on a new loop."""
    path = root / "closed-loop.db"
    store = protocols.AsyncSQLiteQueueStore(path)
    loop = asyncio.new_event_loop()
    loop.run_until_complete(store.get("missing"))
    blocker = sqlite3.connect(path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    entered = asyncio.Event()

    async def publish() -> None:
        entered.set()
        await store.put(replace(entry, entry_id="closed-loop", result=None))

    task = loop.create_task(publish())
    loop.run_until_complete(entered.wait())
    loop.close()
    blocker.rollback()
    blocker.close()

    async def reopen() -> None:
        saved = await store.get("closed-loop")
        lines.append(f"worker closed-loop subsequent-use {saved is not None and saved.payload == entry.payload}")
        await store.aclose()
        await store.aclose()

    try:
        run(reopen)
    finally:
        task.get_coro().close()


def sqlite_queues(package: ModuleType, lines: list[str]) -> None:
    """Report permanent sync/async memory/SQLite parity, restart, capacity, recovery and corrupt-row controls."""
    protocols = importlib.import_module(f"{package.__name__}.protocols")
    responses = importlib.import_module(f"{package.__name__}.responses")
    values = json.loads(_SOURCE.read_text(encoding="utf-8"))
    entry = _record(protocols, responses, values["record"])
    root = Path(package.__file__).resolve().parent / "sqlite-fixtures"
    root.mkdir()

    async def scenario() -> None:
        baseline = tuple(threading.enumerate())
        for name in ("SQLiteQueueStore", "AsyncSQLiteQueueStore"):
            path = root / f"unused-{name}.db"
            store = getattr(protocols, name)(path)
            if name.startswith("Async"):
                unused = store.get("missing")
                unused.close()
            lines.append(f"{name} lazy {not path.exists()}/{tuple(threading.enumerate()) == baseline}")
            await _close(store)
            await _close(store)
            lines.append(f"{name} unused-close {not path.exists()}/{tuple(threading.enumerate()) == baseline}")
            lines.append(f"{name} closed-use {await _error(store, 'get', 'missing')}")
        for name in ("MemoryQueueStore", "AsyncMemoryQueueStore", "SQLiteQueueStore", "AsyncSQLiteQueueStore"):
            await _parity(protocols, entry, name, root, values, lines)
            await _capacity(protocols, entry, name, root, lines)
        await _schema(protocols, root, lines)
        await _corruption(protocols, entry, root, values, lines)
        await _worker(protocols, entry, root, lines)

    run(scenario)
    _processes(package, protocols, entry, root, lines)
    _closed_loop(protocols, entry, root, lines)
