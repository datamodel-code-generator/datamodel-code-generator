from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, RootModel


class AnchorsLibrary(BaseModel):
    self: Optional[AnchorsLibrary] = None
    value: Optional[str] = None


class Root(RootModel[AnchorsLibrary]):
    root: AnchorsLibrary