# RootModel input control

from __future__ import annotations

from pydantic import Field, RootModel


class DefaultRoot(RootModel[int]):
    root: int = Field(3, description='DefaultRoot input control.', title='DefaultRoot')
