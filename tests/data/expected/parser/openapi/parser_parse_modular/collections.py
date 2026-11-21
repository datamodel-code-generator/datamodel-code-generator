from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import AnyUrl, BaseModel, RootModel

from . import models


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
    apiKey: Optional[str] = None
    apiVersionNumber: Optional[str] = None
    apiUrl: Optional[AnyUrl] = None
    apiDocumentationUrl: Optional[AnyUrl] = None
    stage: Optional[Stage] = None


class Apis(RootModel[List[Api]]):
    root: List[Api]
