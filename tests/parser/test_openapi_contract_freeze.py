"""Observe final ordinary module returns and release borrowed graph ownership."""

from __future__ import annotations

import gc
import json
import weakref
from pathlib import Path

import pytest

from datamodel_code_generator import ModuleSplitMode
from datamodel_code_generator._generation_contract import AttemptId
from datamodel_code_generator.parser.openapi_contract import ContractOpenAPIParser
from tests.conftest import assert_output, assert_parser_modules
from tests.data.python.binding_type_snapshot import module_output_snapshot, module_result_snapshot

DATA = Path(__file__).parents[1] / "data"
EXPECTED = DATA / "expected/main/generation_platform/binding"


@pytest.mark.parametrize("split", [False, True])
def test_actual_module_output_and_release(*, split: bool) -> None:
    """Retain actual declaring order and accepted Result identity without another render."""
    parser = ContractOpenAPIParser(
        DATA / "generation_platform/observation.json",
        attempt_id=AttemptId(1),
        formatters=[],
        openapi_scopes=["schemas", "paths"],
        read_only_write_only_model_type="all",
        collapse_root_models=True,
        reuse_model=True,
    )
    try:
        results = parser.parse(module_split_mode=ModuleSplitMode.Single if split else None)
        if split:
            assert_parser_modules(results, EXPECTED.parent / "split")
        else:
            assert_output(results, EXPECTED / "module-outputs-single.py")
        assert_output(
            json.dumps(module_output_snapshot(parser, results), indent=2) + "\n",
            EXPECTED / f"module-outputs-{split}.txt",
        )
        assert_output(
            json.dumps(module_result_snapshot(parser, results), indent=2) + "\n",
            EXPECTED / f"module-bindings-{split}.txt",
        )
        models = [weakref.ref(model) for output in parser.module_outputs for model in output.models]
    finally:
        parser.dispose()
        parser.source_lease.close()
    gc.collect()
    assert_output(
        json.dumps({
            "captures_released": not parser.module_outputs,
            "graph_released": all(ref() is None for ref in models),
        })
        + "\n",
        EXPECTED / "module-outputs-released.txt",
    )


@pytest.mark.parametrize("dotted", [False, True])
def test_actual_module_addresses(*, dotted: bool) -> None:
    """Preserve ordinary normalization and distinguish real definitions from init copies."""
    parser = ContractOpenAPIParser(
        DATA / "generation_platform/binding/module-layout",
        attempt_id=AttemptId(1),
        formatters=[],
        treat_dot_as_module=dotted,
    )
    try:
        results = parser.parse()
        assert_parser_modules(results, EXPECTED / f"module-layout-{dotted}")
        assert_output(
            json.dumps(module_result_snapshot(parser, results), indent=2) + "\n",
            EXPECTED / f"module-layout-{dotted}.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize("chained", [False, True])
def test_recording_failure_does_not_retain_disposed_models(monkeypatch: pytest.MonkeyPatch, *, chained: bool) -> None:
    """Keep first-failure identity while releasing direct and chained traceback graph roots."""
    from tests.data.python.binding_failure_inputs import failed_module_capture

    parser, references, failure = failed_module_capture(
        DATA / "generation_platform/binding/empty-fixed-tuple.json", monkeypatch, chained=chained
    )
    gc.collect()
    assert_output(
        json.dumps({
            "graph_released": bool(references) and all(reference() is None for reference in references),
            "failure_preserved": failure is not None and parser.binding_ledger.failure is failure,
            "traceback_released": failure is not None and failure.__traceback__ is None,
        })
        + "\n",
        EXPECTED / "module-failure-released.txt",
    )


@pytest.mark.parametrize("case", ["final-imports", "final-reused-inheritance"])
def test_final_inventory_matches_actual_imports_and_inheritance(case: str) -> None:
    """Match ordinary emitted imports and reference policy after remapping and reuse."""
    from datamodel_code_generator.model.binding import FrozenImportBindings, index_builtin_field_declarations
    from datamodel_code_generator.parser.openapi_contract import ContractApiOpenAPIParser
    from datamodel_code_generator.parser.openapi_contract_freeze import freeze_model_inventory
    from tests.data.python.binding_inputs import final_inventory_config, final_inventory_declarations

    parser = ContractApiOpenAPIParser(
        DATA / f"generation_platform/binding/{case}.json", attempt_id=AttemptId(9), config=final_inventory_config(case)
    )
    try:
        body = str(parser.parse())
        assert_output(body, EXPECTED / f"{case}.py")
        inventory = freeze_model_inventory(parser, body, output=Path("models.py"), model_package="example.models")
        imports = FrozenImportBindings(
            inventory.imports[0].values, tuple((model.symbol, model.name) for model in inventory.models)
        )
        index = index_builtin_field_declarations(
            body, expected=final_inventory_declarations(inventory), imports=imports
        )
        assert_output(
            json.dumps(
                [[field.expected.model_name, field.expected.native_name, field.annotation] for field in index.fields],
                indent=2,
            )
            + "\n",
            EXPECTED / f"{case}.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize("keyword", ["anyOf", "oneOf"])
def test_inline_allof_union_keeps_original_property_origin(keyword: str) -> None:
    """Distinguish inline union processing from referenced allOf loader materialization."""
    from datamodel_code_generator import DataModelType
    from datamodel_code_generator.parser.openapi_contract import ContractApiOpenAPIParser
    from tests.data.python.binding_inputs import builtin_binding_config

    parser = ContractApiOpenAPIParser(
        DATA / f"generation_platform/binding/inline-allof-{keyword}.json",
        attempt_id=AttemptId(1),
        config=builtin_binding_config(DataModelType.PydanticV2BaseModel),
    )
    try:
        body = str(parser.parse())
        assert_output(body, EXPECTED / f"inline-allof-{keyword}.py")
        pointers = sorted({
            origin.location.pointer
            for observation in parser.field_origins.values()
            if observation.wire_name == "label"
            for origin in observation.origins
        })
        assert_output(json.dumps(pointers, indent=2) + "\n", EXPECTED / f"inline-allof-{keyword}.txt")
    finally:
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize("formatter", ["builtin", "black"])
def test_formatted_constraint_values_keep_semantic_identity(formatter: str) -> None:
    """Accept real formatter quote normalization without executing retained expressions."""
    from datamodel_code_generator.model.binding import FrozenImportBindings, index_builtin_field_declarations
    from datamodel_code_generator.parser.openapi_contract import ContractApiOpenAPIParser
    from datamodel_code_generator.parser.openapi_contract_freeze import freeze_model_inventory
    from tests.data.python.binding_inputs import final_inventory_declarations, quoted_constraints_config

    parser = ContractApiOpenAPIParser(
        DATA / "generation_platform/binding/quoted-constraints.json",
        attempt_id=AttemptId(1),
        config=quoted_constraints_config(formatter),
    )
    try:
        body = str(parser.parse())
        assert_output(body, EXPECTED / f"quoted-constraints-{formatter}.py")
        inventory = freeze_model_inventory(parser, body, output=Path("models.py"), model_package="example.models")
        index = index_builtin_field_declarations(
            body,
            expected=final_inventory_declarations(inventory),
            imports=FrozenImportBindings(inventory.imports[0].values),
        )
        assert_output(
            json.dumps([field.expected.native_name for field in index.fields]) + "\n",
            EXPECTED / "quoted-constraints.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()
