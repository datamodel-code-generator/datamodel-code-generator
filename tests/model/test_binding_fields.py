"""Refuse field ownership that a damaged final inventory cannot prove, after a real model generation."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType
from datamodel_code_generator._generation_contract import AttemptId
from datamodel_code_generator.model.binding_fields import FieldOwnershipIndex
from datamodel_code_generator.parser.openapi_contract import ContractApiOpenAPIParser
from tests.conftest import assert_output
from tests.data.python.binding_inputs import builtin_binding_config
from tests.data.python.binding_type_snapshot import ownership_snapshot
from tests.data.python.field_ownership_inputs import field_owners

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/binding"
EXPECTED = DATA / "expected/main/generation_platform/binding"


@pytest.mark.parametrize("change", json.loads((SOURCE / "invalid-field-ownership.json").read_text()))
def test_unresolved_field_ownership_is_not_invented(change: dict[str, str]) -> None:
    """Refuse ownership through a base the final inventory does not know, after a real generation."""
    parser = ContractApiOpenAPIParser(
        SOURCE / "field-ownership.json",
        attempt_id=AttemptId(1),
        config=builtin_binding_config(DataModelType.PydanticV2BaseModel),
    )
    try:
        parser.parse()
        owners, names = field_owners(parser)
        symbols = {name: symbol for symbol, name in names.items()}
        owners[symbols["A"]] = replace(owners[symbols["A"]], unknown_bases=True)
        projection = FieldOwnershipIndex(owners).project(symbols["Leaf"])
        assert_output(
            json.dumps(ownership_snapshot(projection, names), indent=2) + "\n",
            EXPECTED / f"field-ownership-{change['reason']}.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()
