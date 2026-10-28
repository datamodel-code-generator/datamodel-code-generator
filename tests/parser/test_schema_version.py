"""Tests for schema version detection and features."""

from __future__ import annotations

from pathlib import Path

import pytest
from inline_snapshot import snapshot

import datamodel_code_generator
from datamodel_code_generator.enums import JsonSchemaVersion, OpenAPIVersion, VersionMode, XMLSchemaVersion
from datamodel_code_generator.parser.schema_version import (
    JsonSchemaFeatures,
    OpenAPISchemaFeatures,
    detect_jsonschema_version,
    detect_openapi_version,
)
from datamodel_code_generator.parser.xmlschema import detect_xmlschema_version

# Path to test data
JSON_SCHEMA_DATA_PATH = Path(__file__).parent.parent / "data" / "jsonschema"
XML_SCHEMA_DATA_PATH = Path(__file__).parent.parent / "data" / "xmlschema"


def test_detect_jsonschema_version_draft4() -> None:
    """Test detection of Draft 4 from $schema field."""
    assert detect_jsonschema_version({"$schema": "http://json-schema.org/draft-04/schema#"}) == snapshot(
        JsonSchemaVersion.Draft4
    )


def test_detect_jsonschema_version_draft6() -> None:
    """Test detection of Draft 6 from $schema field."""
    assert detect_jsonschema_version({"$schema": "http://json-schema.org/draft-06/schema#"}) == snapshot(
        JsonSchemaVersion.Draft6
    )


def test_detect_jsonschema_version_draft7() -> None:
    """Test detection of Draft 7 from $schema field."""
    assert detect_jsonschema_version({"$schema": "http://json-schema.org/draft-07/schema#"}) == snapshot(
        JsonSchemaVersion.Draft7
    )


def test_detect_jsonschema_version_2019_09() -> None:
    """Test detection of Draft 2019-09 from $schema field."""
    assert detect_jsonschema_version({"$schema": "https://json-schema.org/draft/2019-09/schema"}) == snapshot(
        JsonSchemaVersion.Draft201909
    )


def test_detect_jsonschema_version_2020_12() -> None:
    """Test detection of Draft 2020-12 from $schema field."""
    assert detect_jsonschema_version({"$schema": "https://json-schema.org/draft/2020-12/schema"}) == snapshot(
        JsonSchemaVersion.Draft202012
    )


def test_detect_jsonschema_version_defs_heuristic() -> None:
    """Test detection using $defs heuristic.

    $defs was introduced in Draft 2019-09, but Draft 2020-12 also uses it.
    Since 2020-12 is a superset, default to 2020-12 to avoid false warnings.
    """
    assert detect_jsonschema_version({"$defs": {"Foo": {"type": "string"}}}) == snapshot(JsonSchemaVersion.Draft202012)


def test_detect_jsonschema_version_definitions_heuristic() -> None:
    """Test detection using definitions heuristic."""
    assert detect_jsonschema_version({"definitions": {"Foo": {"type": "string"}}}) == snapshot(JsonSchemaVersion.Draft7)


def test_detect_jsonschema_version_fallback() -> None:
    """Test fallback to Draft 7 when no indicators present."""
    assert detect_jsonschema_version({"type": "object"}) == snapshot(JsonSchemaVersion.Draft7)


def test_detect_jsonschema_version_non_string_schema() -> None:
    """Test handling of non-string $schema value."""
    assert detect_jsonschema_version({"$schema": 123}) == snapshot(JsonSchemaVersion.Draft7)


def test_detect_openapi_version_30() -> None:
    """Test detection of OpenAPI 3.0."""
    assert detect_openapi_version({"openapi": "3.0.0"}) == snapshot(OpenAPIVersion.V30)


def test_detect_openapi_version_30_patch() -> None:
    """Test detection of OpenAPI 3.0.x."""
    assert detect_openapi_version({"openapi": "3.0.3"}) == snapshot(OpenAPIVersion.V30)


def test_detect_openapi_version_31() -> None:
    """Test detection of OpenAPI 3.1."""
    assert detect_openapi_version({"openapi": "3.1.0"}) == snapshot(OpenAPIVersion.V31)


def test_detect_openapi_version_fallback() -> None:
    """Test fallback to OpenAPI 3.1 when no version present."""
    assert detect_openapi_version({"info": {"title": "Test"}}) == snapshot(OpenAPIVersion.V31)


def test_detect_openapi_version_non_string() -> None:
    """Test handling of non-string openapi value."""
    assert detect_openapi_version({"openapi": 3.0}) == snapshot(OpenAPIVersion.V31)


def test_detect_xmlschema_version_10() -> None:
    """Test fallback to XML Schema 1.0 when no XSD 1.1 signal is present."""
    assert detect_xmlschema_version("<xs:schema xmlns:xs='http://www.w3.org/2001/XMLSchema'/>") == snapshot(
        XMLSchemaVersion.V10
    )


def test_detect_xmlschema_version_11_from_versioning_attributes() -> None:
    """Test XML Schema 1.1 detection from vc:minVersion."""
    assert detect_xmlschema_version(
        "<xs:schema xmlns:xs='http://www.w3.org/2001/XMLSchema' "
        "xmlns:vc='http://www.w3.org/2007/XMLSchema-versioning' vc:minVersion='1.1'/>"
    ) == snapshot(XMLSchemaVersion.V11)


def test_detect_xmlschema_version_11_from_construct() -> None:
    """Test XML Schema 1.1 detection from XSD 1.1 elements."""
    assert detect_xmlschema_version(
        "<xs:schema xmlns:xs='http://www.w3.org/2001/XMLSchema'><xs:assert test='true()'/></xs:schema>"
    ) == snapshot(XMLSchemaVersion.V11)


