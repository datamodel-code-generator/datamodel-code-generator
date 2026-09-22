"""Preserve media encoding applicability and ignored header identities in every scope."""

from __future__ import annotations

from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType, GenerateConfig, OpenAPIScope
from tests.conftest import assert_output
from tests.data.python.generation_contract_consumers import declarations
from tests.data.python.generation_session_inputs import generate_product

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/binding"
EXPECTED = DATA / "expected/main/generation_platform"


@pytest.mark.parametrize("version", ["3.0.3", "3.2.0"])
@pytest.mark.parametrize("scope", [OpenAPIScope.Api, OpenAPIScope.Paths, OpenAPIScope.Schemas])
@pytest.mark.parametrize("backend", list(DataModelType))
def test_encoding_owner_and_media(version: str, scope: OpenAPIScope, backend: DataModelType) -> None:
    """Check literal OAS applicability decisions and fixed-main bytes without extra parsing."""
    product, retained = generate_product(
        (SOURCE / f"session-encoding-{version}.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[scope],
            output_model_type=backend,
            input_filename="encoding.json",
            formatters=[],
            disable_timestamp=True,
        ),
    )
    product.close()
    assert_output(
        product.artifacts[0].content.decode(),
        EXPECTED / "session-review/encoding" / f"{version}-{scope.value}-{backend.name}.py",
    )
    operation = product.batch.operations[0]
    assert_output(
        "".join(sorted(f"{item.source.pointer}: {item.reason}\n" for item in operation.ignored)),
        EXPECTED / "session-review/encoding" / f"ignored-{version}.txt",
    )
    roots = (*operation.parameters, operation.request_body, *operation.responses)
    assert_output(
        "".join(
            sorted(f"{item.declaration.location.pointer}\n" for item in declarations(roots) if item.kind == "header")
        ),
        EXPECTED / "session-review/encoding" / f"headers-{version}.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")
    assert_output(
        "".join(
            f"{use.name}: {use.role} / {use.direction}\n"
            for binding in product.batch.type_uses
            for use in (binding.id,)
            if use.role.endswith("encoding_header")
        ),
        EXPECTED / "session-review/encoding" / f"directions-{version}.txt",
    )
