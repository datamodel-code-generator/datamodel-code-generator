"""Select deterministic pytest shards for CI."""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path

EXCLUDED_PARTS = frozenset({"__pycache__", "cli_doc", "data"})
PAYLOAD_VALIDATION_FILE = "tests/main/test_payload_validation.py"
SPLIT_NODE_FILES = frozenset({PAYLOAD_VALIDATION_FILE})
RECIPE_VERSION = 1
TESTS_ROOT = Path("tests")
# Summed setup/call/teardown milliseconds, not xdist wall time (one CI sample).
# Ubuntu 24.04, Python 3.14.7, builtin formatter, coverage, four workers; 2026-09-07 UTC.
# Run 34153141476; jobs 101839456606 and 101839456694.
# Head 8e96cc18eedb67b2091971f0563cfb4b534fda57; checkout d9d29cb166c8aec689a7d65d7acf035e69d5211e.
# uv 0.12.10 (both executables), tox 4.61.2, tox-uv 1.36.0.
# pytest durations have 10 ms resolution; zero-duration units receive a 1 ms floor.
WEIGHT_OVERRIDES = {
    "tests/main/asyncapi/test_main_asyncapi.py": 5_670,
    "tests/main/avro/test_main_avro.py": 13_630,
    "tests/main/graphql/test_annotated.py": 210,
    "tests/main/graphql/test_main_graphql.py": 860,
    "tests/main/graphql/test_msgspec_list_defaults.py": 640,
    "tests/main/jsonschema/test_external_anchor.py": 90,
    "tests/main/jsonschema/test_main_jsonschema.py": 31_990,
    "tests/main/jsonschema/test_model_metadata.py": 210,
    "tests/main/jsonschema/test_msgspec_alias_defaults.py": 520,
    "tests/main/jsonschema/test_name_union_hardening.py": 290,
    "tests/main/jsonschema/test_reference_resolution_hardening.py": 290,
    "tests/main/jsonschema/test_schema_validation_hardening.py": 270,
    "tests/main/jsonschema/test_serialized_decimal_defaults.py": 480,
    "tests/main/jsonschema/test_symlink_external_ref.py": 10,
    "tests/main/jsonschema/test_unique_items_additional_aliases.py": 140,
    "tests/main/jsonschema/test_unique_items_allof_mapping.py": 140,
    "tests/main/jsonschema/test_unique_items_const_dialects.py": 40,
    "tests/main/jsonschema/test_unique_items_draft7_additional_items.py": 20,
    "tests/main/jsonschema/test_unique_items_prefix_items_dialects.py": 60,
    "tests/main/jsonschema/test_unique_items_property_alias.py": 140,
    "tests/main/jsonschema/test_unique_items_union_mapping_shapes.py": 200,
    "tests/main/jsonschema/test_unique_items_variant_additional.py": 70,
    "tests/main/jsonschema/test_unique_items_variant_ownership.py": 30,
    "tests/main/jsonschema/test_unique_items_variant_root_alias.py": 140,
    "tests/main/openapi/test_main_openapi.py": 35_220,
    "tests/main/protobuf/test_auto_detection.py": 4_920,
    "tests/main/protobuf/test_main_protobuf.py": 1_070,
    "tests/main/protobuf/test_option_preprocessing.py": 3_170,
    "tests/main/test_agent_skill.py": 230,
    "tests/main/test_builtin_parity.py": 20,
    "tests/main/test_cli_fast_paths.py": 8_950,
    "tests/main/test_dynamic_models.py": 2_400,
    "tests/main/test_error_messages.py": 500,
    "tests/main/test_exec_validation.py": 600,
    "tests/main/test_gc_tuning.py": 70,
    "tests/main/test_generation_determinism.py": 2_080,
    "tests/main/test_jsonschema_suite_conformance.py": 1,
    "tests/main/test_main_csv.py": 130,
    "tests/main/test_main_general.py": 11_130,
    "tests/main/test_main_input_diff.py": 1_900,
    "tests/main/test_main_json.py": 430,
    "tests/main/test_main_watch.py": 181_680,
    "tests/main/test_main_yaml.py": 20,
    "tests/main/test_memory.py": 1,
    "tests/main/test_nullable_schema_versions.py": 710,
    "tests/main/test_parsed_source_cache_parity.py": 360,
    "tests/main/test_public_api_signature_baseline.py": 1_020,
    "tests/main/test_types.py": 30,
    "tests/main/test_yaml_cache_paths.py": 200,
    "tests/main/xmlschema/test_main_xmlschema.py": 2_660,
    "tests/model/dataclass/test_param.py": 30,
    "tests/model/pydantic_v2/test_base_model.py": 2_270,
    "tests/model/pydantic_v2/test_config.py": 60,
    "tests/model/pydantic_v2/test_dataclass.py": 220,
    "tests/model/pydantic_v2/test_root_model.py": 70,
    "tests/model/pydantic_v2/test_root_model_type_alias.py": 20,
    "tests/model/pydantic_v2/test_types.py": 80,
    "tests/model/pydantic_v2/test_version.py": 10,
    "tests/model/test_base.py": 3_900,
    "tests/model/test_compiled_templates.py": 4_050,
    "tests/model/test_constraints.py": 1_480,
    "tests/model/test_dataclass.py": 70,
    "tests/model/test_dataclass_ordering.py": 150,
    "tests/model/test_output_model_compatibility.py": 10,
    "tests/parser/test_backend_capabilities.py": 20,
    "tests/parser/test_base.py": 1_250,
    "tests/parser/test_builtin_formatter_contract.py": 170,
    "tests/parser/test_default_put_dict.py": 50,
    "tests/parser/test_generation.py": 820,
    "tests/parser/test_generation_store_usage.py": 630,
    "tests/parser/test_graph.py": 70,
    "tests/parser/test_graphql.py": 1_030,
    "tests/parser/test_imports.py": 2_040,
    "tests/parser/test_jsonschema.py": 4_240,
    "tests/parser/test_model_behavior_capabilities.py": 110,
    "tests/parser/test_model_construction_capabilities.py": 300,
    "tests/parser/test_openapi.py": 4_670,
    "tests/parser/test_output_context.py": 410,
    "tests/parser/test_python_type_imports.py": 120,
    "tests/parser/test_scc.py": 380,
    "tests/parser/test_schema_version.py": 1_100,
    "tests/parser/test_xmlschema.py": 10,
    "tests/skills/datamodel-code-generator/test_skill_flag_drift.py": 170,
    "tests/skills/datamodel-code-generator/test_skill_recipes.py": 9_540,
    "tests/test_architecture_boundaries.py": 6_050,
    "tests/test_assert_helper_usage.py": 1_420,
    "tests/test_build_architecture_docs_script.py": 730,
    "tests/test_build_conformance_docs_script.py": 90,
    "tests/test_build_deprecation_docs_script.py": 120,
    "tests/test_build_docs_examples_script.py": 100,
    "tests/test_build_experimental_docs_script.py": 200,
    "tests/test_build_llms_txt_script.py": 120,
    "tests/test_build_playground_assets_script.py": 30,
    "tests/test_build_preset_docs_script.py": 1_590,
    "tests/test_build_release_benchmark_docs_script.py": 2_580,
    "tests/test_build_schema_docs_script.py": 940,
    "tests/test_check_overview_sync_script.py": 140,
    "tests/test_ci_coverage_script.py": 1_540,
    "tests/test_ci_workflow.py": 1_220,
    "tests/test_conftest_helpers.py": 470,
    "tests/test_deprecations.py": 40,
    "tests/test_enums.py": 10,
    "tests/test_experimental.py": 70,
    "tests/test_format.py": 34_740,
    "tests/test_generate_changelog_script.py": 60,
    "tests/test_http.py": 25_450,
    "tests/test_http_https.py": 2_330,
    "tests/test_http_regressions.py": 20,
    "tests/test_imports.py": 350,
    "tests/test_infer_input_type.py": 690,
    "tests/test_input_diff_timestamp.py": 560,
    "tests/test_input_model.py": 2_440,
    "tests/test_input_model_transport.py": 2_240,
    "tests/test_main_kr.py": 3_230,
    "tests/test_module_name.py": 1,
    "tests/test_package_metadata.py": 1_760,
    "tests/test_prepare_release_draft_analysis_script.py": 1_730,
    "tests/test_prompt.py": 20,
    "tests/test_python_decorator.py": 180,
    "tests/test_python_type_annotation.py": 330,
    "tests/test_python_type_import_registry.py": 90,
    "tests/test_python_type_runtime.py": 1_110,
    "tests/test_reference.py": 430,
    "tests/test_release_draft_workflow.py": 50,
    "tests/test_remote_lock.py": 540,
    "tests/test_resolver.py": 780,
    "tests/test_select_ci_test_shard_script.py": 490,
    "tests/test_types.py": 470,
    "tests/test_update_command_help_on_markdown_script.py": 60,
    "tests/test_util.py": 40,
    "tests/test_validate_release_draft_analysis_script.py": 190,
    "tests/test_validators.py": 40,
    "tests/test_yaml_backend.py": 80,
    "tests/test_yaml_fast_constructor.py": 100,
}
SPLIT_NODE_WEIGHT_OVERRIDES = {
    f"{PAYLOAD_VALIDATION_FILE}::test_generated_payload_backend_accepts_representative_schema_payloads": 4_560,
    f"{PAYLOAD_VALIDATION_FILE}::test_generated_payload_backend_rejects_representative_schema_invalid_payloads": 2_830,
    f"{PAYLOAD_VALIDATION_FILE}::test_generated_pydantic_v2_model_accepts_schema_derived_payloads": 174_330,
    f"{PAYLOAD_VALIDATION_FILE}::test_generated_pydantic_v2_model_dumps_schema_valid_payloads": 92_150,
    f"{PAYLOAD_VALIDATION_FILE}::test_generated_pydantic_v2_model_rejects_schema_invalid_payloads": 107_260,
    f"{PAYLOAD_VALIDATION_FILE}::test_msgspec_pep_695_unique_items_runtime_exclusions_match_target": 30,
    f"{PAYLOAD_VALIDATION_FILE}::test_msgspec_schema_runtime_exclusions_cover_known_semantic_gaps": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_msgspec_schema_runtime_exclusions_detect_untyped_fractional_multiple_of": 40,
    f"{PAYLOAD_VALIDATION_FILE}::test_msgspec_schema_runtime_exclusions_ignore_literal_payloads": 10,
    f"{PAYLOAD_VALIDATION_FILE}::test_msgspec_unique_items_runtime_exclusions_match_type_limits": 10,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_backend_all_case_mode_widens_runtime_validating_backends": 60,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_backend_case_mode_env_is_configurable": 10,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_backend_case_mode_env_rejects_invalid_values": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_backend_full_matrix_exclusions_are_classified": 20,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_backend_representative_matrix_is_classified": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_codegen_restores_parsed_source_cache_on_error": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_max_examples_env_is_configurable": 10,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_max_examples_env_rejects_invalid_values": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_rejection_oracle_covers_supported_policy_constraints": 30,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_rejection_oracle_policy_is_classified": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_round_trip_exclusions_are_classified": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_validation_cases_cover_discovered_schema_files": 1_750,
    f"{PAYLOAD_VALIDATION_FILE}::test_pydantic_round_trip_exclusions_cover_unique_items_normalization": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_pydantic_v2_dataclass_legacy_exclusions_are_version_gated": 10,
    f"{PAYLOAD_VALIDATION_FILE}::test_pydantic_v2_dataclass_type_alias_exclusion_is_backend_specific": 10,
    f"{PAYLOAD_VALIDATION_FILE}::test_pydantic_v2_float_multiple_of_exclusions_are_version_gated": 1,
    (
        f"{PAYLOAD_VALIDATION_FILE}::"
        "test_pydantic_v2_legacy_runtime_cross_module_lookaround_exclusions_are_version_gated"
    ): 10,
    f"{PAYLOAD_VALIDATION_FILE}::test_pydantic_v2_legacy_runtime_exclusions_are_classified": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_pydantic_v2_legacy_runtime_exclusions_are_version_gated": 40,
}