def test_jsonschema_features_draft4() -> None:
    """Test Draft 4 features."""
    assert JsonSchemaFeatures.from_version(JsonSchemaVersion.Draft4) == snapshot(
        JsonSchemaFeatures(
            null_in_type_array=True,
            defs_not_definitions=False,
            const_support=False,
            property_names=False,
            prefix_items=False,
            boolean_schemas=False,
            id_field="id",
            definitions_key="definitions",
            exclusive_as_number=False,
            read_only_write_only=False,
            recursive_ref=False,
            dynamic_ref=False,
        )
    )


def test_jsonschema_features_draft6() -> None:
    """Test Draft 6 features."""
    assert JsonSchemaFeatures.from_version(JsonSchemaVersion.Draft6) == snapshot(
        JsonSchemaFeatures(
            null_in_type_array=True,
            defs_not_definitions=False,
            prefix_items=False,
            boolean_schemas=True,
            id_field="$id",
            definitions_key="definitions",
            exclusive_as_number=True,
            read_only_write_only=False,
            recursive_ref=False,
            dynamic_ref=False,
        )
    )


def test_jsonschema_features_draft7() -> None:
    """Test Draft 7 features."""
    assert JsonSchemaFeatures.from_version(JsonSchemaVersion.Draft7) == snapshot(
        JsonSchemaFeatures(
            null_in_type_array=True,
            defs_not_definitions=False,
            prefix_items=False,
            boolean_schemas=True,
            id_field="$id",
            definitions_key="definitions",
            exclusive_as_number=True,
            read_only_write_only=True,
            recursive_ref=False,
            dynamic_ref=False,
        )
    )


def test_jsonschema_features_2019_09() -> None:
    """Test Draft 2019-09 features."""
    assert JsonSchemaFeatures.from_version(JsonSchemaVersion.Draft201909) == snapshot(
        JsonSchemaFeatures(
            null_in_type_array=True,
            defs_not_definitions=True,
            prefix_items=False,
            boolean_schemas=True,
            id_field="$id",
            definitions_key="$defs",
            exclusive_as_number=True,
            read_only_write_only=True,
            anchor=True,
            recursive_ref=True,
            dynamic_ref=False,
        )
    )


def test_jsonschema_features_2020_12() -> None:
    """Test Draft 2020-12 features."""
    assert JsonSchemaFeatures.from_version(JsonSchemaVersion.Draft202012) == snapshot(
        JsonSchemaFeatures(
            null_in_type_array=True,
            defs_not_definitions=True,
            prefix_items=True,
            boolean_schemas=True,
            id_field="$id",
            definitions_key="$defs",
            exclusive_as_number=True,
            read_only_write_only=True,
            anchor=True,
            recursive_ref=True,
            dynamic_ref=True,
        )
    )


def test_jsonschema_features_auto() -> None:
    """Test Auto version defaults to latest features."""
    assert JsonSchemaFeatures.from_version(JsonSchemaVersion.Auto) == snapshot(
        JsonSchemaFeatures(
            null_in_type_array=True,
            defs_not_definitions=True,
            prefix_items=True,
            boolean_schemas=True,
            id_field="$id",
            definitions_key="$defs",
            exclusive_as_number=True,
            read_only_write_only=True,
            anchor=True,
            recursive_ref=True,
            dynamic_ref=True,
        )
    )


def test_jsonschema_features_frozen() -> None:
    """Test that features are immutable."""
    features = JsonSchemaFeatures.from_version(JsonSchemaVersion.Draft7)
    with pytest.raises(AttributeError):
        features.null_in_type_array = True  # type: ignore[misc]


def test_openapi_features_v30() -> None:
    """Test OpenAPI 3.0 features."""
    assert OpenAPISchemaFeatures.from_openapi_version(OpenAPIVersion.V30) == snapshot(
        OpenAPISchemaFeatures(
            null_in_type_array=False,
            defs_not_definitions=False,
            prefix_items=False,
            boolean_schemas=False,
            id_field="$id",
            definitions_key="definitions",
            exclusive_as_number=False,
            read_only_write_only=True,
            recursive_ref=False,
            dynamic_ref=False,
            nullable_keyword=True,
            discriminator_support=True,
        )
    )


def test_openapi_features_v31() -> None:
    """Test OpenAPI 3.1 features."""
    assert OpenAPISchemaFeatures.from_openapi_version(OpenAPIVersion.V31) == snapshot(
        OpenAPISchemaFeatures(
            null_in_type_array=True,
            defs_not_definitions=True,
            prefix_items=True,
            boolean_schemas=True,
            id_field="$id",
            definitions_key="$defs",
            webhooks=True,
            ref_sibling_keywords=True,
            exclusive_as_number=True,
            read_only_write_only=True,
            anchor=True,
            recursive_ref=True,
            dynamic_ref=True,
            nullable_keyword=False,
            discriminator_support=True,
        )
    )


def test_openapi_features_auto() -> None:
    """Test Auto version defaults to latest features."""
    assert OpenAPISchemaFeatures.from_openapi_version(OpenAPIVersion.Auto) == snapshot(
        OpenAPISchemaFeatures(
            null_in_type_array=True,
            defs_not_definitions=True,
            prefix_items=True,
            boolean_schemas=True,
            id_field="$id",
            definitions_key="$defs",
            webhooks=True,
            ref_sibling_keywords=True,
            exclusive_as_number=True,
            read_only_write_only=True,
            anchor=True,
            recursive_ref=True,
            dynamic_ref=True,
            nullable_keyword=False,
            discriminator_support=True,
        )
    )


def test_openapi_features_inherits_jsonschema() -> None:
    """Test that OpenAPISchemaFeatures inherits from JsonSchemaFeatures."""
    features = OpenAPISchemaFeatures.from_openapi_version(OpenAPIVersion.V31)
    assert isinstance(features, JsonSchemaFeatures)
    assert features.prefix_items == snapshot(True)


