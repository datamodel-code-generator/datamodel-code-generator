"""Keep response types through cache helpers, their results, their stores, and their entries, in both modes."""

from __future__ import annotations

from pets import AsyncClient, Client
from pets.options import ClientOptions, ProtocolClientOptions, RequestOptions
from pets.protocols import (
    AsyncCacheStore,
    AsyncMemoryCacheStore,
    CacheEntry,
    CacheOptions,
    CacheResult,
    CacheStore,
    MemoryCacheStore,
)
from pets.responses import ResponseInfo
from pets.types.secure import GetSecureUserResponse
from pets.types.users import (
    GetUserResponse,
    ListUsersResponse,
)
from pets_models import FieldSecureUsersUserIdGetPathUserIdParameter, FieldUsersUserIdGetPathUserIdParameter
from typing_extensions import Literal, assert_type


def cached_client() -> Client:
    """Lend a bounded memory store to the users.profile helper, which keeps its entries there."""
    store = MemoryCacheStore(max_entries=1000)
    return Client(options=ClientOptions(protocols=ProtocolClientOptions(cache_stores={"users.profile": store})))


def stores() -> tuple[CacheStore, AsyncCacheStore]:
    """Accept the memory stores as stores of the contract in each mode."""
    return MemoryCacheStore(), AsyncMemoryCacheStore()


def fetch(
    client: Client,
    entry: CacheEntry,
    three: FieldUsersUserIdGetPathUserIdParameter,
    one: FieldSecureUsersUserIdGetPathUserIdParameter,
) -> None:
    """Fetch typed results and read their metadata."""
    helper = client.protocols.users.profile
    result = helper.fetch(user_id=three, cache_options=CacheOptions(max_ttl=60), options=RequestOptions())
    assert_type(result, CacheResult[GetUserResponse])
    assert_type(result.data, GetUserResponse)
    assert_type(result.source, Literal["network", "fresh_cache", "revalidated"])
    assert_type(result.response, ResponseInfo)
    assert_type(result.network_status, int | None)
    wider: CacheResult[object] = result
    assert_type(client.protocols.users.listing.fetch(), CacheResult[ListUsersResponse])
    assert_type(client.protocols.secure.profile.fetch(user_id=one).data, GetSecureUserResponse)
    assert_type(entry.body, bytes)
    assert_type(entry.vary_values, tuple[tuple[str, ...], ...])
    del wider


async def afetch(client: AsyncClient, three: FieldUsersUserIdGetPathUserIdParameter) -> None:
    """Await the asyncio helpers, keeping the same result types."""
    helper = client.protocols.users.profile
    assert_type(await helper.fetch(user_id=three), CacheResult[GetUserResponse])
