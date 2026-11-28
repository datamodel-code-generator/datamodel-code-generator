from __future__ import annotations

from typing import Optional, Union

from pydantic import BaseModel, Field, RootModel


class Pet1(BaseModel):
    cat: Optional[bool] = None


class Pet2(BaseModel):
    dog: Optional[bool] = None


class Pet3(BaseModel):
    name: Optional[str] = None


class Pet4(Pet1, Pet3):
    pass


class Pet5(Pet2, Pet3):
    pass


class Pet(RootModel[Union[Pet4, Pet5]]):
    root: Union[Pet4, Pet5] = Field(..., title='Pet')