def _as_posix(path: Path) -> str:
    return path.as_posix()


def _collect_split_nodeids(path: Path) -> list[str]:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=_as_posix(path))
    nodeids: list[str] = []

    for node in module.body:
        match node:
            case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name) if name.startswith("test_"):
                nodeids.append(f"{_as_posix(path)}::{name}")
            case ast.ClassDef(name=class_name) if class_name.startswith("Test"):
                for child in node.body:
                    match child:
                        case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name) if name.startswith("test_"):
                            nodeids.append(f"{_as_posix(path)}::{class_name}::{name}")

    return sorted(nodeids)


def _collect_test_items(root: Path = TESTS_ROOT) -> list[str]:
    file_items: list[str] = []
    for directory, subdirectories, filenames in os.walk(root):
        subdirectories[:] = [name for name in subdirectories if name not in EXCLUDED_PARTS]
        file_items.extend(
            item
            for name in filenames
            if name.startswith("test_") and name.endswith(".py")
            if (item := (Path(directory) / name).as_posix()) not in SPLIT_NODE_FILES
        )
    split_node_items = (
        nodeid for split_file in sorted(SPLIT_NODE_FILES) for nodeid in _collect_split_nodeids(Path(split_file))
    )
    file_items.extend(split_node_items)
    file_items.sort()
    return file_items


