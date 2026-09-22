"""Inject invalid final state after real rendering and exercise accepted-artifact rejection."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from datamodel_code_generator import GenerateConfig, OpenAPIScope
from datamodel_code_generator._openapi_generation import OpenAPIGenerationSession
from datamodel_code_generator._openapi_type_binding import require_type_bindings
from tests.data.python.binding_type_corruptions import corrupt_type
from tests.data.python.generation_session_inputs import generate_product

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def final_type_failure(source: Path, case: str, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Corrupt a real final producer without replacing generation or its failure handling."""
    freeze = OpenAPIGenerationSession.freeze_attempt

    def corrupt(session: OpenAPIGenerationSession, parser: Any, results: Any) -> Any:
        model = next(model for model in parser.results if model.name == "Model")
        field = next(field for field in model.fields if field.name == "value")
        data_type = field.data_type
        corrupt_type(parser, data_type, case)
        return freeze(session, parser, results)

    with monkeypatch.context() as fault:
        fault.setattr(OpenAPIGenerationSession, "freeze_attempt", corrupt)
        product, retained = generate_product(
            source,
            GenerateConfig(
                input_file_type="openapi",
                openapi_scopes=[OpenAPIScope.Api],
                import_overrides={"conint": "pydantic", "Decimal": "decimal"},
                formatters=[],
                disable_timestamp=True,
            ),
        )
    product.close()
    errors = require_type_bindings(product.batch, tuple(use.id for use in product.batch.type_uses))
    return {"codes": sorted({error.code for error in errors}), "retained_graph": retained}
