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
from tests.data.python.binding_type_snapshot import type_snapshot
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
    names = {symbol.id: symbol.name for symbol in batch.symbols}
    assert_output(
        json.dumps(
            {
                "uses": [
                    {
                        "use": f"{use.id.role} {use.id.projection} {use.id.use_site.pointer}",
                        "schema": use.id.schema_site.pointer,
                        "declaration": use.id.declaration.location.pointer,
                        "media": use.id.media,
                        "state": f"{use.state} {use.reason}",
                        "type": type_snapshot(use.type, names),
                    }
                    for use in batch.type_uses
                ],
                "diagnostics": [
                    f"{diagnostic.code} {' '.join(site.pointer for site in diagnostic.source_locations)}"
                    for diagnostic in batch.diagnostics
                ],
                "ignored": [
                    f"{ignored.use_site.pointer}: {ignored.reason}"
                    for operation in batch.operations
                    for ignored in operation.ignored
                ],
            },
            indent=2,
        )
        + "\n",
        expected / "contract.txt",
    )
    assert_output(f"{retained}\n", EXPECTED / "no-retained-graph.txt")
