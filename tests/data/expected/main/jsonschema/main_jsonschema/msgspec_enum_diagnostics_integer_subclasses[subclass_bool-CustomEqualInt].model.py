# Integer subclass control

from __future__ import annotations

from enum import Enum

from msgspec import Struct


class Value(Enum):
    member_0 = 0
    member_1 = False


class Payload(Struct):
    value: Value
