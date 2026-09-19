from __future__ import annotations
from pydantic.experimental.missing_sentinel import MISSING
from typing import Annotated, Dict, List, Optional, Union
from pydantic import BaseModel, Field


class Defaults(BaseModel):
    missing: Union[str, MISSING] = MISSING
    explicit_null: Optional[str] = None
    literal: Optional[int] = 4
    items: Optional[List[int]] = [1, 2]
    mapping: Optional[Dict[str, bool]] = {'enabled': True}
    factory: Annotated[List[str], Field(default_factory=list)]
    required: bool


