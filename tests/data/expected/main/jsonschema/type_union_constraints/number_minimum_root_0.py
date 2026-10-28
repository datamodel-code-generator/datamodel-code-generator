# Type-specific union constraints

from __future__ import annotations

from pydantic import Field, RootModel, conint


class Root(RootModel[str | conint(ge=2)]):
    root: str | conint(ge=2) = Field(..., title='Root')
