# Type-specific union constraints

from __future__ import annotations

from typing import Any

from pydantic import Field, RootModel


class Root(RootModel[dict[str, Any] | list[Any]]):
    root: dict[str, Any] | list[Any] = Field(..., title='Root')
