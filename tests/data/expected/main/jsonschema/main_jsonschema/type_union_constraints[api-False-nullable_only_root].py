# Type-specific union constraints

from __future__ import annotations

from pydantic import Field, RootModel, constr


class Root(RootModel[constr(min_length=2) | None]):
    root: constr(min_length=2) | None = Field(..., title='Root')
