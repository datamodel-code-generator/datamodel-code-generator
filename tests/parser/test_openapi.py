"""Tests for OpenAPI/Swagger schema parser."""

from __future__ import annotations

import os
import platform
import socket
from ipaddress import ip_address
from pathlib import Path
from typing import Any

import black
import pydantic
import pytest
from packaging import version

from datamodel_code_generator import DataModelType, OpenAPIScope, OpenAPIVersion, PythonVersionMin
from datamodel_code_generator.format import Formatter
from datamodel_code_generator.http import _get_http_stack
from datamodel_code_generator.model import DataModelFieldBase, get_data_model_types
from datamodel_code_generator.model.pydantic_v2 import DataModelField
from datamodel_code_generator.parser.base import Result, dump_templates
from datamodel_code_generator.parser.jsonschema import JsonSchemaObject, JsonSchemaParser
from datamodel_code_generator.parser.openapi import (
    MediaObject,
    OpenAPIParser,
    ParameterObject,
    RequestBodyObject,
    ResponseObject,
)
from tests.conftest import (
    assert_generated_runtime_package,
    assert_output,
    assert_parser_modules,
    assert_parser_results,
)

DATA_PATH: Path = Path(__file__).parents[1] / "data" / "openapi"

EXPECTED_OPEN_API_PATH = Path(__file__).parents[1] / "data" / "expected" / "parser" / "openapi"


@pytest.fixture(autouse=True)
def block_dns_by_default(mocker: Any) -> None:
    """Keep tests that mock HTTP requests independent from external DNS."""
    mocker.patch("socket.getaddrinfo", side_effect=OSError)


def test_insert_info_version_constant_empty_body() -> None:
    """Insert OpenAPI info.version constant into an otherwise empty module."""
    parser = OpenAPIParser("openapi: 3.0.0\ninfo:\n  title: Test\n  version: 1.2.3\n")

    assert parser._insert_info_version_constant("", "1.2.3") == "OPENAPI_INFO_VERSION = '1.2.3'"


def test_insert_info_version_constant_blank_body() -> None:
    """Insert OpenAPI info.version constant into a whitespace-only module."""
    parser = OpenAPIParser("openapi: 3.0.0\ninfo:\n  title: Test\n  version: 1.2.3\n")

    assert parser._insert_info_version_constant("\n", "1.2.3") == "\nOPENAPI_INFO_VERSION = '1.2.3'\n\n"


def test_insert_info_version_constant_after_multiline_import() -> None:
    """Insert OpenAPI info.version constant after a multi-line import block."""
    parser = OpenAPIParser("openapi: 3.0.0\ninfo:\n  title: Test\n  version: 1.2.3\n")
    body = "from typing import (\n    Any,\n)\n\n\nclass Model:\n    pass"

    assert parser._insert_info_version_constant(body, "1.2.3") == (
        "from typing import (\n    Any,\n)\n\nOPENAPI_INFO_VERSION = '1.2.3'\n\n\nclass Model:\n    pass"
    )


def test_update_openapi_info_version_warns_when_missing() -> None:
    """Warn when OpenAPI info.version is missing while the option is enabled."""
    parser = OpenAPIParser("openapi: 3.0.0\ninfo:\n  title: Test\n", openapi_include_info_version=True)

    with pytest.warns(UserWarning, match="info.version was not found"):
        parser._update_openapi_info_version({"info": {"title": "Test"}})


def test_collect_discriminator_schemas_override_keeps_no_arg_contract() -> None:
    """Keep existing subclass overrides compatible with the no-argument hook."""

    class CustomOpenAPIParser(OpenAPIParser):
        collect_calls = 0

        def _collect_discriminator_schemas(self) -> None:
            self.collect_calls += 1
            super()._collect_discriminator_schemas()

    parser = CustomOpenAPIParser(
        """
openapi: 3.0.3
info:
  title: Compatibility
  version: 1.0.0
paths: {}
components:
  schemas:
    User:
      type: object
"""
    )

    parser.parse(format_=False)

    assert parser.collect_calls == 1


@pytest.mark.parametrize(
    ("raw_obj", "specification", "expected_root_id"),
    [
        ({"openapi": "3.1.0"}, {"openapi": "3.1.0", "$self": "https://example.com/root.yaml"}, "original"),
        (
            {"openapi": "3.2.1"},
            {"openapi": "3.2.1", "$self": "https://example.com/root.yaml"},
            "https://example.com/root.yaml",
        ),
        ({}, {"$self": "https://example.com/root.yaml"}, "original"),
        ({"openapi": 3.2}, {"openapi": 3.2, "$self": "https://example.com/root.yaml"}, "original"),
        ({"openapi": "4.0.0"}, {"openapi": "4.0.0", "$self": "https://example.com/root.yaml"}, "original"),
        ({"openapi": "3.2.0"}, {"openapi": "3.2.0"}, "original"),
        ({"openapi": "3.2.0"}, {"openapi": "3.2.0", "$self": 123}, "original"),
    ],
)
def test_openapi_self_context_requires_32(
    raw_obj: dict[str, Any],
    specification: dict[str, Any],
    expected_root_id: str,
) -> None:
    """Apply $self as the root id only for OpenAPI 3.2 documents."""
    parser = OpenAPIParser("")
    parser.raw_obj = raw_obj
    parser.root_id = "original"

    with parser.openapi_self_context(specification):
        assert parser.root_id == expected_root_id

    assert parser.root_id == "original"


