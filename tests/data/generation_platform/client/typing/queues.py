"""Keep queue receipts, entries, drain reports, errors, and store contracts typed, with sync and asyncio clients."""

from __future__ import annotations

from datetime import datetime, timezone

from pets import AsyncClient, Client
from pets.errors import QueueBindingError, QueueFullError, QueuePolicyConflictError, QueueStoreError
from pets.options import ClientOptions, ProtocolClientOptions, RequestOptions, SessionOptions
from pets.protocols import (
    AsyncMemoryQueueStore,
    AsyncQueueStore,
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
)
from pets.responses import ResponseInfo
from pets_models import Account, NewOrder
from typing_extensions import assert_type


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
    del asynchronous
    return ClientOptions(protocols=ProtocolClientOptions(queue_stores={"orders.outbox": own, "account.offline": builtin}))


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
