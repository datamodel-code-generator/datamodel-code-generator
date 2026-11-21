from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, Field

Boolean: TypeAlias = bool
"""
The `Boolean` scalar type represents `true` or `false`.
"""


String: TypeAlias = str
"""
The `String` scalar type represents textual data, represented as UTF-8 character sequences. The String type is most often used by GraphQL to represent free-form human-readable text.
"""


class User(BaseModel):
    tags: list[String | None] | None = None
    typename__: Literal['User'] | None = Field('User', alias='__typename')

    @property
    def __typename(self) -> Literal['User'] | None:
        return self.typename__