def _median_weight(weights: dict[str, int]) -> int:
    ordered = sorted(weights.values())
    middle = len(ordered) // 2
    return (ordered[middle] + ordered[~middle]) // 2


# Older Python versions have a different payload-validation/runtime cost balance.
# Legacy profile: Python 3.10.21, Ubuntu 24.04, builtin formatter, coverage, four workers.
# One sample, 2026-09-07 UTC; summed setup/call/teardown ms, 10 ms resolution, zero -> 1 ms.
# Run 34154249401; jobs 101842643284, 101842643351 and 101842643407.
# Head b12b5f49294b0237154854a538f8e914e61a6e66; checkout 4e7871056b160954cb5a37f75867c9277124d0ee.
LEGACY_WEIGHT_OVERRIDES = {
    "tests/main/asyncapi/test_main_asyncapi.py": 8_050,
    "tests/main/avro/test_main_avro.py": 13_280,
    "tests/main/graphql/test_annotated.py": 6_260,
    "tests/main/graphql/test_main_graphql.py": 8_800,
    "tests/main/graphql/test_msgspec_list_defaults.py": 1_040,
    "tests/main/jsonschema/test_external_anchor.py": 310,
    "tests/main/jsonschema/test_main_jsonschema.py": 56_210,
    "tests/main/jsonschema/test_model_metadata.py": 520,
    "tests/main/jsonschema/test_msgspec_alias_defaults.py": 640,
    "tests/main/jsonschema/test_name_union_hardening.py": 110,
    "tests/main/jsonschema/test_reference_resolution_hardening.py": 100,
    "tests/main/jsonschema/test_schema_validation_hardening.py": 180,
    "tests/main/jsonschema/test_serialized_decimal_defaults.py": 840,
    "tests/main/jsonschema/test_symlink_external_ref.py": 20,
    "tests/main/jsonschema/test_unique_items_additional_aliases.py": 160,
    "tests/main/jsonschema/test_unique_items_allof_mapping.py": 80,
    "tests/main/jsonschema/test_unique_items_const_dialects.py": 110,
    "tests/main/jsonschema/test_unique_items_draft7_additional_items.py": 60,
    "tests/main/jsonschema/test_unique_items_prefix_items_dialects.py": 120,
    "tests/main/jsonschema/test_unique_items_property_alias.py": 210,
    "tests/main/jsonschema/test_unique_items_union_mapping_shapes.py": 620,
    "tests/main/jsonschema/test_unique_items_variant_additional.py": 100,
    "tests/main/jsonschema/test_unique_items_variant_ownership.py": 70,
    "tests/main/jsonschema/test_unique_items_variant_root_alias.py": 200,
    "tests/main/openapi/test_main_openapi.py": 65_880,
    "tests/main/protobuf/test_auto_detection.py": 15_820,
    "tests/main/protobuf/test_main_protobuf.py": 1_660,
    "tests/main/protobuf/test_option_preprocessing.py": 5_460,
    "tests/main/test_agent_skill.py": 160,
    "tests/main/test_builtin_parity.py": 1,
    "tests/main/test_cli_fast_paths.py": 8_450,
    "tests/main/test_dynamic_models.py": 7_890,
    "tests/main/test_error_messages.py": 1_570,
    "tests/main/test_exec_validation.py": 400,
    "tests/main/test_gc_tuning.py": 130,
    "tests/main/test_generation_determinism.py": 1_780,
    "tests/main/test_jsonschema_suite_conformance.py": 1,
    "tests/main/test_main_csv.py": 370,
    "tests/main/test_main_general.py": 24_660,
    "tests/main/test_main_input_diff.py": 1_890,
    "tests/main/test_main_json.py": 490,
    "tests/main/test_main_watch.py": 72_790,
    "tests/main/test_main_yaml.py": 140,
    "tests/main/test_memory.py": 10,
    "tests/main/test_nullable_schema_versions.py": 340,
    "tests/main/test_parsed_source_cache_parity.py": 530,
    "tests/main/test_public_api_signature_baseline.py": 600,
    "tests/main/test_types.py": 1,
    "tests/main/test_yaml_cache_paths.py": 20,
    "tests/main/xmlschema/test_main_xmlschema.py": 3_690,
    "tests/model/dataclass/test_param.py": 1,
    "tests/model/pydantic_v2/test_base_model.py": 1_870,
    "tests/model/pydantic_v2/test_config.py": 1,
    "tests/model/pydantic_v2/test_dataclass.py": 20,
    "tests/model/pydantic_v2/test_root_model.py": 20,
    "tests/model/pydantic_v2/test_root_model_type_alias.py": 1,
    "tests/model/pydantic_v2/test_types.py": 70,
    "tests/model/pydantic_v2/test_version.py": 1,
    "tests/model/test_base.py": 13_100,
    "tests/model/test_compiled_templates.py": 14_090,
    "tests/model/test_constraints.py": 3_470,
    "tests/model/test_dataclass.py": 1,
    "tests/model/test_dataclass_ordering.py": 1,
    "tests/model/test_output_model_compatibility.py": 1,
    "tests/parser/test_backend_capabilities.py": 10,
    "tests/parser/test_base.py": 310,
    "tests/parser/test_builtin_formatter_contract.py": 60,
    "tests/parser/test_default_put_dict.py": 1,
    "tests/parser/test_generation.py": 1_250,
    "tests/parser/test_generation_store_usage.py": 1_660,
    "tests/parser/test_graph.py": 1,
    "tests/parser/test_graphql.py": 710,
    "tests/parser/test_imports.py": 2_440,
    "tests/parser/test_jsonschema.py": 10_700,
    "tests/parser/test_model_behavior_capabilities.py": 1,
    "tests/parser/test_model_construction_capabilities.py": 120,
    "tests/parser/test_openapi.py": 4_290,
    "tests/parser/test_output_context.py": 880,
    "tests/parser/test_python_type_imports.py": 1,
    "tests/parser/test_scc.py": 1,
    "tests/parser/test_schema_version.py": 1_400,
    "tests/parser/test_xmlschema.py": 60,
    "tests/skills/datamodel-code-generator/test_skill_flag_drift.py": 130,
    "tests/skills/datamodel-code-generator/test_skill_recipes.py": 12_510,
    "tests/test_architecture_boundaries.py": 13_710,
    "tests/test_assert_helper_usage.py": 10_370,
    "tests/test_build_architecture_docs_script.py": 2_730,
    "tests/test_build_conformance_docs_script.py": 150,
    "tests/test_build_deprecation_docs_script.py": 150,
    "tests/test_build_docs_examples_script.py": 50,
    "tests/test_build_experimental_docs_script.py": 160,
    "tests/test_build_llms_txt_script.py": 130,
    "tests/test_build_playground_assets_script.py": 20,
    "tests/test_build_preset_docs_script.py": 1_880,
    "tests/test_build_release_benchmark_docs_script.py": 2_380,
    "tests/test_build_schema_docs_script.py": 980,
    "tests/test_check_overview_sync_script.py": 180,
    "tests/test_ci_coverage_script.py": 1_700,
    "tests/test_ci_workflow.py": 1_560,
    "tests/test_conftest_helpers.py": 1,
    "tests/test_deprecations.py": 1,
    "tests/test_enums.py": 1,
    "tests/test_experimental.py": 1,
    "tests/test_format.py": 60_580,
    "tests/test_generate_changelog_script.py": 70,
    "tests/test_http.py": 26_470,
    "tests/test_http_https.py": 2_930,
    "tests/test_http_regressions.py": 150,
    "tests/test_imports.py": 1,
    "tests/test_infer_input_type.py": 1_030,
    "tests/test_input_diff_timestamp.py": 260,
    "tests/test_input_model.py": 6_950,
    "tests/test_input_model_transport.py": 190,
    "tests/test_main_kr.py": 9_690,
    "tests/test_module_name.py": 1,
    "tests/test_package_metadata.py": 4_730,
    "tests/test_prepare_release_draft_analysis_script.py": 2_050,
    "tests/test_prompt.py": 1,
    "tests/test_python_decorator.py": 360,
    "tests/test_python_type_annotation.py": 10,
    "tests/test_python_type_import_registry.py": 1,
    "tests/test_python_type_runtime.py": 160,
    "tests/test_reference.py": 1,
    "tests/test_release_draft_workflow.py": 300,
    "tests/test_remote_lock.py": 10,
    "tests/test_resolver.py": 1_490,
    "tests/test_select_ci_test_shard_script.py": 560,
    "tests/test_types.py": 40,
    "tests/test_update_command_help_on_markdown_script.py": 1,
    "tests/test_util.py": 90,
    "tests/test_validate_release_draft_analysis_script.py": 220,
    "tests/test_validators.py": 1,
    "tests/test_yaml_backend.py": 1,
    "tests/test_yaml_fast_constructor.py": 1_040,
}
LEGACY_SPLIT_NODE_WEIGHT_OVERRIDES = {
    f"{PAYLOAD_VALIDATION_FILE}::test_generated_payload_backend_accepts_representative_schema_payloads": 9_780,
    f"{PAYLOAD_VALIDATION_FILE}::test_generated_payload_backend_rejects_representative_schema_invalid_payloads": 6_990,
    f"{PAYLOAD_VALIDATION_FILE}::test_generated_pydantic_v2_model_accepts_schema_derived_payloads": 350_070,
    f"{PAYLOAD_VALIDATION_FILE}::test_generated_pydantic_v2_model_dumps_schema_valid_payloads": 449_220,
    f"{PAYLOAD_VALIDATION_FILE}::test_generated_pydantic_v2_model_rejects_schema_invalid_payloads": 270_440,
    f"{PAYLOAD_VALIDATION_FILE}::test_msgspec_pep_695_unique_items_runtime_exclusions_match_target": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_msgspec_schema_runtime_exclusions_cover_known_semantic_gaps": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_msgspec_schema_runtime_exclusions_detect_untyped_fractional_multiple_of": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_msgspec_schema_runtime_exclusions_ignore_literal_payloads": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_msgspec_unique_items_runtime_exclusions_match_type_limits": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_backend_all_case_mode_widens_runtime_validating_backends": 110,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_backend_case_mode_env_is_configurable": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_backend_case_mode_env_rejects_invalid_values": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_backend_full_matrix_exclusions_are_classified": 30,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_backend_representative_matrix_is_classified": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_codegen_restores_parsed_source_cache_on_error": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_max_examples_env_is_configurable": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_max_examples_env_rejects_invalid_values": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_rejection_oracle_covers_supported_policy_constraints": 80,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_rejection_oracle_policy_is_classified": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_round_trip_exclusions_are_classified": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_payload_validation_cases_cover_discovered_schema_files": 8_760,
    f"{PAYLOAD_VALIDATION_FILE}::test_pydantic_round_trip_exclusions_cover_unique_items_normalization": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_pydantic_v2_dataclass_legacy_exclusions_are_version_gated": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_pydantic_v2_dataclass_type_alias_exclusion_is_backend_specific": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_pydantic_v2_float_multiple_of_exclusions_are_version_gated": 1,
    (
        f"{PAYLOAD_VALIDATION_FILE}::"
        "test_pydantic_v2_legacy_runtime_cross_module_lookaround_exclusions_are_version_gated"
    ): 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_pydantic_v2_legacy_runtime_exclusions_are_classified": 1,
    f"{PAYLOAD_VALIDATION_FILE}::test_pydantic_v2_legacy_runtime_exclusions_are_version_gated": 1,
}
WEIGHT_PROFILES = {
    "default": (WEIGHT_OVERRIDES, SPLIT_NODE_WEIGHT_OVERRIDES),
    "legacy": (LEGACY_WEIGHT_OVERRIDES, LEGACY_SPLIT_NODE_WEIGHT_OVERRIDES),
}
FALLBACK_WEIGHTS = {
    name: tuple(_median_weight(weights) for weights in profile) for name, profile in WEIGHT_PROFILES.items()
}