def test_openapi_features_frozen() -> None:
    """Test that features are immutable."""
    features = OpenAPISchemaFeatures.from_openapi_version(OpenAPIVersion.V30)
    with pytest.raises(AttributeError):
        features.nullable_keyword = False  # type: ignore[misc]


def test_lazy_import_detect_jsonschema_version() -> None:
    """Test that detect_jsonschema_version can be imported from main module."""
    detect_func = datamodel_code_generator.detect_jsonschema_version
    assert detect_func({"$schema": "http://json-schema.org/draft-07/schema#"}) == snapshot(JsonSchemaVersion.Draft7)


def test_lazy_import_detect_openapi_version() -> None:
    """Test that detect_openapi_version can be imported from main module."""
    detect_func = datamodel_code_generator.detect_openapi_version
    assert detect_func({"openapi": "3.1.0"}) == snapshot(OpenAPIVersion.V31)


def test_lazy_import_detect_xmlschema_version() -> None:
    """Test that detect_xmlschema_version can be imported from main module."""
    detect_func = datamodel_code_generator.detect_xmlschema_version
    assert detect_func("<xs:schema xmlns:xs='http://www.w3.org/2001/XMLSchema'/>") == snapshot(XMLSchemaVersion.V10)


def test_lazy_import_jsonschema_version_enum() -> None:
    """Test that JsonSchemaVersion is exported from main module."""
    assert datamodel_code_generator.JsonSchemaVersion is JsonSchemaVersion


def test_lazy_import_openapi_version_enum() -> None:
    """Test that OpenAPIVersion is exported from main module."""
    assert datamodel_code_generator.OpenAPIVersion is OpenAPIVersion


def test_lazy_import_xmlschema_version_enum() -> None:
    """Test that XMLSchemaVersion is exported from main module."""
    assert datamodel_code_generator.XMLSchemaVersion is XMLSchemaVersion


def test_lazy_import_version_mode_enum() -> None:
    """Test that VersionMode is exported from main module."""
    assert datamodel_code_generator.VersionMode is VersionMode


def test_get_data_formats_jsonschema() -> None:
    """Test that JsonSchema formats exclude OpenAPI-specific formats."""
    from datamodel_code_generator.parser.schema_version import get_data_formats
    from datamodel_code_generator.types import Types

    assert get_data_formats(is_openapi=False) == snapshot({
        "integer": {
            "int32": Types.int32,
            "int64": Types.int64,
            "default": Types.integer,
            "date-time": Types.date_time,
            "unix-time": Types.int64,
            "unixtime": Types.int64,
        },
        "number": {
            "float": Types.float,
            "double": Types.double,
            "decimal": Types.decimal,
            "date-time": Types.date_time,
            "time": Types.time,
            "time-delta": Types.timedelta,
            "default": Types.number,
            "unixtime": Types.int64,
        },
        "string": {
            "default": Types.string,
            "byte": Types.byte,
            "date": Types.date,
            "date-time": Types.date_time,
            "timestamp with time zone": Types.date_time,
            "date-time-local": Types.date_time_local,
            "duration": Types.timedelta,
            "time": Types.time,
            "time-local": Types.time_local,
            "path": Types.path,
            "email": Types.email,
            "idn-email": Types.email,
            "idn-hostname": Types.string,
            "iri": Types.string,
            "iri-reference": Types.string,
            "uuid": Types.uuid,
            "uuid1": Types.uuid1,
            "uuid2": Types.uuid2,
            "uuid3": Types.uuid3,
            "uuid4": Types.uuid4,
            "uuid5": Types.uuid5,
            "uri": Types.uri,
            "uri-reference": Types.string,
            "uri-template": Types.string,
            "json-pointer": Types.string,
            "relative-json-pointer": Types.string,
            "regex": Types.string,
            "hostname": Types.hostname,
            "ipv4": Types.ipv4,
            "ipv4-network": Types.ipv4_network,
            "ipv6": Types.ipv6,
            "ipv6-network": Types.ipv6_network,
            "decimal": Types.decimal,
            "integer": Types.integer,
            "unixtime": Types.int64,
            "ulid": Types.ulid,
        },
        "boolean": {"default": Types.boolean},
        "object": {"default": Types.object},
        "null": {"default": Types.null},
        "array": {"default": Types.array},
    })


def test_get_data_formats_openapi() -> None:
    """Test that OpenAPI formats include OpenAPI-specific formats."""
    from datamodel_code_generator.parser.schema_version import get_data_formats
    from datamodel_code_generator.types import Types

    assert get_data_formats(is_openapi=True) == snapshot({
        "integer": {
            "int32": Types.int32,
            "int64": Types.int64,
            "default": Types.integer,
            "date-time": Types.date_time,
            "unix-time": Types.int64,
            "unixtime": Types.int64,
        },
        "number": {
            "float": Types.float,
            "double": Types.double,
            "decimal": Types.decimal,
            "date-time": Types.date_time,
            "time": Types.time,
            "time-delta": Types.timedelta,
            "default": Types.number,
            "unixtime": Types.int64,
        },
        "string": {
            "default": Types.string,
            "byte": Types.byte,
            "date": Types.date,
            "date-time": Types.date_time,
            "timestamp with time zone": Types.date_time,
            "date-time-local": Types.date_time_local,
            "duration": Types.timedelta,
            "time": Types.time,
            "time-local": Types.time_local,
            "path": Types.path,
            "email": Types.email,
            "idn-email": Types.email,
            "idn-hostname": Types.string,
            "iri": Types.string,
            "iri-reference": Types.string,
            "uuid": Types.uuid,
            "uuid1": Types.uuid1,
            "uuid2": Types.uuid2,
            "uuid3": Types.uuid3,
            "uuid4": Types.uuid4,
            "uuid5": Types.uuid5,
            "uri": Types.uri,
            "uri-reference": Types.string,
            "uri-template": Types.string,
            "json-pointer": Types.string,
            "relative-json-pointer": Types.string,
            "regex": Types.string,
            "hostname": Types.hostname,
            "ipv4": Types.ipv4,
            "ipv4-network": Types.ipv4_network,
            "ipv6": Types.ipv6,
            "ipv6-network": Types.ipv6_network,
            "decimal": Types.decimal,
            "integer": Types.integer,
            "unixtime": Types.int64,
            "ulid": Types.ulid,
            "binary": Types.binary,
            "password": Types.password,
        },
        "boolean": {"default": Types.boolean},
        "object": {"default": Types.object},
        "null": {"default": Types.null},
        "array": {"default": Types.array},
    })


