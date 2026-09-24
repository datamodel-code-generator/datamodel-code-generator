from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, condecimal, constr


class Record(BaseModel):
    value: constr(pattern=r"^a/b~c$")
    price: Optional[condecimal(multiple_of=Decimal("0.1"))] = Decimal("1.2")
    wrapped: constr(pattern=r"^word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word word $")
