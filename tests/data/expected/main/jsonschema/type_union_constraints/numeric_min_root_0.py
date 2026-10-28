# Type-specific union constraints

from __future__ import annotations

from pydantic import Field, RootModel, confloat, conint


class Root(RootModel[conint(ge=2) | confloat(ge=2.0)]):
    root: conint(ge=2) | confloat(ge=2.0) = Field(..., title='Root')
