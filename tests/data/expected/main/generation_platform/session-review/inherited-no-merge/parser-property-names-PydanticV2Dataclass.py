from __future__ import annotations
from typing import Annotated, Dict
from pydantic import Field, constr
from typing_extensions import TypeAliasType



Alias = TypeAliasType("Alias", Annotated[Dict[constr(pattern=r'^x'), int], Field(..., min_length=1)])



Names = TypeAliasType("Names", Dict[constr(pattern=r'^x'), int])