import importlib
from importlib import *

ENABLED = True

import_module("datamodel_code_generator.model.pydantic_v2")
__import__("datamodel_code_generator.model.pydantic_v2")
importlib.import_module("datamodel_code_generator.model.pydantic_v2")


def read_through_objects(loader):
    loader.import_module("datamodel_code_generator.model.pydantic_v2")
    loader().__import__("datamodel_code_generator.model.pydantic_v2")


if ENABLED:
    from importlib import import_module as load
else:
    load = print


def read_through_a_conditional_alias():
    load("datamodel_code_generator.model.pydantic_v2")
