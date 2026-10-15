"""Tests for XML Schema code generation."""

from __future__ import annotations

import codecs
import json
import warnings
from typing import TYPE_CHECKING

import msgspec
import pytest
import yaml
from lxml import etree
from pydantic import PydanticUserError, ValidationError

from datamodel_code_generator import DataModelType, InputFileType
from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.format import Formatter, PythonVersion
from datamodel_code_generator.parser import xmlschema as xmlschema_parser
from datamodel_code_generator.parser.xmlschema import (
    _clear_xml_schema_data_cache,
    _load_xml_schema_data_from_path,
    _read_xml_text,
    convert_xml_schema_data,
)
from tests.conftest import assert_output
from tests.main.conftest import (
    BACKEND_GOLDEN_CASES,
    BACKEND_GOLDEN_TARGET_ARGS,
    DATA_PATH,
    EXPECTED_XML_SCHEMA_PATH,
    XML_SCHEMA_DATA_PATH,
    _generated_model,
    _model_json_validator,
    assert_generated_model_json_validation,
    assert_path_cache_evicts_lru_entries,
    run_generate_file_and_assert,
    run_main_and_assert,
)
from tests.main.xmlschema.conftest import assert_file_content

if TYPE_CHECKING:
    from pathlib import Path


def test_main_xmlschema_purchase_order(output_file: Path) -> None:
    """Generate models from an XML Schema document."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "purchase_order.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="purchase_order.py",
    )


def test_convert_xml_schema_data_preserves_yaml_safe_non_finite_defaults() -> None:
    """Keep public XML Schema conversion results serializable by PyYAML."""
    converted = convert_xml_schema_data((XML_SCHEMA_DATA_PATH / "non_finite_enum.xsd").read_text(encoding="utf-8"))

    assert_output(
        yaml.safe_dump(converted["definitions"]["NonFinite"]["enum"], sort_keys=False),
        EXPECTED_XML_SCHEMA_PATH / "converted_non_finite_defaults.txt",
    )


def test_main_xmlschema_with_parsed_source_cache(output_file: Path) -> None:
    """Generate XML Schema models with process-local parsed source cache enabled."""
    _clear_xml_schema_data_cache()
    run_generate_file_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "purchase_order.xsd",
        output_path=output_file,
        input_file_type=InputFileType.XMLSchema,
        assert_func=assert_file_content,
        expected_file="purchase_order.py",
    )


@pytest.mark.parametrize(
    ("byte_order_mark", "xml_encoding"),
    [
        (codecs.BOM_UTF8, "utf-8"),
        (codecs.BOM_UTF32_LE, "utf-32-le"),
        (codecs.BOM_UTF32_BE, "utf-32-be"),
        (codecs.BOM_UTF16_LE, "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16-be"),
    ],
)
def test_read_xml_text_detects_byte_order_mark(tmp_path: Path, byte_order_mark: bytes, xml_encoding: str) -> None:
    """Decode XML source according to its byte order mark."""
    schema_path = tmp_path / "schema.xsd"
    expected_path = EXPECTED_XML_SCHEMA_PATH / "xml_text_initial.txt"
    schema_text = expected_path.read_text(encoding="utf-8")
    schema_path.write_bytes(byte_order_mark + schema_text.encode(xml_encoding))

    assert_output(_read_xml_text(schema_path, "ascii"), expected_path)


def test_read_xml_text_reads_updated_source(tmp_path: Path) -> None:
    """Read the current XML source content without retaining stale text."""
    schema_path = tmp_path / "schema.xsd"
    first_expected_path = EXPECTED_XML_SCHEMA_PATH / "xml_text_initial.txt"
    second_expected_path = EXPECTED_XML_SCHEMA_PATH / "xml_text_updated.txt"
    schema_path.write_bytes(first_expected_path.read_text(encoding="utf-8").encode("latin-1"))
    assert_output(_read_xml_text(schema_path, "latin-1"), first_expected_path)

    schema_path.write_bytes(second_expected_path.read_text(encoding="utf-8").encode("latin-1"))
    assert_output(_read_xml_text(schema_path, "latin-1"), second_expected_path)


def test_load_xml_schema_data_from_path_evicts_lru_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Evict old converted XML Schema cache entries after the configured limit."""
    monkeypatch.setattr(xmlschema_parser, "_XML_SCHEMA_DATA_CACHE_MAX_SIZE", 1)
    _clear_xml_schema_data_cache()
    first_path = tmp_path / "first.xsd"
    second_path = tmp_path / "second.xsd"
    first_path.write_text((XML_SCHEMA_DATA_PATH / "single_root_item.xsd").read_text(encoding="utf-8"), encoding="utf-8")
    second_path.write_text((XML_SCHEMA_DATA_PATH / "inline_root.xsd").read_text(encoding="utf-8"), encoding="utf-8")

    kwargs = {
        "base_path": tmp_path,
        "encoding": "utf-8",
        "xmlschema_version": None,
        "schema_version_mode": None,
        "use_xmlschema_datetime_default": False,
        "source_safe_non_finite": True,
    }

    def load_schema_data(path: Path, encoding: str) -> object:  # noqa: ARG001
        return _load_xml_schema_data_from_path(path, **kwargs)

    assert_path_cache_evicts_lru_entries(load_schema_data, first_path, second_path)