def test_parse_skips_info_version_when_option_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave parser output unchanged when OpenAPI info.version output is disabled."""

    def parse_without_info_version(*args: Any, **kwargs: Any) -> str:  # noqa: ARG001
        return "class Model:\n    pass"

    monkeypatch.setattr(JsonSchemaParser, "parse", parse_without_info_version)
    parser = OpenAPIParser("openapi: 3.0.0\ninfo:\n  title: Test\n  version: 1.2.3\n")

    assert parser.parse() == "class Model:\n    pass"


def test_parse_adds_info_version_to_created_root_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """Create root __init__.py when modular output has no root module."""

    def parse_without_root_init(*args: Any, **kwargs: Any) -> dict[tuple[str, ...], Result]:  # noqa: ARG001
        parser = args[0]
        parser.openapi_info_version = "1.2.3"
        return {("models.py",): Result(body="class Model:\n    pass")}

    monkeypatch.setattr(JsonSchemaParser, "parse", parse_without_root_init)
    parser = OpenAPIParser("openapi: 3.0.0\ninfo:\n  title: Test\n  version: 1.2.3\n")
    parser.openapi_include_info_version = True

    result = parser.parse()

    assert isinstance(result, dict)
    assert result["__init__.py",].body == "OPENAPI_INFO_VERSION = '1.2.3'"


def get_expected_file(
    test_name: str,
    with_import: bool,
    format_: bool,
    base_class: str | None = None,
    prefix: str | None = None,
) -> Path:
    """Get expected output file path for test."""
    params: list[str] = []
    if with_import:
        params.append("with_import")
    if format_:
        params.append("format")
    if base_class:
        params.append(base_class)
    file_name = "_".join(params or "output")

    return EXPECTED_OPEN_API_PATH / test_name / (prefix or "") / f"{file_name}.py"


@pytest.mark.parametrize(
    ("source_obj", "generated_classes"),
    [
        (
            {"properties": {"name": {"type": "string"}}},
            """class Pets(BaseModel):
    name: Optional[str] = None""",
        ),
        (
            {
                "properties": {
                    "kind": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    }
                }
            },
            """class Kind(BaseModel):
    name: Optional[str] = None


class Pets(BaseModel):
    kind: Optional[Kind] = None""",
        ),
        (
            {
                "properties": {
                    "Kind": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    }
                }
            },
            """class Kind(BaseModel):
    name: Optional[str] = None


class Pets(BaseModel):
    Kind: Optional[Kind] = None""",
        ),
        (
            {
                "properties": {
                    "pet_kind": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                    }
                }
            },
            """class PetKind(BaseModel):
    name: Optional[str] = None


class Pets(BaseModel):
    pet_kind: Optional[PetKind] = None""",
        ),
        (
            {
                "properties": {
                    "kind": {
                        "type": "array",
                        "items": [
                            {
                                "type": "object",
                                "properties": {"name": {"type": "string"}},
                            }
                        ],
                    }
                }
            },
            """class KindItem(BaseModel):
    name: Optional[str] = None


class Pets(BaseModel):
    kind: Optional[List[KindItem]] = None""",
        ),
        (
            {"properties": {"kind": {"type": "array", "items": []}}},
            """class Pets(BaseModel):
    kind: Optional[List[Any]] = None""",
        ),
    ],
)
def test_parse_object(source_obj: dict[str, Any], generated_classes: str) -> None:
    """Test parsing OpenAPI object schemas."""
    parser = OpenAPIParser("")
    parser.parse_object("Pets", JsonSchemaObject.model_validate(source_obj), [])
    assert dump_templates(list(parser.results)) == generated_classes


@pytest.mark.parametrize(
    ("source_obj", "generated_classes"),
    [
        (
            {
                "type": "array",
                "items": {"type": "object", "properties": {"name": {"type": "string"}}},
            },
            """class Pet(BaseModel):
    name: Optional[str] = None


class Pets(RootModel[List[Pet]]):
    root: List[Pet]""",
        ),
        (
            {
                "type": "array",
                "items": [{"type": "object", "properties": {"name": {"type": "string"}}}],
            },
            """class Pet(BaseModel):
    name: Optional[str] = None


class Pets(RootModel[List[Pet]]):
    root: List[Pet]""",
        ),
        (
            {
                "type": "array",
                "items": {},
            },
            """class Pets(RootModel[List[Any]]):
    root: List[Any]""",
        ),
    ],
)
def test_parse_array(source_obj: dict[str, Any], generated_classes: str) -> None:
    """Test parsing OpenAPI array schemas."""
    parser = OpenAPIParser("")
    parser.parse_array("Pets", JsonSchemaObject.model_validate(source_obj), [])
    assert dump_templates(list(parser.results)) == generated_classes


@pytest.mark.parametrize(
    ("with_import", "format_", "base_class"),
    [
        (
            True,
            True,
            None,
        ),
        (
            False,
            True,
            None,
        ),
        (
            True,
            False,
            None,
        ),
        (True, True, "custom_module.Base"),
    ],
)
def test_openapi_parser_parse(with_import: bool, format_: bool, base_class: str | None) -> None:
    """Test OpenAPI parser with various configurations."""
    parser = OpenAPIParser(
        data_model_field_type=DataModelFieldBase,
        source=Path(DATA_PATH / "api.yaml"),
        base_class=base_class,
    )
    expected_file = get_expected_file("openapi_parser_parse", with_import, format_, base_class)
    assert_output(parser.parse(with_import=with_import, format_=format_, settings_path=DATA_PATH.parent), expected_file)


@pytest.mark.parametrize(
    ("source_obj", "generated_classes"),
    [
        (
            {"type": "string", "nullable": True},
            """class Name(RootModel[Optional[str]]):
    root: Optional[str] = None""",
        ),
        (
            {"type": "string", "nullable": False},
            """class Name(RootModel[str]):
    root: str""",
        ),
    ],
)
def test_parse_root_type(source_obj: dict[str, Any], generated_classes: str) -> None:
    """Test parsing OpenAPI root type schemas."""
    parser = OpenAPIParser("")
    parser.parse_root_type("Name", JsonSchemaObject.model_validate(source_obj), [])
    assert dump_templates(list(parser.results)) == generated_classes


def test_openapi_parser_parse_duplicate_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test parsing OpenAPI with duplicate model names."""
    monkeypatch.chdir(tmp_path)
    parser = OpenAPIParser(
        Path(DATA_PATH / "duplicate_models.yaml"),
    )
    assert_output(parser.parse(), EXPECTED_OPEN_API_PATH / "openapi_parser_parse_duplicate_models" / "output.py")


