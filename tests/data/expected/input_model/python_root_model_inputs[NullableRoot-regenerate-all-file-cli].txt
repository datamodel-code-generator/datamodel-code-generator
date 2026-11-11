# RootModel input control

from __future__ import annotations

from pydantic import Field, RootModel


class NullableRoot(RootModel[int | None]):
    root: int | None = Field(
        ..., description='NullableRoot input control.', title='NullableRoot'
    )
