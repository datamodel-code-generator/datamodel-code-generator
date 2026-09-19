from __future__ import annotations
from typing import Any, Optional, TypeAlias, Union
from dataclasses import dataclass



@dataclass
class ShipTo:
    postal_code: Optional[str] = None



@dataclass
class Choice1:
    common_key: Optional[str] = None
    ship_to: Optional[ShipTo] = None



@dataclass
class Choice2:
    common_key: Optional[str] = None



Never: TypeAlias = Any



@dataclass
class Named:
    external_name: Optional[str] = None



Choice: TypeAlias = Union[Choice1, Choice2, Named]