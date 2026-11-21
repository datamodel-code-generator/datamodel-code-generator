# RootModel input control

from __future__ import annotations

from pydantic import Field, RootModel
from root_models import Item


class ObjectRoot(RootModel[Item]):
    root: Item = Field(..., description='ObjectRoot input control.', title='ObjectRoot')
