"""Verify final None-default provenance against source declarations and accepted bytes."""

from __future__ import annotations

from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType, GenerateConfig, OpenAPIScope
from datamodel_code_generator._openapi_type_binding import require_type_bindings
from tests.conftest import assert_output
from tests.data.python.generation_session_inputs import generate_product

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/binding/session-default-policies.json"
EXPECTED = DATA / "expected/main/generation_platform/session-review/default-policies"


@pytest.mark.parametrize("backend", list(DataModelType))
@pytest.mark.parametrize("force_optional", [False, True])
def test_final_default_policy(backend: DataModelType, *, force_optional: bool) -> None:
    """Distinguish equal-valued explicit overrides, schema nulls, and configured annotations."""
    product, retained = generate_product(
        SOURCE.resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            default_value_overrides={"override_null": None},
            force_optional_for_required_fields=force_optional,
            input_filename="default-policies.json",
            formatters=[],
            disable_timestamp=True,
        ),
    )
    product.close()
    assert_output(product.artifacts[0].content.decode(), EXPECTED / f"{backend.name}-{force_optional}.py")
    assert_output(
        "".join(
            f"{field.slot.name}: {value.emitted_default} / {value.origin} / {value.annotation_null_origin}\n"
            for field in sorted(product.batch.fields, key=lambda field: field.slot.name)
            for value in (field.model_facts.none_default_provenance,)
        ),
        EXPECTED / f"{backend.name}-{force_optional}.txt",
    )
    assert_output(
        "\n".join(
            error.code
            for error in require_type_bindings(product.batch, tuple(use.id for use in product.batch.type_uses))
        ),
        EXPECTED.parent / "no-diagnostics.txt",
    )
    assert_output(f"{retained}\n", EXPECTED.parents[1] / "no-retained-graph.txt")
