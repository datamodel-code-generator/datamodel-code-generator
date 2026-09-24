from __future__ import annotations
from typing import Any
from pydantic import RootModel


class Error(RootModel[Any]):
    root: Any


class FieldPetsGetResponse(RootModel[Error]):
    root: Error