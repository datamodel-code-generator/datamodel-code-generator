# Protobuf option regression

from __future__ import annotations

from typing_extensions import NotRequired, TypedDict


class Payload(TypedDict, closed=True):
    text: NotRequired[str]
    """
    Ordinary description
    """
    count: NotRequired[int]