def test_main_xmlschema_purchase_order_from_normalized_external_path(tmp_path: Path, output_file: Path) -> None:
    """Generate XML Schema models when the external input path needs normalization."""
    redirect_dir = tmp_path / "redirect"
    redirect_dir.mkdir()
    run_main_and_assert(
        input_path=redirect_dir / ".." / "purchase_order.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="purchase_order.py",
        copy_files=[
            (XML_SCHEMA_DATA_PATH / "purchase_order.xsd", tmp_path / "purchase_order.xsd"),
            (XML_SCHEMA_DATA_PATH / "common.xsd", tmp_path / "common.xsd"),
        ],
    )


def test_main_xmlschema_infer_input_file_type(output_file: Path) -> None:
    """Infer XML Schema input and generate a model."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "single_root_item.xsd",
        output_path=output_file,
        assert_func=assert_file_content,
        expected_file="single_root_item.py",
    )


def test_main_xmlschema_supported_constructs(output_file: Path) -> None:
    """Generate models for supported XML Schema constructs."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "constructs_matrix.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="constructs_matrix.py",
    )


def test_main_xmlschema_edge_cases(output_file: Path) -> None:
    """Generate models for XML Schema edge cases."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "edge_cases.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="edge_cases.py",
    )


def test_main_xmlschema_fixed_decimal(output_file: Path) -> None:
    """Generate Decimal defaults for fixed XML Schema decimal values."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "fixed_decimal.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="fixed_decimal.py",
    )


def test_main_xmlschema_special_float_defaults(output_file: Path) -> None:
    """Generate non-finite float defaults from XML Schema lexical values."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "special_float_defaults.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="special_float_defaults.py",
    )


def test_main_xmlschema_custom_template_non_finite_raw(output_file: Path) -> None:
    """Keep XML non-finite defaults valid in legacy raw custom templates."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "special_float_defaults.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        extra_args=["--custom-template-dir", str(DATA_PATH / "templates_non_finite_raw")],
        assert_func=assert_file_content,
        expected_file="custom_template_non_finite_raw.py",
        force_exec_validation=True,
    )


def test_main_xmlschema_custom_template_non_finite_raw_list(output_file: Path) -> None:
    """Keep nested XML non-finite defaults valid in raw custom templates."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "non_finite_list_default.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        extra_args=["--custom-template-dir", str(DATA_PATH / "templates_non_finite_raw")],
        assert_func=assert_file_content,
        expected_file="custom_template_non_finite_raw_list.py",
        force_exec_validation=True,
    )


def test_main_xmlschema_non_finite_enum(output_file: Path) -> None:
    """Keep non-finite enum member names and values valid."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "non_finite_enum.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="non_finite_enum.py",
        force_exec_validation=True,
        importable_module_name="generated_xml_non_finite_enum",
        importable_module_attribute="NonFinite",
    )


def test_main_xmlschema_non_finite_inline_enum_default(output_file: Path) -> None:
    """Resolve a NaN default to its non-finite inline enum member."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "non_finite_inline_enum_default.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        extra_args=["--set-default-enum-member"],
        assert_func=assert_file_content,
        expected_file="non_finite_inline_enum_default.py",
        force_exec_validation=True,
        importable_module_name="generated_xml_non_finite_inline_enum_default",
        importable_module_attribute="Model",
    )


def test_main_xmlschema_special_float_bounds(output_file: Path) -> None:
    """Generate non-finite float bounds from XML Schema lexical values."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "special_float_bounds.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="special_float_bounds.py",
    )


def test_main_xmlschema_union_defaults(output_file: Path) -> None:
    """Generate typed defaults for XML Schema union values."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "union_defaults.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="union_defaults.py",
    )


def test_main_xmlschema_boolean_whitespace_defaults(output_file: Path) -> None:
    """Generate boolean defaults after XML Schema whitespace normalization."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "boolean_whitespace_defaults.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="boolean_whitespace_defaults.py",
    )


def test_generate_xmlschema_nillable_boolean_api(output_file: Path) -> None:
    """Generate XSD nillable lexical values through the public Python API."""
    run_generate_file_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "nillable_boolean.xsd",
        output_path=output_file,
        input_file_type=InputFileType.XMLSchema,
        assert_func=assert_file_content,
        expected_file="nillable_boolean.py",
    )


def test_main_xmlschema_nillable_boolean(output_file: Path) -> None:
    """Treat XML Schema nillable true and 1 lexical values equivalently."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "nillable_boolean.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="nillable_boolean.py",
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="generated_xmlschema_nillable_boolean",
        model_name="Root",
        valid_json='{"nillableTrue":null,"nillableOne":null,"nillableFalse":"false","nillableZero":"zero","unspecified":"value"}',
        invalid_json='{"nillableTrue":null,"nillableOne":null,"nillableFalse":"false","nillableZero":null,"unspecified":"value"}',
        expected_error_type="string_type",
        expected_attribute_path=("nillableOne",),
        expected_attribute_value=None,
    )


def test_main_xmlschema_temporal_defaults(output_file: Path) -> None:
    """Generate typed defaults for XML Schema temporal lexical values."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "temporal_defaults.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="temporal_defaults.py",
    )


