import importlib

from datamodel_code_generator.parser.openapi import OpenAPIParser
from datamodel_code_generator._api_manifest import canonical_bytes
from datamodel_code_generator import parser as parser_package

import datamodel_code_generator.parser

openapi = importlib.import_module("datamodel_code_generator.parser.openapi")
relative = importlib.import_module(".parser", "datamodel_code_generator")
unresolved = importlib.import_module("...parser", "datamodel_code_generator")
sibling = importlib.import_module(".parser", __package__)


def load(package_name: str) -> object:
    return importlib.import_module(".parser", package_name)

__all__ = [
    "OpenAPIParser",
    "canonical_bytes",
    "datamodel_code_generator",
    "openapi",
    "parser_package",
    "relative",
    "load",
    "sibling",
    "unresolved",
]
