"""Independent native models for explicitly declared frozen sets."""
from __future__ import annotations

from enum import Enum
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID
from typing import ClassVar, Literal, Optional

from pydantic import AwareDatetime, BaseModel, ConfigDict, NaiveDatetime, RootModel


class IdentityBase(BaseModel):
    __hash__ = object.__hash__


class Integer(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: int


class Inherited(Integer):
    label: Optional[str] = 'x'


class Nested(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: Integer


class Recursive(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: int
    child: Optional[Recursive] = None


class TupleItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: tuple[int, ...]


class LiteralItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: Literal['a', 'b']


class Value(Enum):
    a = 'a'
    b = 'b'


class EnumItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: Value


class Classvar(Integer):
    shared: ClassVar[list[int]] = []


class MutableBase(BaseModel):
    value: int


class FrozenChild(MutableBase):
    model_config = ConfigDict(frozen=True)
    label: Optional[str] = 'x'


MODELS = {
    'inherited_mutable_base': FrozenChild,
    'integer': Integer, 'nested': Nested, 'inherited': Inherited,
    'recursive': Recursive, 'tuple': TupleItem, 'literal': LiteralItem,
    'enum': EnumItem, 'classvar': Classvar, 'packaged': Integer,
}


class DateItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: date


class DatetimeItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: AwareDatetime


class LocalDatetimeItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: datetime


class NaiveDatetimeItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: NaiveDatetime


class TimeItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: time


class DurationItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: timedelta


class UUIDItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: UUID


class DecimalItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: Decimal


class OptionalDateItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: Optional[date]


class NestedDateItem(BaseModel):
    model_config = ConfigDict(frozen=True)
    value: DateItem


class UnhashableDate(date):
    __hash__ = None


class UnhashableUUID(UUID):
    __hash__ = None


MODELS.update({
    'opaque': DateItem, 'standard_datetime': DatetimeItem,
    'standard_plain_datetime': LocalDatetimeItem, 'standard_local_datetime': NaiveDatetimeItem, 'standard_naive_datetime': NaiveDatetimeItem,
    'standard_time': TimeItem, 'standard_duration': DurationItem, 'standard_uuid': UUIDItem,
    'standard_decimal': DecimalItem, 'standard_optional': OptionalDateItem,
    'standard_nested': NestedDateItem, 'standard_inherited': DateItem,
})
SUBCLASS_VALUES = {
    'opaque': UnhashableDate(2024, 1, 2),
    'standard_uuid': UnhashableUUID('550e8400-e29b-41d4-a716-446655440000'),
}


class FixedTupleItem(TupleItem):
    """Native frozen model for the transported tuple comparison."""

    value: tuple[int, str]


class NestedTupleItem(TupleItem):
    """Native frozen model for the transported tuple comparison."""

    value: tuple[tuple[int, str], frozenset[int]]


class OptionalTupleItem(TupleItem):
    """Native frozen model for the transported tuple comparison."""

    value: tuple[int | None, ...]


class EmptyTupleItem(TupleItem):
    """Native frozen model for the transported tuple comparison."""

    value: tuple[()]


class FrozenTupleItem(TupleItem):
    """Native frozen model for the transported tuple comparison."""

    value: tuple[frozenset[int]]


class ScalarTupleItem(TupleItem):
    """Native frozen model for the transported tuple comparison."""

    value: tuple[float, bool, str, None]


MODELS.update({
    "tuple_fixed": FixedTupleItem,
    "tuple_typing": FixedTupleItem,
    "tuple_builtin": FixedTupleItem,
    "tuple_nested": NestedTupleItem,
    "tuple_optional": OptionalTupleItem,
    "tuple_empty": EmptyTupleItem,
    "tuple_frozen_typing": FrozenTupleItem,
    "tuple_scalars": ScalarTupleItem,
    "tuple_packaged": TupleItem,
})

MODELS["tuple_collision"] = TupleItem

MODELS["transported_date"] = DateItem


class TransportedDateTuple(TupleItem):
    """Native frozen tuple of canonical immutable values."""

    value: tuple[date, ...]


MODELS["transported_date_tuple"] = TransportedDateTuple
MODELS["transported_datetime"] = LocalDatetimeItem


class TransportedDatetimeTuple(TupleItem):
    """Native frozen tuple of canonical immutable values."""

    value: tuple[datetime, ...]


MODELS["transported_datetime_tuple"] = TransportedDatetimeTuple
MODELS["transported_time"] = TimeItem


class TransportedTimeTuple(TupleItem):
    """Native frozen tuple of canonical immutable values."""

    value: tuple[time, ...]


MODELS["transported_time_tuple"] = TransportedTimeTuple
MODELS["transported_duration"] = DurationItem


class TransportedDurationTuple(TupleItem):
    """Native frozen tuple of canonical immutable values."""

    value: tuple[timedelta, ...]


MODELS["transported_duration_tuple"] = TransportedDurationTuple
MODELS["transported_decimal"] = DecimalItem


class TransportedDecimalTuple(TupleItem):
    """Native frozen tuple of canonical immutable values."""

    value: tuple[Decimal, ...]


MODELS["transported_decimal_tuple"] = TransportedDecimalTuple
MODELS["transported_uuid"] = UUIDItem


class TransportedUuidTuple(TupleItem):
    """Native frozen tuple of canonical immutable values."""

    value: tuple[UUID, ...]


MODELS["transported_uuid_tuple"] = TransportedUuidTuple
MODELS["transported_aware"] = DatetimeItem


class TransportedAwareTuple(TupleItem):
    """Native frozen tuple of canonical immutable values."""

    value: tuple[AwareDatetime, ...]


MODELS["transported_aware_tuple"] = TransportedAwareTuple
MODELS["transported_naive"] = NaiveDatetimeItem


class TransportedNaiveTuple(TupleItem):
    """Native frozen tuple of canonical immutable values."""

    value: tuple[NaiveDatetime, ...]


MODELS["transported_naive_tuple"] = TransportedNaiveTuple

MODELS["tuple_date"] = TransportedDateTuple
MODELS["standard_opaque_date"] = DateItem
MODELS["transported_collision"] = DateItem

SUBCLASS_VALUES["transported_date"] = SUBCLASS_VALUES["opaque"]
SUBCLASS_VALUES["transported_uuid"] = SUBCLASS_VALUES["standard_uuid"]


class FrozenList(Integer):
    value: list[int]


class MutableRoot(RootModel[int]):
    pass


class FrozenRoot(MutableRoot):
    model_config = ConfigDict(frozen=True)


MODELS.update({
    "mutable": MutableBase,
    "list": FrozenList,
    "root_mutable": MutableRoot,
    "root_frozen": FrozenRoot,
})


class CustomHashBase(BaseModel):
    def __hash__(self) -> int:
        return hash(self.model_dump_json())


class CustomHash(CustomHashBase):
    value: int


MODELS["custom_hash"] = CustomHash


class UnhashableInt(int):
    __hash__ = None


class UnhashableStr(str):
    __hash__ = None


class UnhashableFloat(float):
    __hash__ = None


class UnhashableBytes(bytes):
    __hash__ = None


class UnhashableTuple(tuple):
    __hash__ = None


class UnhashableFrozenSet(frozenset):
    __hash__ = None


class BytesItem(Integer):
    value: bytes


class PrimitiveUnion(Integer):
    value: int | float | str | bool | None


class BoundUnion(Integer):
    value: int | str


MODELS.update({'bytes': BytesItem, 'primitive_union': PrimitiveUnion, 'bound_union': BoundUnion})
SUBCLASS_VALUES['bytes'] = UnhashableBytes(b'x')
HASH_SUBCLASS_CASES = [
    *((name, value, False) for name, value in SUBCLASS_VALUES.items()),
    ('integer', UnhashableInt(1), True),
    ('tuple', UnhashableTuple((UnhashableInt(1),)), True),
    ('tuple_fixed', (UnhashableInt(1), UnhashableStr('x')), True),
    ('tuple_optional', (UnhashableInt(1), None), True),
    ('tuple_frozen_typing', (UnhashableFrozenSet((1,)),), True),
    ('tuple_scalars', (UnhashableFloat(1.5), True, UnhashableStr('x'), None), True),
    # Pydantic 2.0 preserves these subclasses; newer versions normalize them.
    ('primitive_union', UnhashableStr('a'), None),
    ('bound_union', UnhashableInt(1), None),
]
