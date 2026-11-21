# Protobuf detection regression

from __future__ import annotations

from typing_extensions import NotRequired, TypedDict


class M(TypedDict, closed=True):
    value: NotRequired[str]
    count: NotRequired[int]