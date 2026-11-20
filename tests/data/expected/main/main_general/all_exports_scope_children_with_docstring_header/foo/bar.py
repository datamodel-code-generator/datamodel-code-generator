"""
Custom module docstring header.

This is a multi-line docstring used for testing.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Thing(BaseModel):
    attributes: dict[str, Any] | None = None


class Thang(BaseModel):
    attributes: list[dict[str, Any]] | None = None


class Others(BaseModel):
    name: str | None = None


class Clone(Thing):
    others: Others | None = None
