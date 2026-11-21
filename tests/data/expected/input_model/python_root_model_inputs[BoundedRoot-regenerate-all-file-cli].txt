# RootModel input control

from __future__ import annotations

from pydantic import Field, RootModel


class BoundedRoot(RootModel[int]):
    root: int = Field(
        ..., description='BoundedRoot input control.', ge=2, le=5, title='BoundedRoot'
    )
