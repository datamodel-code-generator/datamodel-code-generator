# Type-specific union constraints

from __future__ import annotations

from pydantic import Field, RootModel, conint, constr


class Root(RootModel[constr(pattern=r'^a') | conint(le=3) | None]):
    root: constr(pattern=r'^a') | conint(le=3) | None = Field(..., title='Root')
