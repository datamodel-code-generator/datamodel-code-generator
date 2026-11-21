"""
Custom module docstring header.

This is a multi-line docstring used for testing.
"""

from __future__ import annotations

from pydantic import Field, RootModel


class FieldModel(RootModel[str]):
    root: str = Field(..., examples=['green'])
