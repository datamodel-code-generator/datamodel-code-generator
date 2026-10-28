# Type-specific union constraints

from __future__ import annotations

from pydantic import Field, RootModel, confloat, conint


class Root(RootModel[confloat(ge=2.0, le=5.0) | conint(ge=2, le=5) | None]):
    root: confloat(ge=2.0, le=5.0) | conint(ge=2, le=5) | None = Field(
        ..., title='Root'
    )
