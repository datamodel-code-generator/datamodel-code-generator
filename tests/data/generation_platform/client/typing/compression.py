"""Keep request compression settings typed on clients and inherited views, calls, and helpers."""

from __future__ import annotations

from pets import AsyncClient, Client
from pets.options import RequestOptions
from pets.types.items import CreateItemResponse
from pets_models import Item, Query
from typing_extensions import assert_type


def calls(client: Client) -> None:
    """Select or disable gzip on the client, with views and helpers inheriting it."""
    Client(compression="gzip")
    AsyncClient(compression=None)
    view = client.with_options(max_retries=0)
    created = view.items.create_item(body=Item(name="n"), options=RequestOptions())
    assert_type(created, CreateItemResponse)
    pages = client.protocols.items.search_all.iterate(body=Query(text="q"), options=RequestOptions())
    del pages
