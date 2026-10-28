# Type-specific union constraints

from __future__ import annotations

from typing import Union

from pydantic import Field, RootModel, conint, constr


class Root(RootModel[Union[conint(ge=2), constr(max_length=2)]]):
    root: Union[conint(ge=2), constr(max_length=2)] = Field(..., title='Root')
