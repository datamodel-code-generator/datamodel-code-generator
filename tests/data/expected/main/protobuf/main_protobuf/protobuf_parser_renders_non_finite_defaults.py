from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field, RootModel


class Model(RootModel[Any]):
    root: Any = Field(..., title='Model')


class NonFiniteDefaults(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    positive: Optional[float] = float('inf')
    negative: Optional[float] = float('-inf')
    not_a_number: Optional[float] = float('nan')
