from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel


class Special(Enum):
    number_inf = float('inf')
    number__inf = float('-inf')
    number_nan = float('nan')


class Model(BaseModel):
    special: Optional[Special] = Special.number_nan
