"""Module header using syntax from the target runtime."""
from __future__ import annotations

type HeaderAlias[T] = list[T]

from pydantic import BaseModel


class Model(BaseModel):
    s: str