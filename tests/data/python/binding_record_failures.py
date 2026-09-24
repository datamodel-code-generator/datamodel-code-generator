"""Damage completed observations and verify the real accepted-product boundary."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from datamodel_code_generator import GenerateConfig, OpenAPIScope
from datamodel_code_generator._openapi_generation import OpenAPIGenerationSession
from datamodel_code_generator._openapi_type_binding import require_type_bindings
from datamodel_code_generator.model.base import DataModel
from datamodel_code_generator.reference import Reference
from tests.data.python.generation_session_inputs import generate_product

if TYPE_CHECKING:
    import pytest


def record_failure(source: Path, case: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Keep ordinary generation intact until one completed producer record is corrupted."""
    freeze = OpenAPIGenerationSession.freeze_attempt

    def corrupt(session: OpenAPIGenerationSession, parser: Any, results: Any) -> Any:
        match case["id"]:
            case "missing-module-result":
                results = {}
            case "duplicate-module-owner":
                parser.module_outputs.append(parser.module_outputs[0])
                results = {}
            case "custom-model-policy":
                parser.data_model_type = DataModel
            case "custom-template-policy":
                for model in parser.results:
                    model._custom_template_dir = Path("unsupported-template")
            case "lost-extra-items":
                parser.additional_types.clear()
            case "malformed-model-settings":
                for model in parser.results:
                    model.dataclass_arguments = {"init": object()}
            case "functional-unknown-parent":
                parser.results[0].base_classes.append(
                    parser.data_type(reference=Reference(path="#/lost-parent", name="LostParent"))
                )
            case "functional-lost-type":
                parser.results[0].fields[0].data_type.type = "not_a_builtin"
                parser.results[0].fields[0].data_type.import_ = None
            case "missing-declarations":
                for output in parser.module_outputs:
                    output.result.body = ""
            case "legacy-unknown-operation":
                parser.legacy_operations[:] = [
                    replace(item, origin_state="ambiguous") for item in parser.legacy_operations
                ]
                parser.request_types[:] = [replace(item, operation=None) for item in parser.request_types]
                parser.response_types[:] = [replace(item, operation=None) for item in parser.response_types]
            case "legacy-lost-parameter":
                for observation in parser.parameter_fields:
                    parser.field_origins.pop(parser.binding_ledger.identity(observation.field), None)
            case "legacy-malformed-parameters":
                parser.legacy_operations[:] = [
                    replace(item, effective={**item.effective, "parameters": {}}) for item in parser.legacy_operations
                ]
            case "legacy-foreign-parameter":
                parser.legacy_operations[:] = [
                    replace(item, effective={**item.effective, "parameters": [{"name": "foreign", "in": "query"}]})
                    for item in parser.legacy_operations
                ]
            case "unresolved-emitted-reference":
                parser.unresolved_references.update(model.reference.path for model in parser.results)
            case _:
                raise ValueError(case["id"])
        return freeze(session, parser, results)

    with monkeypatch.context() as fault:
        fault.setattr(OpenAPIGenerationSession, "freeze_attempt", corrupt)
        product, retained = generate_product(
            source,
            GenerateConfig(
                input_file_type="openapi",
                openapi_scopes=[OpenAPIScope.Paths] if case.get("legacy") else [OpenAPIScope.Api],
                output_model_type=case.get("backend", "pydantic_v2.BaseModel"),
                formatters=[],
                disable_timestamp=True,
            ),
        )
    product.close()
    errors = require_type_bindings(product.batch, tuple(use.id for use in product.batch.type_uses))
    return {
        "diagnostics": sorted({diagnostic.code for diagnostic in product.batch.diagnostics}),
        "demands": sorted({error.code for error in errors}),
        "retained_graph": retained,
    }
