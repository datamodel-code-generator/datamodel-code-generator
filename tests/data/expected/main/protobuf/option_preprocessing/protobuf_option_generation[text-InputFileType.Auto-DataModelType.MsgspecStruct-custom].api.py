# Protobuf option regression

from __future__ import annotations

from typing import Annotated

from msgspec import UNSET, Meta, Struct, UnsetType, field


class Config(Struct):
    note: str | UnsetType = UNSET
    numbers: list[int] | UnsetType = field(default_factory=list)
    child: Config | UnsetType = UNSET


class Payload(Struct):
    text: Annotated[str, Meta(description='Docs [x(y)')] | UnsetType = 'x(y),z]\\\'"'
    """
    Docs [x(y)
    """
    second: str | UnsetType = 'a\\"\'[,]'
    count: int | UnsetType = -7
    joined: str | UnsetType = 'ab'
    absent: str | UnsetType = UNSET