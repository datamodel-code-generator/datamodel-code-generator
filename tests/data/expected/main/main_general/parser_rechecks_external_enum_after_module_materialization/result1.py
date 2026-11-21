from __future__ import annotations

from pydantic import BaseModel

from . import nested_state1


class Result1(BaseModel):
    state: nested_state1.NestedState1
