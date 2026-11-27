"""Runtime annotations that must retain structure through --input-model.

The classes after ``NestedRuntimeAnnotations`` name classes that Pydantic builds only with ``arbitrary_types_allowed``.
"""

from __future__ import annotations

from collections import UserDict
from collections.abc import Callable  # noqa: TC003  # Pydantic resolves this annotation at runtime.
from dataclasses import dataclass
from typing import Annotated, Any, Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, InstanceOf, RootModel
from pydantic.dataclasses import dataclass as pydantic_dataclass
from pydantic_core import core_schema
from typing_extensions import TypedDict

T = TypeVar("T")


class Wrapper(Generic[T]):
    """Arbitrary generic used to force x-python-type transport."""


class StructuredAnnotations(BaseModel):
    """Model containing a nested Callable and runtime symbols."""

    model_config = {"arbitrary_types_allowed": True}

    handler: Wrapper[Callable[[int, str], bool]]


class StructuredBase(BaseModel):
    """Base model whose field is transported into a generated $defs entry."""

    model_config = {"arbitrary_types_allowed": True}

    callback: Wrapper[Callable[[str], int]]


class StructuredChild(StructuredBase):
    """Child model combining inherited and array-nested runtime expressions."""

    handlers: list[Wrapper[Callable[[int], bool]]]


class RuntimeNamespace:
    """Namespace proving that a nested runtime class is not an import module."""

    class Nested:
        """Arbitrary nested runtime type."""


class NestedRuntimeAnnotations(BaseModel):
    """Model whose annotation must retain its runtime module and qualname."""

    model_config = {"arbitrary_types_allowed": True}

    value: RuntimeNamespace.Nested


class Thing:
    """A class no schema can describe."""


class Hooked:
    """A class that builds its own schema."""

    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> core_schema.CoreSchema:
        return core_schema.is_instance_schema(cls)


class Base(BaseModel):
    """A base class that allows the arbitrary types itself."""

    model_config = ConfigDict(arbitrary_types_allowed=True)


class Holder(BaseModel):
    """The issue: one field of a class that needs the config."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    thing: Thing


class Containers(BaseModel):
    """The class beside None, another type and in containers."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    name: str
    things: list[Thing]
    maybe: Optional[Thing] = None
    by_key: dict[str, Thing] = {}
    either: int | Thing = 0


class ListOnly(BaseModel):
    """Containers of the class alone: their schema type is written."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    things: list[Thing]
    by_key: dict[str, Thing]


class Outer(BaseModel):
    """A model that holds the model of a field of the class."""

    label: str
    holder: Holder


class OuterDataclass(BaseModel):
    """A model that holds a dataclass of a field of the class."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    inner: "DataclassHolder"


class HookedOnly(BaseModel):
    """A class with its own schema, and no config."""

    hooked: Hooked


class HookedAllowed(BaseModel):
    """A class with its own schema, and the config."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    hooked: Hooked


class InstanceOfOnly(BaseModel):
    """The instance check named in the annotation, no config."""

    thing: InstanceOf[Thing]


class UserDictHolder(BaseModel):
    """A class of the standard library that Pydantic builds an instance check of."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    nicknames: UserDict[str, int]


class Forbid(BaseModel):
    """A model with another config item."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
    thing: Thing


class Custom(Base):
    """A model whose base class allows the arbitrary types."""

    thing: Thing


class SubclassOf(BaseModel):
    """A subclass of the class: Pydantic builds it without the config."""

    kind: type[Thing]


class OptionalSubclassOf(BaseModel):
    """A subclass of the class beside None."""

    kind: Optional[type[Thing]] = None


class CallableOf(BaseModel):
    """A callable that takes the class."""

    function: Callable[[Thing], int]


class CallableReturning(BaseModel):
    """A callable that returns the class."""

    function: Callable[[int], Thing]


class AnyType(BaseModel):
    """A class object of any class, with the config."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    kind: type


class RootOptional(RootModel):
    """A root of the class beside None."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    root: Optional[Thing]


class RootUnion(RootModel):
    """A root of the class beside another type."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    root: int | Thing


class HoldsRoot(BaseModel):
    """A model that holds a root of the class."""

    root_field: RootOptional


class RootThing(RootModel):
    """A root of a class no schema can describe."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    root: Thing


class RootInstanceOf(RootModel):
    """A root of an ``InstanceOf`` class, with no config."""

    root: InstanceOf[Thing]


class RootHooked(RootModel):
    """A root of a class that builds its own schema."""

    root: Hooked


class HoldsRootThing(BaseModel):
    """A model that holds a root of a class no schema can describe."""

    a: RootThing
    b: int


class HoldsRootHooked(BaseModel):
    """A model that holds a root of a class that builds its own schema."""

    a: RootHooked


class PreservedUnion(BaseModel):
    """The class beside a type whose Python type the field keeps: the field's own type spells the union."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    value: Thing | set[int]


class OptionalPreservedUnion(BaseModel):
    """That union beside None."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    value: Optional[Thing | frozenset[int]] = None


class ListBesidePreserved(BaseModel):
    """A list of the class beside a type whose Python type the field keeps."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    value: list[Thing] | set[int]


class ConstrainedBranch(BaseModel):
    """A constrained list of the class beside another type."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    value: Annotated[list[Thing], Field(min_length=1)] | int


class PreservedUnionRoot(RootModel):
    """A root of the class beside a type whose Python type the root keeps."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    root: Thing | set[int]


class Parent(BaseModel):
    """A parent with a field of the class."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    thing: Thing


class Child(Parent):
    """A child with a field of the class beside the parent's."""

    other: Thing
    name: str


class ChildOnly(Parent):
    """A child that adds no field of the class."""

    name: str


@pydantic_dataclass(config=ConfigDict(arbitrary_types_allowed=True))
class PydanticDataclassHolder:
    """A Pydantic dataclass root."""

    name: str
    thing: Thing


@dataclass
class DataclassHolder:
    """A standard library dataclass root, which Pydantic reads the config of by ``__pydantic_config__``."""

    __pydantic_config__ = ConfigDict(arbitrary_types_allowed=True)
    name: str
    thing: Thing


class TypedDictHolder(TypedDict):
    """A TypedDict root, which Pydantic reads the config of by ``__pydantic_config__``."""

    __pydantic_config__ = ConfigDict(arbitrary_types_allowed=True)  # type: ignore[misc]
    name: str
    thing: Thing


OuterDataclass.model_rebuild()
