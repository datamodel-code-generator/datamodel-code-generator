"""Preserve forward inherited discriminator copies across actual module boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType, GenerateConfig, OpenAPIScope
from datamodel_code_generator._openapi_type_binding import require_type_bindings
from tests.conftest import assert_output
from tests.data.python.generation_session_inputs import compare_api_session, generate_product

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/binding/session-discriminator-forward.json"
EXPECTED = DATA / "expected/main/generation_platform/session-review/split-discriminators"


@pytest.mark.parametrize("backend", list(DataModelType))
@pytest.mark.parametrize("enum_values", [False, True])
@pytest.mark.parametrize("collapse", [False, True])
def test_forward_discriminator_module_copy(backend: DataModelType, *, enum_values: bool, collapse: bool) -> None:
    """Import enum literal classes across modules and reject their qualified annotations explicitly."""
    config = GenerateConfig(
        input_file_type="openapi",
        openapi_scopes=[OpenAPIScope.Api],
        output_model_type=backend,
        module_split_mode="single",
        input_filename="forward.json",
        use_enum_values_in_discriminator=enum_values,
        use_subclass_enum=True,
        reuse_model=True,
        collapse_root_models=collapse,
        formatters=[],
        disable_timestamp=True,
    )
    source = SOURCE.with_name("session-discriminator-collapse.json") if collapse else SOURCE
    expected = EXPECTED / f"{backend.name}-{enum_values}{'-collapse' if collapse else ''}"
    product, retained = generate_product(source.resolve(), config)
    product.close()
    for artifact in product.artifacts:
        assert_output(artifact.content.decode(), expected / Path(*artifact.path))
    errors = require_type_bindings(product.batch, tuple(use.id for use in product.batch.type_uses))
    assert_output(
        "".join(f"{code}\n" for code in sorted({error.code for error in errors})),
        EXPECTED
        / ("qualified-enum-literal.txt" if enum_values and backend != DataModelType.TypingTypedDict else "valid.txt"),
    )
    if collapse:
        holder = tuple(
            use.id for use in product.batch.type_uses if use.id.schema_site.pointer == "/components/schemas/Holder"
        )
        assert_output(
            "\n".join(error.code for error in require_type_bindings(product.batch, holder)), EXPECTED / "valid.txt"
        )
    comparison = compare_api_session(source.resolve(), config)
    assert_output(
        f"bytes equal: {comparison['outputs_equal']}\n"
        f"engine calls equal: {comparison['engine_calls_equal']}\n"
        f"factory reads: {comparison['factory_reads']}\n"
        f"retained parsers: {comparison['retained_parsers']}\n"
        f"retained graph: {retained}\n",
        EXPECTED / "parity.txt",
    )
