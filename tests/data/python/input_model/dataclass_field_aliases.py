"""A Pydantic dataclass whose fields keep Python types JSON Schema cannot spell, one of them under an alias."""

from collections import deque

from pydantic import BaseModel, Field
from pydantic.dataclasses import dataclass


@dataclass
class Inner:
    """Two deque fields; only the second has no alias."""

    my_items: deque[int] = Field(default_factory=deque, alias="myItems")
    other_items: deque[int] = Field(default_factory=deque)


class Outer(BaseModel):
    """A model holding the dataclass."""

    inner: Inner
