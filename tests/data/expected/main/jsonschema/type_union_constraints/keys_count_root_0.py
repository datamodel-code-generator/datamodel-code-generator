# Type-specific union constraints

from __future__ import annotations

from typing import Any

from pydantic import Field, RootModel, constr


class Root(RootModel[dict[constr(pattern=r'^a'), Any] | list[Any] | None]):
    root: dict[constr(pattern=r'^a'), Any] | list[Any] | None = Field(..., title='Root')
