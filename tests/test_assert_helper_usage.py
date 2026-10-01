"""Tests that e2e test modules use shared assertion helpers."""

from __future__ import annotations

import ast
import re
import sys
from collections import Counter
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator.http import _get_httpx
from tests.conftest import (
    HttpxGetMockFactory,
    MockHttpxResponse,
    assert_httpx_get_kwargs,
    create_httpx_get_mock,
)
from tests.main.conftest import (
    _assert_generated_package_model_validation,
    assert_generated_model_json_invalid,
    assert_generated_model_json_validation,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pytest_mock import MockerFixture

TESTS_ROOT = Path(__file__).parent
DIRECT_ASSERT_EXEMPT_FILES_INI = "assert_helper_direct_assert_exempt_files"
HELPER_ROOTS = (Path("data", "python"), Path("data", "generation_platform"))
PACKAGE = "datamodel_code_generator"
INTERNAL_MODULE_PREFIXES = ("parser.openapi_contract", "parser.openapi_scope", "model.binding")
RUNTIME_SEGMENT = re.compile(r"(?:^|\.)_runtime(?:\.|$)")
PACKAGE_PATH = re.compile(rf"^{PACKAGE}(?:\.|$)")
ASSERTION_HELPER_NAME = re.compile(r"^_*(?:assert|check|verify|expect)_")
ABNORMAL_PATH_MARKER = "pytest.mark.abnormal_path"
ALLOW_DIRECT_ASSERT_MARKER = "pytest.mark.allow_direct_assert"
PATCH_CALLS = frozenset({
    ("mocker", "patch"),
    ("mocker", "patch", "dict"),
    ("mocker", "patch", "multiple"),
    ("mocker", "patch", "object"),
    ("mocker", "spy"),
    ("monkeypatch", "delattr"),
    ("monkeypatch", "delitem"),
    ("monkeypatch", "setattr"),
    ("monkeypatch", "setitem"),
    ("unittest", "mock", "patch"),
    ("unittest", "mock", "patch", "dict"),
    ("unittest", "mock", "patch", "multiple"),
    ("unittest", "mock", "patch", "object"),
})
PATCH_TARGET_KEYWORDS = frozenset({"target", "in_dict"})
REGISTRY_PATCH_CALLS = frozenset({("mocker", "patch", "dict"), ("unittest", "mock", "patch", "dict")})
PROFILE_HOOKS = frozenset({
    ("sys", "setprofile"),
    ("sys", "settrace"),
    ("threading", "setprofile"),
    ("threading", "setprofile_all_threads"),
    ("threading", "settrace"),
    ("threading", "settrace_all_threads"),
})
MONKEYPATCH_CONTEXTS = frozenset({("monkeypatch", "context"), ("pytest", "MonkeyPatch", "context")})
TEST_CASE_BASES = frozenset({("unittest", "IsolatedAsyncioTestCase"), ("unittest", "TestCase")})

DIRECT_ASSERT = "direct-assert"
DISGUISED_ASSERT = "disguised-assert"
ASSERTION_HELPER = "assertion-helper"
PRIVATE_IMPORT = "private-import"
NORMAL_PATH_MOCK = "normal-path-mock"
ABNORMAL_PATH_REASON = "abnormal-path-reason"
EXEMPT_TEST_RULES = frozenset({PRIVATE_IMPORT, NORMAL_PATH_MOCK, ABNORMAL_PATH_REASON})
TEST_RULES = EXEMPT_TEST_RULES | {DIRECT_ASSERT, DISGUISED_ASSERT}
HELPER_RULES = TEST_RULES | {ASSERTION_HELPER}
DIRECT_ASSERT_FAILURE_MESSAGE = (
    "Direct assert statements, raise AssertionError, pytest.fail, and unittest.TestCase assertions in guarded test "
    "modules and report helpers require explicit permission.\n"
    "HTTP, external-request, or similar mock assertions may be unavoidable, but generation tests should generally "
    "be e2e tests that compare generated output through the shared assert helpers.\n"
    "Use @pytest.mark.allow_direct_assert for a narrow exception, or add intentional legacy/unit files to "
    f"{DIRECT_ASSERT_EXEMPT_FILES_INI}. A report helper under tests/data returns facts for the shared assert helpers "
    "to compare; one that rejects an impossible input raises a specific exception such as ValueError."
)
RULE_FAILURE_MESSAGES = {
    DIRECT_ASSERT: DIRECT_ASSERT_FAILURE_MESSAGE,
    DISGUISED_ASSERT: DIRECT_ASSERT_FAILURE_MESSAGE,
    ASSERTION_HELPER: (
        "Report helpers under tests/data return facts and leave every comparison to the shared assert helpers in "
        "tests/conftest.py and tests/main/conftest.py, so they define no assert_*, check_*, verify_*, or expect_* "
        "function of their own. Extend a shared assert helper instead."
    ),
    PRIVATE_IMPORT: (
        "Tests and report helpers reach generation through its public entry points, the command line, "
        "datamodel_code_generator.generate, or datamodel_code_generator.fastapi, and reach generated packages through "
        "import_generated, as the model tests reach model generation.\n"
        "They import no private datamodel_code_generator module or name (an underscore segment at any depth) and no "
        "binding or contract internals (parser.openapi_contract*, parser.openapi_scope, model.binding*), "
        "statically or through importlib.import_module.\n"
        "Patch a private function by its dotted name only to inject an abnormal path under "
        '@pytest.mark.abnormal_path("reason"). Justified exceptions are listed in JUSTIFIED_VIOLATIONS.'
    ),
    NORMAL_PATH_MOCK: (
        "Mocks and spies replace only abnormal paths that an e2e test cannot reproduce, or external services.\n"
        "sys.setprofile, sys.settrace, and threading profile hooks, and patch, patch.object, mocker.patch, mocker.spy, "
        "or monkeypatch.setattr on datamodel_code_generator or generated _runtime attributes pin internals on a "
        "normal path. Drive the behavior through public entry points and generated packages instead, or mark the "
        'enclosing test or helper with @pytest.mark.abnormal_path("why e2e cannot reproduce it").'
    ),
    ABNORMAL_PATH_REASON: (
        "@pytest.mark.abnormal_path takes exactly one non-empty string literal that states why an e2e test cannot "
        "reproduce the path."
    ),
}
STALE_ALLOWLIST_FAILURE_MESSAGE = (
    "These allowlist entries no longer match a violation. Remove them, so each allowlist only shrinks and stays exact:"
)
FROZEN_ALLOWLIST_POLICY_MESSAGE = (
    "FROZEN_VIOLATIONS and LEGACY_VIOLATIONS only shrink: fix a new violation instead of listing it there."
)
MALFORMED_ALLOWLIST_FAILURE_MESSAGE = (
    "Allowlist groups are sorted, have a reason, and every entry appears in exactly one group:"
)

JUSTIFIED_VIOLATIONS: dict[str, tuple[str, ...]] = {
    "client-coordinator": (
        "private-import tests/data/python/client_generation.py",
        "private-import tests/data/python/client_protocol_records.py",
        "private-import tests/data/python/client_runtime.py",
        "private-import tests/data/python/client_typing.py",
    ),
    "generated-runtime": (
        "private-import tests/data/python/fastapi_generation.py",
        "private-import tests/data/python/generated_packages.py",
    ),
    "runtime-tables": (
        "private-import tests/data/generation_platform/codecs/typing/annotations.py",
        "private-import tests/data/generation_platform/codecs/typing/codecs.py",
        "private-import tests/data/generation_platform/codecs/typing/codecs_negative.py",
        "private-import tests/data/generation_platform/codecs/typing/first_use.py",
        "private-import tests/data/generation_platform/codecs/typing/negative.py",
        "private-import tests/data/generation_platform/codecs/typing/positive.py",
        "private-import tests/data/python/model_codec_reports.py",
        "private-import tests/data/python/model_codec_suite.py",
    ),
}
FROZEN_VIOLATIONS: dict[str, tuple[str, ...]] = {
    "model-codec-e2e": (
        "private-import tests/data/python/model_codec_adapters.py",
        "private-import tests/data/python/model_codec_builtin.py",
        "private-import tests/data/python/model_codec_plans.py",
    ),
    "binding-e2e": (
        "normal-path-mock tests/data/python/binding_failure_inputs.py::failed_module_capture",
        "normal-path-mock tests/data/python/binding_final_failures.py::final_type_failure",
        "normal-path-mock tests/data/python/binding_provenance_failures.py::producer_fault",
        "normal-path-mock tests/data/python/binding_record_failures.py::record_failure",
        "normal-path-mock tests/main/test_generation_api_scope.py::test_api_empty_factory_requires_capability",
        "normal-path-mock tests/main/test_generation_session.py::test_parser_dispose_preserves_primary",
        "normal-path-mock tests/main/test_generation_session.py::test_session_release_failure",
        "private-import tests/data/generation_platform/api_scope/typing/annotations.py",
        "private-import tests/data/generation_platform/api_scope/typing/negative.py",
        "private-import tests/data/generation_platform/api_scope/typing/positive.py",
        "private-import tests/data/python/api_declaration_observer.py",
        "private-import tests/data/python/binding_backend_failures.py",
        "private-import tests/data/python/binding_batch_failures.py",
        "private-import tests/data/python/binding_declaration_failures.py",
        "private-import tests/data/python/binding_failure_inputs.py",
        "private-import tests/data/python/binding_final_failures.py",
        "private-import tests/data/python/binding_inherited_inputs.py",
        "private-import tests/data/python/binding_inputs.py",
        "private-import tests/data/python/binding_projection_failures.py",
        "private-import tests/data/python/binding_provenance_failures.py",
        "private-import tests/data/python/binding_record_failures.py",
        "private-import tests/data/python/binding_type_corruptions.py",
        "private-import tests/data/python/binding_type_snapshot.py",
        "private-import tests/data/python/field_ownership_inputs.py",
        "private-import tests/data/python/generation_contract_consumers.py",
        "private-import tests/data/python/generation_session_inputs.py",
        "private-import tests/main/test_generation_api_contract.py",
        "private-import tests/main/test_generation_api_http.py",
        "private-import tests/main/test_generation_api_scope.py",
        "private-import tests/main/test_generation_bare_containers.py",
        "private-import tests/main/test_generation_common_composition.py",
        "private-import tests/main/test_generation_constrained_unions.py",
        "private-import tests/main/test_generation_default_policies.py",
        "private-import tests/main/test_generation_extra_items.py",
        "private-import tests/main/test_generation_inherited_contract.py",
        "private-import tests/main/test_generation_module_bindings.py",
        "private-import tests/main/test_generation_nullable_aliases.py",
        "private-import tests/main/test_generation_serialize_imports.py",
        "private-import tests/main/test_generation_session.py",
        "private-import tests/main/test_generation_split_discriminators.py",
        "private-import tests/model/test_binding.py",
        "private-import tests/model/test_binding_artifacts.py",
        "private-import tests/model/test_binding_backend_failures.py",
        "private-import tests/model/test_binding_fields.py",
        "private-import tests/parser/test_api_reference.py",
        "private-import tests/parser/test_openapi_contract.py",
        "private-import tests/parser/test_openapi_contract_freeze.py",
        "private-import tests/parser/test_openapi_scope.py",
    ),
    "clock-injection": (
        "normal-path-mock tests/data/python/client_deadline_races.py::_clock",
        "normal-path-mock tests/data/python/client_oauth_client_credentials.py::_async_renewal",
        "normal-path-mock tests/data/python/client_oauth_client_credentials.py::_expiry",
        "normal-path-mock tests/data/python/client_oauth_refresh.py::_expiry",
        "normal-path-mock tests/data/python/client_retry_boundaries.py::_clock",
        "normal-path-mock tests/data/python/client_retry_boundaries.py::_closing_wait",
        "normal-path-mock tests/data/python/client_retry_policy.py::_retention_boundaries",
        "normal-path-mock tests/data/python/client_retry_policy.py::_timing",
    ),
    "spies": (
        "normal-path-mock tests/data/python/binding_engine_observer.py::compare_engine_runs",
        "normal-path-mock tests/data/python/binding_inherited_inputs.py::inherited_fields",
        "normal-path-mock tests/data/python/client_oauth_refresh.py::_faults",
        "normal-path-mock tests/data/python/client_retry_ownership.py::_async",
        "normal-path-mock tests/data/python/client_retry_ownership.py::retry_ownership",
        "normal-path-mock tests/data/python/generation_session_inputs.py::_exercise_session_protocol",
        "normal-path-mock tests/data/python/generation_session_inputs.py::compare_api_session",
        "normal-path-mock tests/data/python/generation_session_inputs.py::generate_product",
        "normal-path-mock tests/data/python/generation_session_inputs.py::observe_api_session",
        "normal-path-mock tests/data/python/generation_session_inputs.py::run_generation_session",
        "normal-path-mock tests/data/python/generation_session_inputs.py::session_cleanup_failures.exercise",
        "normal-path-mock tests/main/test_generation_api_contract.py::test_api_declaration_frame_origins",
        "normal-path-mock tests/main/test_generation_observation.py::test_generation_observation",
        "normal-path-mock tests/model/test_binding.py::test_builtin_model_facts_match_adopted_settings",
        "normal-path-mock tests/model/test_binding.py::test_emitted_meta_uses_projected_node_locations",
        "normal-path-mock tests/model/test_binding_fields.py::test_final_declaring_field_ownership",
        "normal-path-mock tests/model/test_binding_fields.py::test_functional_emissions_keep_declaring_slots",
        "normal-path-mock tests/parser/test_openapi_contract.py::test_side_effect_free_type_projection",
    ),
    "abnormal-e2e": (
        "normal-path-mock tests/api_generation/test_fastapi_cli.py::test_fastapi_cli_report_replaced",
        "normal-path-mock tests/api_generation/test_target_generation.py::test_target_generate_lock_discard",
        "normal-path-mock tests/api_generation/test_target_generation.py::test_target_generate_state_changed",
        "private-import tests/api_generation/test_target_generation.py",
        "private-import tests/data/python/target_generation.py",
    ),
    "disguised-asserts": (
        "disguised-assert tests/data/python/generation_session_inputs.py::session_cleanup_failures.exercise",
        "disguised-assert tests/data/python/model_codec_adapters.py::_use_keys",
        "disguised-assert tests/data/python/target_generation.py::_Scenario.current",
        "disguised-assert tests/data/python/target_generation.py::_Scenario.generate",
        "disguised-assert tests/data/python/target_generation.py::_Scenario.relocate",
        "disguised-assert tests/data/python/target_generation.py::_Scenario.render",
    ),
}
LEGACY_VIOLATIONS: dict[str, tuple[str, ...]] = {
    "pre-platform": (
        "disguised-assert tests/cli_doc/test_cli_doc_coverage.py",
        "disguised-assert tests/main/graphql/test_main_graphql.py",
        "disguised-assert tests/main/jsonschema/test_main_jsonschema.py",
        "disguised-assert tests/main/openapi/test_main_openapi.py",
        "disguised-assert tests/main/protobuf/test_main_protobuf.py",
        "disguised-assert tests/main/test_agent_skill.py",
        "disguised-assert tests/main/test_error_messages.py",
        "disguised-assert tests/main/test_generation_determinism.py",
        "disguised-assert tests/main/test_jsonschema_suite_conformance.py",
        "disguised-assert tests/main/test_main_general.py",
        "disguised-assert tests/main/test_main_watch.py",
        "disguised-assert tests/main/test_parsed_source_cache_parity.py",
        "disguised-assert tests/main/test_payload_validation.py",
        "disguised-assert tests/model/pydantic_v2/test_types.py",
        "disguised-assert tests/model/test_compiled_templates.py",
        "disguised-assert tests/parser/test_builtin_formatter_contract.py",
        "disguised-assert tests/parser/test_default_put_dict.py",
        "disguised-assert tests/skills/datamodel-code-generator/test_skill_flag_drift.py",
        "disguised-assert tests/skills/datamodel-code-generator/test_skill_recipes.py",
        "disguised-assert tests/test_build_release_benchmark_docs_script.py",
        "disguised-assert tests/test_build_schema_docs_script.py",
        "disguised-assert tests/test_input_model_transport.py",
        "disguised-assert tests/test_package_metadata.py",
        "disguised-assert tests/test_validate_release_draft_analysis_script.py",
        "disguised-assert tests/test_yaml_fast_constructor.py",
        "normal-path-mock tests/cli_doc/test_cli_options_sync.py",
        "normal-path-mock tests/main/jsonschema/test_main_jsonschema.py",
        "normal-path-mock tests/main/jsonschema/test_reference_resolution_fallback.py",
        "normal-path-mock tests/main/jsonschema/test_reference_resolution_hardening.py",
        "normal-path-mock tests/main/openapi/test_main_openapi.py",
        "normal-path-mock tests/main/test_agent_skill.py",
        "normal-path-mock tests/main/test_error_messages.py",
        "normal-path-mock tests/main/test_gc_tuning.py",
        "normal-path-mock tests/main/test_main_general.py",
        "normal-path-mock tests/main/test_main_watch.py",
        "normal-path-mock tests/main/xmlschema/test_main_xmlschema.py",
        "normal-path-mock tests/model/test_base.py",
        "normal-path-mock tests/model/test_compiled_templates.py",
        "normal-path-mock tests/parser/test_base.py",
        "normal-path-mock tests/parser/test_jsonschema.py",
        "normal-path-mock tests/parser/test_openapi.py",
        "normal-path-mock tests/test_deprecations.py",
        "normal-path-mock tests/test_experimental.py",
        "normal-path-mock tests/test_format.py",
        "normal-path-mock tests/test_http.py",
        "normal-path-mock tests/test_http_regressions.py",
        "normal-path-mock tests/test_input_model.py",
        "normal-path-mock tests/test_main_kr.py",
        "normal-path-mock tests/test_python_type_annotation.py",
        "normal-path-mock tests/test_remote_lock.py",
        "normal-path-mock tests/test_yaml_backend.py",
        "private-import tests/cli_doc/test_cli_options_sync.py",
        "private-import tests/data/python/unique_model_sets/opaque_generator.py",
        "private-import tests/main/jsonschema/test_external_anchor.py",
        "private-import tests/main/jsonschema/test_main_jsonschema.py",
        "private-import tests/main/jsonschema/test_numeric_constraint_precision.py",
        "private-import tests/main/test_agent_skill.py",
        "private-import tests/main/test_dynamic_models.py",
        "private-import tests/main/test_gc_tuning.py",
        "private-import tests/main/test_main_general.py",
        "private-import tests/main/test_main_input_diff.py",
        "private-import tests/main/test_main_watch.py",
        "private-import tests/main/test_parsed_source_cache_parity.py",
        "private-import tests/main/test_performance.py",
        "private-import tests/main/test_public_api_signature_baseline.py",
        "private-import tests/main/xmlschema/test_main_xmlschema.py",
        "private-import tests/model/pydantic_v2/test_base_model.py",
        "private-import tests/model/pydantic_v2/test_config.py",
        "private-import tests/model/pydantic_v2/test_root_model.py",
        "private-import tests/model/pydantic_v2/test_version.py",
        "private-import tests/model/test_base.py",
        "private-import tests/model/test_compiled_templates.py",
        "private-import tests/model/test_constraints.py",
        "private-import tests/model/test_output_model_compatibility.py",
        "private-import tests/parser/test_backend_capabilities.py",
        "private-import tests/parser/test_base.py",
        "private-import tests/parser/test_builtin_formatter_contract.py",
        "private-import tests/parser/test_default_put_dict.py",
        "private-import tests/parser/test_generation.py",
        "private-import tests/parser/test_graph.py",
        "private-import tests/parser/test_jsonschema.py",
        "private-import tests/parser/test_model_behavior_capabilities.py",
        "private-import tests/parser/test_model_construction_capabilities.py",
        "private-import tests/parser/test_openapi.py",
        "private-import tests/parser/test_output_context.py",
        "private-import tests/parser/test_python_type_imports.py",
        "private-import tests/parser/test_scc.py",
        "private-import tests/parser/test_schema_version.py",
        "private-import tests/parser/test_xmlschema.py",
        "private-import tests/test_assert_helper_usage.py",
        "private-import tests/test_build_preset_docs_script.py",
        "private-import tests/test_deprecations.py",
        "private-import tests/test_enums.py",
        "private-import tests/test_format.py",
        "private-import tests/test_http.py",
        "private-import tests/test_http_https.py",
        "private-import tests/test_http_regressions.py",
        "private-import tests/test_infer_input_type.py",
        "private-import tests/test_input_model.py",
        "private-import tests/test_input_model_transport.py",
        "private-import tests/test_main_kr.py",
        "private-import tests/test_prompt.py",
        "private-import tests/test_python_decorator.py",
        "private-import tests/test_python_type_annotation.py",
        "private-import tests/test_python_type_import_registry.py",
        "private-import tests/test_python_type_runtime.py",
        "private-import tests/test_remote_lock.py",
        "private-import tests/test_types.py",
        "private-import tests/test_util.py",
        "private-import tests/test_yaml_backend.py",
        "private-import tests/test_yaml_fast_constructor.py",
    ),
}
ALLOWLISTS = {
    "JUSTIFIED_VIOLATIONS": JUSTIFIED_VIOLATIONS,
    "FROZEN_VIOLATIONS": FROZEN_VIOLATIONS,
    "LEGACY_VIOLATIONS": LEGACY_VIOLATIONS,
}
ALLOWLIST_GROUP_REASONS = {
    "client-coordinator": (
        "The client target has no public entry point yet, so its report helpers reach the coordinator directly."
    ),
    "generated-runtime": (
        "Generated packages run this checkout's runtime sources, which import_generated and the runtime copy "
        "comparison locate through datamodel_code_generator._runtime."
    ),
    "runtime-tables": (
        "Combinatorial wire-rule tables, the official JSON Schema suite, and the codec type-check probes exercise "
        "the runtime directly."
    ),
    "model-codec-e2e": "Drive model codecs through FastAPI target e2e tests and import_generated.",
    "binding-e2e": (
        "Fold binding, session, and contract tests into target scenarios after the guard reachability sweep."
    ),
    "clock-injection": "Give the generated client a documented clock and random injection point.",
    "spies": "Replace profile observers and internal spies with observable effects.",
    "abnormal-e2e": (
        "Reproduce these abnormal paths e2e, and patch the rest by dotted name under @pytest.mark.abnormal_path."
    ),
    "disguised-asserts": "Move these report helper checks into report text compared with assert_output.",
    "pre-platform": "Tests written before the generation platform keep these constructs until they are converted.",
}


@dataclass(frozen=True)
class Finding:
    """A guarded construct in a test module or report helper."""

    rule: str
    path: Path
    scope: str
    lineno: int
    statement: str

    @property
    def keys(self) -> tuple[str, str]:
        """Allowlist entries that match this finding, by file and by enclosing scope."""
        location = f"{self.rule} tests/{self.path.as_posix()}"
        return location, f"{location}::{self.scope}"

    def __str__(self) -> str:
        """Report the finding as a file:line location."""
        return f"  tests/{self.path.as_posix()}:{self.lineno} ({self.scope}): {self.statement}"


@dataclass(frozen=True)
class WholeSysModulesPatch:
    """A patch.dict call that replaces the complete module registry on teardown."""

    path: Path
    lineno: int
    statement: str


def _attribute_chain(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if not isinstance(node, ast.Attribute) or not (parent := _attribute_chain(node.value)):
        return ()
    return (*parent, node.attr)


def _add_import_aliases(aliases: dict[str, tuple[str, ...]], node: ast.Import | ast.ImportFrom) -> None:
    if isinstance(node, ast.Import):
        aliases.update(
            (alias.asname, tuple(alias.name.split("."))) if alias.asname else (root, (root,))
            for alias in node.names
            if (root := alias.name.split(".", 1)[0])
        )
    elif node.module is not None and node.level == 0:
        aliases.update((alias.asname or alias.name, (*node.module.split("."), alias.name)) for alias in node.names)


def _add_context_aliases(aliases: dict[str, tuple[str, ...]], items: Iterable[ast.withitem]) -> None:
    aliases.update(
        (item.optional_vars.id, ("monkeypatch",))
        for item in items
        if isinstance(item.optional_vars, ast.Name)
        and isinstance(item.context_expr, ast.Call)
        and _canonical_chain(item.context_expr.func, aliases) in MONKEYPATCH_CONTEXTS
    )


def _canonical_chain(node: ast.AST, aliases: Mapping[str, tuple[str, ...]]) -> tuple[str, ...]:
    if not (attribute_chain := _attribute_chain(node)):
        return ()
    return _canonical(attribute_chain, aliases)


def _canonical(attribute_chain: tuple[str, ...], aliases: Mapping[str, tuple[str, ...]]) -> tuple[str, ...]:
    return (*aliases.get(attribute_chain[0], attribute_chain[:1]), *attribute_chain[1:])


class _SourceLines:
    """Source segments of one module, split once."""

    def __init__(self, source: str) -> None:
        self.lines = source.encode().splitlines(keepends=True)

    def segment(self, node: ast.expr | ast.stmt) -> str:
        """Return the node's source with whitespace collapsed."""
        start, end = node.lineno - 1, (node.end_lineno or node.lineno) - 1
        if start == end:
            text = self.lines[start][node.col_offset : node.end_col_offset]
        else:
            text = b"".join((
                self.lines[start][node.col_offset :],
                *self.lines[start + 1 : end],
                self.lines[end][: node.end_col_offset],
            ))
        return " ".join(text.decode().split())


def _is_whole_sys_modules_patch(call: ast.Call, aliases: Mapping[str, tuple[str, ...]]) -> bool:
    if _canonical_chain(call.func, aliases) not in REGISTRY_PATCH_CALLS:
        return False
    if (target := _patch_target(call)) is None:
        return False
    if isinstance(target, ast.Constant):
        return target.value == "sys.modules"
    return _canonical_chain(target, aliases) == ("sys", "modules")


def _collect_whole_sys_modules_patches(path: Path, tests_root: Path = TESTS_ROOT) -> list[WholeSysModulesPatch]:
    source = path.read_text(encoding="utf-8")
    aliases: dict[str, tuple[str, ...]] = {}
    calls: list[ast.Call] = []
    for node in ast.walk(ast.parse(source, filename=str(path))):
        match node:
            case ast.Import() | ast.ImportFrom():
                _add_import_aliases(aliases, node)
            case ast.Call():
                calls.append(node)
            case _:
                pass
    lines = _SourceLines(source)
    return [
        WholeSysModulesPatch(path=path.relative_to(tests_root), lineno=call.lineno, statement=lines.segment(call))
        for call in calls
        if _is_whole_sys_modules_patch(call, aliases)
    ]


def _patch_target(call: ast.Call) -> ast.expr | None:
    if call.args:
        return call.args[0]
    return next((keyword.value for keyword in call.keywords if keyword.arg in PATCH_TARGET_KEYWORDS), None)


def _marker(function: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef, name: str) -> ast.expr | None:
    return next(
        (decorator for decorator in function.decorator_list if ast.unparse(decorator).split("(", 1)[0] == name),
        None,
    )


def _allows_direct_assert(function: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> bool:
    return _marker(function, ALLOW_DIRECT_ASSERT_MARKER) is not None


def _has_abnormal_path_reason(marker: ast.expr) -> bool:
    if not isinstance(marker, ast.Call) or marker.keywords or len(marker.args) != 1:
        return False
    reason = marker.args[0]
    return isinstance(reason, ast.Constant) and isinstance(reason.value, str) and bool(reason.value.strip())


def _static_prefix(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if not isinstance(node, ast.JoinedStr):
        return None
    prefix = ""
    for value in node.values:
        if not isinstance(value, ast.Constant):
            break
        prefix += str(value.value)
    return prefix


def _is_private_module(module: str) -> bool:
    root, *parts = module.split(".")
    if root != PACKAGE:
        return False
    if any(part.startswith("_") and not (part.startswith("__") and part.endswith("__")) for part in parts):
        return True
    return ".".join(parts[:2]).startswith(INTERNAL_MODULE_PREFIXES)


def _imported_modules(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if node.module is not None and node.level == 0:
        return [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
    return []


def _reaches_internals(expression: ast.AST, names: set[str], aliases: Mapping[str, tuple[str, ...]]) -> bool:
    """Whether an expression reaches datamodel_code_generator or a generated _runtime package."""
    for node in ast.walk(expression):
        match node:
            case ast.Name() if node.id in names or aliases.get(node.id, ("",))[0] == PACKAGE:
                return True
            case ast.Attribute(attr="_runtime"):
                return True
            case ast.Constant(value=str()) if PACKAGE_PATH.search(node.value) or RUNTIME_SEGMENT.search(node.value):
                return True
            case _:
                pass
    return False


def _internal_names(
    assignments: Iterable[tuple[frozenset[str], ast.expr]], aliases: Mapping[str, tuple[str, ...]]
) -> set[str]:
    """Names bound, directly or through other bound names, to package or generated runtime internals."""
    names: set[str] = set()
    pending = list(assignments)
    while reached := {index for index, (_, value) in enumerate(pending) if _reaches_internals(value, names, aliases)}:
        names.update(name for index in reached for name in pending[index][0])
        pending = [assignment for index, assignment in enumerate(pending) if index not in reached]
    return names


@dataclass(frozen=True)
class _Scope:
    """A function or class around a finding, with whether it or an enclosing scope marks an abnormal path."""

    parent: _Scope | None
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    abnormal_path: bool

    @property
    def name(self) -> str:
        """Dotted name from the outermost enclosing scope."""
        return self.node.name if self.parent is None else f"{self.parent.name}.{self.node.name}"


class _FindingVisitor(ast.NodeVisitor):
    """Collects findings in one pass, and decides alias-dependent calls once every import is known."""

    def __init__(self, path: Path, source: str, rules: frozenset[str]) -> None:
        self.path = path
        self.rules = rules
        self.lines = _SourceLines(source)
        self.scope: _Scope | None = None
        self.aliases: dict[str, tuple[str, ...]] = {}
        self.items: list[ast.withitem] = []
        self.assignments: list[tuple[frozenset[str], ast.expr]] = []
        self.calls: list[tuple[ast.Call, tuple[str, ...], _Scope | None]] = []
        self.classes: list[tuple[ast.ClassDef, _Scope]] = []
        self.findings: list[Finding] = []

    def record(self, rule: str, node: ast.expr | ast.stmt, scope: _Scope | None, statement: str | None = None) -> None:
        """Keep a finding unless its rule does not apply here or an enclosing marker allows it."""
        if rule not in self.rules:
            return
        if rule in {DIRECT_ASSERT, DISGUISED_ASSERT} and scope is not None and _allows_direct_assert(scope.node):
            return
        if rule == NORMAL_PATH_MOCK and scope is not None and scope.abnormal_path:
            return
        self.findings.append(
            Finding(
                rule=rule,
                path=self.path,
                scope="<module>" if scope is None else scope.name,
                lineno=node.lineno,
                statement=statement or self.lines.segment(node),
            )
        )

    def visit(self, node: ast.AST) -> None:
        """Inspect the node kinds the guard cares about, then visit the children of every other node."""
        match node:
            case ast.FunctionDef() | ast.AsyncFunctionDef() | ast.ClassDef():
                self.visit_scope(node)
                return
            case ast.Import() | ast.ImportFrom():
                self.visit_import(node)
                return
            case ast.Assert():
                self.record(DIRECT_ASSERT, node, self.scope)
            case ast.Raise(exc=ast.Call(func=ast.Name(id="AssertionError")) | ast.Name(id="AssertionError")):
                self.record(DISGUISED_ASSERT, node, self.scope)
            case ast.Assign() | ast.AnnAssign() | ast.NamedExpr() if node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                self.assignments.append((
                    frozenset(target.id for target in targets if isinstance(target, ast.Name)),
                    node.value,
                ))
            case ast.withitem():
                self.items.append(node)
                if isinstance(node.optional_vars, ast.Name):
                    self.assignments.append((frozenset({node.optional_vars.id}), node.context_expr))
            case ast.Call():
                if function := _attribute_chain(node.func):
                    self.calls.append((node, function, self.scope))
            case _:
                pass
        self.generic_visit(node)

    def visit_scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        """Visit a function or class inside its own scope, decorators included."""
        parent = self.scope
        marker = _marker(node, ABNORMAL_PATH_MARKER)
        valid = marker is not None and _has_abnormal_path_reason(marker)
        self.scope = scope = _Scope(parent, node, valid or (parent is not None and parent.abnormal_path))
        if marker is not None and not valid:
            self.record(ABNORMAL_PATH_REASON, marker, scope)
        if isinstance(node, ast.ClassDef):
            self.classes.append((node, scope))
        elif ASSERTION_HELPER_NAME.match(node.name):
            self.record(ASSERTION_HELPER, node, scope, f"def {node.name}")
        self.generic_visit(node)
        self.scope = parent

    def visit_import(self, node: ast.Import | ast.ImportFrom) -> None:
        """Record the import's aliases and its first private module."""
        _add_import_aliases(self.aliases, node)
        if module := next((name for name in _imported_modules(node) if _is_private_module(name)), None):
            self.record(PRIVATE_IMPORT, node, self.scope, module)

    def finish(self) -> list[Finding]:
        """Decide the deferred calls and classes, and return findings in source order."""
        _add_context_aliases(self.aliases, self.items)
        internal_names = _internal_names(self.assignments, self.aliases)
        for node, attribute_chain, scope in self.calls:
            function = _canonical(attribute_chain, self.aliases)
            if function == ("pytest", "fail") or (
                function[0] == "self"
                and len(function) == 2
                and (function[1] == "fail" or function[1].startswith("assert"))
            ):
                self.record(DISGUISED_ASSERT, node, scope)
            elif function in PROFILE_HOOKS or (
                function in PATCH_CALLS
                and (target := _patch_target(node)) is not None
                and _reaches_internals(target, internal_names, self.aliases)
            ):
                self.record(NORMAL_PATH_MOCK, node, scope)
            elif (
                function == ("importlib", "import_module")
                and node.args
                and (module := _static_prefix(node.args[0])) is not None
                and _is_private_module(module)
            ):
                self.record(PRIVATE_IMPORT, node, scope, module)
        for node, scope in self.classes:
            if any(_canonical_chain(base, self.aliases) in TEST_CASE_BASES for base in node.bases):
                self.record(
                    DISGUISED_ASSERT, node, scope, f"class {node.name}({', '.join(map(ast.unparse, node.bases))})"
                )
        return sorted(self.findings, key=lambda finding: finding.lineno)


@cache
def _file_findings(path: Path, tests_root: Path, rules: frozenset[str]) -> tuple[Finding, ...]:
    source = path.read_text(encoding="utf-8")
    visitor = _FindingVisitor(path.relative_to(tests_root), source, rules)
    visitor.visit(ast.parse(source, filename=str(path)))
    return tuple(visitor.finish())


def _is_test_file(path: Path, tests_root: Path) -> bool:
    relative_path = path.relative_to(tests_root)
    is_test_file = False
    match relative_path.parts:
        case ("data", *_):
            pass
        case (*_, file_name) if path.suffix == ".py" and (
            file_name.startswith("test_") or file_name.endswith("_test.py")
        ):
            is_test_file = True
        case _:
            pass
    return is_test_file


def _normalize_exempt_file(raw_path: str) -> Path:
    path = Path(raw_path.strip())
    normalized_path = path
    match path.parts:
        case ("tests", *parts):
            normalized_path = Path(*parts)
        case _:
            pass
    return normalized_path


def _configured_exempt_files(config: pytest.Config) -> frozenset[Path]:
    return frozenset(
        normalized_path
        for raw_path in config.getini(DIRECT_ASSERT_EXEMPT_FILES_INI)
        if raw_path.strip() and (normalized_path := _normalize_exempt_file(raw_path))
    )


def _iter_test_files(tests_root: Path) -> Iterable[Path]:
    return (path for path in sorted(tests_root.rglob("*.py")) if _is_test_file(path, tests_root))


def _iter_helper_files(tests_root: Path) -> Iterable[Path]:
    for helper_root in HELPER_ROOTS:
        yield from sorted(tests_root.joinpath(helper_root).rglob("*.py"))


def _collect_findings(tests_root: Path, exempt_files: Iterable[Path]) -> list[Finding]:
    """Every guarded construct, before allowlists apply.

    Direct and disguised asserts are guarded in test modules outside the configured exemptions and in report helpers;
    assertion helper definitions only in report helpers; every other rule in all test modules and report helpers.
    """
    exempt_file_set = frozenset(exempt_files)
    return [
        *(
            finding
            for path in _iter_test_files(tests_root)
            for finding in _file_findings(
                path,
                tests_root,
                EXEMPT_TEST_RULES if path.relative_to(tests_root) in exempt_file_set else TEST_RULES,
            )
        ),
        *(
            finding
            for path in _iter_helper_files(tests_root)
            for finding in _file_findings(path, tests_root, HELPER_RULES)
        ),
    ]


def _allowlist_entries(allowlists: Mapping[str, Mapping[str, tuple[str, ...]]] = ALLOWLISTS) -> frozenset[str]:
    return frozenset(entry for groups in allowlists.values() for entries in groups.values() for entry in entries)


def _unlisted_findings(findings: Iterable[Finding], entries: frozenset[str]) -> list[Finding]:
    return [finding for finding in findings if entries.isdisjoint(finding.keys)]


def _stale_entries(findings: Iterable[Finding], entries: frozenset[str]) -> list[str]:
    matched = {key for finding in findings for key in finding.keys}
    return sorted(entries - matched)


def _malformed_groups(allowlists: Mapping[str, Mapping[str, tuple[str, ...]]] = ALLOWLISTS) -> list[str]:
    entries = [entry for groups in allowlists.values() for group in groups.values() for entry in group]
    duplicates = sorted(entry for entry, count in Counter(entries).items() if count > 1)
    return [
        *(
            f"  {name}[{group!r}] is not sorted"
            for name, groups in allowlists.items()
            for group, group_entries in groups.items()
            if list(group_entries) != sorted(group_entries)
        ),
        *(f"  {entry} appears more than once" for entry in duplicates),
        *(
            f"  {name}[{group!r}] has no reason in ALLOWLIST_GROUP_REASONS"
            for name, groups in allowlists.items()
            for group in groups
            if group not in ALLOWLIST_GROUP_REASONS
        ),
    ]


def _format_findings(findings: Iterable[Finding]) -> str:
    by_rule: dict[str, list[Finding]] = {}
    for finding in findings:
        by_rule.setdefault(finding.rule, []).append(finding)
    return "\n\n".join(
        "\n".join((f"[{rule}] {RULE_FAILURE_MESSAGES[rule]}", *map(str, rule_findings)))
        for rule, rule_findings in by_rule.items()
    )


def _guard_failure(tests_root: Path, exempt_files: Iterable[Path]) -> str | None:
    if not (findings := _unlisted_findings(_collect_findings(tests_root, exempt_files), _allowlist_entries())):
        return None
    return f"{_format_findings(findings)}\n\n{FROZEN_ALLOWLIST_POLICY_MESSAGE}"


def test_modules_use_shared_assertion_helpers(pytestconfig: pytest.Config) -> None:
    """Tests and helpers assert through shared helpers, reach generation publicly, and mock only abnormal paths."""
    if (failure := _guard_failure(TESTS_ROOT, _configured_exempt_files(pytestconfig))) is None:
        return
    pytest.fail(failure, pytrace=False)  # pragma: no cover


def test_allowlists_stay_exact(pytestconfig: pytest.Config) -> None:
    """Every allowlist entry still matches a violation, and every group is sorted without duplicates."""
    findings = _collect_findings(TESTS_ROOT, _configured_exempt_files(pytestconfig))
    misordered = _malformed_groups()
    stale = _stale_entries(findings, _allowlist_entries())
    if not misordered and not stale:
        return
    pytest.fail(  # pragma: no cover
        "\n".join((
            *((MALFORMED_ALLOWLIST_FAILURE_MESSAGE, *misordered) if misordered else ()),
            *((STALE_ALLOWLIST_FAILURE_MESSAGE, *(f"  {entry}" for entry in stale)) if stale else ()),
        )),
        pytrace=False,
    )


def test_modules_never_patch_the_complete_module_registry() -> None:
    """Module import seams must restore only exact entries; imported aliases stay reserved when shadowed."""
    whole_registry_patches = [
        whole_registry_patch
        for path in _iter_test_files(TESTS_ROOT)
        for whole_registry_patch in _collect_whole_sys_modules_patches(path, TESTS_ROOT)
    ]
    if not whole_registry_patches:
        return

    details = "\n".join(f"  tests/{patch.path}:{patch.lineno}: {patch.statement}" for patch in whole_registry_patches)
    pytest.fail(  # pragma: no cover
        "patch.dict(sys.modules, ...) restores the complete module registry and invalidates lazy-import caches. "
        "For deterministic static enforcement, imported sys/patch aliases are reserved across every scope, "
        "including when shadowed. "
        "Use monkeypatch.setitem(sys.modules, name, value) so only the intended entry is restored.\n"
        f"{details}",
        pytrace=False,
    )


def test_modules_never_patch_the_complete_module_registry_reports_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unsafe registry patches report the exact test location and safe replacement."""
    test_file = tmp_path / "test_unsafe.py"
    test_file.write_text(
        """\
import sys
from unittest.mock import patch

patch.dict(sys.modules, {"unsafe": None})
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "TESTS_ROOT", tmp_path)

    with pytest.raises(pytest.fail.Exception, match=r"(?s)monkeypatch\.setitem.*tests/test_unsafe\.py:4"):
        test_modules_never_patch_the_complete_module_registry()


def test_configured_exempt_files_exist(pytestconfig: pytest.Config) -> None:
    """Configured direct-assert exemptions must point to existing test files."""
    if not (
        missing := sorted(path for path in _configured_exempt_files(pytestconfig) if not (TESTS_ROOT / path).is_file())
    ):
        return

    pytest.fail(  # pragma: no cover
        "assert_helper_direct_assert_exempt_files contains missing files:\n"
        + "\n".join(f"  - tests/{path}" for path in missing),
        pytrace=False,
    )


class _MissingExemptConfig:
    def getini(self, name: str) -> list[str]:
        if name == DIRECT_ASSERT_EXEMPT_FILES_INI:
            return ["missing.py"]
        raise KeyError(name)  # pragma: no cover


def test_configured_exempt_files_exist_reports_missing_file() -> None:
    """Missing configured direct-assert exemptions are reported clearly."""
    with pytest.raises(pytest.fail.Exception, match=r"tests/missing\.py"):
        test_configured_exempt_files_exist(_MissingExemptConfig())  # type: ignore[arg-type]


def test_collect_whole_sys_modules_patches_covers_supported_targets(tmp_path: Path) -> None:
    """The registry guard recognizes string, attribute, alias, and keyword targets."""
    test_file = tmp_path / "test_module_registry.py"
    test_file.write_text(
        """\
import sys as system
from sys import modules as registry
from unittest.mock import patch as patcher

patcher.dict("sys.modules", {"one": None})
mocker.patch.dict(system.modules, {"two": None})
patcher.dict(registry, {"three": None})
patcher.dict(in_dict="sys.modules", values={"four": None})
monkeypatch.setitem(system.modules, "safe", None)
patcher.dict("other.registry", {"safe": None})
dict()
""",
        encoding="utf-8",
    )

    assert _collect_whole_sys_modules_patches(test_file, tmp_path) == [
        WholeSysModulesPatch(
            Path("test_module_registry.py"),
            5,
            'patcher.dict("sys.modules", {"one": None})',
        ),
        WholeSysModulesPatch(
            Path("test_module_registry.py"),
            6,
            'mocker.patch.dict(system.modules, {"two": None})',
        ),
        WholeSysModulesPatch(
            Path("test_module_registry.py"),
            7,
            'patcher.dict(registry, {"three": None})',
        ),
        WholeSysModulesPatch(
            Path("test_module_registry.py"),
            8,
            'patcher.dict(in_dict="sys.modules", values={"four": None})',
        ),
    ]


def test_collect_whole_sys_modules_patches_covers_local_aliases(tmp_path: Path) -> None:
    """Aliases imported in function scope and exact mocker calls are guarded."""
    test_file = tmp_path / "test_local_module_registry.py"
    test_file.write_text(
        """\
def test_local_aliases(mocker):
    import sys as system
    from sys import modules as registry
    from unittest import mock as mock_module
    from unittest.mock import patch as patcher
    import unittest as unit
    import unittest.mock
    import unittest.mock as imported_mock
    patcher.dict(in_dict=system.modules, values={"one": None})
    mock_module.patch.dict(registry, {"two": None})
    mocker.patch.dict("sys.modules", {"three": None})
    unit.mock.patch.dict(system.modules, {"four": None})
    unittest.mock.patch.dict(registry, {"five": None})
    imported_mock.patch.dict(system.modules, {"six": None})
""",
        encoding="utf-8",
    )

    assert _collect_whole_sys_modules_patches(test_file, tmp_path) == [
        WholeSysModulesPatch(
            Path("test_local_module_registry.py"),
            9,
            'patcher.dict(in_dict=system.modules, values={"one": None})',
        ),
        WholeSysModulesPatch(
            Path("test_local_module_registry.py"),
            10,
            'mock_module.patch.dict(registry, {"two": None})',
        ),
        WholeSysModulesPatch(
            Path("test_local_module_registry.py"),
            11,
            'mocker.patch.dict("sys.modules", {"three": None})',
        ),
        WholeSysModulesPatch(
            Path("test_local_module_registry.py"),
            12,
            'unit.mock.patch.dict(system.modules, {"four": None})',
        ),
        WholeSysModulesPatch(
            Path("test_local_module_registry.py"),
            13,
            'unittest.mock.patch.dict(registry, {"five": None})',
        ),
        WholeSysModulesPatch(
            Path("test_local_module_registry.py"),
            14,
            'imported_mock.patch.dict(system.modules, {"six": None})',
        ),
    ]


def test_collect_whole_sys_modules_patches_ignores_unrelated_calls(tmp_path: Path) -> None:
    """Unrelated patch-like APIs, targets, and exact-entry monkeypatches remain allowed."""
    test_file = tmp_path / "test_unrelated_module_registry.py"
    test_file.write_text(
        """\
import sys
from unittest.mock import patch

foo.patch.dict(sys.modules, {"safe": None})
factory().patch.dict(sys.modules, {"safe": None})
patch.dict("other.registry", {"safe": None})
patch.dict(factory(), {"safe": None})
patch.dict()
monkeypatch.setitem(sys.modules, "safe", None)
""",
        encoding="utf-8",
    )

    assert _collect_whole_sys_modules_patches(test_file, tmp_path) == []


@pytest.mark.parametrize(
    ("raw_path", "expected"),
    [
        ("test_unit.py", Path("test_unit.py")),
        ("tests/test_unit.py", Path("test_unit.py")),
        ("tests/main/test_unit.py", Path("main/test_unit.py")),
    ],
)
def test_normalize_exempt_file_accepts_tests_prefix(raw_path: str, expected: Path) -> None:
    """Config paths may be relative to tests/ or include the tests/ prefix."""
    assert _normalize_exempt_file(raw_path) == expected


def test_assert_httpx_get_kwargs_accepts_expected_urls_with_explicit_call_count(mocker: MockerFixture) -> None:
    """Explicit call_count works with multi-URL HTTP client assertions."""
    mock_get = create_httpx_get_mock(mocker)
    mock_get(
        "https://example.com/person.json",
        headers=None,
        verify=True,
        follow_redirects=True,
        params=None,
        timeout=30.0,
    )
    mock_get(
        "https://example.com/address.json",
        headers=None,
        verify=True,
        follow_redirects=True,
        params=None,
        timeout=30.0,
    )

    assert_httpx_get_kwargs(
        mock_get,
        expected_urls=["https://example.com/person.json", "https://example.com/address.json"],
        call_count=2,
    )


def test_assert_httpx_get_kwargs_accepts_called_true(mocker: MockerFixture) -> None:
    """called=True asserts that at least one URL request was made."""
    mock_get = create_httpx_get_mock(mocker)
    mock_get(
        "https://example.com/person.json",
        headers=None,
        verify=True,
        follow_redirects=True,
        params=None,
        timeout=30.0,
    )

    assert_httpx_get_kwargs(mock_get, called=True)


def test_assert_httpx_get_kwargs_validates_call_options_for_every_call(mocker: MockerFixture) -> None:
    """HTTP option checks apply to all recorded URL requests."""
    mock_get = create_httpx_get_mock(mocker)
    expected_headers = [("Authorization", "Bearer token")]
    expected_params = [("version", "v2")]
    mock_get(
        "https://example.com/person.json",
        headers=expected_headers,
        verify=False,
        follow_redirects=True,
        params=expected_params,
        timeout=60.0,
    )
    mock_get(
        "https://example.com/address.json",
        headers=expected_headers,
        verify=False,
        follow_redirects=True,
        params=expected_params,
        timeout=60.0,
    )

    assert_httpx_get_kwargs(
        mock_get,
        headers=expected_headers,
        params=expected_params,
        verify=False,
        timeout=60.0,
    )


def test_assert_httpx_get_kwargs_reports_call_option_mismatch_per_call(mocker: MockerFixture) -> None:
    """HTTP option failures catch mismatches before the last URL request."""
    mock_get = create_httpx_get_mock(mocker)
    mock_get(
        "https://example.com/person.json",
        headers=[("Authorization", "Bearer old")],
        verify=True,
        follow_redirects=True,
        params=None,
        timeout=30.0,
    )
    mock_get(
        "https://example.com/address.json",
        headers=[("Authorization", "Bearer token")],
        verify=True,
        follow_redirects=True,
        params=None,
        timeout=30.0,
    )

    with pytest.raises(AssertionError):
        assert_httpx_get_kwargs(mock_get, headers=[("Authorization", "Bearer token")])


def test_assert_httpx_get_kwargs_validates_params_contains_for_every_call(mocker: MockerFixture) -> None:
    """Subset query parameter checks apply to all recorded URL requests."""
    mock_get = create_httpx_get_mock(mocker)
    mock_get(
        "https://example.com/person.json",
        headers=None,
        verify=True,
        follow_redirects=True,
        params=[("version", "v2"), ("format", "json")],
        timeout=30.0,
    )
    mock_get(
        "https://example.com/address.json",
        headers=None,
        verify=True,
        follow_redirects=True,
        params=[("version", "v2"), ("format", "yaml")],
        timeout=30.0,
    )

    assert_httpx_get_kwargs(
        mock_get,
        expected_urls=["https://example.com/person.json", "https://example.com/address.json"],
        params_contains={"version": "v2"},
    )


def test_assert_httpx_get_kwargs_reports_params_contains_mismatch_per_call(mocker: MockerFixture) -> None:
    """Subset query parameter failures identify the request that mismatched."""
    mock_get = create_httpx_get_mock(mocker)
    mock_get(
        "https://example.com/person.json",
        headers=None,
        verify=True,
        follow_redirects=True,
        params=[("version", "v2")],
        timeout=30.0,
    )
    mock_get(
        "https://example.com/address.json",
        headers=None,
        verify=True,
        follow_redirects=True,
        params=[("version", "v1")],
        timeout=30.0,
    )

    with pytest.raises(AssertionError, match="call 2"):
        assert_httpx_get_kwargs(mock_get, params_contains={"version": "v2"})


def test_mock_httpx_get_returns_response_for_registered_url(mock_httpx_get: HttpxGetMockFactory) -> None:
    """URL-bound HTTP mocks return fixture content for the registered URL."""
    mock_httpx_get(MockHttpxResponse("https://example.com/schema.json", '{"type": "object"}'))

    response = _get_httpx().get("https://example.com/schema.json")

    assert response.text == '{"type": "object"}'


def test_mock_httpx_get_rejects_unregistered_url(mock_httpx_get: HttpxGetMockFactory) -> None:
    """URL-bound HTTP mocks fail when code fetches an unexpected URL."""
    mock_httpx_get(MockHttpxResponse("https://example.com/schema.json", '{"type": "object"}'))

    with pytest.raises(pytest.fail.Exception, match="Unexpected HTTP client URL"):
        _get_httpx().get("https://example.com/other.json")


def test_assert_generated_model_json_validation_without_attribute_check(tmp_path: Path) -> None:
    """Generated-model validation helper supports tests that only check rejection type."""
    output_path = tmp_path / "generated_model.py"
    output_path.write_text(
        "from pydantic import BaseModel\n\nclass Model(BaseModel):\n    name: str\n",
        encoding="utf-8",
    )

    assert_generated_model_json_validation(
        output_path,
        module_name="generated_model_without_attribute_check",
        model_name="Model",
        valid_json='{"name": "x"}',
        invalid_json='{"name": 1}',
        expected_error_type="string_type",
    )


def test_generated_model_json_helpers_restore_existing_module(tmp_path: Path) -> None:
    """Generated-model validation helpers restore an existing sys.modules entry."""
    output_path = tmp_path / "generated_model.py"
    output_path.write_text(
        "from pydantic import BaseModel\n\nclass Model(BaseModel):\n    value: int\n",
        encoding="utf-8",
    )
    module_name = "preexisting_generated_model"
    previous_module = ModuleType(module_name)
    sys.modules[module_name] = previous_module

    try:
        assert_generated_model_json_validation(
            output_path,
            module_name=module_name,
            model_name="Model",
            valid_json='{"value": 1}',
            invalid_json='{"value": "bad"}',
            expected_error_type="int_parsing",
        )
        assert sys.modules[module_name] is previous_module

        assert_generated_model_json_invalid(
            output_path,
            module_name=module_name,
            model_name="Model",
            invalid_json='{"value": "bad"}',
            expected_error_type="int_parsing",
        )
        assert sys.modules[module_name] is previous_module
    finally:
        sys.modules.pop(module_name, None)


def test_generated_package_model_validation_restores_existing_modules(tmp_path: Path) -> None:
    """Generated-package validation restores existing package modules."""
    output_dir = tmp_path / "model"
    output_dir.mkdir()
    (output_dir / "__init__.py").write_text("", encoding="utf-8")
    (output_dir / "target.py").write_text(
        "from pydantic import BaseModel\n\nclass Target(BaseModel):\n    value: int\n",
        encoding="utf-8",
    )

    previous_package = ModuleType("model")
    previous_module = ModuleType("model.target")
    sys.modules["model"] = previous_package
    sys.modules["model.target"] = previous_module

    try:
        _assert_generated_package_model_validation(
            output_dir,
            module_path="target",
            model_name="Target",
            data={"value": 1},
        )
        assert sys.modules["model"] is previous_package
        assert sys.modules["model.target"] is previous_module
    finally:
        sys.modules.pop("model", None)
        sys.modules.pop("model.target", None)


@pytest.mark.skipif(sys.version_info >= (3, 14), reason="requires a target version newer than the runtime")
def test_run_main_runtime_validation_skips_newer_python_target(tmp_path: Path) -> None:
    """Runtime validation follows generated-code compatibility guards."""
    input_path = tmp_path / "schema.json"
    input_path.write_text('{"type": "object", "properties": {"value": {"type": "integer"}}}', encoding="utf-8")

    run_main_and_assert(
        input_path=input_path,
        output_path=tmp_path / "model.py",
        input_file_type="jsonschema",
        extra_args=[
            "--target-python-version",
            "3.14",
            "--formatters",
            "builtin",
        ],
        skip_code_validation=True,
        runtime_validation_module="missing_module",
        runtime_validation_model_name="MissingModel",
        runtime_validation_data={},
    )


def _probe_findings(root: Path, files: Mapping[str, str], exempt_files: Iterable[Path] = ()) -> list[str]:
    for name, source in files.items():
        (path := root / name).parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return [
        f"{finding.rule} {finding.path.as_posix()}:{finding.lineno} {finding.scope}: {finding.statement}"
        for finding in _collect_findings(root, exempt_files)
    ]


def test_collect_findings_reports_asserts_in_every_scope(tmp_path: Path) -> None:
    """Asserts in module blocks, class bodies, and functions nested in an allowed test are all reported."""
    source = """\
import pytest

if True:
    assert "module block"
with open(__file__):
    assert "with block"


class Holder:
    assert "class body"


@pytest.mark.allow_direct_assert
def test_allowed():
    assert "allowed"

    def nested():
        assert "nested"

    class Local:
        assert "local class"


def test_plain():
    for _ in ():
        assert "loop"
"""
    assert _probe_findings(tmp_path, {"test_scopes.py": source}) == [
        'direct-assert test_scopes.py:4 <module>: assert "module block"',
        'direct-assert test_scopes.py:6 <module>: assert "with block"',
        'direct-assert test_scopes.py:10 Holder: assert "class body"',
        'direct-assert test_scopes.py:18 test_allowed.nested: assert "nested"',
        'direct-assert test_scopes.py:21 test_allowed.Local: assert "local class"',
        'direct-assert test_scopes.py:26 test_plain: assert "loop"',
    ]


def test_collect_findings_reports_disguised_asserts(tmp_path: Path) -> None:
    """Raising AssertionError, pytest.fail, and unittest.TestCase assertions count as direct asserts."""
    source = """\
import unittest
from unittest import TestCase as Case

import pytest
from pytest import fail as stop


def test_raises():
    raise AssertionError


def test_raises_message():
    raise AssertionError("message")


def test_fails():
    pytest.fail("message")
    stop("message")


def test_specific():
    raise ValueError("message")


def test_reraise():
    try:
        pass
    except ValueError:
        raise


class TestUnit(unittest.TestCase):
    def test_method(self):
        self.assertEqual(1, 1)
        self.fail("message")
        self.asserts.append(1)
        self.failures()


class TestAlias(Case):
    pass


class Plain(object):
    pass


@pytest.mark.allow_direct_assert
def test_allowed():
    pytest.fail("allowed")
"""
    assert _probe_findings(tmp_path, {"test_disguised.py": source}) == [
        "disguised-assert test_disguised.py:9 test_raises: raise AssertionError",
        'disguised-assert test_disguised.py:13 test_raises_message: raise AssertionError("message")',
        'disguised-assert test_disguised.py:17 test_fails: pytest.fail("message")',
        'disguised-assert test_disguised.py:18 test_fails: stop("message")',
        "disguised-assert test_disguised.py:32 TestUnit: class TestUnit(unittest.TestCase)",
        "disguised-assert test_disguised.py:34 TestUnit.test_method: self.assertEqual(1, 1)",
        'disguised-assert test_disguised.py:35 TestUnit.test_method: self.fail("message")',
        "disguised-assert test_disguised.py:40 TestAlias: class TestAlias(Case)",
    ]


def test_collect_findings_reports_assertion_helpers_in_report_helpers(tmp_path: Path) -> None:
    """Report helpers define no assertion helpers of their own, while test modules and other test data may."""
    helpers = """\
def assert_report():
    pass


def _check_report():
    pass


async def verify_report():
    pass


class Checks:
    def expect_value(self):
        pass


def compare_report():
    pass
"""
    assert _probe_findings(
        tmp_path,
        {
            "test_helpers.py": "def assert_local():\n    pass\n",
            "data/python/helpers.py": helpers,
            "data/generation_platform/probe/helpers.py": "def check_probe():\n    assert True\n",
            "data/expected/helpers.py": "def assert_expected():\n    assert True\n",
        },
    ) == [
        "assertion-helper data/python/helpers.py:1 assert_report: def assert_report",
        "assertion-helper data/python/helpers.py:5 _check_report: def _check_report",
        "assertion-helper data/python/helpers.py:9 verify_report: def verify_report",
        "assertion-helper data/python/helpers.py:14 Checks.expect_value: def expect_value",
        "assertion-helper data/generation_platform/probe/helpers.py:1 check_probe: def check_probe",
        "direct-assert data/generation_platform/probe/helpers.py:2 check_probe: assert True",
    ]


def test_collect_findings_reports_private_imports(tmp_path: Path) -> None:
    """Private modules and names, binding internals, and statically visible import_module targets are reported."""
    source = """\
import datamodel_code_generator._runtime.client
import importlib
import other._private
from importlib import import_module

from datamodel_code_generator import __version__, _run_generation, generate
from datamodel_code_generator.model.base import DataModel
from datamodel_code_generator.model.binding import FieldSlot
from datamodel_code_generator.parser import openapi_scope
from datamodel_code_generator.parser.openapi import OpenAPIParser
from datamodel_code_generator.parser.openapi_contract_store import BindingLedger

from . import sibling


def test_dynamic(package, name):
    importlib.import_module("datamodel_code_generator._client.target")
    import_module(f"datamodel_code_generator._runtime.{name}")
    import_module(f"datamodel_code_generator._api_types")
    importlib.import_module(f"{package.__name__}._runtime.client")
    importlib.import_module(name)
    importlib.import_module("datamodel_code_generator.parser.openapi")
    importlib.import_module()
"""
    assert _probe_findings(
        tmp_path,
        {
            "test_private.py": source,
            "data/python/helper.py": "from datamodel_code_generator._fastapi import config\n",
            "data/generation_platform/typing/probe.py": "from datamodel_code_generator import _source\n",
            "data/boundaries/forbidden.py": "from datamodel_code_generator._api_generation import render_target\n",
        },
    ) == [
        "private-import test_private.py:1 <module>: datamodel_code_generator._runtime.client",
        "private-import test_private.py:6 <module>: datamodel_code_generator._run_generation",
        "private-import test_private.py:8 <module>: datamodel_code_generator.model.binding",
        "private-import test_private.py:9 <module>: datamodel_code_generator.parser.openapi_scope",
        "private-import test_private.py:11 <module>: datamodel_code_generator.parser.openapi_contract_store",
        "private-import test_private.py:17 test_dynamic: datamodel_code_generator._client.target",
        "private-import test_private.py:18 test_dynamic: datamodel_code_generator._runtime.",
        "private-import test_private.py:19 test_dynamic: datamodel_code_generator._api_types",
        "private-import data/python/helper.py:1 <module>: datamodel_code_generator._fastapi",
        "private-import data/generation_platform/typing/probe.py:1 <module>: datamodel_code_generator._source",
    ]


def test_collect_findings_reports_normal_path_mocks(tmp_path: Path) -> None:
    """Profile hooks and patches of package or generated runtime internals need an abnormal_path reason."""
    source = """\
import importlib
import sys
import sys as system
import threading
from pathlib import Path
from sys import setprofile
from unittest.mock import patch

import pytest

from datamodel_code_generator import _publication
from datamodel_code_generator.parser.openapi import OpenAPIParser


def test_profiles(observer):
    sys.setprofile(observer)
    system.settrace(observer)
    threading.setprofile(observer)
    setprofile(None)


def test_patches(mocker, monkeypatch, package):
    module = importlib.import_module(f"{package.__name__}._runtime.client.timing")
    clock: object = module.monotonic
    width: int
    patch("datamodel_code_generator.parser.openapi.OpenAPIParser.parse")
    patch.object(OpenAPIParser, "parse")
    patch.object(target=_publication, attribute="_replace_source")
    mocker.patch.object(clock, "real")
    mocker.spy(package._runtime.client, "send")
    monkeypatch.setattr("datamodel_code_generator._publication._unlink", None)
    with monkeypatch.context() as fault, open(__file__):
        fault.setattr(OpenAPIParser, "dispose", None)
    with pytest.MonkeyPatch.context() as scoped:
        scoped.setattr(sys, "argv", [])
    if (opened := Path.open) is not None:
        patch.object(opened, "close")
    patch.object(
        OpenAPIParser,
        "dispose",
    )
    factory().patch.object(OpenAPIParser, "parse")
    patch.object(Path, "open")
    patch("os.path.relpath")
    monkeypatch.setattr(sys, "argv", [])
    patch()


@pytest.mark.abnormal_path("the disk fills only on a failing device")
def test_marked(monkeypatch):
    monkeypatch.setattr(_publication, "_replace_source", None)

    def nested():
        sys.setprofile(None)


@pytest.mark.abnormal_path("a racing process replaces the directory")
class TestMarked:
    def test_method(self, monkeypatch):
        monkeypatch.setattr(_publication, "_rmdir", None)


@pytest.mark.abnormal_path
def test_bare(monkeypatch):
    monkeypatch.setattr(_publication, "_unlink", None)


@pytest.mark.abnormal_path("")
def test_empty():
    pass


@pytest.mark.abnormal_path(reason="keyword")
def test_keyword():
    pass


@pytest.mark.abnormal_path(1)
def test_number():
    pass
"""
    findings = _probe_findings(tmp_path, {"test_mocks.py": source})

    assert [finding for finding in findings if not finding.startswith("private-import ")] == [
        "normal-path-mock test_mocks.py:16 test_profiles: sys.setprofile(observer)",
        "normal-path-mock test_mocks.py:17 test_profiles: system.settrace(observer)",
        "normal-path-mock test_mocks.py:18 test_profiles: threading.setprofile(observer)",
        "normal-path-mock test_mocks.py:19 test_profiles: setprofile(None)",
        (
            "normal-path-mock test_mocks.py:26 test_patches: "
            'patch("datamodel_code_generator.parser.openapi.OpenAPIParser.parse")'
        ),
        'normal-path-mock test_mocks.py:27 test_patches: patch.object(OpenAPIParser, "parse")',
        (
            "normal-path-mock test_mocks.py:28 test_patches: "
            'patch.object(target=_publication, attribute="_replace_source")'
        ),
        'normal-path-mock test_mocks.py:29 test_patches: mocker.patch.object(clock, "real")',
        'normal-path-mock test_mocks.py:30 test_patches: mocker.spy(package._runtime.client, "send")',
        (
            "normal-path-mock test_mocks.py:31 test_patches: "
            'monkeypatch.setattr("datamodel_code_generator._publication._unlink", None)'
        ),
        'normal-path-mock test_mocks.py:33 test_patches: fault.setattr(OpenAPIParser, "dispose", None)',
        'normal-path-mock test_mocks.py:38 test_patches: patch.object( OpenAPIParser, "dispose", )',
        "abnormal-path-reason test_mocks.py:63 test_bare: pytest.mark.abnormal_path",
        'normal-path-mock test_mocks.py:65 test_bare: monkeypatch.setattr(_publication, "_unlink", None)',
        'abnormal-path-reason test_mocks.py:68 test_empty: pytest.mark.abnormal_path("")',
        'abnormal-path-reason test_mocks.py:73 test_keyword: pytest.mark.abnormal_path(reason="keyword")',
        "abnormal-path-reason test_mocks.py:78 test_number: pytest.mark.abnormal_path(1)",
    ]


def test_collect_findings_limits_direct_asserts_to_guarded_files(tmp_path: Path) -> None:
    """Exempt files and other modules skip the assert rules, while every test module keeps the other rules."""
    findings = _probe_findings(
        tmp_path,
        {
            "test_exempt.py": "from datamodel_code_generator import _source\n\nassert True\n",
            "nested/example_test.py": "assert True\n",
            "nested/helper.py": "assert True\n",
            "data/test_fixture.py": "assert True\n",
        },
        exempt_files=(Path("test_exempt.py"),),
    )

    assert findings == [
        "direct-assert nested/example_test.py:1 <module>: assert True",
        "private-import test_exempt.py:1 <module>: datamodel_code_generator._source",
    ]


def test_guard_failure_groups_findings_by_rule(tmp_path: Path) -> None:
    """Failures explain each rule once and list every location under it."""
    (tmp_path / "test_rules.py").write_text(
        "from datamodel_code_generator import _source\n\nassert True\nassert False\n", encoding="utf-8"
    )

    assert _guard_failure(tmp_path, ()) == "\n".join((
        f"[private-import] {RULE_FAILURE_MESSAGES[PRIVATE_IMPORT]}",
        "  tests/test_rules.py:1 (<module>): datamodel_code_generator._source",
        "",
        f"[direct-assert] {RULE_FAILURE_MESSAGES[DIRECT_ASSERT]}",
        "  tests/test_rules.py:3 (<module>): assert True",
        "  tests/test_rules.py:4 (<module>): assert False",
        "",
        FROZEN_ALLOWLIST_POLICY_MESSAGE,
    ))


def test_guard_failure_passes_clean_modules(tmp_path: Path) -> None:
    """Modules without findings pass the guard."""
    (tmp_path / "test_clean.py").write_text("def test_clean():\n    pass\n", encoding="utf-8")

    assert _guard_failure(tmp_path, ()) is None


def test_allowlist_entries_match_by_file_or_scope() -> None:
    """Entries name a whole file or one enclosing scope, and entries without a violation are stale."""
    findings = [
        Finding(PRIVATE_IMPORT, Path("test_a.py"), "<module>", 1, "datamodel_code_generator._source"),
        Finding(NORMAL_PATH_MOCK, Path("test_a.py"), "test_x", 2, "sys.setprofile(None)"),
        Finding(NORMAL_PATH_MOCK, Path("test_a.py"), "test_y", 3, "sys.setprofile(None)"),
    ]
    entries = _allowlist_entries({
        "LISTED": {
            "files": ("private-import tests/test_a.py",),
            "scopes": ("normal-path-mock tests/test_a.py::test_gone", "normal-path-mock tests/test_a.py::test_x"),
        }
    })

    assert _unlisted_findings(findings, entries) == [findings[2]]
    assert _stale_entries(findings, entries) == ["normal-path-mock tests/test_a.py::test_gone"]


def test_malformed_groups_reports_unsorted_and_duplicate_entries() -> None:
    """Allowlist groups stay sorted and explained, and an entry belongs to one group only."""
    assert _malformed_groups({"FIRST": {"spies": ("b", "a")}, "SECOND": {"other": ("a",)}}) == [
        "  FIRST['spies'] is not sorted",
        "  a appears more than once",
        "  SECOND['other'] has no reason in ALLOWLIST_GROUP_REASONS",
    ]
