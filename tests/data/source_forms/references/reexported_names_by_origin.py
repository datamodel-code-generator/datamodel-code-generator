"""#3866 architecture boundaries: a name the package re-exports is read as what its home module imports.

``DataModelType`` comes from ``base_config`` and ``import_module`` from ``format``; neither is spelled as the path the
rules look for, so the rules follow the name through the module that binds it.
layers: parser, input-model
"""
from datamodel_code_generator.base_config import DataModelType
from datamodel_code_generator.format import import_module

BACKEND = "datamodel_code_generator.model.pydantic_v2"
OUTPUT_FAMILY = DataModelType.PydanticV2BaseModel


def load():
    return import_module(BACKEND)
