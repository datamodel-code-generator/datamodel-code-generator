from __future__ import annotations
from typing import Tuple
from pydantic import BaseModel


class Record(BaseModel):
    value: Tuple[()]


