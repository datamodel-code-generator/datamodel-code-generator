from __future__ import annotations

import dataclasses
import importlib
from collections.abc import Mapping

import pydantic
from typing_extensions import final

from datamodel_code_generator import enums
from datamodel_code_generator.enums import TargetPydanticVersion
from datamodel_code_generator.format import PythonVersion
from datamodel_code_generator.model.pydantic_v2 import BaseModel

from . import imports
from .enums import DataModelType


def load_dynamically():
    importlib.import_module("datamodel_code_generator.enums")
    importlib.import_module("datamodel_code_generator.imports")
    return __import__("pydantic")


__all__ = [
    "BaseModel",
    "DataModelType",
    "Mapping",
    "PythonVersion",
    "TargetPydanticVersion",
    "dataclasses",
    "enums",
    "final",
    "imports",
    "load_dynamically",
    "pydantic",
]
