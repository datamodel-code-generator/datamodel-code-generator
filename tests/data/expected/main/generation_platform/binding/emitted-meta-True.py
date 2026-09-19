from __future__ import annotations
from typing import Annotated, Dict, List, Optional, TypeAlias, Union
from msgspec import Meta, Struct, UNSET, UnsetType



NestedItem: TypeAlias = Annotated[str, Meta(max_length=8, min_length=2, pattern='^a')]



MappingAdditionalProperty: TypeAlias = Annotated[int, Meta(ge=1)]



Choice: TypeAlias = Annotated[str, Meta(min_length=3)]



Choice1: TypeAlias = Annotated[int, Meta(le=5)]



class Metadata(Struct):
    integer: Annotated[int, Meta(ge=4, le=12, multiple_of=2)]
    number: Annotated[float, Meta(ge=1.5, lt=9.75, multiple_of=0.5)]
    nested: Annotated[List[List[NestedItem]], Meta(max_length=4, min_length=1)]
    mapping: Dict[str, MappingAdditionalProperty]
    choice: Union[Choice, Choice1]
    nullable: Union[Annotated[str, Meta(min_length=2)], None]
    optional: Union[Annotated[str, Meta(max_length=6)], UnsetType] = UNSET