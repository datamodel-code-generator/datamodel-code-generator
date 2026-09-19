from __future__ import annotations
from collections.abc import Callable
from uuid import UUID
from typing import Literal, Optional
import external
from pydantic import BaseModel


class Bindings(BaseModel):
    handler: Callable[[UUID, Literal['uuid.UUID', True, 1]], str]
    variadic: Callable[..., str]
    pair: tuple[int, str]
    choice: UUID | str
    optional_choice: Optional[UUID | str] = None
    empty: Callable[[], bool]
    empty_tuple: tuple[()]
    native_field: external.Model.model_fields['items'].annotation.__args__[0].__annotations__['raw-key']


