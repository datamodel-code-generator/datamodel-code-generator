from __future__ import annotations
from typing import Dict
from pydantic import Field, RootModel, constr


class Alias(RootModel[Dict[constr(pattern=r'^x'), int]]):
    root: Dict[constr(pattern=r'^x'), int] = Field(..., min_length=1)


class Names(RootModel[Dict[constr(pattern=r'^x'), int]]):
    root: Dict[constr(pattern=r'^x'), int]


