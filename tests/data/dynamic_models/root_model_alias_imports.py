"""Real imported dependencies that dynamic extraction must not publish."""

from pydantic import BaseModel, RootModel

ImportedIntegerRoot = RootModel[int]


class ImportedObject(BaseModel):
    value: str
