from pydantic import BaseModel

from datamodel_code_generator.types import DataType


class Ref(BaseModel):
    pass


def name_model() -> Ref:
    return Ref()


def outside() -> DataType:
    return DataType(is_dict=True)
