# Header
\
from __future__ import annotations

import os

from pydantic import BaseModel


class A(BaseModel):
    a: str | None = None
