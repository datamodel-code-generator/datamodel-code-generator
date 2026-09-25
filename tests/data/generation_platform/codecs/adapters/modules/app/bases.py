"""A custom model base class that the builtin-v1 codec contract does not cover."""

from pydantic import BaseModel


class Base(BaseModel):
    """Stand in for an application base class with behavior the generator cannot see."""
