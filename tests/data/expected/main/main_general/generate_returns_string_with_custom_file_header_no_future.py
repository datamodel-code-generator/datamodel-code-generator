# Custom header for legacy code

from pydantic import BaseModel


class Model(BaseModel):
    id: int | None = None