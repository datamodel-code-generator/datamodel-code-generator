# Custom header
# More comments

from __future__ import annotations

from pydantic import BaseModel


class Model(BaseModel):
    name: str | None = None