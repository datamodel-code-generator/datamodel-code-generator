"""Deliberately invalid API typing cases; every error is checked by code and line."""

from pathlib import Path
from datamodel_code_generator._source import YamlValue
from datamodel_code_generator.parser.openapi_media import encoding_media
from datamodel_code_generator.parser._api_reference import ApiDeclarationId, ApiModelResolver
from datamodel_code_generator.parser.openapi_scope import ApiDeclarationFrame, ApiOpenAPIParser

raw: dict[str, YamlValue] = {1: "invalid key"}
declaration = ApiDeclarationId("api.json", ("paths", 1))
frame = ApiDeclarationFrame(declaration, {}, "RootGet", declaration, "body", "operation")
frame.candidate_name = "mutated"
ApiModelResolver().resolve_ref(1)
encoding_media("text/plain", "request", openapi_32=True)
ApiOpenAPIParser(Path("api.json"), unknown_option=True)
