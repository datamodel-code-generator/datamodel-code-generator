"""Open enum bases: an Enum that keeps a value it does not list as a member of its own."""

from __future__ import annotations

from enum import Enum, IntEnum, StrEnum


class OpenStrEnum(StrEnum):
    """A StrEnum that keeps a string it does not list."""

    @classmethod
    def _missing_(cls, value: object) -> OpenStrEnum | None:
        if not isinstance(value, str):
            return None
        member = str.__new__(cls, value)
        member._name_ = value
        member._value_ = value
        return cls._value2member_map_.setdefault(value, member)


class OpenIntEnum(IntEnum):
    """An IntEnum that keeps an integer it does not list."""

    @classmethod
    def _missing_(cls, value: object) -> OpenIntEnum | None:
        if not isinstance(value, int) or isinstance(value, bool):
            return None
        member = int.__new__(cls, value)
        member._name_ = f"VALUE_{value}"
        member._value_ = value
        return cls._value2member_map_.setdefault(value, member)


class OpenEnum(Enum):
    """An Enum that keeps a string it does not list; after a value type (str, int), a value of that type."""

    @classmethod
    def _missing_(cls, value: object) -> OpenEnum | None:
        member_type = cls._member_type_
        if not isinstance(value, str if member_type is object else member_type) or isinstance(value, bool):
            return None
        member = object.__new__(cls) if member_type is object else member_type.__new__(cls, value)
        member._name_ = str(value)
        member._value_ = value
        return cls._value2member_map_.setdefault(value, member)
