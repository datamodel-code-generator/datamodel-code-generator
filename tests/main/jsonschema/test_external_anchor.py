"""Regression coverage for external JSON Schema anchors."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser, _find_json_schema_anchor_pointer
from datamodel_code_generator.reference import ModelResolver
from tests.conftest import assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, run_main_and_assert
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH, assert_file_content

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_main_jsonschema_external_anchor_with_legacy_shorthand(output_file: Path) -> None:
    """Resolve external anchors without regressing legacy shorthand JSON pointers."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "external_anchor" / "root.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="external_anchor.py",
        extra_args=["--disable-timestamp", "--target-python-version", "3.10"],
        force_exec_validation=True,
    )


def test_jsonschema_anchor_search_visits_only_schema_keyword_locations() -> None:
    """Traverse every schema container shape while ignoring instance data."""
    source = JSON_SCHEMA_DATA_PATH / "external_anchor" / "keyword_locations.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    output = "".join(
        f"{anchor}: {_find_json_schema_anchor_pointer(document, anchor)}\n"
        for anchor in ("mapped", "sequence", "single", "mixed-object", "mixed-list", "instance-only")
    )
    output += f"non-object root: {_find_json_schema_anchor_pointer(document['examples'], 'instance-only')}\n"

    assert_output(output, EXPECTED_JSON_SCHEMA_PATH / "anchor_keyword_locations.txt")


def test_jsonschema_anchor_search_keeps_nested_resources_isolated() -> None:
    """Do not leak nested-resource anchors into their parent resource."""
    source = JSON_SCHEMA_DATA_PATH / "external_anchor" / "child.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    nested_resource = document["$defs"]["NestedChild"]
    output = (
        f"parent: {_find_json_schema_anchor_pointer(document, 'anchored-child')}\n"
        f"nested: {_find_json_schema_anchor_pointer(nested_resource, 'anchored-child')}\n"
    )

    assert_output(output, EXPECTED_JSON_SCHEMA_PATH / "anchor_nested_resource.txt")


def test_main_jsonschema_external_id_resolved_to_local_ref(output_file: Path) -> None:
    """Do not reinterpret an external $id that resolves to a local reference."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "local_id_ref.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file="local_id_ref.py",
        extra_args=["--disable-timestamp", "--target-python-version", "3.10"],
        force_exec_validation=True,
    )


def test_main_jsonschema_malformed_external_ref_is_wrapped(
    capsys: pytest.CaptureFixture[str], output_file: Path
) -> None:
    """Report malformed external references as generator errors."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "external_ref_errors" / "malformed.json",
        output_path=output_file,
        input_file_type="jsonschema",
        expected_exit=Exit.ERROR,
    )
    assert_output(capsys.readouterr().err, EXPECTED_JSON_SCHEMA_PATH / "malformed_external_ref.txt")


def test_main_jsonschema_malformed_anchor_ref_is_wrapped(capsys: pytest.CaptureFixture[str], output_file: Path) -> None:
    """Reject multi-fragment external refs before fetching their external target."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "external_ref_errors" / "malformed_anchor.json",
        output_path=output_file,
        input_file_type="jsonschema",
        expected_exit=Exit.ERROR,
    )
    assert_output(capsys.readouterr().err, EXPECTED_JSON_SCHEMA_PATH / "malformed_anchor_external_ref.txt")


def test_model_resolver_uses_cached_external_anchor_from_schema_fixture() -> None:
    """Reuse an external $anchor registered while parsing its schema document."""
    source = JSON_SCHEMA_DATA_PATH / "external_anchor" / "child.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    definition_name, definition = next(iter(document["$defs"].items()))
    anchor = definition["$anchor"]
    resolver = ModelResolver(base_url="https://example.test/root.json")
    resolver.set_current_root([source.name])
    resolver.add_id(f"#{anchor}", ["#", "$defs", definition_name])

    cached_result = resolver.resolve_ref(f"{source.name}#{anchor}")
    local_result = resolver.resolve_ref(f"#{anchor}")

    assert_output(f"{cached_result}\n{local_result}\n", EXPECTED_JSON_SCHEMA_PATH / "cached_external_anchor_ref.txt")


def test_jsonschema_external_id_resolved_to_local_ref_skips_anchor_normalization() -> None:
    """Do not load an external document when its $id maps into the local schema."""
    source = JSON_SCHEMA_DATA_PATH / "local_id_ref.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    definition_name, definition = next(iter(document["$defs"].items()))
    external_id = definition["$id"]
    parser = JsonSchemaParser(source, base_path=source.parent)
    parser.model_resolver.add_id(external_id, ["#", "$defs", definition_name])

    result = parser._normalize_external_ref(f"{external_id}#")

    assert_output(f"{result}\n", EXPECTED_JSON_SCHEMA_PATH / "normalized_external_id_ref.txt")
