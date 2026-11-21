from __future__ import annotations
from typing import Optional
from pydantic import BaseModel
from .._internal import Cocoa, Source
from .. import bar


class Chocolate(BaseModel):
    flavour: Optional[str] = None
    source: Optional[Source] = None
    cocoa: Optional[Cocoa] = None
    field: Optional[bar.FieldModel] = None