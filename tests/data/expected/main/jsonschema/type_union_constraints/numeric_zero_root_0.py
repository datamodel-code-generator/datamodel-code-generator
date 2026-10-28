# Type-specific union constraints

from __future__ import annotations

from pydantic import Field, RootModel, confloat, conint


class Root(RootModel[conint(ge=0) | confloat(ge=0.0)]):
    root: conint(ge=0) | confloat(ge=0.0) = Field(..., title='Root')