def test_main_xmlschema_temporal_default_import_alias(output_file: Path) -> None:
    """Keep temporal default expressions bound when a generated field reserves the import alias."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "temporal_default_import_alias.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="temporal_default_import_alias.py",
        extra_args=[
            "--generate-schema-validators",
            "--output-model-type",
            "pydantic_v2.BaseModel",
        ],
        force_exec_validation=True,
        importable_module_name="generated_xml_temporal_default_import_alias",
        importable_module_attribute="Root",
    )


def test_main_xmlschema_single_root_same_name(output_file: Path) -> None:
    """Inline a single root type when no other definition references it."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "single_root_item.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="single_root_item.py",
    )


def test_main_xmlschema_inline_root(output_file: Path) -> None:
    """Generate a titled model for a single inline root element."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "inline_root.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="inline_root.py",
    )


def test_main_xmlschema_recursive_root(output_file: Path) -> None:
    """Keep recursive root definitions addressable."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "recursive_node.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="recursive_node.py",
    )


def test_main_xmlschema_import_resolves_by_namespace(output_file: Path) -> None:
    """Resolve imported components by namespace without selecting imported roots."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "import_namespace.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="import_namespace.py",
    )


def test_main_xmlschema_imported_namespace_name_collisions(output_file: Path) -> None:
    """Keep imported definitions distinct when local names collide."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "namespace_collisions.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="namespace_collisions.py",
    )


def test_main_xmlschema_no_namespace_name_collision(output_file: Path) -> None:
    """Name no-namespace definitions distinctly when imported names collide."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "no_namespace_collision.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="no_namespace_collision.py",
    )


def test_main_xmlschema_namespace_fallback(output_file: Path) -> None:
    """Resolve unprefixed target-namespace names and referenced definitions."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "namespace_fallback.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="namespace_fallback.py",
    )


def test_main_xmlschema_advanced_constructs(output_file: Path) -> None:
    """Generate models for abstract substitution groups and mixed content."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "advanced_constructs.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="advanced_constructs.py",
    )


@pytest.mark.parametrize(("output_model_type", "expected_name"), BACKEND_GOLDEN_CASES)
def test_main_xmlschema_output_model_types(
    output_file: Path,
    output_model_type: str,
    expected_name: str,
) -> None:
    """Generate representative XML Schema models across supported output backends."""
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        run_main_and_assert(
            input_path=XML_SCHEMA_DATA_PATH / "advanced_constructs.xsd",
            output_path=output_file,
            input_file_type="xmlschema",
            assert_func=assert_file_content,
            expected_file=f"output_model_types/advanced_constructs_{expected_name}.py",
            extra_args=[*BACKEND_GOLDEN_TARGET_ARGS, "--output-model-type", output_model_type],
            force_exec_validation=True,
            importable_module_name=f"generated_xmlschema_{expected_name}",
            importable_module_attribute="Zoo",
        )
    assert_output(
        "".join(
            f"{message}\n"
            for message in dict.fromkeys(str(record.message) for record in recorded if record.category is UserWarning)
        ),
        EXPECTED_XML_SCHEMA_PATH / "output_model_types" / f"advanced_constructs_{expected_name}_warnings.txt",
    )


def test_main_xmlschema_multiple_substitution_groups(output_file: Path) -> None:
    """Generate models for elements affiliated with multiple substitution groups."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "multiple_substitution_groups.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="multiple_substitution_groups.py",
    )


def test_main_xmlschema_include_local_elements(output_file: Path) -> None:
    """Treat included global elements as local root candidates."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "include_local_elements.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="include_local_elements.py",
    )


def test_main_xmlschema_type_element_symbol_spaces(output_file: Path) -> None:
    """Keep same-name type definitions and element declarations distinct."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "type_element_symbol_spaces.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="type_element_symbol_spaces.py",
    )


def test_main_xmlschema_type_alias_with_field_description_py312(output_file: Path) -> None:
    """Render a PEP 695 alias and field description through structured generation."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "type_alias_with_field_description.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="type_alias_with_field_description_py312.py",
        extra_args=[
            "--use-type-alias",
            "--target-python-version",
            "3.12",
            "--use-field-description",
            "--formatters",
            "builtin",
        ],
    )


def test_main_xmlschema_model_groups_and_wildcards(output_file: Path) -> None:
    """Generate models for repeating model groups, defaults, fixed values, and wildcards."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "model_groups_and_wildcards.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="model_groups_and_wildcards.py",
    )


def test_main_xmlschema_spec_constructs(output_file: Path) -> None:
    """Generate models for additional XML Schema 1.0 declaration and particle constructs."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "spec_constructs.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="spec_constructs.py",
    )


def test_main_xmlschema_list_defaults(output_file: Path) -> None:
    """Generate list-typed defaults from XML Schema list lexical values."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "list_defaults.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="list_defaults.py",
    )


def test_main_xmlschema_utf16_input(tmp_path: Path, output_file: Path) -> None:
    """Generate models from XML Schema files that rely on XML encoding detection."""
    input_path = tmp_path / "utf16_schema.xsd"
    input_path.write_text(
        """<?xml version="1.0" encoding="UTF-16"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="utf16Item">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="value" type="xs:string"/>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>
""",
        encoding="utf-16",
    )

    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
    )


