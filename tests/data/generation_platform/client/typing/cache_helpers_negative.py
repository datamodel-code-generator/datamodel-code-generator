"""Reject unawaited asyncio fetches, other options, other argument types, and narrowed or mutated results."""

from __future__ import annotations

from pets import AsyncClient, Client
from pets.options import SessionOptions
from pets.protocols import CacheResult, PaginationOptions
from pets.types.users import GetUserRequestCodecs, GetUserResponse, ListUsersResponse


async def wrong_fetches(client: Client, async_client: AsyncClient, result: CacheResult[GetUserResponse]) -> None:
    """Reject each misuse of a cache helper or its results."""
    user = GetUserRequestCodecs.parameter(location="path", name="userId").from_wire(3)
    helper = client.protocols.users.profile
    unawaited: CacheResult[GetUserResponse] = async_client.protocols.users.profile.fetch(user_id=user)  # error
    helper.fetch(user_id=b"three")  # error
    helper.fetch(user_id=user, cache_options=PaginationOptions())  # error
    helper.fetch(user_id=user, session_options=SessionOptions())  # error
    helper.fetch()  # error
    helper.invalidate("users")  # error
    helper.mutations.rename(user_id=user)  # error
    listing: CacheResult[ListUsersResponse] = helper.fetch(user_id=user)  # error
    result.source = "network"  # error
    client.protocols.users.listing.mutations.create()  # error
    del unawaited, listing
