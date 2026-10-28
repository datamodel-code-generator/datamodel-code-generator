import importlib

import msgspec
from datamodel_code_generator.model import typed_dict
from datamodel_code_generator.model.field_name import MsgspecFieldNameResolver

BACKEND_MODULE = "datamodel_code_generator.model.pydantic_base"
EXTERNAL_BACKEND_MODULE = "msgspec.json"


class PydanticFieldNameResolver:
    pass


def build_dataclass_resolver():
    return MsgspecFieldNameResolver, msgspec, typed_dict


def load_backend():
    return importlib.import_module(BACKEND_MODULE), importlib.import_module(EXTERNAL_BACKEND_MODULE)
