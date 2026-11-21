from __future__ import annotations

from typing import List, Optional

from pydantic import AwareDatetime, BaseModel, RootModel


class FoodFoodIdGetResponse(RootModel[List[int]]):
    root: List[int]


class UserGetResponse(BaseModel):
    timestamp: AwareDatetime
    name: str
    age: Optional[str] = None


class UserPostRequest(BaseModel):
    timestamp: AwareDatetime
    name: str
    age: Optional[str] = None


class UsersGetResponseItem(BaseModel):
    timestamp: AwareDatetime
    name: str
    age: Optional[str] = None


class UsersGetResponse(RootModel[List[UsersGetResponseItem]]):
    root: List[UsersGetResponseItem]


class UsersPostRequestItem(BaseModel):
    timestamp: AwareDatetime
    name: str
    age: Optional[str] = None


class UsersPostRequest(RootModel[List[UsersPostRequestItem]]):
    root: List[UsersPostRequestItem]


class Error(BaseModel):
    code: int
    message: str


class Pet(BaseModel):
    id: int
    name: str
    tag: Optional[str] = None


class PetForm(BaseModel):
    name: Optional[str] = None
    age: Optional[int] = None


class PetsGetResponse(RootModel[List[Pet]]):
    root: List[Pet]
