from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


class Error(BaseModel):
    root: Optional[bool] = None


class ErrorModel(BaseModel):
    message: Optional[str] = None


class FieldOneGetResponse(BaseModel):
    error: Optional[ErrorModel] = None