def test_main_xmlschema_unicode_ncname_alias(tmp_path: Path, output_file: Path) -> None:
    """Generate aliases for XML NCName values that are not valid Python identifiers."""
    input_path = tmp_path / "unicode_ncname_schema.xsd"
    input_path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="root">
    <xs:complexType>
      <xs:attribute name="ำ62" type="xs:integer"/>
    </xs:complexType>
  </xs:element>
</xs:schema>
""",
        encoding="utf-8",
    )

    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
    )


def test_main_xmlschema_unsupported_xsd_pattern_importable(tmp_path: Path, output_file: Path) -> None:
    """Skip XSD regex facets that Python/Pydantic cannot compile at import time."""
    input_path = tmp_path / "unsupported_xsd_pattern.xsd"
    input_path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:simpleType name="NameToken">
    <xs:restriction base="xs:string">
      <xs:pattern value="\\i\\c*"/>
    </xs:restriction>
  </xs:simpleType>
  <xs:simpleType name="BrokenPattern">
    <xs:restriction base="xs:string">
      <xs:pattern value="("/>
    </xs:restriction>
  </xs:simpleType>
  <xs:element name="root" type="NameToken"/>
  <xs:element name="broken" type="BrokenPattern"/>
</xs:schema>
""",
        encoding="utf-8",
    )

    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
    )


def test_main_xmlschema_self_extension_without_redefine_importable(tmp_path: Path, output_file: Path) -> None:
    """Emit the local content for a self-extension when no redefined base exists."""
    input_path = tmp_path / "self_extension_without_redefine.xsd"
    input_path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:complexType name="Node">
    <xs:complexContent>
      <xs:extension base="Node">
        <xs:sequence>
          <xs:element name="child" type="xs:string"/>
        </xs:sequence>
      </xs:extension>
    </xs:complexContent>
  </xs:complexType>
  <xs:element name="root" type="Node"/>
</xs:schema>
""",
        encoding="utf-8",
    )

    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
    )


def test_main_xmlschema_redefine_self_references_importable(tmp_path: Path, output_file: Path) -> None:
    """Resolve self-references inside xs:redefine against the original definition."""
    (tmp_path / "base.xsd").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:redefine" xmlns="urn:redefine">
  <xs:group name="group">
    <xs:sequence>
      <xs:element name="base" type="xs:string"/>
    </xs:sequence>
  </xs:group>
  <xs:attributeGroup name="attrs">
    <xs:attribute name="baseAttr" type="xs:string"/>
  </xs:attributeGroup>
  <xs:complexType name="Container">
    <xs:group ref="group"/>
    <xs:attributeGroup ref="attrs"/>
  </xs:complexType>
  <xs:element name="root" type="Container"/>
</xs:schema>
""",
        encoding="utf-8",
    )
    input_path = tmp_path / "redefine_self_reference.xsd"
    input_path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:redefine" xmlns="urn:redefine">
  <xs:redefine schemaLocation="base.xsd">
    <xs:group name="group">
      <xs:sequence>
        <xs:element name="before" type="xs:string"/>
        <xs:group ref="group"/>
        <xs:element name="after" type="xs:string"/>
      </xs:sequence>
    </xs:group>
    <xs:attributeGroup name="attrs">
      <xs:attributeGroup ref="attrs"/>
      <xs:attribute name="addedAttr" type="xs:string"/>
    </xs:attributeGroup>
    <xs:attributeGroup name="newAttrs">
      <xs:attribute name="newAttr" type="xs:string"/>
    </xs:attributeGroup>
    <xs:complexType name="Container">
      <xs:complexContent>
        <xs:extension base="Container">
          <xs:sequence>
            <xs:element name="tail" type="xs:string"/>
          </xs:sequence>
        </xs:extension>
      </xs:complexContent>
    </xs:complexType>
  </xs:redefine>
</xs:schema>
""",
        encoding="utf-8",
    )

    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
    )


def test_main_xmlschema_schema_composition(output_file: Path) -> None:
    """Generate models from include, redefine, override, and chameleon schemas."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "schema_composition.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="schema_composition.py",
    )


def test_main_xmlschema_xsd11_constructs(output_file: Path) -> None:
    """Generate models from XML Schema 1.1 open content and alternatives."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "xsd11_constructs.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="xsd11_constructs.py",
    )


def test_main_xmlschema_versioning_auto(output_file: Path) -> None:
    """Apply XSD 1.1 conditional inclusion when auto-detection sees versioning attributes."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "versioning.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="versioning.py",
    )


def test_main_xmlschema_versioning_xsd10(output_file: Path) -> None:
    """Apply XSD 1.0 conditional inclusion when requested through --schema-version."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "versioning.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        extra_args=["--schema-version", "1.0"],
        assert_func=assert_file_content,
        expected_file="versioning_xsd10.py",
    )


def test_main_xmlschema_versioning_auto_from_include(output_file: Path) -> None:
    """Detect XSD 1.1 versioning attributes from included schemas in auto mode."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "versioning_include.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="versioning_include.py",
    )


