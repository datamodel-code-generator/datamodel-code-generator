# Protobuf option regression

from __future__ import annotations

from typing_extensions import NotRequired, TypedDict


class Payload(TypedDict, closed=True):
    text: NotRequired[str]
    second: NotRequired[str]
    marker: NotRequired[str]
    count: NotRequired[int]