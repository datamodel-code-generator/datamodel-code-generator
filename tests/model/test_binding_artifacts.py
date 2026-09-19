"""Corroborate real emitted artifacts independently of accepted-batch assembly."""

from __future__ import annotations

import json
from operator import itemgetter
from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType
from datamodel_code_generator._generation_contract import AttemptId, SymbolId
from datamodel_code_generator.imports import Import
from datamodel_code_generator.model.binding import (
    FrozenImportBindings,
    index_builtin_field_declarations,
    same_artifact_model_facts,
    same_emitted_field_facts,
    split_artifact_models,
)
from datamodel_code_generator.parser.openapi_contract import ContractApiOpenAPIParser
from tests.conftest import assert_output
from tests.data.python.binding_inputs import (
    builtin_binding_config,
    builtin_field_imports,
    projected_field_expectations,
)

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/binding"
EXPECTED = DATA / "expected/main/generation_platform/binding"


@pytest.mark.parametrize("case", json.loads((SOURCE / "artifact-comparisons.json").read_text()), ids=itemgetter("id"))
def test_real_artifact_fact_comparison(case: dict[str, str]) -> None:
    """Distinguish harmless spelling from changes to defaults, factories and model policies."""
    backend = DataModelType[case["backend"]]
    metadata = case["source"] == "emitted-meta"
    config = builtin_binding_config(backend, annotated=metadata, missing_sentinel=not metadata)
    config.collapse_root_models = metadata
    parser = ContractApiOpenAPIParser(SOURCE / f"{case['source']}.json", attempt_id=AttemptId(1), config=config)
    try:
        body = str(parser.parse())
        assert_output(
            body,
            EXPECTED / ("emitted-meta-True.py" if metadata else f"emitted-defaults-{backend.name}-False.py"),
        )
        declarations = projected_field_expectations(parser, "Metadata" if metadata else "Defaults", backend)
        imports = FrozenImportBindings(
            (
                *builtin_field_imports(),
                Import(import_="field", from_="msgspec" if backend == DataModelType.MsgspecStruct else "dataclasses"),
            ),
            tuple((SymbolId(index), model.name) for index, model in enumerate(parser.results)),
        )
        original = index_builtin_field_declarations(body, expected=declarations, imports=imports)
        actual = index_builtin_field_declarations(
            body.replace(case["old"], case["new"]), expected=declarations, imports=imports
        )
        original_models, actual_models = split_artifact_models(original), split_artifact_models(actual)
        assert_output(
            json.dumps(
                {
                    "changed_fields": [
                        left.expected.native_name
                        for left, right in zip(original.fields, actual.fields, strict=True)
                        if not same_emitted_field_facts(left.facts, right.facts)
                    ],
                    "same_models": same_artifact_model_facts(original, actual),
                    "same_partitioned_models": all(
                        same_artifact_model_facts(original_models[name], actual_models[name], model_name=name)
                        for name in original_models
                    ),
                },
                indent=2,
            )
            + "\n",
            EXPECTED / "artifact-comparisons" / f"{case['expected']}.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()
