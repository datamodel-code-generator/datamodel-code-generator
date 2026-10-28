from datamodel_code_generator.model import typed_dict as output_backend
from datamodel_code_generator.parser import LiteralType


class Config:
    output_type = output_backend.TypedDict
    literal_type = LiteralType
