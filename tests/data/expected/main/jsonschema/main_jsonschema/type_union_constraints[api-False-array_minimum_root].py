# Type-specific union constraints

from __future__ import annotations

from typing import Any

from pydantic import Field, RootModel


class Root(RootModel[list[Any] | dict[str, Any]]):
    root: list[Any] | dict[str, Any] = Field(..., title='Root')
