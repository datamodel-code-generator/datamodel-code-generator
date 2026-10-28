from __future__ import annotations

import importlib
from types import MappingProxyType

from typing_extensions import final

from datamodel_code_generator.enums import TargetPydanticVersion
from datamodel_code_generator.model.base import ConstraintsBase
from datamodel_code_generator.types import DataType

from . import imports


def load_dynamically():
    importlib.import_module("datamodel_code_generator.enums")
    return importlib.import_module("datamodel_code_generator.types")


__all__ = [
    "ConstraintsBase",
    "DataType",
    "MappingProxyType",
    "TargetPydanticVersion",
    "final",
    "imports",
    "load_dynamically",
]
