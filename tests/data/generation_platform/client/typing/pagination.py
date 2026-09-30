"""Keep item and page types through pagination helpers, their pagers, and their pages, with sync and asyncio clients."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

from pets import AsyncClient, Client
from pets.options import RequestOptions, SessionOptions
from pets.protocols import AsyncPager, Continuation, Page, Pager, PaginationOptions, ProtocolProgress
from pets.responses import ResponseInfo
from pets.types.labels import ListLabelsResponse
from pets.types.users import ListUsersResponse, SearchUsersResponse
from pets_models import Label, User, UserQuery
from typing_extensions import assert_type


def pages(client: Client, query: UserQuery) -> None:
    """Yield typed items and pages, and continue a page with the same types."""
    helper = client.protocols.users.all
    pager = helper.iterate(
        pagination_options=PaginationOptions(max_items=10),
        options=RequestOptions(),
        session_options=SessionOptions(max_network_sends=5),
    )
    assert_type(pager, Pager[User, ListUsersResponse])
    items: Iterator[User] = pager
    for user in pager:
        assert_type(user, User)
    for page in pager.iter_pages():
        assert_type(page, Page[User, ListUsersResponse])
        assert_type(page.items, tuple[User, ...])
        assert_type(page.data, ListUsersResponse)
        assert_type(page.response, ResponseInfo)
        assert_type(page.continuation, Continuation | None)
    progress: ProtocolProgress = pager.progress
    with helper.iterate() as managed:
        assert_type(managed, Pager[User, ListUsersResponse])
    first = helper.page()
    assert_type(first, Page[User, ListUsersResponse])
    assert_type(helper.next_page(first), Page[User, ListUsersResponse] | None)
    wider: Page[object, object] = first
    assert_type(client.protocols.labels.all.iterate(), Pager[Label, ListLabelsResponse])
    assert_type(client.protocols.users.search.page(body=query), Page[User, SearchUsersResponse])
    del items, progress, wider


async def async_pages(client: AsyncClient) -> None:
    """Yield typed items and pages with asyncio, without awaiting the pager or its page iterator."""
    helper = client.protocols.users.all
    pager = helper.iterate()
    assert_type(pager, AsyncPager[User, ListUsersResponse])
    items: AsyncIterator[User] = pager
    async for user in pager:
        assert_type(user, User)
    pages = pager.iter_pages()
    assert_type(pages, AsyncIterator[Page[User, ListUsersResponse]])
    async for page in pages:
        assert_type(page.items, tuple[User, ...])
    async with helper.iterate() as managed:
        assert_type(managed, AsyncPager[User, ListUsersResponse])
    first = await helper.page()
    assert_type(await helper.next_page(first), Page[User, ListUsersResponse] | None)
    await pager.aclose()
    del items