def _data_format_key_order(data_formats: dict[str, dict[str, object]]) -> dict[str, list[str]]:
    return {data_type: list(formats) for data_type, formats in data_formats.items()}


def test_get_data_formats_openapi_matches_jsonschema_public_mapping() -> None:
    """Pin parity and ordering for data-format table deduplication."""
    from datamodel_code_generator.parser.jsonschema import json_schema_data_formats
    from datamodel_code_generator.parser.schema_version import get_data_formats

    schema_version_data_formats = get_data_formats(is_openapi=True)

    assert schema_version_data_formats == json_schema_data_formats
    assert schema_version_data_formats is not json_schema_data_formats
    assert schema_version_data_formats["string"] is not json_schema_data_formats["string"]
    assert _data_format_key_order(schema_version_data_formats) == _data_format_key_order(json_schema_data_formats)
    # Dict equality ignores order. Preserve json_schema_data_formats as a public importable mapping.
    assert _data_format_key_order(json_schema_data_formats) == snapshot({
        "integer": ["int32", "int64", "default", "date-time", "unix-time", "unixtime"],
        "number": [
            "float",
            "double",
            "decimal",
            "date-time",
            "time",
            "time-delta",
            "default",
            "unixtime",
        ],
        "string": [
            "default",
            "byte",
            "date",
            "date-time",
            "timestamp with time zone",
            "date-time-local",
            "duration",
            "time",
            "time-local",
            "path",
            "email",
            "idn-email",
            "idn-hostname",
            "iri",
            "iri-reference",
            "uuid",
            "uuid1",
            "uuid2",
            "uuid3",
            "uuid4",
            "uuid5",
            "uri",
            "uri-reference",
            "uri-template",
            "json-pointer",
            "relative-json-pointer",
            "regex",
            "hostname",
            "ipv4",
            "ipv4-network",
            "ipv6",
            "ipv6-network",
            "decimal",
            "integer",
            "unixtime",
            "ulid",
            "binary",
            "password",
        ],
        "boolean": ["default"],
        "object": ["default"],
        "null": ["default"],
        "array": ["default"],
    })


def test_jsonschema_parser_schema_features_detection() -> None:
    """Test that JsonSchemaParser detects schema version from $schema."""
    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

    parser = JsonSchemaParser("")
    parser.raw_obj = {"$schema": "http://json-schema.org/draft-07/schema#"}
    features = parser.schema_features
    assert features.boolean_schemas == snapshot(True)
    assert features.definitions_key == snapshot("definitions")


def test_openapi_parser_schema_features_detection() -> None:
    """Test that OpenAPIParser detects OpenAPI version from openapi field."""
    from datamodel_code_generator.parser.openapi import OpenAPIParser

    parser = OpenAPIParser("")
    parser.raw_obj = {"openapi": "3.1.0"}
    features = parser.schema_features
    assert features.nullable_keyword == snapshot(False)
    assert features.null_in_type_array == snapshot(True)


def test_jsonschema_parser_config_version_override() -> None:
    """Test that JsonSchemaParser uses config version over auto-detection."""
    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

    parser = JsonSchemaParser("", jsonschema_version=JsonSchemaVersion.Draft4)
    parser.raw_obj = {"$schema": "http://json-schema.org/draft-07/schema#"}
    features = parser.schema_features
    assert features.id_field == snapshot("id")
    assert features.boolean_schemas == snapshot(False)


def test_openapi_parser_config_version_override() -> None:
    """Test that OpenAPIParser uses config version over auto-detection."""
    from datamodel_code_generator.parser.openapi import OpenAPIParser

    parser = OpenAPIParser("", openapi_version=OpenAPIVersion.V30)
    parser.raw_obj = {"openapi": "3.1.0"}
    features = parser.schema_features
    assert features.nullable_keyword == snapshot(True)
    assert features.null_in_type_array == snapshot(False)


@pytest.mark.cli_doc(
    options=["--schema-version"],
    option_description="""Schema version to use for parsing.

The `--schema-version` option specifies the schema version to use instead of auto-detection.
Valid values depend on input type: JsonSchema (draft-04, draft-06, draft-07, 2019-09, 2020-12)
or OpenAPI (3.0, 3.1, 3.2). Default is 'auto' (detected from $schema or openapi field).""",
    input_schema="jsonschema/simple_string.json",
    cli_args=["--schema-version", "draft-07"],
    golden_output="jsonschema/simple_string.py",
)
def test_cli_schema_version_jsonschema() -> None:
    """Test --schema-version option with JSON Schema input."""
    from datamodel_code_generator import generate

    result = generate(
        JSON_SCHEMA_DATA_PATH / "simple_string.json",
        input_file_type=datamodel_code_generator.InputFileType.JsonSchema,
        schema_version="draft-07",
    )
    assert result is not None
    assert "class Model" in result or "Model" in result


