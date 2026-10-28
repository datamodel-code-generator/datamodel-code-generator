"""Root and ordinary Python model input controls."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, RootModel


class ScalarRoot(RootModel[int]):
    """ScalarRoot input control."""


class ScalarChild(ScalarRoot):
    """ScalarChild input control."""


class BoundedRoot(RootModel[Annotated[int, Field(ge=2, le=5)]]):
    """BoundedRoot input control."""


class DefaultRoot(RootModel[int]):
    """DefaultRoot input control."""

    root: int = 3


class ListRoot(RootModel[list[int]]):
    """ListRoot input control."""


class BoundedList(RootModel[Annotated[list[int], Field(min_length=1, max_length=3)]]):
    """BoundedList input control."""


class ListDefault(RootModel[list[int]]):
    """ListDefault input control."""

    root: list[int] = [2, 1]


class NullableRoot(RootModel[int | None]):
    """NullableRoot input control."""


class Item(BaseModel):
    """Item input control."""

    first: int
    second: str = "default"


class ObjectRoot(RootModel[Item]):
    """ObjectRoot input control."""


class ObjectList(RootModel[list[Item]]):
    """ObjectList input control."""


class OrdinaryBase(BaseModel):
    """OrdinaryBase input control."""

    first: int


class OrdinaryChild(OrdinaryBase):
    """OrdinaryChild input control."""

    second: str = "default"


class NestedRoot(BaseModel):
    """NestedRoot input control."""

    payload: ScalarRoot
