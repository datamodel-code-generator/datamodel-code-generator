from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from datamodel_code_generator.model.pydantic_v2 import BaseModel
from datamodel_code_generator.parser.generation import GenerationStore
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

if TYPE_CHECKING:
    from datamodel_code_generator.parser.base import Parser

__all__ = ["BaseModel", "GenerationStore", "JsonSchemaParser", "Parser"]

DYNAMIC_PARSER = import_module("datamodel_code_generator.parser.openapi")
