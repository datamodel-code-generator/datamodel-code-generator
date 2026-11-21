from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from .. import bar
from .._internal import Cocoa, Source


class Chocolate(BaseModel):
    flavour: Optional[str] = None
    source: Optional[Source] = None
    cocoa: Optional[Cocoa] = None
    field: Optional[bar.Field] = None
