from __future__ import annotations
from pydantic import BaseModel, condecimal
from decimal import Decimal
from typing import Optional


class Prices(BaseModel):
    price: Optional[condecimal(ge=Decimal('-1.5'), le=Decimal('12.3'), multiple_of=Decimal('0.1'))] = None


