# Integer subclass control

from __future__ import annotations

from enum import Enum

from msgspec import Struct


class Value(Enum):
    member_0 = 0
    member_1 = False
    member_2 = 1
    member_3 = 1.0


class Payload(Struct):
    value: Value
