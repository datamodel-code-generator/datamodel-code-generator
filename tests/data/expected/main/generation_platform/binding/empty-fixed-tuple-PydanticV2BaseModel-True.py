from __future__ import annotations
from pydantic import BaseModel


class Record(BaseModel):
    value: tuple[()]


