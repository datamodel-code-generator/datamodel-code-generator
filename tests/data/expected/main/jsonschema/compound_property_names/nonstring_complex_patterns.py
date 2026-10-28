# Compound property names

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Root(BaseModel):
    backreference: dict[dict[str, Any] | Any, int]
    end_anchor: dict[dict[str, Any] | Any, int]
    lookbehind: dict[dict[str, Any] | Any, int]
