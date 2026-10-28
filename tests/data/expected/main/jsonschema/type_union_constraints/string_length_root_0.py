# Type-specific union constraints

from __future__ import annotations

from pydantic import Field, RootModel, constr


class Root(RootModel[constr(min_length=2) | int]):
    root: constr(min_length=2) | int = Field(..., title='Root')