def test_openapi_parser_parse_duplicate_model_with_simplify(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test parsing OpenAPI with duplicate models and simplification."""
    monkeypatch.chdir(tmp_path)
    raw = Path(DATA_PATH / "duplicate_model_simplify.yaml")
    parser = OpenAPIParser(raw)
    assert_output(
        parser.parse(), EXPECTED_OPEN_API_PATH / "openapi_parser_parse_duplicate_models_simplify" / "output.py"
    )


def test_openapi_parser_parse_resolved_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test parsing OpenAPI with resolved model references."""
    monkeypatch.chdir(tmp_path)
    parser = OpenAPIParser(
        Path(DATA_PATH / "resolved_models.yaml"),
    )
    assert_output(parser.parse(), EXPECTED_OPEN_API_PATH / "openapi_parser_parse_resolved_models" / "output.py")


def test_openapi_parser_parse_lazy_resolved_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test parsing OpenAPI with lazy resolved model references."""
    monkeypatch.chdir(tmp_path)
    parser = OpenAPIParser(
        Path(DATA_PATH / "lazy_resolved_models.yaml"),
    )
    assert (
        parser.parse()
        == """from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, RootModel


class Pet(BaseModel):
    id: int
    name: str
    tag: Optional[str] = None


class Pets(RootModel[List[Pet]]):
    root: List[Pet]


class Error(BaseModel):
    code: int
    message: str


class Event(BaseModel):
    name: Optional[str] = None
    event: Optional[Event] = None


class Events(RootModel[List[Event]]):
    root: List[Event]


class Results(BaseModel):
    envets: Optional[List[Events]] = None
    event: Optional[List[Event]] = None
"""
    )


def test_openapi_parser_parse_x_enum_varnames(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test parsing OpenAPI with x-enum-varnames extension."""
    monkeypatch.chdir(tmp_path)
    parser = OpenAPIParser(
        Path(DATA_PATH / "x_enum_varnames.yaml"),
    )
    assert (
        parser.parse()
        == """from __future__ import annotations

from enum import Enum


class String(Enum):
    dog = 'dog'
    cat = 'cat'
    snake = 'snake'


class UnknownTypeString(Enum):
    dog = 'dog'
    cat = 'cat'
    snake = 'snake'


class NamedString(Enum):
    EQ = '='
    NE = '!='
    GT = '>'
    LT = '<'
    GE = '>='
    LE = '<='


class NamedNumber(Enum):
    one = 1
    two = 2
    three = 3


class Number(Enum):
    number_1 = 1
    number_2 = 2
    number_3 = 3


class UnknownTypeNumber(Enum):
    int_1 = 1
    int_2 = 2
    int_3 = 3
"""
    )


@pytest.mark.skipif(pydantic.VERSION < "1.9.0", reason="Require Pydantic version 1.9.0 or later ")
def test_openapi_parser_parse_enum_models() -> None:
    """Test parsing OpenAPI enum models."""
    parser = OpenAPIParser(
        Path(DATA_PATH / "enum_models.yaml").read_text(encoding="utf-8"),
        target_python_version=PythonVersionMin,
    )
    expected_dir = EXPECTED_OPEN_API_PATH / "openapi_parser_parse_enum_models"
    assert_output(parser.parse(), expected_dir / "output.py")


def test_openapi_parser_parse_anyof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test parsing OpenAPI with anyOf schemas."""
    monkeypatch.chdir(tmp_path)
    parser = OpenAPIParser(
        Path(DATA_PATH / "anyof.yaml"),
    )
    assert_output(parser.parse(), EXPECTED_OPEN_API_PATH / "openapi_parser_parse_anyof" / "output.py")


def test_openapi_parser_parse_anyof_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test parsing OpenAPI with anyOf and required fields."""
    monkeypatch.chdir(tmp_path)
    parser = OpenAPIParser(
        Path(DATA_PATH / "anyof_required.yaml"),
    )
    assert_output(parser.parse(), EXPECTED_OPEN_API_PATH / "openapi_parser_parse_anyof_required" / "output.py")


def test_openapi_parser_parse_nested_anyof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test parsing OpenAPI with nested anyOf schemas."""
    monkeypatch.chdir(tmp_path)
    parser = OpenAPIParser(
        Path(DATA_PATH / "nested_anyof.yaml").read_text(encoding="utf-8"),
    )
    assert_output(parser.parse(), EXPECTED_OPEN_API_PATH / "openapi_parser_parse_nested_anyof" / "output.py")


def test_openapi_parser_parse_oneof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test parsing OpenAPI with oneOf schemas."""
    monkeypatch.chdir(tmp_path)
    parser = OpenAPIParser(
        Path(DATA_PATH / "oneof.yaml"),
    )
    assert_output(parser.parse(), EXPECTED_OPEN_API_PATH / "openapi_parser_parse_oneof" / "output.py")


def test_openapi_parser_parse_nested_oneof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test parsing OpenAPI with nested oneOf schemas."""
    monkeypatch.chdir(tmp_path)
    parser = OpenAPIParser(
        Path(DATA_PATH / "nested_oneof.yaml").read_text(encoding="utf-8"),
    )
    assert_output(parser.parse(), EXPECTED_OPEN_API_PATH / "openapi_parser_parse_nested_oneof" / "output.py")


def test_openapi_parser_parse_allof_ref(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test parsing OpenAPI with allOf references."""
    monkeypatch.chdir(tmp_path)
    parser = OpenAPIParser(
        Path(DATA_PATH / "allof_same_prefix_with_ref.yaml"),
    )
    assert_output(
        parser.parse(), EXPECTED_OPEN_API_PATH / "openapi_parser_parse_allof_same_prefix_with_ref" / "output.py"
    )


def test_openapi_parser_parse_allof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test parsing OpenAPI with allOf schemas."""
    monkeypatch.chdir(tmp_path)
    parser = OpenAPIParser(
        Path(DATA_PATH / "allof.yaml"),
    )
    assert_output(parser.parse(), EXPECTED_OPEN_API_PATH / "openapi_parser_parse_allof" / "output.py")


def test_openapi_parser_parse_allof_required_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test parsing OpenAPI with allOf and required fields."""
    monkeypatch.chdir(tmp_path)
    parser = OpenAPIParser(
        Path(DATA_PATH / "allof_required_fields.yaml"),
    )
    assert_output(parser.parse(), EXPECTED_OPEN_API_PATH / "openapi_parser_parse_allof_required_fields" / "output.py")


def test_openapi_parser_parse_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test parsing OpenAPI with field aliases."""
    monkeypatch.chdir(tmp_path)
    parser = OpenAPIParser(
        Path(DATA_PATH / "alias.yaml"),
    )
    delimiter = "\\" if platform.system() == "Windows" else "/"
    results = {delimiter.join(p): r for p, r in parser.parse().items()}
    assert_parser_results(results, EXPECTED_OPEN_API_PATH / "openapi_parser_parse_alias")


def test_openapi_parser_parse_modular(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test parsing OpenAPI with modular structure."""
    monkeypatch.chdir(tmp_path)
    parser = OpenAPIParser(Path(DATA_PATH / "modular.yaml"), data_model_field_type=DataModelFieldBase)
    modules = parser.parse()
    assert_parser_modules(modules, EXPECTED_OPEN_API_PATH / "openapi_parser_parse_modular")


def test_openapi_parser_parse_invalid_dotted_without_imports() -> None:
    """Inspect multiple final modules when import rendering is disabled."""
    parser = OpenAPIParser(
        (DATA_PATH / "invalid_dotted_schema_name.yaml").resolve(),
        data_model_field_type=DataModelFieldBase,
        formatters=[Formatter.BUILTIN],
    )
    parser.repair_invalid_dotted_stdout = True
    modules = parser.parse(with_import=False, format_=True)
    assert_parser_modules(
        modules,
        EXPECTED_OPEN_API_PATH / "openapi_parser_parse_invalid_dotted_without_imports",
    )


def test_openapi_parser_parse_modular_pydantic_v2_ruff_keeps_runtime_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test OpenAPIParser.parse() keeps runtime imports for modular Pydantic v2 Ruff output."""
    monkeypatch.chdir(tmp_path)
    data_model_types = get_data_model_types(DataModelType.PydanticV2BaseModel, target_python_version=PythonVersionMin)
    parser = OpenAPIParser(
        Path(DATA_PATH / "modular.yaml"),
        data_model_type=data_model_types.data_model,
        data_model_root_type=data_model_types.root_model,
        data_model_field_type=data_model_types.field_model,
        data_type_manager_type=data_model_types.data_type_manager,
        dump_resolve_reference_action=data_model_types.dump_resolve_reference_action,
        formatters=[Formatter.RUFF_CHECK, Formatter.RUFF_FORMAT],
    )

    modules = parser.parse(settings_path=DATA_PATH.parent)
    assert_generated_runtime_package(
        tmp_path / "model",
        modules,
        EXPECTED_OPEN_API_PATH / "openapi_parser_parse_modular_pydantic_v2_ruff",
    )


@pytest.mark.parametrize(
    ("with_import", "format_", "base_class"),
    [
        (
            True,
            True,
            None,
        ),
        (
            False,
            True,
            None,
        ),
        (
            True,
            False,
            None,
        ),
        (
            True,
            True,
            "custom_module.Base",
        ),
    ],
)
def test_openapi_parser_parse_additional_properties(with_import: bool, format_: bool, base_class: str | None) -> None:
    """Test parsing OpenAPI with additional properties."""
    parser = OpenAPIParser(
        Path(DATA_PATH / "additional_properties.yaml").read_text(encoding="utf-8"),
        base_class=base_class,
        data_model_field_type=DataModelFieldBase,
    )

    assert_output(
        parser.parse(with_import=with_import, format_=format_, settings_path=DATA_PATH.parent),
        get_expected_file(
            "openapi_parser_parse_additional_properties",
            with_import,
            format_,
            base_class,
        ),
    )


def test_openapi_parser_parse_array_enum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test parsing OpenAPI with array enum types."""
    monkeypatch.chdir(tmp_path)
    parser = OpenAPIParser(source=Path(DATA_PATH / "array_enum.yaml"))
    expected_file = get_expected_file("openapi_parser_parse_array_enum", True, True)
    assert_output(parser.parse(), expected_file)


def test_openapi_parser_parse_remote_ref(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: Any) -> None:
    """Test parsing OpenAPI with remote references."""
    monkeypatch.chdir(tmp_path)

    remote_schema = """
schemas:
  Problem:
    properties:
      detail:
        description: A human readable explanation specific to this occurrence of the problem.
        type: string
      instance:
        description: An absolute URI that identifies the specific occurrence of the problem.
        format: uri
        type: string
      status:
        description: The HTTP status code generated by the origin server for this occurrence of the problem.
        exclusiveMaximum: true
        format: int32
        maximum: 600
        minimum: 100
        type: integer
      title:
        description: A short, summary of the problem type.
        type: string
      type:
        default: about:blank
        description: An absolute URI that identifies the problem type.
        format: uri
        type: string
    type: object
"""
    mock_response = mocker.Mock()
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.text = remote_schema
    mocker.patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))],
    )
    mock_fetch = mocker.patch(
        "datamodel_code_generator.http._HTTPFetchSession.get_response",
        return_value=mock_response,
    )

    parser = OpenAPIParser(
        data_model_field_type=DataModelFieldBase,
        source=(DATA_PATH / "refs.yaml").read_text(),
        http_ignore_tls=bool(os.environ.get("HTTP_IGNORE_TLS")),
        allow_remote_refs=True,
    )
    expected_file = get_expected_file("openapi_parser_parse_remote_ref", True, True)

    assert_output(parser.parse(), expected_file)
    mock_fetch.assert_called_once_with(
        _get_http_stack(),
        "https://teamdigitale.github.io/openapi/0.0.6/definitions.yaml",
        headers=None,
        verify=True,
        follow_redirects=False,
        query_parameters=None,
        timeout=30.0,
        pinned_host="teamdigitale.github.io",
        pinned_ips=(ip_address("93.184.216.34"),),
    )


