"""Keep input items and result records through batch helpers and their iterators, with sync and asyncio clients."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal

from pets import AsyncClient, Client
from pets.errors import BatchDeliveryUnknownError
from pets.options import RequestOptions, SessionOptions
from pets.protocols import AsyncBatchIterator, BatchIterator, BatchOptions, ProtocolProgress
from pets.protocols.batches import (
    TagsPutDeliveryUnknown,
    TagsPutError,
    TagsPutResult,
    TagsPutSuccess,
    UsersCreateResult,
)
from pets.responses import ResponseInfo
from pets_models import ItemError, NewUser, Tag, User
from typing_extensions import assert_type


def batches(client: Client, users: list[NewUser], tags: tuple[Tag, ...]) -> None:
    """Send items and narrow each record by its outcome."""
    iterator = client.protocols.users.create.iterate(
        users,
        batch_options=BatchOptions(batch_size=10, parallelism=2, max_items=None, raise_on_error=True),
        options=RequestOptions(),
        session_options=SessionOptions(max_network_sends=5),
    )
    assert_type(iterator, BatchIterator[UsersCreateResult])
    for result in iterator:
        assert_type(result.index, int)
        assert_type(result.item_id, str)
        assert_type(result.response, ResponseInfo | None)
        assert_type(result.retry_token, bytes | None)
        if result.outcome == "success":
            assert_type(result.value, User)
        elif result.outcome == "error":
            assert_type(result.error, ItemError)
    progress: ProtocolProgress = iterator.progress
    iterator.close()
    with client.protocols.tags.put.iterate(iter(tags)) as managed:
        record = next(managed)
        assert_type(record, TagsPutResult)
        assert_type(record.item_id, None)
        match record:
            case TagsPutSuccess():
                assert_type(record.value, Tag)
            case TagsPutError():
                assert_type(record.error, ItemError)
            case TagsPutDeliveryUnknown():
                assert_type(record.outcome, Literal["delivery_unknown"])
    del progress


def unknown(error: BatchDeliveryUnknownError[TagsPutResult]) -> None:
    """Read a typed unknown delivery's records."""
    assert_type(error.partial_results, tuple[TagsPutResult, ...])
    assert_type(error.batch_indices, tuple[int, ...])


async def source(tags: list[Tag]) -> AsyncIterator[Tag]:
    """Yield tags asynchronously."""
    for tag in tags:
        yield tag


async def async_batches(client: AsyncClient, users: list[NewUser], tags: list[Tag]) -> None:
    """Send items with asyncio from synchronous and asynchronous sources."""
    iterator = client.protocols.users.create.iterate(users)
    assert_type(iterator, AsyncBatchIterator[UsersCreateResult])
    async for result in iterator:
        assert_type(result, UsersCreateResult)
    async with client.protocols.tags.put.iterate(source(tags)) as managed:
        assert_type(await anext(managed), TagsPutResult)
    await iterator.aclose()
