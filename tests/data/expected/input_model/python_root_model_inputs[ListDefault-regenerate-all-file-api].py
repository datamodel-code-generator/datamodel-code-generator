# RootModel input control

from __future__ import annotations

from pydantic import Field, RootModel


class ListDefault(RootModel[list[int]]):
    root: list[int] = Field(
        [2, 1], description='ListDefault input control.', title='ListDefault'
    )
