# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Item(BaseModel):
    # Template fields: n
    n: int


class Root(BaseModel):
    # Template fields: value metadata
    value: Item
    metadata: dict[str, Any] | None = {
        '$id': 'item.json',
        '$ref': 'unread-default.json',
        'examples': [{'$ref': 'unread-example.json'}],
    }