def _item_weight(item: str, profile: str = "default") -> int:
    index = 1 if "::" in item else 0
    return WEIGHT_PROFILES[profile][index].get(item, FALLBACK_WEIGHTS[profile][index])


def _build_recipe_items(profile: str = "default") -> list[dict[str, int | str]]:
    return [{"nodeid": item, "weight": _item_weight(item, profile)} for item in _collect_test_items()]


def _validate_recipe_items(items: object) -> list[dict[str, int | str]]:
    if not isinstance(items, list) or not items:
        msg = "recipe items must be a nonempty list"
        raise SystemExit(msg)

    validated: list[dict[str, int | str]] = []
    seen: set[str] = set()
    for item in items:
        match item:
            case {"nodeid": str(nodeid), "weight": int(weight)} if (
                not isinstance(weight, bool)
                and weight > 0
                and nodeid.strip()
                and "\n" not in nodeid
                and "\r" not in nodeid
                and nodeid not in seen
            ):
                seen.add(nodeid)
                validated.append({"nodeid": nodeid, "weight": weight})
            case _:
                msg = f"invalid recipe item: {item!r}"
                raise SystemExit(msg)
    return validated


def _load_recipe_items(path: Path) -> list[dict[str, int | str]]:
    recipe = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(recipe, dict) or recipe.get("version") != RECIPE_VERSION or "items" not in recipe:
        msg = f"unsupported shard recipe: {recipe!r}"
        raise SystemExit(msg)
    return _validate_recipe_items(recipe["items"])


