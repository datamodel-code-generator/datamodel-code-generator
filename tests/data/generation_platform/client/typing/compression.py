"""Keep request compression settings typed on clients, views, calls, and helper calls."""

from __future__ import annotations

from pets import Client
from pets.options import ClientOptions, RequestOptions, Unset
from pets.types.items import CreateItemResponse
from pets_models import Item, Query
from typing_extensions import assert_type


def calls(client: Client) -> None:
    """Select gzip on a client, turn it off for a view, and select it again for a call and a helper call."""
    assert_type(ClientOptions(compression="gzip").compression, str | Unset | None)
    view = client.with_options(RequestOptions(compression=None))
    created = view.items.create_item(body=Item(name="n"), options=RequestOptions(compression="gzip"))
    assert_type(created, CreateItemResponse)
    pages = client.protocols.items.search_all.iterate(body=Query(text="q"), options=RequestOptions(compression="gzip"))
    del pages
