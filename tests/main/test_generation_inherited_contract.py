"""Capture existing no-merge inherited schema materialization without changing model output."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType, GenerateConfig, OpenAPIScope, SchemaParseError
from datamodel_code_generator._openapi_type_binding import require_type_bindings
from tests.conftest import assert_output
from tests.data.python.generation_session_inputs import generate_product

DATA = Path(__file__).parents[1] / "data"
EXPECTED = DATA / "expected/main/generation_platform"


@pytest.mark.parametrize("backend", list(DataModelType))
@pytest.mark.parametrize(
    "case", ["shapes", "tuple", "distributed", "ref-sibling", "ref-chain", "property-names", "x-property-names"]
)
def test_forward_parent_nested_field_origins(backend: DataModelType, case: str) -> None:
    """Retain parent-only members and both contributors to a child constraint."""
    product, retained = generate_product(
        (DATA / f"generation_platform/binding/session-inherited-{case}.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            input_filename="inherited.json",
            allof_merge_mode="none",
            formatters=[],
            disable_timestamp=True,
        ),
    )
    stem = "nested" if case == "shapes" else case
    expected = EXPECTED / "session-review/inherited-no-merge" / f"{stem}-fields.txt"
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
                    tuple(use.id for use in product.batch.type_uses if use.id.schema_site.pointer.count("/") == 3),
                )
            })
        ),
        EXPECTED / "session-review/no-diagnostics.txt",
    )
    assert_output(
        product.artifacts[0].content.decode(),
        EXPECTED / "session-review/inherited-no-merge" / f"{stem}-{backend.name}.py",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize(
    "case",
    ["backend_types", "constraint_type_shape", "recursive_constraints"],
)
@pytest.mark.parametrize("backend", list(DataModelType))
def test_actual_inherited_no_merge_materialization(case: str, backend: DataModelType) -> None:
    """Keep actual inherited producers, field occurrences, and owned artifact identities."""
    product, retained = generate_product(
        (DATA / f"openapi/allof_no_merge_{case}.yaml").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            input_filename="inherited.yaml",
            allof_merge_mode="none",
            formatters=[],
            disable_timestamp=True,
        ),
    )
    for field in product.batch.fields:
        assert_output(field.origin_state, EXPECTED / "session-review/known-origin.txt")
        for origin in field.occurrences:
            product.source_lease.borrow(origin.location)
    product.close()
    assert_output(
        product.artifacts[0].content.decode(),
        EXPECTED / "session-review/inherited-no-merge" / f"{case}-{backend.name}.py",
    )
    assert_output(
        "\n".join(
            sorted({
                error.code
                for error in require_type_bindings(
                    product.batch,
                    tuple(use.id for use in product.batch.type_uses if use.id.schema_site.pointer.count("/") == 3),
                )
            })
        ),
        EXPECTED / "session-review/no-diagnostics.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize("case", ["boolean_false_ref", "boolean_false_literal", "boolean_false_nested"])
@pytest.mark.parametrize("backend", list(DataModelType))
def test_inherited_false_branch_preserves_engine_failure(case: str, backend: DataModelType) -> None:
    """Preserve the baseline unsatisfiable-schema error through the capture driver."""
    with pytest.raises(SchemaParseError) as error:
        generate_product(
            (DATA / f"openapi/allof_no_merge_{case}.yaml").resolve(),
            GenerateConfig(
                input_file_type="openapi",
                openapi_scopes=[OpenAPIScope.Api],
                output_model_type=backend,
                allof_merge_mode="none",
                formatters=[],
                disable_timestamp=True,
            ),
        )
    assert_output(str(error.value) + "\n", EXPECTED / "session-review/inherited-no-merge" / f"{case}.txt")
