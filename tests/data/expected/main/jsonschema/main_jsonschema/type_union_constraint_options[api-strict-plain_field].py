# Type-specific union constraints

from __future__ import annotations

from pydantic import BaseModel, StrictInt, StrictStr


class Root(BaseModel):
    before: bool | None = True
    value: StrictStr | StrictInt
    after: StrictInt | None = 7
