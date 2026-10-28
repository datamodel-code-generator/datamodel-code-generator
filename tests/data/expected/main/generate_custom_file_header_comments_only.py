# Header comment containing """quotes""" and a semicolon;
# Another comment containing 'quotes'; nothing here is Python syntax.

from __future__ import annotations

from pydantic import BaseModel


class Model(BaseModel):
    s: str