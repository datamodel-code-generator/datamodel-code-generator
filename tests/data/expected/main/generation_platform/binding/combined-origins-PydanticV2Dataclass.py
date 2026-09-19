from __future__ import annotations
from typing import Any, Optional, Union
from pydantic import Field
from pydantic.dataclasses import dataclass
from typing_extensions import TypeAliasType



@dataclass
class ShipTo:
    postal_code: Optional[str] = Field(None, alias='postal-code')



@dataclass
class Choice1:
    common_key: Optional[str] = Field(None, alias='common-key')
    ship_to: Optional[ShipTo] = Field(None, alias='ship-to')



@dataclass
class Choice2:
    common_key: Optional[str] = Field(None, alias='common-key')



Never = TypeAliasType("Never", Any)



@dataclass
class Named:
    external_name: Optional[str] = Field(None, alias='external-name')



Choice = TypeAliasType("Choice", Union[Choice1, Choice2, Named])