@pytest.mark.cli_doc(
    options=["--schema-version-mode"],
    option_description="""Schema version validation mode.

The `--schema-version-mode` option controls how schema version validation is performed.
'lenient' (default): accept all features regardless of version.
'strict': warn on features outside the declared/detected version.""",
    input_schema="jsonschema/simple_string.json",
    cli_args=["--schema-version-mode", "lenient"],
    golden_output="jsonschema/simple_string.py",
)
def test_cli_schema_version_mode() -> None:
    """Test --schema-version-mode option."""
    from datamodel_code_generator import generate

    result = generate(
        JSON_SCHEMA_DATA_PATH / "simple_string.json",
        input_file_type=datamodel_code_generator.InputFileType.JsonSchema,
        schema_version_mode=VersionMode.Lenient,
    )
    assert result is not None


def test_schema_paths_lenient_mode_draft7() -> None:
    """Test schema_paths returns both paths in Lenient mode for Draft 7."""
    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

    parser = JsonSchemaParser("", jsonschema_version=JsonSchemaVersion.Draft7)
    paths = parser.schema_paths
    assert paths == snapshot([
        ("#/definitions", ["definitions"]),
        ("#/$defs", ["$defs"]),
    ])


def test_jsonschema_default_schema_paths_remain_mutable_list() -> None:
    """Test default schema paths keep the public mutable list shape."""
    from datamodel_code_generator.parser.jsonschema import _DEFAULT_SCHEMA_PATHS, JsonSchemaParser

    assert isinstance(JsonSchemaParser.SCHEMA_PATHS, list)
    assert list(_DEFAULT_SCHEMA_PATHS) == JsonSchemaParser.SCHEMA_PATHS
    assert JsonSchemaParser.SCHEMA_PATHS is not _DEFAULT_SCHEMA_PATHS


def test_schema_paths_lenient_mode_2020_12() -> None:
    """Test schema_paths returns $defs first in Lenient mode for 2020-12."""
    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

    parser = JsonSchemaParser("", jsonschema_version=JsonSchemaVersion.Draft202012)
    paths = parser.schema_paths
    assert paths == snapshot([
        ("#/$defs", ["$defs"]),
        ("#/definitions", ["definitions"]),
    ])


def test_schema_paths_strict_mode_draft7() -> None:
    """Test schema_paths returns only definitions in Strict mode for Draft 7."""
    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

    parser = JsonSchemaParser(
        "",
        jsonschema_version=JsonSchemaVersion.Draft7,
        schema_version_mode=VersionMode.Strict,
    )
    paths = parser.schema_paths
    assert paths == snapshot([("#/definitions", ["definitions"])])


def test_schema_paths_strict_mode_2020_12() -> None:
    """Test schema_paths returns only $defs in Strict mode for 2020-12."""
    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

    parser = JsonSchemaParser(
        "",
        jsonschema_version=JsonSchemaVersion.Draft202012,
        schema_version_mode=VersionMode.Strict,
    )
    paths = parser.schema_paths
    assert paths == snapshot([("#/$defs", ["$defs"])])


def test_openapi_schema_paths_unchanged() -> None:
    """Test that OpenAPI schema_paths uses SCHEMA_PATHS regardless of version mode."""
    from datamodel_code_generator.parser.openapi import OpenAPIParser

    parser = OpenAPIParser(
        "",
        openapi_version=OpenAPIVersion.V31,
        schema_version_mode=VersionMode.Strict,
    )
    paths = parser.schema_paths
    assert paths == snapshot([("#/components/schemas", ["components", "schemas"])])


def test_nullable_keyword_openapi_31_strict_warning() -> None:
    """Test that nullable keyword emits warning in OpenAPI 3.1 Strict mode."""
    import warnings

    from datamodel_code_generator.parser.jsonschema import JsonSchemaObject
    from datamodel_code_generator.parser.openapi import OpenAPIParser

    parser = OpenAPIParser(
        "",
        openapi_version=OpenAPIVersion.V31,
        schema_version_mode=VersionMode.Strict,
        strict_nullable=True,
    )
    obj = JsonSchemaObject(type="string", nullable=True)

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        parser.get_data_type(obj)
        assert len(w) == 1
        assert issubclass(w[0].category, DeprecationWarning)
        assert "nullable keyword is deprecated" in str(w[0].message)


def test_nullable_keyword_openapi_30_no_warning() -> None:
    """Test that nullable keyword does NOT emit warning in OpenAPI 3.0."""
    import warnings

    from datamodel_code_generator.parser.jsonschema import JsonSchemaObject
    from datamodel_code_generator.parser.openapi import OpenAPIParser

    parser = OpenAPIParser(
        "",
        openapi_version=OpenAPIVersion.V30,
        schema_version_mode=VersionMode.Strict,
        strict_nullable=True,
    )
    obj = JsonSchemaObject(type="string", nullable=True)

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        parser.get_data_type(obj)
        deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(deprecation_warnings) == 0


def test_nullable_keyword_openapi_31_lenient_no_warning() -> None:
    """Test that nullable keyword does NOT emit warning in OpenAPI 3.1 Lenient mode."""
    import warnings

    from datamodel_code_generator.parser.jsonschema import JsonSchemaObject
    from datamodel_code_generator.parser.openapi import OpenAPIParser

    parser = OpenAPIParser(
        "",
        openapi_version=OpenAPIVersion.V31,
        schema_version_mode=VersionMode.Lenient,
        strict_nullable=True,
    )
    obj = JsonSchemaObject(type="string", nullable=True)

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        parser.get_data_type(obj)
        deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(deprecation_warnings) == 0


