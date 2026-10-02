"""Keep queue receipts, entries, drain reports, errors, and store contracts typed, with sync and asyncio clients."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from pets.options import ClientOptions, ProtocolClientOptions, RequestOptions, SessionOptions
from pets.protocols import (
    AsyncMemoryQueueStore,
    AsyncQueueStore,
    AsyncSQLiteQueueStore,
    BlobRef,
    DrainReport,
    MemoryQueueStore,
    QueueEntry,
    QueueLease,
    QueueOptions,
    QueueOutcome,
    QueueReceipt,
    QueueStore,
    ResolvedQueueOptions,
    SQLiteQueueStore,
)
from pets.responses import ResponseInfo
from typing_extensions import assert_type

if TYPE_CHECKING:
    from pets import AsyncClient, Client
    from pets.errors import QueueBindingError, QueueFullError, QueuePolicyConflictError, QueueStoreError
    from pets_models import Account, NewOrder


class Store:
    """A queue store of the caller's own, written against the public records."""

    def __init__(self) -> None:
        """Start empty."""
        self.entries: dict[str, QueueEntry] = {}

    def put(self, entry: QueueEntry) -> None:
        """Keep a new entry."""
        self.entries[entry.entry_id] = entry

    def get(self, entry_id: str) -> QueueEntry | None:
        """Return an entry."""
        return self.entries.get(entry_id)

    def claim(self, *, now: datetime, lease_until: datetime, limit: int) -> tuple[QueueLease, ...]:
        """Lease nothing."""
        del now, lease_until, limit
        return ()

    def exchange(self, entry_id: str, expected_version: str, entry: QueueEntry) -> bool:
        """Replace an entry."""
        del expected_version
        self.entries[entry_id] = entry
        return True

    compare_exchange = exchange

    def purge_terminal(self, before: datetime) -> tuple[QueueEntry, ...]:
        """Purge nothing."""
        del before
        return ()


def stores() -> ClientOptions:
    """Lend a caller's store and the builtin ones to a client."""
    own: QueueStore = Store()
    builtin: QueueStore = MemoryQueueStore(max_entries=10, max_bytes=1024)
    asynchronous: AsyncQueueStore = AsyncMemoryQueueStore()
    persistent: QueueStore = SQLiteQueueStore("queue.db", max_entries=10, max_bytes=1024)
    async_persistent: AsyncQueueStore = AsyncSQLiteQueueStore("async-queue.db")
    del asynchronous, persistent, async_persistent
    return ClientOptions(
        protocols=ProtocolClientOptions(queue_stores={"orders.outbox": own, "account.offline": builtin})
    )


def queued(client: Client, order: NewOrder, account: Account) -> None:
    """Enqueue typed calls, drain them, and read their entries."""
    outbox = client.protocols.orders.outbox
    receipt = outbox.operations.create_order.enqueue(body=order, queue_options=QueueOptions(max_deliveries=3))
    assert_type(receipt, QueueReceipt)
    assert_type(receipt.entry_id, str)
    assert_type(receipt.created_at, datetime)
    offline = client.protocols.account.offline
    assert_type(offline.operations.rename.enqueue(body=account), QueueReceipt)
    assert_type(offline.operations.fetch.enqueue(), QueueReceipt)
    report = outbox.drain(
        queue_options=QueueOptions(max_entries=10, parallelism=2),
        options=RequestOptions(),
        session_options=SessionOptions(max_network_sends=5),
    )
    assert_type(report, DrainReport)
    assert_type(report.succeeded, tuple[str, ...])
    entry = outbox.inspect(receipt.entry_id)
    assert_type(entry, QueueEntry | None)
    if entry is not None:
        assert_type(entry.payload, bytes)
        assert_type(entry.policy, ResolvedQueueOptions)
        assert_type(entry.blob, BlobRef | None)
        if (result := entry.result) is not None:
            assert_type(result, QueueOutcome)
            assert_type(result.response, ResponseInfo | None)
    assert_type(outbox.cancel(receipt.entry_id), QueueEntry | None)
    assert_type(outbox.retry_unknown(receipt.entry_id), QueueEntry | None)
    assert_type(outbox.purge_terminal(datetime.now(timezone.utc)), tuple[QueueEntry, ...])


def failures(binding: QueueBindingError, full: QueueFullError, conflict: QueuePolicyConflictError) -> None:
    """Read the fields queue errors keep."""
    assert_type(binding.entry_id, str)
    assert_type(conflict.fields, tuple[str, ...])
    assert_type(full.limit, int)
    store_error: QueueStoreError = full
    del store_error


async def async_queued(client: AsyncClient, order: NewOrder) -> None:
    """Await enqueue, drain, and entry management with asyncio."""
    outbox = client.protocols.orders.outbox
    receipt = await outbox.operations.create_order.enqueue(body=order)
    assert_type(receipt, QueueReceipt)
    assert_type(await outbox.drain(), DrainReport)
    assert_type(await outbox.inspect(receipt.entry_id), QueueEntry | None)
    assert_type(await outbox.cancel(receipt.entry_id), QueueEntry | None)
    assert_type(await outbox.retry_unknown(receipt.entry_id), QueueEntry | None)
    assert_type(await outbox.purge_terminal(datetime.now(timezone.utc)), tuple[QueueEntry, ...])


async def persistent_stores(entry: QueueEntry) -> None:
    """Use each persistent adapter structurally and preserve ordinary sync versus native coroutine methods."""
    sync = SQLiteQueueStore("queue.db")
    asynchronous = AsyncSQLiteQueueStore("async-queue.db")
    now = datetime.now(timezone.utc)
    assert_type(sync.put(entry), None)
    assert_type(sync.get(entry.entry_id), QueueEntry | None)
    assert_type(sync.claim(now=now, lease_until=now, limit=1), tuple[QueueLease, ...])
    assert_type(sync.compare_exchange(entry.entry_id, entry.version, entry), bool)
    assert_type(sync.purge_terminal(now), tuple[QueueEntry, ...])
    assert_type(sync.close(), None)
    assert_type(await asynchronous.put(entry), None)
    assert_type(await asynchronous.get(entry.entry_id), QueueEntry | None)
    assert_type(await asynchronous.claim(now=now, lease_until=now, limit=1), tuple[QueueLease, ...])
    assert_type(await asynchronous.compare_exchange(entry.entry_id, entry.version, entry), bool)
    assert_type(await asynchronous.purge_terminal(now), tuple[QueueEntry, ...])
    assert_type(await asynchronous.aclose(), None)
