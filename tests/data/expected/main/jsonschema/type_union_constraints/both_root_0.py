# Type-specific union constraints

from __future__ import annotations

from pydantic import Field, RootModel, conint, constr


class Root(RootModel[conint(ge=2) | constr(max_length=2)]):
    root: conint(ge=2) | constr(max_length=2) = Field(..., title='Root')
