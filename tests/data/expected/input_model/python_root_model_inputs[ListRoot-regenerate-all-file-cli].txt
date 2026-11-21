# RootModel input control

from __future__ import annotations

from pydantic import Field, RootModel


class ListRoot(RootModel[list[int]]):
    root: list[int] = Field(
        ..., description='ListRoot input control.', title='ListRoot'
    )
