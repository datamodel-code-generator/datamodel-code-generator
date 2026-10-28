# Protobuf option regression

from __future__ import annotations

from typing import Annotated

from msgspec import Meta, Struct, UnsetType


class Payload(Struct):
    text: Annotated[str, Meta(description='Ordinary description')] | UnsetType = 'ok'
    """
    Ordinary description
    """
    count: int | UnsetType = 7