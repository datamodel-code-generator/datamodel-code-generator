"""Bind or explicitly report contract edges while ordinary generation keeps fixed-main bytes."""

from __future__ import annotations

import json
from contextlib import nullcontext
from operator import itemgetter
from pathlib import Path
from typing import Any

import pytest

from datamodel_code_generator import DanglingRefWarning, GenerateConfig
from tests.conftest import assert_output
from tests.data.python.binding_provenance_failures import producer_fault
from tests.data.python.generation_session_inputs import generate_product

DATA = Path(__file__).parents[1] / "data"
EXPECTED = DATA / "expected/main/generation_platform"


@pytest.mark.parametrize(
    "case", json.loads((DATA / "generation_platform/binding/contract-edges.json").read_text()), ids=itemgetter("id")
)
def test_contract_edges(case: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ordinary bytes while every type use is bound or carries its explicit reason."""
    config = GenerateConfig(
        input_file_type="openapi",
        input_filename=Path(case["source"]).name,
        formatters=[],
        disable_timestamp=True,
        **case["options"],
    )
    with (
        pytest.warns(DanglingRefWarning) if case.get("dangling") else nullcontext(),
        producer_fault(case["fault"], monkeypatch) if "fault" in case else nullcontext(),
    ):
        product, retained = generate_product((DATA / case["source"]).resolve(), config)
    product.close()
    expected = EXPECTED / "session-review/contract-edges" / case["id"]
    for artifact in product.artifacts:
        assert_output(artifact.content.decode(), expected / Path(*artifact.path))
    batch = product.batch
    assert_output(
        "".join((
            *(
                f"use {use.id.role} {use.id.projection} {use.id.use_site.pointer}: {use.state} {use.reason}\n"
                for use in batch.type_uses
            ),
            *(
                f"diagnostic {diagnostic.code} {' '.join(site.pointer for site in diagnostic.source_locations)}\n"
                for diagnostic in batch.diagnostics
            ),
            *(
                f"ignored {ignored.use_site.pointer}: {ignored.reason}\n"
                for operation in batch.operations
                for ignored in operation.ignored
            ),
        )),
        expected / "contract.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")
