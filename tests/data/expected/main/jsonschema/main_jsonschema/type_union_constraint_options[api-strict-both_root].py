# Type-specific union constraints

from __future__ import annotations

from pydantic import Field, RootModel, conint, constr


class Root(RootModel[conint(ge=2, strict=True) | constr(max_length=2, strict=True)]):
    root: conint(ge=2, strict=True) | constr(max_length=2, strict=True) = Field(
        ..., title='Root'
    )
