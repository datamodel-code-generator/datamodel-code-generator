"""Known unbound names in stored generated modules, checked by tests/test_generated_import_closure.py.

Each entry is ``<path below tests/data/expected>::<name>``; the group reason states why the group's names are unbound.
Entries only shrink: a new unbound name is a generator bug to fix, not an entry to add.
"""

from __future__ import annotations

UNBOUND_NAME_VIOLATIONS: dict[str, tuple[str, ...]] = {
    "custom-file-header": (
        "main/main_general/generate_custom_file_header_does_not_treat_fstring_as_docstring.py::runtime_value",
    ),
    "imports-left-out": (
        "main/error_messages/parse_deferred_json_pointer[array].py::Any",
        "main/error_messages/parse_deferred_json_pointer[array].py::RootModel",
        "main/error_messages/parse_deferred_json_pointer[missing].py::Any",
        "main/error_messages/parse_deferred_json_pointer[missing].py::RootModel",
        "main/error_messages/parse_file_object_path_without_resolved_ref.py::Any",
        "main/error_messages/parse_file_object_path_without_resolved_ref.py::RootModel",
        "parser/openapi/parser_parse[False-True-None].py::AnyUrl",
        "parser/openapi/parser_parse[False-True-None].py::BaseModel",
        "parser/openapi/parser_parse[False-True-None].py::List",
        "parser/openapi/parser_parse[False-True-None].py::Optional",
        "parser/openapi/parser_parse[False-True-None].py::RootModel",
        "parser/openapi/parser_parse_additional_properties[False-True-None].py::BaseModel",
        "parser/openapi/parser_parse_additional_properties[False-True-None].py::ConfigDict",
        "parser/openapi/parser_parse_additional_properties[False-True-None].py::Dict",
        "parser/openapi/parser_parse_additional_properties[False-True-None].py::List",
        "parser/openapi/parser_parse_additional_properties[False-True-None].py::Optional",
        "parser/openapi/parser_parse_additional_properties[False-True-None].py::RootModel",
        "parser/openapi/parser_parse_invalid_dotted_without_imports/SaveTrifectaV2.py::BaseModel",
        "parser/openapi/parser_parse_invalid_dotted_without_imports/SaveTrifectaV2.py::Optional",
        "parser/openapi/parser_parse_invalid_dotted_without_imports/__init__.py::BaseModel",
        "parser/openapi/parser_parse_invalid_dotted_without_imports/__init__.py::Optional",
        "parser/openapi/parser_parse_invalid_dotted_without_imports/__init__.py::SaveTrifectaV2",
    ),
    "not-generated": (
        "main/main_watch/batch_watch_nested_dependency_reruns_full_batch_without_output_loop.stale.py::stale",
    ),
    "unknown-x-python-type": ("main/jsonschema/main_jsonschema/x_python_type_nested_unknown_type.py::MyCustomType",),
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
