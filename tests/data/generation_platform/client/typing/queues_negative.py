"""Reject unawaited asyncio calls, missing bodies, other options, wrong records, and changes to frozen entries."""

from __future__ import annotations

from pets import AsyncClient, Client
from pets.options import ProtocolClientOptions, RequestOptions, SessionOptions
from pets.protocols import DrainReport, QueueEntry, QueueOptions, QueueReceipt
from pets_models import NewOrder


async def misuses(client: Client, async_client: AsyncClient, order: NewOrder, entry: QueueEntry) -> None:
    """Reject each misuse of a queue helper or its records."""
    outbox = client.protocols.orders.outbox
    outbox.operations.create_order.enqueue()  # error
    outbox.operations.create_order.enqueue(body=order, options=RequestOptions())  # error
    outbox.operations.create_order.enqueue(body=order, queue_options=SessionOptions())  # error
    outbox.drain(queue_options=SessionOptions())  # error
    outbox.purge_terminal("yesterday")  # error
    unawaited: QueueReceipt = async_client.protocols.orders.outbox.operations.create_order.enqueue(body=order)  # error
    await outbox.drain()  # error
    report: DrainReport = outbox.inspect("e1")  # error
    outbox.inspect(5)  # error
    payload: str = entry.payload  # error
    entry.state = "pending"  # error
    QueueOptions(max_entries="10")  # error
    ProtocolClientOptions(queue_stores={"orders.outbox": object()})  # error
    del unawaited, report, payload