def test_main_xmlschema_versioning_include_xsd10(output_file: Path) -> None:
    """Keep included XSD versioning conditional on an explicit XSD 1.0 processor version."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "versioning_include.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        extra_args=["--schema-version", "1.0"],
        assert_func=assert_file_content,
        expected_file="versioning_include_xsd10.py",
    )


def test_main_xmlschema_default_open_content_applies_to_empty(output_file: Path) -> None:
    """Apply defaultOpenContent to empty complex content only when requested."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "default_open_content_applies_to_empty.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="default_open_content_applies_to_empty.py",
    )


def test_main_xmlschema_datatypes_and_mixed_content(output_file: Path) -> None:
    """Generate models from XSD built-in datatypes and complexContent mixed content."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "datatypes_and_mixed_content.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="datatypes_and_mixed_content.py",
    )


def test_main_xmlschema_builtin_datatypes_matrix(output_file: Path) -> None:
    """Generate models for the W3C XML Schema built-in datatype set."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "builtin_datatypes_matrix.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="builtin_datatypes_matrix.py",
    )


def test_main_xmlschema_datetime_classes_default(output_file: Path) -> None:
    """Use XML Schema datetime defaults when no datetime class option is set."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "datetime_classes.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="datetime_classes.py",
    )


def test_main_xmlschema_datetime_classes_naive(output_file: Path) -> None:
    """Respect an explicit NaiveDatetime class for XML Schema date-time types."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "datetime_classes.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="datetime_classes_naive.py",
        extra_args=["--output-datetime-class", "NaiveDatetime"],
    )


def test_main_xmlschema_datetime_classes_aware(output_file: Path) -> None:
    """Respect an explicit AwareDatetime class for XML Schema date-time types."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "datetime_classes.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        assert_func=assert_file_content,
        expected_file="datetime_classes_aware.py",
        extra_args=["--output-datetime-class", "AwareDatetime"],
    )


def test_main_xmlschema_blocks_relative_schema_location_outside_base_path(
    capsys: pytest.CaptureFixture[str],
    output_file: Path,
) -> None:
    """Reject XML Schema includes that resolve outside the input base path."""
    project_dir = output_file.parent / "project"
    secret_dir = output_file.parent / "secret"
    project_dir.mkdir()
    secret_dir.mkdir()
    (secret_dir / "leak.xsd").write_text(
        """<?xml version="1.0"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:simpleType name="Leaked"><xs:restriction base="xs:string"/></xs:simpleType>
</xs:schema>
""",
        encoding="utf-8",
    )
    input_file = project_dir / "attack.xsd"
    input_file.write_text(
        """<?xml version="1.0"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:include schemaLocation="../secret/leak.xsd"/>
  <xs:element name="Root" type="Leaked"/>
</xs:schema>
""",
        encoding="utf-8",
    )

    run_main_and_assert(
        input_path=input_file,
        output_path=output_file,
        input_file_type="xmlschema",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Blocked unsafe XML Schema schemaLocation",
        output_should_not_exist=True,
    )


def test_main_xmlschema_blocks_absolute_schema_location_outside_base_path(
    capsys: pytest.CaptureFixture[str],
    output_file: Path,
) -> None:
    """Reject absolute XML Schema includes outside the input base path."""
    project_dir = output_file.parent / "project"
    secret_dir = output_file.parent / "secret"
    project_dir.mkdir()
    secret_dir.mkdir()
    secret_schema = secret_dir / "leak.xsd"
    secret_schema.write_text(
        """<?xml version="1.0"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:simpleType name="Leaked"><xs:restriction base="xs:string"/></xs:simpleType>
</xs:schema>
""",
        encoding="utf-8",
    )
    input_file = project_dir / "attack.xsd"
    input_file.write_text(
        f"""<?xml version="1.0"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:include schemaLocation="{secret_schema}"/>
  <xs:element name="Root" type="Leaked"/>
</xs:schema>
""",
        encoding="utf-8",
    )

    run_main_and_assert(
        input_path=input_file,
        output_path=output_file,
        input_file_type="xmlschema",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Blocked unsafe XML Schema schemaLocation",
        output_should_not_exist=True,
    )


def test_main_xmlschema_parse_error(capsys: pytest.CaptureFixture[str], output_file: Path) -> None:
    """Report invalid XML Schema syntax through the CLI."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "invalid_xml.xml",
        output_path=output_file,
        input_file_type="xmlschema",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Invalid XML Schema document",
        output_should_not_exist=True,
    )


def test_main_xmlschema_wrong_root_error(capsys: pytest.CaptureFixture[str], output_file: Path) -> None:
    """Report non-schema XML roots through explicit XML Schema parsing."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "invalid_root.xml",
        output_path=output_file,
        input_file_type="xmlschema",
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="XML Schema root element must be xs:schema",
        output_should_not_exist=True,
    )


def test_main_xmlschema_auto_broken_xml_error(capsys: pytest.CaptureFixture[str], output_file: Path) -> None:
    """Exercise XML Schema auto-detection for malformed XML input."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "invalid_xml.xml",
        output_path=output_file,
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Can't infer input file type",
        output_should_not_exist=True,
    )


