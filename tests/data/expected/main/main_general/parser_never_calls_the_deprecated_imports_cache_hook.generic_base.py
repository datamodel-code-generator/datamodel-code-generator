from __future__ import annotations

from typing import Optional

from pydantic import BaseModel as _BaseModel
from pydantic import ConfigDict


class BaseModel(_BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )


class Foo(BaseModel):
    model_config = ConfigDict(
        extra='allow',
    )
    x: Optional[int] = None


class Bar(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    y: Optional[int] = None


class Baz(BaseModel):
    z: Optional[int] = None


class Test(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    foo: Foo
    bar: Optional[Bar] = None
    baz: Optional[Baz] = None