def test_exclusive_as_number_strict_warning_draft4() -> None:
    """Test that numeric exclusiveMinimum emits warning in Draft 4 Strict mode."""
    import warnings

    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

    parser = JsonSchemaParser(
        "",
        jsonschema_version=JsonSchemaVersion.Draft4,
        schema_version_mode=VersionMode.Strict,
    )
    raw_schema = {"type": "number", "exclusiveMinimum": 5}

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        parser._check_version_specific_features(raw_schema, ["test"])
        user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
        assert len(user_warnings) == 1
        assert "exclusiveMinimum as number" in str(user_warnings[0].message)


def test_exclusive_as_bool_strict_warning_draft7() -> None:
    """Test that boolean exclusiveMinimum emits warning in Draft 7 Strict mode."""
    import warnings

    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

    parser = JsonSchemaParser(
        "",
        jsonschema_version=JsonSchemaVersion.Draft7,
        schema_version_mode=VersionMode.Strict,
    )
    raw_schema = {"type": "number", "minimum": 5, "exclusiveMinimum": True}

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        parser._check_version_specific_features(raw_schema, ["test"])
        user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
        assert len(user_warnings) == 1
        assert "exclusiveMinimum as boolean" in str(user_warnings[0].message)


def test_prefix_items_strict_warning_draft7() -> None:
    """Test that prefixItems emits warning in Draft 7 Strict mode."""
    import warnings

    from datamodel_code_generator.parser.jsonschema import JsonSchemaObject, JsonSchemaParser

    parser = JsonSchemaParser(
        "",
        jsonschema_version=JsonSchemaVersion.Draft7,
        schema_version_mode=VersionMode.Strict,
    )
    obj = JsonSchemaObject(
        type="array",
        prefixItems=[JsonSchemaObject(type="string"), JsonSchemaObject(type="number")],
    )

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        parser._check_array_version_features(obj, ["test"])
        user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
        assert len(user_warnings) == 1
        assert "prefixItems is not supported" in str(user_warnings[0].message)


def test_items_array_strict_warning_2020_12() -> None:
    """Test that items as array emits warning in Draft 2020-12 Strict mode."""
    import warnings

    from datamodel_code_generator.parser.jsonschema import JsonSchemaObject, JsonSchemaParser

    parser = JsonSchemaParser(
        "",
        jsonschema_version=JsonSchemaVersion.Draft202012,
        schema_version_mode=VersionMode.Strict,
    )
    obj = JsonSchemaObject(
        type="array",
        items=[JsonSchemaObject(type="string"), JsonSchemaObject(type="number")],
    )

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        parser._check_array_version_features(obj, ["test"])
        user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
        assert len(user_warnings) == 1
        assert "items as array" in str(user_warnings[0].message)


def test_boolean_schema_strict_warning_draft4() -> None:
    """Test that boolean schema emits warning in Draft 4 Strict mode."""
    import warnings

    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

    parser = JsonSchemaParser(
        "",
        jsonschema_version=JsonSchemaVersion.Draft4,
        schema_version_mode=VersionMode.Strict,
    )

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        parser._check_version_specific_features(True, ["test"])
        user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
        assert len(user_warnings) == 1
        assert "Boolean schemas" in str(user_warnings[0].message)


def test_boolean_schema_no_warning_draft7() -> None:
    """Test that boolean schema does NOT emit warning in Draft 7."""
    import warnings

    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

    parser = JsonSchemaParser(
        "",
        jsonschema_version=JsonSchemaVersion.Draft7,
        schema_version_mode=VersionMode.Strict,
    )

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        parser._check_version_specific_features(True, ["test"])
        user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
        assert len(user_warnings) == 0


def test_read_only_strict_warning_draft6() -> None:
    """Test that readOnly emits warning in Draft 6 Strict mode."""
    import warnings

    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

    parser = JsonSchemaParser(
        "",
        jsonschema_version=JsonSchemaVersion.Draft6,
        schema_version_mode=VersionMode.Strict,
    )
    raw_schema = {"type": "string", "readOnly": True}

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        parser._check_version_specific_features(raw_schema, ["test"])
        user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
        assert len(user_warnings) == 1
        assert "readOnly is not supported" in str(user_warnings[0].message)


def test_write_only_strict_warning_draft4() -> None:
    """Test that writeOnly emits warning in Draft 4 Strict mode."""
    import warnings

    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

    parser = JsonSchemaParser(
        "",
        jsonschema_version=JsonSchemaVersion.Draft4,
        schema_version_mode=VersionMode.Strict,
    )
    raw_schema = {"type": "string", "writeOnly": True}

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        parser._check_version_specific_features(raw_schema, ["test"])
        user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
        assert len(user_warnings) == 1
        assert "writeOnly is not supported" in str(user_warnings[0].message)


def test_read_only_no_warning_draft7() -> None:
    """Test that readOnly does NOT emit warning in Draft 7."""
    import warnings

    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

    parser = JsonSchemaParser(
        "",
        jsonschema_version=JsonSchemaVersion.Draft7,
        schema_version_mode=VersionMode.Strict,
    )
    raw_schema = {"type": "string", "readOnly": True}

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        parser._check_version_specific_features(raw_schema, ["test"])
        user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
        assert len(user_warnings) == 0


def test_write_only_no_warning_openapi_30() -> None:
    """Test that writeOnly does NOT emit warning in OpenAPI 3.0."""
    import warnings

    from datamodel_code_generator.parser.openapi import OpenAPIParser

    parser = OpenAPIParser(
        "",
        openapi_version=OpenAPIVersion.V30,
        schema_version_mode=VersionMode.Strict,
    )
    raw_schema = {"type": "string", "writeOnly": True}

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        parser._check_version_specific_features(raw_schema, ["test"])
        user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
        assert len(user_warnings) == 0


