"""Known unbound names in stored generated modules, checked by tests/test_generated_import_closure.py.

Each entry is ``<path below tests/data/expected>::<name>``; the group reason states why the group's names are unbound.
Entries only shrink: a new unbound name is a generator bug to fix, not an entry to add.
"""

from __future__ import annotations

UNBOUND_NAME_VIOLATIONS: dict[str, tuple[str, ...]] = {
    "custom-file-header": ("main/generate_custom_file_header_with_fstring.py::runtime_value",),
    "imports-left-out": (
        "main/malformed/parsed_array_pointer.py::Any",
        "main/malformed/parsed_array_pointer.py::RootModel",
        "main/malformed/parsed_empty_pointer.py::Any",
        "main/malformed/parsed_empty_pointer.py::RootModel",
        "main/malformed/parsed_missing_pointer.py::Any",
        "main/malformed/parsed_missing_pointer.py::RootModel",
        "parser/openapi/openapi_parser_parse/format.py::AnyUrl",
        "parser/openapi/openapi_parser_parse/format.py::BaseModel",
        "parser/openapi/openapi_parser_parse/format.py::List",
        "parser/openapi/openapi_parser_parse/format.py::Optional",
        "parser/openapi/openapi_parser_parse/format.py::RootModel",
        "parser/openapi/openapi_parser_parse_additional_properties/format.py::BaseModel",
        "parser/openapi/openapi_parser_parse_additional_properties/format.py::ConfigDict",
        "parser/openapi/openapi_parser_parse_additional_properties/format.py::Dict",
        "parser/openapi/openapi_parser_parse_additional_properties/format.py::List",
        "parser/openapi/openapi_parser_parse_additional_properties/format.py::Optional",
        "parser/openapi/openapi_parser_parse_additional_properties/format.py::RootModel",
        "parser/openapi/openapi_parser_parse_invalid_dotted_without_imports/SaveTrifectaV2.py::BaseModel",
        "parser/openapi/openapi_parser_parse_invalid_dotted_without_imports/SaveTrifectaV2.py::Optional",
        "parser/openapi/openapi_parser_parse_invalid_dotted_without_imports/__init__.py::BaseModel",
        "parser/openapi/openapi_parser_parse_invalid_dotted_without_imports/__init__.py::Optional",
        "parser/openapi/openapi_parser_parse_invalid_dotted_without_imports/__init__.py::SaveTrifectaV2",
    ),
    "not-generated": ("main_kr/jobs/stale.py::stale",),
    "unknown-x-python-type": ("main/jsonschema/x_python_type_nested_unknown_type.py::MyCustomType",),
}
UNBOUND_NAME_GROUP_REASONS: dict[str, str] = {
    "custom-file-header": (
        "--custom-file-header text is written verbatim; this header is an f-string that loads a name only the user "
        "can bind."
    ),
    "imports-left-out": (
        "Parser results stored without their import block on purpose: parse(with_import=False) and template dumps of "
        "parser.results."
    ),
    "not-generated": (
        "The test overwrites the output with a stale placeholder to show that --check leaves it untouched."
    ),
    "unknown-x-python-type": (
        "x-python-type names a type no known import provides, so the generator writes the bare name the schema asks "
        "for."
    ),
}
