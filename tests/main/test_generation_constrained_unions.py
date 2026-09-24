"""Bind materialized constraint branches without replacing the complete schema helper."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType, GenerateConfig, OpenAPIScope
from datamodel_code_generator._openapi_type_binding import require_type_bindings
from tests.conftest import assert_output
from tests.data.python.binding_type_snapshot import type_snapshot
from tests.data.python.generation_session_inputs import generate_product

DATA = Path(__file__).parents[1] / "data"
EXPECTED = DATA / "expected/main/generation_platform"
CASES = {
    "array": "openapi/array_type_union_constraints.yaml",
    "object": "generation_platform/binding/session-constrained-object-False.json",
    "values": "generation_platform/binding/session-constrained-object-True.json",
    "nested": "generation_platform/binding/session-constrained-object-nested.json",
    "plain": "generation_platform/binding/session-plain-unions.json",
}


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("backend", list(DataModelType))
@pytest.mark.parametrize("annotated", [False, True])
def test_constrained_union_helper(case: str, backend: DataModelType, *, annotated: bool) -> None:
    """Compare complete helper types and every emitted root origin with independent tables."""
    product, retained = generate_product(
        (DATA / CASES[case]).resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            input_filename="constrained-union.json",
            field_constraints=True,
            use_annotated=annotated,
            formatters=[],
            disable_timestamp=True,
        ),
    )
    product.close()
    names = {symbol.id: symbol.name for symbol in product.batch.symbols}
    expected = EXPECTED / "session-review/constrained-unions"
    variant = (
        "MsgspecAnnotated" if backend == DataModelType.MsgspecStruct and annotated and case != "array" else backend.name
    )
    origins = {
        f"{names[field.consumer]}.{field.slot.name}": {
            "state": field.origin_state,
            "locations": sorted({origin.location.pointer for origin in field.occurrences}),
        }
        for field in product.batch.fields
    }
    if case == "nested":
        variant = f"{backend.name}-{annotated}"
    assert_output(json.dumps(origins, indent=2) + "\n", expected / f"{case}-{variant}-origins.txt")
    helpers = [
        type_snapshot(use.type, names)
        for use in product.batch.type_uses
        if use.id.schema_site.pointer == "/components/schemas/Payload/properties/value"
        or (case == "nested" and use.id.schema_site.pointer.endswith("/additionalProperties"))
    ]
    assert_output(json.dumps(helpers, indent=2) + "\n", expected / f"{case}-{variant}-helper.txt")
    assert_output(
        "\n".join(
            error.code
            for error in require_type_bindings(product.batch, tuple(use.id for use in product.batch.type_uses))
        ),
        EXPECTED / "session-review/no-diagnostics.txt",
    )
    assert_output(product.artifacts[0].content.decode(), expected / f"{case}-{backend.name}-{annotated}.py")
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")
