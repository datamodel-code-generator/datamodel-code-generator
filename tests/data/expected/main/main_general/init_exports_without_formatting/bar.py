from __future__ import annotations
from pydantic import Field, RootModel


class FieldModel(RootModel[str]):
    root: str = Field(..., examples=['green'])