# Type-specific union constraints

from __future__ import annotations

from pydantic import Field, RootModel, conint


class Root(RootModel[conint(ge=2)]):
    root: conint(ge=2) = Field(..., title='Root')
