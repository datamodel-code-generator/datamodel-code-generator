"""Preserve both origins when common and branch schemas compose the same nested member."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType, GenerateConfig, OpenAPIScope
from datamodel_code_generator._openapi_type_binding import require_type_bindings
from tests.conftest import assert_output
from tests.data.python.generation_session_inputs import generate_product

DATA = Path(__file__).parents[1] / "data"
EXPECTED = DATA / "expected/main/generation_platform"


@pytest.mark.parametrize("backend", list(DataModelType))
@pytest.mark.parametrize("case", ["tuple", "any", "one"])
def test_recursive_common_composition(backend: DataModelType, case: str) -> None:
    """Keep original sequence ordinals and common/branch contributors after materialization."""
    product, retained = generate_product(
        (DATA / f"generation_platform/binding/session-common-{case}.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            input_filename="common-composition.json",
            formatters=[],
            disable_timestamp=True,
        ),
    )
    expected = EXPECTED / "session-review/common-composition" / f"{case}-origins.txt"
    members = json.loads(expected.read_text())
    names = {symbol.id: symbol.name for symbol in product.batch.symbols}
    origins = {
        member: sorted({origin.location.pointer for origin in field.occurrences})
        for field in product.batch.fields
        if (member := f"{names[field.consumer]}.{field.wire_name}") in members
    }
    for field in product.batch.fields:
        assert_output(field.origin_state, EXPECTED / "session-review/known-origin.txt")
        for origin in field.occurrences:
            product.source_lease.borrow(origin.location)
    product.close()
    assert_output(json.dumps(origins, indent=2) + "\n", expected)
    assert_output(
        "\n".join(
            sorted({
                error.code
                for error in require_type_bindings(
                    product.batch,
                    tuple(
                        use.id
                        for use in product.batch.type_uses
                        if use.id.schema_site.pointer == "/components/schemas/Payload"
                    ),
                )
            })
        ),
        EXPECTED / "session-review/no-diagnostics.txt",
    )
    assert_output(
        product.artifacts[0].content.decode(),
        EXPECTED / "session-review/common-composition" / f"{case}-{backend.name}.py",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")