def test_main_xmlschema_auto_wrong_root_error(capsys: pytest.CaptureFixture[str], output_file: Path) -> None:
    """Exercise XML Schema auto-detection for non-schema XML input."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "invalid_root.xml",
        output_path=output_file,
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Can't infer input file type",
        output_should_not_exist=True,
    )


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize("fixture", ["pattern_alternatives", "pattern_alternatives_simple", "pattern_controls"])
@pytest.mark.parametrize(
    ("backend", "suffix", "field_constraints"),
    [
        ("pydantic_v2.BaseModel", "pydantic", True),
        ("pydantic_v2.BaseModel", "pydantic_constrained", False),
        ("pydantic_v2.dataclass", "pydantic_dataclass", True),
        ("msgspec.Struct", "msgspec", True),
    ],
)
def test_xmlschema_pattern_alternatives(
    output_file: Path, entrypoint: str, fixture: str, backend: str, suffix: str, *, field_constraints: bool
) -> None:
    """Match the XSD oracle for sibling alternatives and intersect inherited constraints."""
    input_path = XML_SCHEMA_DATA_PATH / f"{fixture}.xsd"
    expected_file = f"{fixture}_{suffix}.py"
    # Keep the deep regex fixture on its selected formatter; the compact fixture checks default parity.
    explicit_builtin = fixture == "pattern_alternatives"
    if entrypoint == "cli":
        extra_args = ["--output-model-type", backend, "--target-python-version", "3.10", "--disable-timestamp"]
        if field_constraints:
            extra_args.append("--field-constraints")
        if explicit_builtin:
            extra_args.extend(["--formatters", "builtin"])
        run_main_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type="xmlschema",
            extra_args=extra_args,
            assert_func=assert_file_content,
            expected_file=expected_file,
        )
    else:
        run_generate_file_and_assert(
            input_path=input_path,
            output_path=output_file,
            input_file_type=InputFileType.XMLSchema,
            output_model_type=DataModelType(backend),
            target_python_version=PythonVersion.PY_310,
            field_constraints=field_constraints,
            use_annotated=backend == "msgspec.Struct",
            disable_timestamp=True,
            **({"formatters": [Formatter.BUILTIN]} if explicit_builtin else {}),
            assert_func=assert_file_content,
            expected_file=expected_file,
        )
    if fixture == "pattern_controls":
        return
    cases = json.loads((XML_SCHEMA_DATA_PATH / f"{fixture}.cases.json").read_text())
    results = []
    with _generated_model(output_file, f"generated_{fixture}_{suffix}_{entrypoint}", "Root") as model:
        validate = (
            msgspec.json.Decoder(type=model).decode if backend == "msgspec.Struct" else _model_json_validator(model)
        )
        for field, values in cases["values"].items():
            for value in values:
                try:
                    validate(json.dumps(cases["base"] | {field: value}))
                except (ValidationError, msgspec.ValidationError):
                    valid = False
                else:
                    valid = True
                results.append({"field": field, "value": value, "valid": valid})
    assert_output(json.dumps(results, indent=2) + "\n", EXPECTED_XML_SCHEMA_PATH / f"{fixture}.validation.txt")
    if backend != "msgspec.Struct":
        assert_generated_model_json_validation(
            output_file,
            module_name=f"generated_{fixture}_{suffix}_{entrypoint}_alias",
            model_name="Token",
            valid_json=json.dumps(cases["alias"]["valid"]),
            invalid_json=json.dumps(cases["alias"]["invalid"]),
            expected_error_type="string_pattern_mismatch",
        )


@pytest.mark.parametrize(
    ("extra_args", "expected_file"),
    [
        ([], "pattern_alternatives_simple_pydantic_type_alias.py"),
        (["--field-constraints"], "pattern_alternatives_simple_pydantic_type_alias_field_constraints.py"),
    ],
)
def test_xmlschema_compiled_pattern_alias_without_regex_engine(
    output_file: Path, extra_args: list[str], expected_file: str
) -> None:
    """Leave ``regex_engine`` off the models that use a type alias whose XSD pattern is already compiled.

    Targets before Pydantic 2.8 keep JSON Schema patterns as strings in type aliases, so their users select the
    engine. Combined XSD patterns are compiled for every target, ``2`` included, so they need Pydantic 2.8, from which
    they validate without a config. ``--field-constraints`` keeps the compiled pattern in ``Field()``.
    """
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "pattern_alternatives_simple.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-type-alias",
            "--target-pydantic-version",
            "2",
            "--target-python-version",
            "3.10",
            "--disable-timestamp",
            *extra_args,
        ],
        assert_func=assert_file_content,
        expected_file=expected_file,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="generated_pattern_alternatives_simple_type_alias",
        model_name="Root",
        valid_json=json.dumps({"value": "ABC"}),
        invalid_json=json.dumps({"value": "ABC\n"}),
        expected_error_type="string_pattern_mismatch",
    )


@pytest.mark.parametrize(
    ("name", "backend", "extra_args"),
    [
        (
            "inherited",
            "pydantic_v2.BaseModel",
            [
                "--field-constraints",
                "--base-class",
                "tests.data.python.pattern_intersection_base.PythonRegexIntermediate",
                "--reuse-model",
            ],
        ),
        ("constrained", "pydantic_v2.dataclass", []),
        ("collapsed", "pydantic_v2.BaseModel", ["--field-constraints", "--collapse-root-models"]),
        (
            "collapsed_dataclass",
            "pydantic_v2.dataclass",
            ["--field-constraints", "--collapse-root-models", "--target-pydantic-version", "2.14"],
        ),
        (
            "custom_alias",
            "pydantic_v2.BaseModel",
            [
                "--field-constraints",
                "--use-root-model-type-alias",
                "--use-annotated",
                "--custom-template-dir",
                str(DATA_PATH / "templates" / "root_alias_constraints"),
            ],
        ),
    ],
)
@pytest.mark.filterwarnings("ignore:Possible set symmetric difference:FutureWarning")
def test_xmlschema_compiled_patterns(output_file: Path, name: str, backend: str, extra_args: list[str]) -> None:
    """Keep Python pattern semantics across import aliases, inheritance and root model transformations."""
    run_main_and_assert(
        input_path=XML_SCHEMA_DATA_PATH / "pattern_runtime.xsd",
        output_path=output_file,
        input_file_type="xmlschema",
        extra_args=[
            "--output-model-type",
            backend,
            "--disable-timestamp",
            "--target-python-version",
            "3.10",
            "--formatters",
            "builtin",
            *extra_args,
        ],
        assert_func=assert_file_content,
        expected_file=f"pattern_runtime_{name}.py",
    )
    cases = json.loads((XML_SCHEMA_DATA_PATH / "pattern_runtime.cases.json").read_text(encoding="utf-8"))
    for case in cases["cases"]:
        assert_generated_model_json_validation(
            output_file,
            module_name=f"generated_pattern_runtime_{name}",
            model_name="Root",
            valid_json=json.dumps(cases["base"] | case["valid"]),
            invalid_json=json.dumps(cases["base"] | case["invalid"]),
            expected_error_type="string_pattern_mismatch",
        )


XSD_PROPERTY_COLLISIONS = (
    "mixed_element",
    "mixed_attribute",
    "element_attribute",
    "namespaced_elements",
    "namespaced_attributes",
    "local_forms",
    "simple_content_collision",
    "simple_content_value",
    "default_namespace_distinct",
    "default_namespace_attributes",
)
XSD_PROPERTY_CONTROLS = (
    "ordinary",
    "repeated_refs",
    "group_reuse",
    "choice_reuse",
    "inherited_override",
    "nested_scope",
    "simple_content",
    "global_elements",
    "global_local_reuse",
    "chameleon_reuse",
    "default_namespace_reuse",
    "default_namespace_alias_reuse",
    "empty_namespace_reuse",
)
XSD_PROPERTY_BACKENDS = (
    DataModelType.PydanticV2BaseModel,
    DataModelType.PydanticV2Dataclass,
    DataModelType.DataclassesDataclass,
    DataModelType.TypingTypedDict,
    DataModelType.MsgspecStruct,
)


@pytest.mark.parametrize("name", [*XSD_PROPERTY_COLLISIONS, *XSD_PROPERTY_CONTROLS])
def test_native_xsd_property_names(name: str) -> None:
    """Confirm collision and reuse fixtures are legal XSD with valid XML instances."""
    source = XML_SCHEMA_DATA_PATH / "field_name_collisions" / name
    schema = etree.XMLSchema(etree.parse(str(source.with_suffix(".xsd"))))
    schema.assertValid(etree.parse(str(source.with_suffix(".xml"))))
    with pytest.raises(etree.DocumentInvalid):
        schema.assertValid(etree.parse(str(source.with_suffix(".invalid.xml"))))


@pytest.mark.parametrize("name", [*XSD_PROPERTY_CONTROLS, "unused"])
@pytest.mark.parametrize("entrypoint", ["cli", "api"])
def test_xsd_property_reuse(name: str, entrypoint: str, output_file: Path) -> None:
    """Preserve baseline output for legal reuse, scope changes, and ordinary fields."""
    source = XML_SCHEMA_DATA_PATH / "field_name_collisions" / f"{name}.xsd"
    expected = f"field_name_collisions/{name}.py"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=source,
            output_path=output_file,
            input_file_type="xmlschema",
            extra_args=["--disable-timestamp"],
            assert_func=assert_file_content,
            expected_file=expected,
        )
    else:
        run_generate_file_and_assert(
            input_path=source,
            output_path=output_file,
            input_file_type=InputFileType.XMLSchema,
            disable_timestamp=True,
            assert_func=assert_file_content,
            expected_file=expected,
        )

    if name != "unused":
        return
    schema = etree.XMLSchema(etree.parse(str(source)))
    schema.assertValid(etree.parse(str(source.with_name("ordinary.xml"))))
    with pytest.raises(etree.DocumentInvalid):
        schema.assertValid(etree.parse(str(source.with_name("ordinary.invalid.xml"))))
    payloads = DATA_PATH / "payloads/xsd_field_name_collisions"
    assert_generated_model_json_validation(
        output_file,
        module_name=f"unused_xsd_property_{entrypoint}",
        model_name="Root",
        valid_json=(payloads / "ordinary.json").read_text(),
        invalid_json=(payloads / "ordinary_invalid.json").read_text(),
        expected_error_type="int_parsing",
        expected_attribute_path=("item",),
        expected_attribute_value=1,
    )


@pytest.mark.parametrize("backend", XSD_PROPERTY_BACKENDS)
@pytest.mark.parametrize("entrypoint", ["cli", "api"])
def test_xsd_ordinary_fields_runtime(backend: DataModelType, entrypoint: str, output_file: Path) -> None:
    """Keep field order and usable generated types across all supported backends."""
    source = XML_SCHEMA_DATA_PATH / "field_name_collisions/ordinary.xsd"
    expected = (
        "field_name_collisions/ordinary.py"
        if backend == DataModelType.PydanticV2BaseModel
        else f"field_name_collisions/ordinary_{backend.name}.py"
    )
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=source,
            output_path=output_file,
            input_file_type="xmlschema",
            extra_args=["--disable-timestamp", "--output-model-type", backend.value],
            assert_func=assert_file_content,
            expected_file=expected,
        )
    else:
        run_generate_file_and_assert(
            input_path=source,
            output_path=output_file,
            input_file_type=InputFileType.XMLSchema,
            output_model_type=backend,
            disable_timestamp=True,
            assert_func=assert_file_content,
            expected_file=expected,
        )
    payloads = DATA_PATH / "payloads/xsd_field_name_collisions"
    valid_json = (payloads / "ordinary.json").read_text(encoding="utf-8")
    invalid_json = (payloads / "ordinary_invalid.json").read_text(encoding="utf-8")
    with _generated_model(output_file, f"b65_{backend.name}_{entrypoint}", "Root") as model:
        fields = list(model.__annotations__)
        if backend == DataModelType.TypingTypedDict:
            with _generated_model(
                DATA_PATH / "python/xsd_field_name_collisions/native_typeddict.py", "native_xsd_typeddict", "NativeRoot"
            ) as native_model:
                try:
                    _model_json_validator(native_model)
                except PydanticUserError as native_error:
                    assert_output(
                        f"{native_error.code}: {native_error.message}\n",
                        EXPECTED_XML_SCHEMA_PATH / "field_name_collisions/typeddict_native_error.txt",
                    )
                    with pytest.raises(PydanticUserError) as generated_error:
                        _model_json_validator(model)
                    assert_output(
                        f"{generated_error.value.code}: {generated_error.value.message}\n",
                        EXPECTED_XML_SCHEMA_PATH / "field_name_collisions/typeddict_native_error.txt",
                    )
                    assert_output(
                        json.dumps({"fields": fields, "item": model(**json.loads(valid_json))["item"]}, indent=2)
                        + "\n",
                        EXPECTED_XML_SCHEMA_PATH / "field_name_collisions/ordinary_runtime.txt",
                    )
                    return
        if backend == DataModelType.MsgspecStruct:
            value = msgspec.json.decode(valid_json, type=model)
            item = value.item
            with pytest.raises(msgspec.ValidationError):
                msgspec.json.decode(invalid_json, type=model)
        else:
            validate = _model_json_validator(model)
            value = validate(valid_json)
            item = value["item"] if isinstance(value, dict) else value.item
            with pytest.raises(ValidationError):
                validate(invalid_json)

    assert_output(
        json.dumps({"fields": fields, "item": item}, indent=2) + "\n",
        EXPECTED_XML_SCHEMA_PATH / "field_name_collisions/ordinary_runtime.txt",
    )


@pytest.mark.parametrize("entrypoint", ["cli", "api"])
@pytest.mark.parametrize(
    ("schema_group", "case"),
    [
        (schema_group, case)
        for schema_group in ("occurrence_bounds", "simple_content_inheritance")
        for case in json.loads((DATA_PATH / f"payloads/xmlschema_{schema_group}/cases.json").read_text())
    ],
    ids=lambda value: value["name"] if isinstance(value, dict) else value,
)
def test_xmlschema_composed_content(output_file: Path, entrypoint: str, schema_group: str, case: dict) -> None:
    """Preserve occurrence limits and inherited scalar content through real generation."""
    name = case["name"]
    source = XML_SCHEMA_DATA_PATH / schema_group / f"{name}.xsd"
    expected = f"{schema_group}/{name}.py"
    if entrypoint == "cli":
        run_main_and_assert(
            input_path=source,
            output_path=output_file,
            input_file_type="xmlschema",
            assert_func=assert_file_content,
            expected_file=expected,
            extra_args=[
                "--output-model-type",
                "pydantic_v2.BaseModel",
                "--field-constraints",
                "--use-field-description",
                "--disable-timestamp",
            ],
            force_exec_validation=True,
        )
    else:
        run_generate_file_and_assert(
            input_path=source,
            output_path=output_file,
            input_file_type=InputFileType.XMLSchema,
            output_model_type=DataModelType.PydanticV2BaseModel,
            field_constraints=True,
            use_field_description=True,
            disable_timestamp=True,
            assert_func=assert_file_content,
            expected_file=expected,
        )
    with _generated_model(output_file, f"generated_{schema_group}_{name}", "Root") as model:
        results = []
        for sample in case["samples"]:
            if sample["valid"]:
                value = model.model_validate(sample["data"])
                if schema_group == "simple_content_inheritance":
                    results.append(value.model_dump(mode="json"))
            else:
                with pytest.raises(ValidationError):
                    model.model_validate(sample["data"])
        if schema_group == "simple_content_inheritance":
            assert_output(
                json.dumps(results, indent=2) + "\n",
                EXPECTED_XML_SCHEMA_PATH / schema_group / f"{case.get('expected', name)}.txt",
            )
