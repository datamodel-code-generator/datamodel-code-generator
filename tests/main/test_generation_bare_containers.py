"""Bind actual bare containers without inventing element types after recursive-array reduction."""

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
SOURCE = DATA / "generation_platform/binding"
EXPECTED = DATA / "expected/main/generation_platform"


@pytest.mark.parametrize("backend", list(DataModelType))
@pytest.mark.parametrize("collapse", [False, True])
@pytest.mark.parametrize("standard", [False, True])
@pytest.mark.parametrize("generic", [False, True])
def test_recursive_bare_array(backend: DataModelType, *, collapse: bool, standard: bool, generic: bool) -> None:
    """Preserve the engine's bare list/Sequence and every accepted source/artifact identity."""
    product, retained = generate_product(
        (SOURCE / "session-self-array.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            input_filename="self-array.json",
            collapse_root_models=collapse,
            use_standard_collections=standard,
            use_generic_container_types=generic,
            formatters=[],
            disable_timestamp=True,
        ),
    )
    product.close()
    assert_output(
        product.artifacts[0].content.decode(),
        EXPECTED / "session-review/self-array" / f"{backend.name}-{standard}-{generic}.py",
    )
    assert_output(
        "\n".join(
            error.code
            for error in require_type_bindings(product.batch, tuple(use.id for use in product.batch.type_uses))
        ),
        EXPECTED / "session-review/no-diagnostics.txt",
    )
    assert_output(
        json.dumps([type_snapshot(field.model_facts.type) for field in product.batch.fields], indent=2) + "\n",
        EXPECTED / "session-review/self-array" / f"types-{standard}-{generic}.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")


@pytest.mark.parametrize("backend", list(DataModelType))
@pytest.mark.parametrize("replacement", json.loads((SOURCE / "bare-container-artifacts.json").read_text()))
def test_bare_array_rejects_changed_container(backend: DataModelType, replacement: str) -> None:
    """Reject inserted element arguments, empty-tuple syntax, and changed container identities."""
    product, retained = generate_product(
        (SOURCE / "session-self-array.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            use_standard_collections=True,
            formatters=[],
            disable_timestamp=True,
        ),
        artifact_rewrite=("list", replacement),
    )
    product.close()
    errors = require_type_bindings(product.batch, tuple(use.id for use in product.batch.type_uses))
    assert_output(
        "\n".join(sorted({error.code for error in errors})) + "\n",
        EXPECTED / "session-review/unsupported-artifact.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")
