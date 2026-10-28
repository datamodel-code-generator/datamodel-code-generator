from __future__ import annotations

from pydantic import BaseModel

from . import nested_state2


class Result2(BaseModel):
    state: nested_state2.NestedState2
