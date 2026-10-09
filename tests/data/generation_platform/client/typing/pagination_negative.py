"""Reject sync iteration of asyncio pagers, awaited factories, widened pagers, other options, and other pages."""

from __future__ import annotations

from pets import AsyncClient, Client
from pets.errors import SessionLimitError
from pets.options import SessionOptions
from pets.protocols import Page, Pager
from pets.types.users import ListUsersResponse
from pets.model_codecs import JSONValue
from pets_models import User


async def wrong_pagers(client: Client, async_client: AsyncClient, page: Page[User, object], state: JSONValue) -> None:
    """Reject each misuse of a helper, its pager, or its pages."""
    list(async_client.protocols.users.all.iterate())  # error
    await async_client.protocols.users.all.iterate()  # error
    items: Pager[object, ListUsersResponse] = client.protocols.users.all.iterate()  # error
    pages: Pager[User, object] = client.protocols.users.all.iterate()  # error
    client.protocols.users.all.iterate(pagination_options=SessionOptions())  # error
    client.protocols.users.all.page(response_media_type="application/json")  # error
    client.protocols.users.all.next_page(client.protocols.labels.all.page())  # error
    client.protocols.users.all.next_page(page)  # error
    page.items = ()  # error
    client.protocols.users.everyone = client.protocols.users.all  # error
    await async_client.protocols.users.all.resume(state)  # error
    exported: bytes = async_client.protocols.users.all.iterate().checkpoint()  # error
    client.protocols.users.all.resume(b"state")  # error
    client.protocols.users.all.resume(state, cursor=5)  # error
    client.protocols.users.all.resume(state, pagination_options=SessionOptions())  # error
    resumed: Pager[object, ListUsersResponse] = client.protocols.users.all.resume(state)  # error
    client.protocols.users.all.resume(page)  # error
    del items, pages, resumed, exported


def wrong_states(client: Client, limit: SessionLimitError) -> None:
    """Reject another helper's resume state as a pager's continuation."""
    client.protocols.users.all.resume(limit.resume_state)  # error
