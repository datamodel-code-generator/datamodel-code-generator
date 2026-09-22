from __future__ import annotations
from typing import List, Optional, Union
from pydantic import BaseModel, Field, RootModel, constr


class ODatamodelCodeGeneratorA(BaseModel):
    x: Optional[str] = None


class Holder(BaseModel):
    s__datamodel_code_generator___a: Optional[str] = Field(None, alias='s#-datamodel-code-generator-#-a')
    o__datamodel_code_generator___a: Optional[ODatamodelCodeGeneratorA] = Field(None, alias='o#-datamodel-code-generator-#-a')
    u__datamodel_code_generator___a: Optional[Union[str, int]] = Field(None, alias='u#-datamodel-code-generator-#-a')


class B(BaseModel):
    x: Optional[int] = None


class Arr(RootModel[List[str]]):
    root: List[str]


class Q2(BaseModel):
    x: Optional[int] = None


class Q3(BaseModel):
    y: Optional[str] = None


class Q4(RootModel[List[str]]):
    root: List[str] = Field(..., max_length=3)


class Q5(RootModel[List[str]]):
    root: List[str]


class PGetParametersQuery(BaseModel):
    q1: Optional[constr(max_length=5)] = None
    q2: Optional[Q2] = None
    q3: Optional[Q3] = None
    q4: Optional[Union[List[str], Q4]] = None
    q5: Optional[Union[List[str], Q5]] = None


