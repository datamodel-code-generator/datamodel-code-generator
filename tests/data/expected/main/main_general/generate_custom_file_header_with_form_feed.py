"""Module docstring after form-feed whitespace."""
from __future__ import annotations

import os

from pydantic import BaseModel


class Model(BaseModel):
    s: str