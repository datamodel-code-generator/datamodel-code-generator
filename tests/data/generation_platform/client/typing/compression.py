"""Keep request compression settings typed on clients and inherited views, calls, and helpers."""

from __future__ import annotations

from typing import Literal

from pets import Client
from pets.options import ClientOptions, RequestOptions
from pets.types.items import CreateItemResponse
from pets_models import Item, Query
from typing_extensions import assert_type


def calls(client: Client) -> None:
    """Select or disable gzip on the client, with views and helpers inheriting it."""
    assert_type(ClientOptions(compression="gzip").compression, Literal["gzip"] | None)
    assert_type(ClientOptions(compression=None).compression, Literal["gzip"] | None)
    view = client.with_options(RequestOptions())
    created = view.items.create_item(body=Item(name="n"), options=RequestOptions())
    assert_type(created, CreateItemResponse)
    pages = client.protocols.items.search_all.iterate(body=Query(text="q"), options=RequestOptions())
    del pages
