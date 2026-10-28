# a comment
from __future__ import annotations
from typing import Any, List, Optional
from pydantic import BaseModel, Field, conint


class Person(BaseModel):
    firstName: Optional[str] = Field(None, description="The person's first name.")
    lastName: Optional[str] = Field(None, description="The person's last name.")
    age: Optional[conint(ge=0)] = Field(None, description='Age in years which must be equal to or greater than zero.')
    friends: Optional[List[Any]] = None
    comment: None = Field(None)