def test_version_checks_lenient_no_warnings() -> None:
    """Test that version checks do NOT emit warnings in Lenient mode."""
    import warnings

    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

    parser = JsonSchemaParser(
        "",
        jsonschema_version=JsonSchemaVersion.Draft4,
        schema_version_mode=VersionMode.Lenient,
    )
    raw_schema = {"type": ["string", "null"], "exclusiveMinimum": 5}

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        parser._check_version_specific_features(raw_schema, ["test"])
        parser._check_version_specific_features(True, ["test"])
        user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
        assert len(user_warnings) == 0


# =============================================================================
# Parameterized E2E tests for --schema-version and --schema-version-mode
# =============================================================================

OPENAPI_DATA_PATH = Path(__file__).parent.parent / "data" / "openapi"


@pytest.mark.parametrize(
    "schema_version",
    ["draft-04", "draft-06", "draft-07", "2019-09", "2020-12"],
    ids=["draft-04", "draft-06", "draft-07", "2019-09", "2020-12"],
)
@pytest.mark.cli_doc(
    options=["--schema-version"],
    option_description="""Schema version to use for parsing JSON Schema.

The `--schema-version` option specifies the JSON Schema version to use instead of auto-detection.
Valid values: draft-04, draft-06, draft-07, 2019-09, 2020-12.
Default is 'auto' (detected from $schema field).""",
    input_schema="jsonschema/simple_string.json",
    cli_args=["--schema-version", "draft-07"],
    golden_output="jsonschema/simple_string.py",
)
def test_cli_schema_version_jsonschema_parametrized(schema_version: str) -> None:
    """Test --schema-version option with different JSON Schema versions."""
    from datamodel_code_generator import generate

    result = generate(
        JSON_SCHEMA_DATA_PATH / "simple_string.json",
        input_file_type=datamodel_code_generator.InputFileType.JsonSchema,
        schema_version=schema_version,
        disable_timestamp=True,
    )
    assert result is not None
    assert "class Model" in result
    assert result == snapshot(
        """\
# generated by datamodel-codegen:
#   filename:  simple_string.json

from __future__ import annotations

from pydantic import BaseModel


class Model(BaseModel):
    s: str"""
    )


@pytest.mark.parametrize(
    "openapi_version",
    ["3.0", "3.1"],
    ids=["openapi-3.0", "openapi-3.1"],
)
@pytest.mark.cli_doc(
    options=["--schema-version"],
    option_description="""Schema version to use for parsing OpenAPI.

The `--schema-version` option specifies the OpenAPI version to use instead of auto-detection.
Valid values: 3.0, 3.1, 3.2.
Default is 'auto' (detected from openapi field).""",
    input_schema="openapi/api.yaml",
    cli_args=["--schema-version", "3.0"],
    golden_output="openapi/api.py",
)
def test_cli_schema_version_openapi_parametrized(openapi_version: str) -> None:
    """Test --schema-version option with different OpenAPI versions."""
    from datamodel_code_generator import generate

    result = generate(
        OPENAPI_DATA_PATH / "api.yaml",
        input_file_type=datamodel_code_generator.InputFileType.OpenAPI,
        schema_version=openapi_version,
        disable_timestamp=True,
    )
    assert result is not None
    assert "Pet" in result or "Pets" in result


@pytest.mark.parametrize(
    ("xmlschema_version", "expected", "unexpected"),
    [("1.0", "legacy: str", "modern: str"), ("1.1", "modern: str", "legacy: str")],
    ids=["xmlschema-1.0", "xmlschema-1.1"],
)
def test_cli_schema_version_xmlschema_parametrized(
    xmlschema_version: str,
    expected: str,
    unexpected: str,
) -> None:
    """Test --schema-version option with different XML Schema versions."""
    from datamodel_code_generator import generate

    result = generate(
        XML_SCHEMA_DATA_PATH / "versioning.xsd",
        input_file_type=datamodel_code_generator.InputFileType.XMLSchema,
        schema_version=xmlschema_version,
        disable_timestamp=True,
    )
    assert result is not None
    assert expected in result
    assert unexpected not in result


def test_xmlschema_strict_warning_for_xsd11_construct() -> None:
    """Test strict mode warning for XSD 1.1 constructs under XML Schema 1.0."""
    from datamodel_code_generator import generate

    with pytest.warns(UserWarning, match="XSD 1.1 construct"):
        generate(
            XML_SCHEMA_DATA_PATH / "xsd11_constructs.xsd",
            input_file_type=datamodel_code_generator.InputFileType.XMLSchema,
            schema_version="1.0",
            schema_version_mode=VersionMode.Strict,
            disable_timestamp=True,
        )


def test_xmlschema_strict_warning_for_versioning_attributes() -> None:
    """Test strict mode warning for XSD 1.1 versioning attributes under XML Schema 1.0."""
    from datamodel_code_generator import generate

    with pytest.warns(UserWarning, match="versioning attributes"):
        generate(
            XML_SCHEMA_DATA_PATH / "versioning.xsd",
            input_file_type=datamodel_code_generator.InputFileType.XMLSchema,
            schema_version="1.0",
            schema_version_mode=VersionMode.Strict,
            disable_timestamp=True,
        )


def test_xmlschema_strict_xsd10_without_xsd11_features_does_not_warn() -> None:
    """Test strict XML Schema 1.0 mode for a plain XSD 1.0 schema."""
    import warnings

    from datamodel_code_generator import generate

    with warnings.catch_warnings(record=True) as warning_records:
        generate(
            XML_SCHEMA_DATA_PATH / "single_root_item.xsd",
            input_file_type=datamodel_code_generator.InputFileType.XMLSchema,
            schema_version="1.0",
            schema_version_mode=VersionMode.Strict,
            disable_timestamp=True,
        )
    assert warning_records == []


