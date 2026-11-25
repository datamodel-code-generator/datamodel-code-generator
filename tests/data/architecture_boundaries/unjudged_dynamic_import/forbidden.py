import importlib
from importlib import *

import_module("datamodel_code_generator.model.pydantic_v2")
__import__("datamodel_code_generator.model.pydantic_v2")
importlib.import_module("datamodel_code_generator.model.pydantic_v2")


def read_through_objects(loader):
    loader.import_module("datamodel_code_generator.model.pydantic_v2")
    loader().__import__("datamodel_code_generator.model.pydantic_v2")
