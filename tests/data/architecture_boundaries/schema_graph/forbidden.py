from __future__ import annotations

import importlib
from urllib.parse import urljoin

from typing_extensions import final

from datamodel_code_generator import Error
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
from datamodel_code_generator.reference import SchemaLocation

from . import base

parser = importlib.import_module("datamodel_code_generator.parser.base")

__all__ = ["Error", "JsonSchemaParser", "SchemaLocation", "base", "final", "parser", "urljoin"]
