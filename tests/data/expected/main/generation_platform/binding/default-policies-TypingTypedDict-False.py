from __future__ import annotations
from typing_extensions import NotRequired
from typing import TypedDict



class Defaults(TypedDict):
    implicit: NotRequired[str]
    schema_null: NotRequired[str]
    override_null: NotRequired[str]
    nullable: NotRequired[str]
    required_value: int
    required_nullable: str