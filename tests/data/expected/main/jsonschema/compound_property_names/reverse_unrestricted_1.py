# Compound property names

from __future__ import annotations

from typing import Any

from pydantic import Field, RootModel, constr


class Root(RootModel[dict[constr(pattern=r'^a') | str, Any]]):
    root: dict[constr(pattern=r'^a') | str, Any] = Field(..., title='Root')
