from typing import ClassVar
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class Model(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
    )
    class_var_field: ClassVar[str]
    plain_field: str
