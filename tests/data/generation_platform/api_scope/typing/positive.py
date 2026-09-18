"""Positive API declaration typing cases, checked with both pinned checkers."""

from pathlib import Path
from typing_extensions import assert_type
from datamodel_code_generator import OpenAPIScope
from datamodel_code_generator._source import YamlValue
from datamodel_code_generator.parser.openapi_media import MediaType, parse_media_type
from datamodel_code_generator.parser._api_reference import ApiDeclarationId, ApiModelResolver
from datamodel_code_generator.parser.openapi_scope import ApiDeclarationFrame, ApiOpenAPIParser
from datamodel_code_generator.reference import Reference

raw: dict[str, YamlValue] = {"paths": {"/": {"get": {}}}}
declaration = ApiDeclarationId("api.json", ("paths", "/", "get"))
frame = ApiDeclarationFrame(declaration, raw, "RootGet", declaration, "schema", "operation")
assert_type(frame.declaration, ApiDeclarationId)
assert_type(frame.raw_document, dict[str, YamlValue])
assert_type(parse_media_type('multipart/form-data; boundary="a\\ b"'), MediaType)
resolver = ApiModelResolver()
assert_type(resolver.add_ref("#/components/schemas/Foo"), Reference)
assert_type(resolver.join_path(("api.json", "#", "paths", "/")), str)
parser = ApiOpenAPIParser(Path("api.json"), openapi_scopes=[OpenAPIScope.Api])
assert_type(parser.model_resolver, ApiModelResolver)
parser.dispose()
