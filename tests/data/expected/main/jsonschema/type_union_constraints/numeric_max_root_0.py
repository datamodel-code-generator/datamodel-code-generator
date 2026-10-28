# Type-specific union constraints

from __future__ import annotations

from pydantic import Field, RootModel, confloat, conint


class Root(RootModel[confloat(le=5.0) | conint(le=5)]):
    root: confloat(le=5.0) | conint(le=5) = Field(..., title='Root')
