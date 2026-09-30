from __future__ import annotations

from typing import Annotated as Annotated_aliased

from pydantic import BaseModel, Field

StringValueWithAVeryLongTypeAliasName = str
IntegerValueWithAVeryLongTypeAliasName = int


class SomeSpec(BaseModel):
    services: dict[Annotated_aliased[str, Field(pattern=r'^[a-z]+$', min_length=2, max_length=4)], str]
    a_field_with_a_long_annotation_name: Annotated_aliased[str, Field(min_length=2, max_length=4)] = 'ok'
    expanded: Annotated_aliased[str, Field(description='A long description for the constrained field.', min_length=2, max_length=4)] = 'ok'
    optional: Annotated_aliased[str, Field(description='A long description for the constrained union.', min_length=2, max_length=4)] | None = None
    union_values: dict[Annotated_aliased[str, Field(pattern=r'^[a-z]+$', min_length=2, max_length=4)], str | None] | None = None
    long_union_values: dict[Annotated_aliased[str, Field(pattern=r'^[a-z]+$', min_length=2, max_length=4)], StringValueWithAVeryLongTypeAliasName | IntegerValueWithAVeryLongTypeAliasName | None] | None = None
