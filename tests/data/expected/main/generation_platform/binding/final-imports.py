from __future__ import annotations
from palette import Color
from pydantic import BaseModel
from pydantic.types import StrictStr
from palette import Color


class Record(BaseModel):
    value: StrictStr
    color: Color


