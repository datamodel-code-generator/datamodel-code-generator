# Protobuf option regression

from __future__ import annotations

from typing_extensions import NotRequired, TypedDict


class Config(TypedDict, closed=True):
    note: NotRequired[str]
    numbers: NotRequired[list[int]]
    child: NotRequired[Config]


class Payload(TypedDict, closed=True):
    text: NotRequired[str]
    """
    Docs [x(y)
    """
    second: NotRequired[str]
    count: NotRequired[int]
    joined: NotRequired[str]
    absent: NotRequired[str]