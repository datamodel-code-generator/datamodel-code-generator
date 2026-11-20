# Copyright 2024 MyCompany

from __future__ import annotations

from pydantic import BaseModel, Field


class Person(BaseModel):
    first_name: str = Field(..., alias='first-name')
    last_name: str = Field(..., alias='last-name')
    email_address: str | None = None
