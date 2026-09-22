from __future__ import annotations
from typing_extensions import NotRequired
from typing import TypedDict



class Pet(TypedDict):
    name: NotRequired[str]