@pytest.mark.parametrize(
    "version_mode",
    [VersionMode.Lenient, VersionMode.Strict],
    ids=["lenient", "strict"],
)
@pytest.mark.cli_doc(
    options=["--schema-version-mode"],
    option_description="""Schema version validation mode.

The `--schema-version-mode` option controls how schema version validation is performed.
'lenient' (default): accept all features regardless of version.
'strict': warn on features outside the declared/detected version.""",
    input_schema="jsonschema/simple_string.json",
    cli_args=["--schema-version-mode", "lenient"],
    golden_output="jsonschema/simple_string.py",
)
def test_cli_schema_version_mode_parametrized(version_mode: VersionMode) -> None:
    """Test --schema-version-mode option with different modes."""
    from datamodel_code_generator import generate

    result = generate(
        JSON_SCHEMA_DATA_PATH / "simple_string.json",
        input_file_type=datamodel_code_generator.InputFileType.JsonSchema,
        schema_version_mode=version_mode,
        disable_timestamp=True,
    )
    assert result is not None
    assert "class Model" in result
    assert result == snapshot(
        """\
# generated by datamodel-codegen:
#   filename:  simple_string.json

from __future__ import annotations

from pydantic import BaseModel


class Model(BaseModel):
    s: str"""
    )


# =============================================================================
# Error handling tests for invalid schema versions
# =============================================================================


def test_invalid_jsonschema_version_error() -> None:
    """Test that invalid JSON Schema version raises Error."""
    from datamodel_code_generator import Error, generate

    with pytest.raises(Error) as exc_info:
        generate(
            JSON_SCHEMA_DATA_PATH / "simple_string.json",
            input_file_type=datamodel_code_generator.InputFileType.JsonSchema,
            schema_version="invalid-version",
        )
    assert "Invalid JSON Schema version" in str(exc_info.value)
    assert "invalid-version" in str(exc_info.value)


def test_invalid_openapi_version_error() -> None:
    """Test that invalid OpenAPI version raises Error."""
    from datamodel_code_generator import Error, generate

    with pytest.raises(Error) as exc_info:
        generate(
            OPENAPI_DATA_PATH / "api.yaml",
            input_file_type=datamodel_code_generator.InputFileType.OpenAPI,
            schema_version="invalid-version",
        )
    assert "Invalid OpenAPI version" in str(exc_info.value)
    assert "invalid-version" in str(exc_info.value)


def test_invalid_xmlschema_version_error() -> None:
    """Test that invalid XML Schema version raises Error."""
    from datamodel_code_generator import Error, generate

    with pytest.raises(Error) as exc_info:
        generate(
            XML_SCHEMA_DATA_PATH / "single_root_item.xsd",
            input_file_type=datamodel_code_generator.InputFileType.XMLSchema,
            schema_version="invalid-version",
        )
    assert "Invalid XML Schema version" in str(exc_info.value)
    assert "invalid-version" in str(exc_info.value)


def test_graphql_schema_version_not_supported() -> None:
    """Test that --schema-version is not supported for GraphQL."""
    from datamodel_code_generator import Error, generate

    graphql_data_path = Path(__file__).parent.parent / "data" / "graphql"

    with pytest.raises(Error) as exc_info:
        generate(
            graphql_data_path / "schema.graphql",
            input_file_type=datamodel_code_generator.InputFileType.GraphQL,
            schema_version="draft-07",
        )
    assert "--schema-version is not supported" in str(exc_info.value)
    assert "graphql" in str(exc_info.value).lower()


# =============================================================================
# E2E tests for strict mode warnings
# =============================================================================


def test_e2e_exclusive_maximum_as_bool_strict_warning_draft7() -> None:
    """Test that boolean exclusiveMaximum emits warning in Draft 7 Strict mode via generate()."""
    import json
    import tempfile
    import warnings

    from datamodel_code_generator import generate

    # Draft 4 style schema with boolean exclusiveMaximum in definitions
    schema = {
        "type": "object",
        "definitions": {
            "MyValue": {
                "type": "number",
                "maximum": 10,
                "exclusiveMaximum": True,
            }
        },
        "properties": {"value": {"$ref": "#/definitions/MyValue"}},
    }

    with tempfile.NamedTemporaryFile(encoding="utf-8", mode="w", suffix=".json", delete=False) as f:
        json.dump(schema, f)
        f.flush()

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = generate(
                Path(f.name),
                input_file_type=datamodel_code_generator.InputFileType.JsonSchema,
                schema_version="draft-07",
                schema_version_mode=VersionMode.Strict,
            )
            user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
            assert any("exclusiveMaximum as boolean" in str(uw.message) for uw in user_warnings)
            assert result is not None


def test_e2e_exclusive_maximum_as_number_strict_warning_draft4() -> None:
    """Test that numeric exclusiveMaximum emits warning in Draft 4 Strict mode via generate()."""
    import json
    import tempfile
    import warnings

    from datamodel_code_generator import generate

    # Draft 6+ style schema with numeric exclusiveMaximum in definitions
    schema = {
        "type": "object",
        "definitions": {
            "MyValue": {
                "type": "number",
                "exclusiveMaximum": 10,
            }
        },
        "properties": {"value": {"$ref": "#/definitions/MyValue"}},
    }

    with tempfile.NamedTemporaryFile(encoding="utf-8", mode="w", suffix=".json", delete=False) as f:
        json.dump(schema, f)
        f.flush()

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = generate(
                Path(f.name),
                input_file_type=datamodel_code_generator.InputFileType.JsonSchema,
                schema_version="draft-04",
                schema_version_mode=VersionMode.Strict,
            )
            user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
            assert any("exclusiveMaximum as number" in str(uw.message) for uw in user_warnings)
            assert result is not None
