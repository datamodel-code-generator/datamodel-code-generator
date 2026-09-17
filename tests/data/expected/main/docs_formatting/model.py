from pydantic import BaseModel, Field


class Pet(BaseModel):
    name: str = Field(
        'dog',
        description='Pet name',
    )
