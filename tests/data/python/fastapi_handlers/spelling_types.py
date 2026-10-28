"""Library types of the type spelling server: a class named as a typing construct, and one read from a model field."""

from __future__ import annotations

from pydantic import BaseModel, RootModel


class Annotated(RootModel[str]):
    """A library string type whose name the models also import from typing."""


class List(RootModel[str]):
    """A library string type whose name the models also import from typing for lists."""


class Thing(BaseModel):
    """A library model that another library model's field holds."""

    w: int = 0


class Holder(BaseModel):
    """A library model whose field type the models name through its module."""

    items: list[Thing] = []
