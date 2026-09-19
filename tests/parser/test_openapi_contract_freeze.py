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
