"""A real module that dynamic models named after it import their base class from."""

from pydantic import BaseModel


class CustomBase(BaseModel):
    """Base class of the generated models."""