def test_openapi_parser_parse_required_null(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test parsing OpenAPI with required nullable fields."""
    monkeypatch.chdir(tmp_path)
    parser = OpenAPIParser(source=Path(DATA_PATH / "required_null.yaml"))
    assert_output(parser.parse(), EXPECTED_OPEN_API_PATH / "openapi_parser_parse_required_null" / "output.py")


def test_openapi_model_resolver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test OpenAPI model resolver functionality."""
    monkeypatch.chdir(tmp_path)
    parser = OpenAPIParser(source=(DATA_PATH / "api.yaml"))
    parser.parse()

    references = {
        k: v.model_dump(exclude={"source", "module_name", "actual_module_name", "children"})
        for k, v in parser.model_resolver.references.items()
    }
    assert references == {
        "api.yaml#/components/schemas/Error": {
            "duplicate_name": None,
            "loaded": True,
            "name": "Error",
            "original_name": "Error",
            "path": "api.yaml#/components/schemas/Error",
        },
        "api.yaml#/components/schemas/Event": {
            "duplicate_name": None,
            "loaded": True,
            "name": "Event",
            "original_name": "Event",
            "path": "api.yaml#/components/schemas/Event",
        },
        "api.yaml#/components/schemas/Id": {
            "duplicate_name": None,
            "loaded": True,
            "name": "Id",
            "original_name": "Id",
            "path": "api.yaml#/components/schemas/Id",
        },
        "api.yaml#/components/schemas/Pet": {
            "duplicate_name": None,
            "loaded": True,
            "name": "Pet",
            "original_name": "Pet",
            "path": "api.yaml#/components/schemas/Pet",
        },
        "api.yaml#/components/schemas/Pets": {
            "duplicate_name": None,
            "loaded": True,
            "name": "Pets",
            "original_name": "Pets",
            "path": "api.yaml#/components/schemas/Pets",
        },
        "api.yaml#/components/schemas/Result": {
            "duplicate_name": None,
            "loaded": True,
            "name": "Result",
            "original_name": "Result",
            "path": "api.yaml#/components/schemas/Result",
        },
        "api.yaml#/components/schemas/Rules": {
            "duplicate_name": None,
            "loaded": True,
            "name": "Rules",
            "original_name": "Rules",
            "path": "api.yaml#/components/schemas/Rules",
        },
        "api.yaml#/components/schemas/Users": {
            "duplicate_name": None,
            "loaded": True,
            "name": "Users",
            "original_name": "Users",
            "path": "api.yaml#/components/schemas/Users",
        },
        "api.yaml#/components/schemas/Users/Users/0#-datamodel-code-generator-#-object-#-special-#": {
            "duplicate_name": None,
            "loaded": True,
            "name": "User",
            "original_name": "Users",
            "path": "api.yaml#/components/schemas/Users/Users/0#-datamodel-code-generator-#-object-#-special-#",
        },
        "api.yaml#/components/schemas/apis": {
            "duplicate_name": None,
            "loaded": True,
            "name": "Apis",
            "original_name": "apis",
            "path": "api.yaml#/components/schemas/apis",
        },
        "api.yaml#/components/schemas/apis/apis/0#-datamodel-code-generator-#-object-#-special-#": {
            "duplicate_name": None,
            "loaded": True,
            "name": "Api",
            "original_name": "apis",
            "path": "api.yaml#/components/schemas/apis/apis/0#-datamodel-code-generator-#-object-#-special-#",
        },
    }


def test_openapi_parser_parse_any(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test parsing OpenAPI with any type schemas."""
    monkeypatch.chdir(tmp_path)
    parser = OpenAPIParser(
        data_model_field_type=DataModelFieldBase,
        source=Path(DATA_PATH / "any.yaml"),
    )
    assert_output(parser.parse(), EXPECTED_OPEN_API_PATH / "openapi_parser_parse_any" / "output.py")


def test_openapi_parser_responses_without_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test parsing OpenAPI responses without content."""
    monkeypatch.chdir(tmp_path)
    parser = OpenAPIParser(
        data_model_field_type=DataModelFieldBase,
        source=Path(DATA_PATH / "body_and_parameters.yaml"),
        openapi_scopes=[OpenAPIScope.Paths],
        allow_responses_without_content=True,
    )
    assert_output(parser.parse(), EXPECTED_OPEN_API_PATH / "openapi_parser_responses_without_content" / "output.py")


def test_openapi_parser_responses_with_tag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test parsing OpenAPI responses with tags."""
    monkeypatch.chdir(tmp_path)
    parser = OpenAPIParser(
        data_model_field_type=DataModelFieldBase,
        source=Path(DATA_PATH / "body_and_parameters.yaml"),
        openapi_scopes=[OpenAPIScope.Tags, OpenAPIScope.Schemas, OpenAPIScope.Paths],
    )
    assert_output(parser.parse(), EXPECTED_OPEN_API_PATH / "openapi_parser_responses_with_tag" / "output.py")


@pytest.mark.skipif(
    black.__version__.split(".")[0] >= "24",
    reason="Installed black doesn't support the old style",
)
def test_openapi_parser_with_query_parameters() -> None:
    """Test parsing OpenAPI with query parameters."""
    parser = OpenAPIParser(
        data_model_field_type=DataModelFieldBase,
        source=Path(DATA_PATH / "query_parameters.yaml"),
        openapi_scopes=[
            OpenAPIScope.Parameters,
            OpenAPIScope.Schemas,
            OpenAPIScope.Paths,
        ],
    )
    assert_output(parser.parse(), EXPECTED_OPEN_API_PATH / "openapi_parser_with_query_parameters" / "output.py")


@pytest.mark.skipif(
    black.__version__.split(".")[0] >= "24",
    reason="Installed black doesn't support the old style",
)
def test_openapi_parser_with_include_path_parameters() -> None:
    """Test parsing OpenAPI with included path parameters."""
    parser = OpenAPIParser(
        data_model_field_type=DataModelFieldBase,
        source=Path(DATA_PATH / "query_parameters.yaml"),
        openapi_scopes=[
            OpenAPIScope.Parameters,
            OpenAPIScope.Schemas,
            OpenAPIScope.Paths,
        ],
        include_path_parameters=True,
    )
    assert_output(
        parser.parse(), EXPECTED_OPEN_API_PATH / "openapi_parser_with_query_parameters" / "with_path_params.py"
    )


def test_parse_all_parameters_duplicate_names_exception() -> None:
    """Test parsing parameters with duplicate names raises exception."""
    parser = OpenAPIParser("", include_path_parameters=True)
    parameters = [
        ParameterObject.model_validate({"name": "duplicate_param", "in": "path", "schema": {"type": "string"}}),
        ParameterObject.model_validate({"name": "duplicate_param", "in": "query", "schema": {"type": "integer"}}),
    ]

    with pytest.raises(Exception) as exc_info:  # noqa: PT011
        parser.parse_all_parameters("TestModel", parameters, ["test", "path"])

    assert "Parameter name 'duplicate_param' is used more than once." in str(exc_info.value)


@pytest.mark.skipif(
    version.parse(pydantic.VERSION) < version.parse("2.9.0"),
    reason="Require Pydantic version 2.0.0 or later ",
)
def test_openapi_parser_array_called_fields_with_one_of_items() -> None:
    """Test parsing OpenAPI array fields with oneOf items."""
    parser = OpenAPIParser(
        data_model_field_type=DataModelField,
        source=Path(DATA_PATH / "array_called_fields_with_oneOf_items.yaml"),
        openapi_scopes=[
            OpenAPIScope.Parameters,
            OpenAPIScope.Schemas,
            OpenAPIScope.Paths,
        ],
        field_constraints=True,
    )
    assert_output(
        parser.parse(),
        EXPECTED_OPEN_API_PATH / "openapi_parser_parse_array_called_fields_with_oneOf_items" / "output.py",
    )


def test_additional_imports() -> None:
    """Test that additional imports are inside imports container."""
    new_parser = OpenAPIParser(source="", additional_imports=["collections.deque"])
    assert len(new_parser.imports) == 1
    assert new_parser.imports["collections"] == {"deque"}


def test_no_additional_imports() -> None:
    """Test that not additional imports are not affecting imports container."""
    new_parser = OpenAPIParser(
        source="",
    )
    assert len(new_parser.imports) == 0


@pytest.mark.parametrize("media_obj", [{"schema": None}, {"itemSchema": None}])
def test_get_raw_media_schema_explicit_null_schema(media_obj: dict[str, Any]) -> None:
    """Test that explicit null raw media schemas are skipped."""
    parser = OpenAPIParser(source="", openapi_version=OpenAPIVersion.V32)

    assert parser._get_raw_media_schema(media_obj) is None


@pytest.mark.parametrize(
    ("request_body_data", "expected_type_hints"),
    [
        pytest.param(
            {"application/json": {"schema": {"type": "object", "properties": {"name": {"type": "string"}}}}},
            {"application/json": "TestRequest"},
            id="object_with_properties",
        ),
        pytest.param(
            {
                "application/json": {"schema": {"type": "object", "properties": {"name": {"type": "string"}}}},
                "text/plain": {"schema": {"type": "string"}},
            },
            {"application/json": "TestRequest", "text/plain": "str"},
            id="multiple_media_types",
        ),
        pytest.param(
            {"application/json": {"schema": {"$ref": "#/components/schemas/RequestRef"}}},
            {"application/json": "RequestRef"},
            id="schema_reference",
        ),
        pytest.param(
            {"application/json": {}},  # MediaObject with no schema
            {},  # Should result in empty dict since no schema to process
            id="missing_schema",
        ),
    ],
)
def test_parse_request_body_return(request_body_data: dict[str, Any], expected_type_hints: dict[str, str]) -> None:
    """Test parsing request body returns correct type hints."""
    parser = OpenAPIParser(
        data_model_field_type=DataModelFieldBase,
        source="",
        use_standard_collections=True,
    )
    result = parser.parse_request_body(
        "TestRequest",
        RequestBodyObject(
            content={
                media_type: MediaObject.model_validate(media_data)
                for media_type, media_data in request_body_data.items()
            }
        ),
        ["test", "path"],
    )

    assert isinstance(result, dict)
    assert len(result) == len(expected_type_hints)
    for media_type, expected_hint in expected_type_hints.items():
        assert media_type in result
        assert result[media_type].type_hint == expected_hint


@pytest.mark.parametrize(
    ("parameters_data", "expected_type_hint"),
    [
        pytest.param([], None, id="no_parameters"),
        pytest.param(
            [{"name": "search", "in": "query", "required": False, "schema": {"type": "string"}}],
            "TestParametersQuery",
            id="with_query_parameters",
        ),
        pytest.param(
            [{"name": "userId", "in": "path", "required": True, "schema": {"type": "string"}}],
            None,
            id="path_parameter_only",
        ),
    ],
)
def test_parse_all_parameters_return(parameters_data: list[dict[str, Any]], expected_type_hint: str | None) -> None:
    """Test parsing parameters returns correct type hints."""
    parser = OpenAPIParser(
        data_model_field_type=DataModelFieldBase,
        source="",
        openapi_scopes=[OpenAPIScope.Parameters],
    )
    result = parser.parse_all_parameters(
        "TestParametersQuery",
        [ParameterObject.model_validate(param_data) for param_data in parameters_data],
        ["test", "path"],
    )
    if expected_type_hint is None:
        assert result is None
    else:
        assert result is not None
        assert result.type_hint == expected_type_hint


@pytest.mark.parametrize(
    ("responses_data", "expected_type_hints"),
    [
        pytest.param(
            {
                "200": {
                    "description": "Success",
                    "content": {"application/json": {"schema": {"type": "string"}}},
                }
            },
            {"200": {"application/json": "str"}},
            id="simple_response_with_schema",
        ),
        pytest.param(
            {
                "200": {
                    "description": "Success",
                    "content": {
                        "application/json": {"schema": {"type": "object", "properties": {"name": {"type": "string"}}}},
                        "text/plain": {"schema": {"type": "string"}},
                    },
                },
                "400": {
                    "description": "Bad Request",
                    "content": {"text/plain": {"schema": {"type": "string"}}},
                },
            },
            {"200": {"application/json": "TestResponse", "text/plain": "str"}, "400": {"text/plain": "str"}},
            id="multiple_status_codes_and_content_types",
        ),
        pytest.param(
            {
                "200": {
                    "description": "Success",
                    "content": {"application/json": {}},  # Content but no schema
                }
            },
            {},  # Should skip since no schema in content
            id="response_with_no_schema",
        ),
    ],
)
def test_parse_responses_return(
    responses_data: dict[str, dict[str, Any]],
    expected_type_hints: dict[str, dict[str, str]],
) -> None:
    """Test parsing responses returns correct type hints."""
    parser = OpenAPIParser(
        data_model_field_type=DataModelFieldBase,
        source="",
    )

    result = parser.parse_responses(
        "TestResponse",
        {
            status_code: ResponseObject.model_validate(response_data)
            for status_code, response_data in responses_data.items()
        },
        ["test", "path"],
    )

    assert isinstance(result, dict)
    assert len(result) == len(expected_type_hints)
    for status_code, expected_content_types in expected_type_hints.items():
        assert status_code in result
        assert len(result[status_code]) == len(expected_content_types)
        for content_type, expected_type_hint in expected_content_types.items():
            assert content_type in result[status_code]
            assert result[status_code][content_type].type_hint == expected_type_hint


def test_parse_all_parameters_strict_nullable() -> None:
    """Test that strict_nullable exposes nullable for optional parameters without default."""
    parser = OpenAPIParser(
        data_model_field_type=DataModelField,
        source="",
        openapi_scopes=[OpenAPIScope.Parameters],
        strict_nullable=True,
    )
    parameters_data = [
        {"name": "nullable_param", "in": "query", "required": False, "schema": {"type": "string", "nullable": True}},
        {
            "name": "non_nullable_param",
            "in": "query",
            "required": False,
            "schema": {"type": "string", "nullable": False},
        },
    ]
    result = parser.parse_all_parameters(
        "TestParametersQuery",
        [ParameterObject.model_validate(param_data) for param_data in parameters_data],
        ["test", "path"],
    )
    assert result is not None
    fields = parser.results[0].fields
    assert len(fields) == 2
    assert fields[0].nullable is True
    assert fields[1].nullable is False


@pytest.mark.parametrize(
    ("output_model_type", "validates_structured_default"),
    [
        pytest.param(DataModelType.PydanticV2BaseModel, True, id="pydantic-v2"),
        pytest.param(DataModelType.PydanticV2Dataclass, True, id="pydantic-v2-dataclass"),
        pytest.param(DataModelType.DataclassesDataclass, False, id="dataclass"),
        pytest.param(DataModelType.TypingTypedDict, False, id="typed-dict"),
        pytest.param(DataModelType.MsgspecStruct, False, id="msgspec"),
    ],
)
def test_parameter_field_policy_is_output_owned(
    output_model_type: DataModelType,
    validates_structured_default: bool,
) -> None:
    """Keep content flags and structured-default policy in their output layers."""
    data_model_types = get_data_model_types(output_model_type, target_python_version=PythonVersionMin)
    parser = OpenAPIParser(
        source=Path(DATA_PATH / "parameter_field_policy.yaml"),
        data_model_type=data_model_types.data_model,
        data_model_root_type=data_model_types.root_model,
        data_model_field_type=data_model_types.field_model,
        data_type_manager_type=data_model_types.data_type_manager,
        dump_resolve_reference_action=data_model_types.dump_resolve_reference_action,
        openapi_scopes=[OpenAPIScope.Paths, OpenAPIScope.Schemas, OpenAPIScope.Parameters],
        use_operation_id_as_name=True,
        use_frozen_field=True,
        use_default_factory_for_optional_nested_models=True,
    )

    parser.parse(format_=False)
    models = {model.class_name: model for model in parser.results}
    parameter_fields = {field.name: field for field in models["ListItemsParametersQuery"].fields}
    defaulted_field = models["Defaulted"].fields[0]

    assert parameter_fields["schema_read_only"].read_only is True
    assert parameter_fields["content_read_only"].read_only is True
    assert parameter_fields["schema_write_only"].write_only is True
    assert parameter_fields["content_write_only"].write_only is True
    assert parameter_fields["schema_nested"].use_default_factory_for_optional_nested_models is True
    assert parameter_fields["content_nested"].use_default_factory_for_optional_nested_models is True
    assert parameter_fields["content_multi"].use_default_factory_for_optional_nested_models is True
    assert (defaulted_field.extras.get("validate_default") is True) is validates_structured_default


def test_openapi_parser_with_request_bodies_scope() -> None:
    """Test parsing OpenAPI with requestBodies scope generates models from components/requestBodies."""
    parser = OpenAPIParser(
        data_model_field_type=DataModelFieldBase,
        source=Path(DATA_PATH / "request_bodies_scope.yaml"),
        openapi_scopes=[OpenAPIScope.RequestBodies],
    )
    result = parser.parse()
    assert "CreatePet" in result
    assert "name: Optional[str]" in result
    assert "age: Optional[int]" in result


def test_openapi_parser_with_request_bodies_scope_ref() -> None:
    """Test parsing OpenAPI with requestBodies scope handles $ref in schema."""
    parser = OpenAPIParser(
        data_model_field_type=DataModelFieldBase,
        source=Path(DATA_PATH / "request_bodies_scope.yaml"),
        openapi_scopes=[OpenAPIScope.RequestBodies, OpenAPIScope.Schemas],
    )
    result = parser.parse()
    assert "UpdatePet" in result
    assert "PetUpdate" in result


def test_openapi_parser_with_request_bodies_scope_empty() -> None:
    """Test parsing OpenAPI with requestBodies scope when requestBodies is empty."""
    parser = OpenAPIParser(
        data_model_field_type=DataModelFieldBase,
        source=Path(DATA_PATH / "request_bodies_scope_empty.yaml"),
        openapi_scopes=[OpenAPIScope.RequestBodies],
    )
    result = parser.parse()
    assert result in ({}, "")


def test_openapi_parser_with_request_bodies_scope_no_schema() -> None:
    """Test parsing OpenAPI with requestBodies scope skips content without schema."""
    parser = OpenAPIParser(
        data_model_field_type=DataModelFieldBase,
        source=Path(DATA_PATH / "request_bodies_scope.yaml"),
        openapi_scopes=[OpenAPIScope.RequestBodies],
    )
    result = parser.parse()
    assert "EmptyContent" not in result


def test_openapi_parser_with_request_bodies_scope_body_ref() -> None:
    """Test parsing OpenAPI with requestBodies scope handles $ref at requestBody level."""
    parser = OpenAPIParser(
        data_model_field_type=DataModelFieldBase,
        source=Path(DATA_PATH / "request_bodies_scope_with_ref.yaml"),
        openapi_scopes=[OpenAPIScope.RequestBodies],
    )
    result = parser.parse()
    assert "CreatePet" in result or "BasePet" in result
    assert "name: Optional[str]" in result
