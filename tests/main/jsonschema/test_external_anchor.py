"""Regression coverage for external JSON Schema anchors."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datamodel_code_generator import DanglingRefWarning, SchemaResourceRefWarning
from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
from datamodel_code_generator.reference import ModelResolver
from tests.conftest import assert_output, assert_warnings_contain
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, run_main_and_assert
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH, assert_file_content


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


@pytest.mark.parametrize("output_kind", ["file", "modules"])
def test_main_jsonschema_external_anchor_in_subdirectory(output_kind: str, output_file: Path, output_dir: Path) -> None:
    """Resolve a plain-name anchor of the document beside one in a subdirectory against that subdirectory once.

    ``sub/anchor.json`` names an anchor and a shorthand pointer of ``sub/defs.json`` before that document is read,
    ``sub/pointer.json`` reads it first through a JSON pointer, so the anchors it declares are registered before the
    others name them; the directory input reads ``sub/defs.json`` as an input too. Up to 0.89.0 each of them looked
    for ``sub/sub/defs.json``.
    """
    input_path = JSON_SCHEMA_DATA_PATH / "external_anchor_subdirectory"
    run_main_and_assert(
        input_path=input_path / "root.json" if output_kind == "file" else input_path,
        output_path=output_file if output_kind == "file" else output_dir,
        input_file_type="jsonschema",
        extra_args=["--disable-timestamp", "--formatters", "builtin"],
        force_exec_validation=True,
    )


def test_main_jsonschema_external_anchor_in_schema_keyword_locations(output_file: Path) -> None:
    """Name an `$anchor` of another file in every shape of a keyword that holds schemas, never in instance data.

    `keyword_locations.json` declares one anchor under a map of schemas, a list, one schema, a keyword that holds
    one schema or a list, and in `default` and `examples`, which hold instance data: that one names nothing.
    """
    with pytest.warns(DanglingRefWarning) as warning_records:
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "external_anchor" / "keyword_locations_root.json",
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=["--disable-timestamp", "--formatters", "builtin"],
            force_exec_validation=True,
        )
    assert_warnings_contain(
        warning_records,
        "Unresolved local $ref '#/instance-only' in keyword_locations.json: JSON pointer was not found.",
    )


def test_main_jsonschema_external_anchor_released_search(output_file: Path) -> None:
    """Name an `$anchor` of another file as the released search did (row ``legacy-external-anchor-search``, #7158).

    A name two sibling schemas declare names the one written later, a name an ancestor and a schema inside it declare
    names the ancestor, and an anchor in a namespace of definitions (`$defs/v1/Pet`) names nothing, as in 0.98.0.
    """
    with pytest.warns((SchemaResourceRefWarning, DanglingRefWarning)) as warning_records:
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "external_anchor_released_search" / "root.json",
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=["--disable-timestamp", "--formatters", "builtin"],
            force_exec_validation=True,
        )
    assert_warnings_contain(
        warning_records,
        "The anchor 'twice' is declared by more than one schema in anchors.json ('#/$defs/Earlier', '#/$defs/Later')",
        "The anchor 'nested' is declared by more than one schema in anchors.json ('#/$defs/Outer', "
        "'#/$defs/Outer/properties/inner')",
        "Unresolved local $ref '#/pet' in anchors.json: JSON pointer was not found.",
    )


def test_main_jsonschema_external_anchor_in_passed_namespace(output_file: Path) -> None:
    """Name an `$anchor` of another file on a schema the resource walk passes over, as the released search did.

    A namespace of definitions (an object of metadata and `$defs` alone) is read for its entries, yet an `$anchor` on
    it, under its `contentSchema`, `not` or `if`, or under a known keyword of a version namespace (`v1`) is named as in
    0.98.0; a name a later passed-over schema and an earlier definition both declare names the later one.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "external_anchor_passed_namespace" / "root.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--disable-timestamp", "--formatters", "builtin"],
        force_exec_validation=True,
    )


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


def test_jsonschema_anchor_with_relative_base_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register the ``$anchor`` of a document the parser reads from a relative base path against that path once.

    The base path is applied once, so ``#name`` does not name ``jsonschema/jsonschema/anchor_ref.json``.
    """
    monkeypatch.chdir(JSON_SCHEMA_DATA_PATH.parent)
    base_path = Path(JSON_SCHEMA_DATA_PATH.name)
    assert_output(JsonSchemaParser(base_path / "anchor_ref.json", base_path=base_path).parse())


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
