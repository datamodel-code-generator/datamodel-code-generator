"""Nested model families retain collection annotations from their Python types."""

from dataclasses import dataclass
from typing import ForwardRef, FrozenSet, Optional

from pydantic import BaseModel
from typing_extensions import TypedDict


@dataclass
class DataInner:
    """A dataclass carrying a Python collection type."""

    values: FrozenSet[int]


class TypedInner(TypedDict):
    """A TypedDict carrying a Python collection type."""

    values: FrozenSet[int]


class ModelInner(BaseModel):
    """A Pydantic collection control."""

    values: FrozenSet[int]


@dataclass
class DataRoot:
    """A dataclass containing all three supported model families."""

    data: DataInner
    typed: TypedInner
    model: ModelInner


class TypedRoot(TypedDict):
    """A TypedDict containing all three supported model families."""

    data: DataInner
    typed: TypedInner
    model: ModelInner


class ModelRoot(BaseModel):
    """A Pydantic model containing all three supported model families."""

    data: DataInner
    typed: TypedInner
    model: ModelInner


@dataclass
class RecursiveNode:
    """A cyclic dataclass definition is supplemented once."""

    values: FrozenSet[int]
    next: Optional[ForwardRef("RecursiveNode", module=__name__)] = None


@dataclass
class Recursive:
    """A stable root containing a cyclic definition."""

    node: RecursiveNode


@dataclass
class DeepRoot:
    """A second dataclass layer retains leaf metadata."""

    inner: DataRoot


class LeftOwner:
    """Own the first class with an identical short name."""

    @dataclass
    class Inner:
        """Integer-valued collection definition."""

        values: FrozenSet[int]

    class Record(TypedDict):
        values: FrozenSet[int]


class RightOwner:
    """Own the second class with an identical short name."""

    @dataclass
    class Inner:
        """String-valued collection definition."""

        values: FrozenSet[str]

    class Record(TypedDict):
        values: FrozenSet[str]


@dataclass
class SameNames:
    """Both definition identities must remain distinct."""

    left: LeftOwner.Inner
    right: RightOwner.Inner
    left_typed: LeftOwner.Record
    right_typed: RightOwner.Record
