"""A second module with the same owner and nested class names."""
from pydantic import BaseModel


class Outer:
    class Inner(BaseModel):
        label: str


class OtherRoot(BaseModel):
    child: Outer.Inner
