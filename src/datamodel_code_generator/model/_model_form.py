"""How a model holds its value (``DataModel.form``)."""

from __future__ import annotations

from typing import Literal, TypeAlias

__all__ = ["ModelForm"]

ModelForm: TypeAlias = Literal["alias", "root", "enum", "object"]
"""How a model holds its value: a type alias stands for the type of its field, a root model wraps the value of its
field, an enum is one of its members and any other model holds its fields."""
