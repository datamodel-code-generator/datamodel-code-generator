# RootModel input control

from __future__ import annotations

from pydantic import BaseModel
from root_models import ScalarRoot


class NestedRoot(BaseModel):
    payload: ScalarRoot
