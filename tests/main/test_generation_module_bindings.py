"""Reject module control-flow overwrites while preserving unrelated and local bindings."""

from __future__ import annotations

import json
from operator import itemgetter
from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType, GenerateConfig, OpenAPIScope
from datamodel_code_generator._openapi_type_binding import require_type_bindings
from tests.conftest import assert_output
from tests.data.python.generation_session_inputs import generate_product

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/binding"
EXPECTED = DATA / "expected/main/generation_platform/session-review/module-writes"


@pytest.mark.parametrize("backend", list(DataModelType))
@pytest.mark.parametrize("case", json.loads((SOURCE / "module-binding-writes.json").read_text()), ids=itemgetter("id"))
def test_module_binding_writes(backend: DataModelType, case: dict[str, object]) -> None:
    """Keep a changed model unusable even when the overwrite occurs inside an executable suite."""
    product, retained = generate_product(
        (SOURCE / "session-module-writes.json").resolve(),
        GenerateConfig(
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Api],
            output_model_type=backend,
            formatters=[],
            disable_timestamp=True,
        ),
        artifact_append=case["append"],
    )
    product.close()
    rejected = {
        use.id.schema_site.pointer.rsplit("/", 1)[-1]: bool(require_type_bindings(product.batch, (use.id,)))
        for use in product.batch.type_uses
        if use.id.schema_site.pointer.count("/") == 3
    }
    assert_output(
        json.dumps({**rejected, "retained_graph": retained}, indent=2) + "\n",
        EXPECTED / case.get("expected", f"{case['rejected']}.txt"),
    )
