from __future__ import annotations

from pydantic import RootModel


class Field(RootModel[str]):
    root: str
