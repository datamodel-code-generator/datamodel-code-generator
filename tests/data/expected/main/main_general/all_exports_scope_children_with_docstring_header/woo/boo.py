"""
Custom module docstring header.

This is a multi-line docstring used for testing.
"""

from __future__ import annotations

from pydantic import BaseModel

from .. import bar
from .._internal import Cocoa, Source


class Chocolate(BaseModel):
    flavour: str | None = None
    source: Source | None = None
    cocoa: Cocoa | None = None
    field: bar.FieldModel | None = None
