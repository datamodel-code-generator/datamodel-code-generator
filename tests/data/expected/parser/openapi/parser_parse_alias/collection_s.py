from __future__ import annotations

from typing import List, Optional

from pydantic import AnyUrl, BaseModel, Field, RootModel

from . import model_s


class PetS(RootModel[List[model_s.PeT]]):
    root: List[model_s.PeT]


class UserS(RootModel[List[model_s.UseR]]):
    root: List[model_s.UseR]


class RuleS(RootModel[List[str]]):
    root: List[str]


class Api(BaseModel):
    apiKey: Optional[str] = Field(
        None, description='To be used as a dataset parameter value'
    )
    apiVersionNumber: Optional[str] = Field(
        None, description='To be used as a version parameter value'
    )
    apiUrl: Optional[AnyUrl] = Field(
        None, description="The URL describing the dataset's fields"
    )
    apiDocumentationUrl: Optional[AnyUrl] = Field(
        None, description='A URL to the API console for each API'
    )


class ApiS(RootModel[List[Api]]):
    root: List[Api]
