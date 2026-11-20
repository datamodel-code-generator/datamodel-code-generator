"""Module docstring."""
from __future__ import annotations

import sys

from pydantic import BaseModel


class Model(BaseModel):
    id: int | None = None