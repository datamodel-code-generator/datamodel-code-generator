# RootModel input control

from __future__ import annotations

from pydantic import Field, RootModel


class BoundedList(RootModel[list[int]]):
    root: list[int] = Field(
        ...,
        description='BoundedList input control.',
        max_length=3,
        min_length=1,
        title='BoundedList',
    )
