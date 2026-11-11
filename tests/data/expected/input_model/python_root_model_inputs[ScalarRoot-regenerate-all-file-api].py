# RootModel input control

from __future__ import annotations

from pydantic import Field, RootModel


class ScalarRoot(RootModel[int]):
    root: int = Field(..., description='ScalarRoot input control.', title='ScalarRoot')
