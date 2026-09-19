"""Keep nullable alias values distinct from RootModel instances and nullable container items."""

from __future__ import annotations

from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType, GenerateConfig, OpenAPIScope
from datamodel_code_generator._openapi_type_binding import require_type_bindings
from tests.conftest import assert_output
from tests.data.python.generation_session_inputs import generate_product

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/binding"
EXPECTED = DATA / "expected/main/generation_platform"


@pytest.mark.parametrize("backend", list(DataModelType))
@pytest.mark.parametrize("collapse", [False, True])
@pytest.mark.parametrize("annotated", [False, True])
def test_nullable_alias_provenance(backend: DataModelType, *, collapse: bool, annotated: bool) -> None:
    """Compare literal omission proofs and fixed-main bytes through the real driver and disposal."""
    product, retained = generate_product(
        (SOURCE / "session-nullable-aliases.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            input_filename="nullable-aliases.json",
            collapse_root_models=collapse,
            use_annotated=annotated,
            field_constraints=annotated,
            formatters=[],
            disable_timestamp=True,
        ),
    )
    product.close()
    assert_output(
        product.artifacts[0].content.decode(),
        EXPECTED / "session-review/nullable-aliases" / f"{backend.name}-{collapse}-{annotated}.py",
    )
    names = {symbol.id: symbol.name for symbol in product.batch.symbols}
    fields = sorted(
        (field for field in product.batch.fields if names[field.consumer] == "Model"),
        key=lambda field: field.slot.name,
    )
    assert_output(
        "".join(
            f"{field.slot.name}: {provenance.emitted_default} / {provenance.origin} / "
            f"{provenance.annotation_null_origin}\n"
            for field in fields
            for provenance in (field.model_facts.none_default_provenance,)
        ),
        EXPECTED / "session-review/nullable-aliases" / f"provenance-{backend.name}-{collapse}.txt",
    )
    assert_output(
        "\n".join(
            error.code
            for error in require_type_bindings(product.batch, tuple(use.id for use in product.batch.type_uses))
        ),
        EXPECTED / "session-review/no-diagnostics.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")
