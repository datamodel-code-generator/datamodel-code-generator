from __future__ import annotations
from typing import List, Optional
from pydantic import AnyUrl, BaseModel, Field, RootModel
from . import models
from enum import Enum


class Pets(RootModel[List[models.Pet]]):
    root: List[models.Pet]


class Users(RootModel[List[models.User]]):
    root: List[models.User]


class Rules(RootModel[List[str]]):
    root: List[str]


class Stage(Enum):
    test = 'test'
    dev = 'dev'
    stg = 'stg'
    prod = 'prod'


class Api(BaseModel):
    apiKey: Optional[str] = Field(None, description='To be used as a dataset parameter value')
    apiVersionNumber: Optional[str] = Field(None, description='To be used as a version parameter value')
    apiUrl: Optional[AnyUrl] = Field(None, description="The URL describing the dataset's fields")
    apiDocumentationUrl: Optional[AnyUrl] = Field(None, description='A URL to the API console for each API')
    stage: Optional[Stage] = None


class Apis(RootModel[List[Api]]):
    root: List[Api]