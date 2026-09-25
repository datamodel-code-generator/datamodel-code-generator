import importlib

from datamodel_code_generator.parser.openapi import OpenAPIParser
from datamodel_code_generator._api_manifest import canonical_bytes
from datamodel_code_generator import parser as parser_package

import datamodel_code_generator.parser

openapi = importlib.import_module("datamodel_code_generator.parser.openapi")

__all__ = ["OpenAPIParser", "canonical_bytes", "datamodel_code_generator", "openapi", "parser_package"]
