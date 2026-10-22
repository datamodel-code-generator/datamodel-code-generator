from __future__ import annotations

from datetime import date as date_aliased
from typing import Optional

from pydantic import AwareDatetime, BaseModel


class TypeDate(BaseModel):
    date: Optional[date_aliased]
    datetime: Optional[AwareDatetime] = None
