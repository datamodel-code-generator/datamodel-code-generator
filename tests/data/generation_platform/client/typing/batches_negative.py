"""Reject items of another type, other options, other iterator types, misread records, and undeclared arguments."""

from __future__ import annotations

from pets import AsyncClient, Client
from pets.options import SessionOptions
from pets.protocols import BatchIterator
from pets.protocols.batches import TagsPutResult, TagsPutSuccess, UsersCreateResult
from pets_models import ItemError, NewUser, Tag


async def wrong_batches(client: Client, async_client: AsyncClient, users: list[NewUser], tags: list[Tag]) -> None:
    """Reject each misuse of a helper, its iterator, or its records."""
    client.protocols.users.create.iterate(tags)  # error
    client.protocols.users.create.iterate(users, batch_options=SessionOptions())  # error
    client.protocols.users.create.iterate(items=users, body=None)  # error
    results: BatchIterator[TagsPutResult] = client.protocols.users.create.iterate(users)  # error
    await async_client.protocols.users.create.iterate(users)  # error
    record: UsersCreateResult = next(client.protocols.users.create.iterate(users))
    if record.outcome == "success":
        refused: ItemError = record.value  # error
        del refused
    success: TagsPutSuccess = next(client.protocols.tags.put.iterate(tags))  # error
    success.index = 1  # error
    TagsPutSuccess(index=0, item_id=None, response=None, value=tags[0], outcome="error")  # error
    del results, record
