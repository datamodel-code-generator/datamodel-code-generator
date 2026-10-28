# multiline custom ;
# header ;
# file ;

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Item(BaseModel):
    n: int


class Root(BaseModel):
    value: Item
    metadata: dict[str, Any] | None = {
        '$id': 'item.json',
        '$ref': 'unread-default.json',
        'examples': [{'$ref': 'unread-example.json'}],
    }
