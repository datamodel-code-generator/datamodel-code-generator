# RootModel input control

from __future__ import annotations

from pydantic import Field, RootModel


class ScalarChild(RootModel[int]):
    root: int = Field(
        ..., description='ScalarChild input control.', title='ScalarChild'
    )
