# RootModel input control

from __future__ import annotations

from pydantic import Field, RootModel
from root_models import Item


class ObjectList(RootModel[list[Item]]):
    root: list[Item] = Field(
        ..., description='ObjectList input control.', title='ObjectList'
    )