def _write_recipe(path: Path, items: list[dict[str, int | str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": RECIPE_VERSION, "items": items}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _select_shard(items: list[dict[str, int | str]], shard_index: int, shard_total: int) -> list[str]:
    """Assign items to shards heaviest-first and keep that order so xdist starts long tests early."""
    shards: list[list[str]] = [[] for _ in range(shard_total)]
    shard_weights = [0] * shard_total
    weighted_items = sorted(
        ((int(item["weight"]), str(item["nodeid"])) for item in items),
        key=lambda item: (-item[0], item[1]),
    )

    for weight, item in weighted_items:
        target = min(
            range(shard_total),
            key=lambda index: (shard_weights[index], len(shards[index]), index),
        )
        shards[target].append(item)
        shard_weights[target] += weight

    return shards[shard_index - 1]


def main(argv: list[str] | None = None) -> None:
    """Select a shard or serialize its reproducible recipe."""
    parser = argparse.ArgumentParser()
    parser.add_argument("shard_index", type=int, nargs="?")
    parser.add_argument("shard_total", type=int, nargs="?")
    parser.add_argument("--profile", choices=WEIGHT_PROFILES, default="default")
    parser.add_argument("--recipe", type=Path)
    parser.add_argument("--write-recipe", type=Path)
    args = parser.parse_args(argv)

    items = _load_recipe_items(args.recipe) if args.recipe else _build_recipe_items(args.profile)
    if args.write_recipe:
        _write_recipe(args.write_recipe, items)
        if args.shard_index is None and args.shard_total is None:
            return

    if args.shard_index is None or args.shard_total is None:
        parser.error("shard_index and shard_total are required unless only --write-recipe is used")

    shard_index = args.shard_index
    shard_total = args.shard_total

    match 1 <= shard_index <= shard_total:
        case False:
            msg = "shard_index must be between 1 and shard_total"
            raise SystemExit(msg)
        case _:
            if selected := _select_shard(items, shard_index, shard_total):
                print(*selected, sep="\n")
                return

    msg = f"No tests selected for shard {shard_index}/{shard_total}"
    raise SystemExit(msg)


if __name__ == "__main__":
    main()
