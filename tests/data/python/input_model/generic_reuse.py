"""Real specialized Pydantic models with behavior absent from JSON Schema."""
from typing import Generic, Literal, TypeVar
from pydantic import BaseModel, field_validator
T = TypeVar('T')
U = TypeVar('U')
class Box(BaseModel, Generic[T]):
    value: T
    @field_validator('value')
    @classmethod
    def positive(cls, value):
        if isinstance(value, int) and value <= 0:
            raise ValueError('must be positive')
        return value
class Item(BaseModel):
    count: int
class Pair(BaseModel, Generic[T, U]):
    first: T
    second: U
class NamedBox(Box[T], Generic[T]):
    @classmethod
    def model_parametrized_name(cls, params):
        return 'Special'
class Outer:
    class Item(BaseModel):
        count: int
    class Wrapped(Box[T], Generic[T]):
        pass
class ChangingOwner:
    class Packet(Box[T], Generic[T]):
        pass
Packet = ChangingOwner.Packet
ChangingOwner = object()
class IntRoot(BaseModel):
    box: Box[int]
class RepeatedRoot(BaseModel):
    first: Box[int]
    second: Box[int]
    strings: Box[str]
    items: list[Box[int]]
class ModelRoot(BaseModel):
    box: Box[Item]
class NestedRoot(BaseModel):
    box: Box[Box[int]]
class ArgumentsRoot(BaseModel):
    box: Box[list[Outer.Item]]
    choice: Box[Literal['x', 'y']]
    pair: Pair[int, str]
class QualifiedRoot(BaseModel):
    box: Outer.Wrapped[int]
class ExportRoot(BaseModel):
    box: Packet[int]
class NamedRoot(BaseModel):
    number: NamedBox[int]
    text: NamedBox[str]
class PlainRoot(BaseModel):
    item: Item
class BaseRoot(BaseModel):
    base: Box[int]
class ChildRoot(BaseRoot):
    child: Box[str]

from dataclasses import dataclass
from typing_extensions import TypedDict
@dataclass
class DataclassRoot:
    box: Box[int]
class TypedRoot(TypedDict):
    box: Box[int]

from enum import Enum, IntEnum
class Tag(str, Enum):
    A = 'a'
class EnumRoot(BaseModel):
    box: Box[Literal[Tag.A]]

@dataclass
class PlainDataclassRoot:
    item: Item

class Special(BaseModel):
    text: str
class MixedRoot(BaseModel):
    box: NamedBox[int]
    ordinary: Special

from collections import deque
from typing import Annotated
from pydantic import Field
class PreservedRoot(BaseModel):
    first: deque[NamedBox[int]]
    second: deque[NamedBox[str]]
    optional: deque[NamedBox[int]] | None = None
    annotated: Annotated[deque[NamedBox[int]], Field(min_length=1)]
    nested_annotation: list[Annotated[deque[NamedBox[int]], "marker"]]
class PreservedChild(PreservedRoot):
    child: Box[str]
@dataclass
class PreservedDataclass:
    items: deque[NamedBox[int]]

Positive = Annotated[int, Field(gt=3)]
class AnnotatedAliasRoot(BaseModel):
    box: Box[Positive]
class AnnotatedInlineRoot(BaseModel):
    box: Box[Annotated[int, Field(gt=3)]]
class AnnotatedNestedRoot(BaseModel):
    box: Box[Box[Annotated[int, Field(gt=3)]]]
class AnnotatedContainersRoot(BaseModel):
    boxes: list[NamedBox[Annotated[int, Field(gt=3)]]]
    deque_boxes: deque[NamedBox[Annotated[int, Field(gt=4)]]]
class AnnotatedRepeatedRoot(BaseModel):
    first: Box[Positive]
    second: Box[Positive]
@dataclass
class AnnotatedDataclassRoot:
    box: Box[Annotated[int, Field(gt=3)]]
class AnnotatedTypedRoot(TypedDict):
    box: Box[Annotated[int, Field(gt=3)]]

class AnnotatedWrapperRoot(BaseModel):
    boxes: list[Annotated[NamedBox[Positive], "marker"]]


def _local_enum_model():
    class LocalTag(str, Enum):
        A = "a"

    class LocalNumber(IntEnum):
        ONE = 1
        ZERO = 0

    class LocalEnumRoot(BaseModel):
        box: Box[Literal[LocalTag.A]]
        validated: Box[Literal[LocalNumber.ONE, LocalNumber.ZERO]]

    @dataclass
    class LocalEnumDataclass:
        box: Box[Literal[LocalTag.A]]
        validated: Box[Literal[LocalNumber.ONE, LocalNumber.ZERO]]

    class LocalEnumTyped(TypedDict):
        box: Box[Literal[LocalTag.A]]
        validated: Box[Literal[LocalNumber.ONE, LocalNumber.ZERO]]

    return LocalEnumRoot, LocalEnumDataclass, LocalEnumTyped


LocalEnumRoot, LocalEnumDataclass, LocalEnumTyped = _local_enum_model()


class DefaultBox(Box[T], Generic[T]):
    value: T = 4
    label: str = 'kept'

    @field_validator('value')
    @classmethod
    def not_five(cls, value):
        if value == 5:
            raise ValueError('must not be five')
        return value
