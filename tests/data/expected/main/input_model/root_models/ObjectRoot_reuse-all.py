# RootModel input control

from __future__ import annotations

from pydantic import Field, RootModel

from tests.data.python.input_model.root_models import Item


class ObjectRoot(RootModel[Item]):
    root: Item = Field(..., description='ObjectRoot input control.', title='ObjectRoot')
