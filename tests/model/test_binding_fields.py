"""Refuse field ownership that a damaged final inventory cannot prove, after a real model generation."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType
from datamodel_code_generator._generation_contract import AttemptId, BindingCaptureError
from datamodel_code_generator.model.binding_fields import FieldOwnershipIndex
from datamodel_code_generator.parser.openapi_contract import ContractApiOpenAPIParser
from tests.conftest import assert_output
from tests.data.python.binding_inputs import builtin_binding_config, projected_field_expectations
from tests.data.python.binding_type_snapshot import ownership_snapshot
from tests.data.python.field_ownership_inputs import field_owners

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/binding"
EXPECTED = DATA / "expected/main/generation_platform/binding"


@pytest.mark.parametrize("change", json.loads((SOURCE / "invalid-field-ownership.json").read_text()))
def test_unresolved_field_ownership_is_not_invented(change: dict[str, str]) -> None:
    """Reject damaged final inventories after an otherwise successful real generation."""
    parser = ContractApiOpenAPIParser(
        SOURCE / "field-ownership.json",
        attempt_id=AttemptId(1),
        config=builtin_binding_config(DataModelType.PydanticV2BaseModel),
    )
    try:
        parser.parse()
        owners, names = field_owners(parser)
        symbols = {name: symbol for symbol, name in names.items()}
        leaf, base = symbols["Leaf"], symbols["A"]
        match change["mutation"]:
            case "missing_consumer":
                del owners[leaf]
            case "missing_base":
                del owners[base]
            case "custom_base":
                owners[base] = replace(owners[base], unknown_bases=True)
            case _:
                owners[leaf] = replace(owners[leaf], fields=(*owners[leaf].fields, *owners[leaf].fields))
        projection = FieldOwnershipIndex(owners).project(leaf)
        assert_output(
            json.dumps(ownership_snapshot(projection, names), indent=2) + "\n",
            EXPECTED / f"field-ownership-{change['reason']}.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize("change", ["missing", "backend", "excluded"])
def test_functional_entries_need_their_declaring_fields(change: str) -> None:
    """Refuse a functional TypedDict entry whose declaring field is absent or has other semantics."""
    backend = DataModelType.TypingTypedDict
    parser = ContractApiOpenAPIParser(
        SOURCE / "field-ownership.json", attempt_id=AttemptId(1), config=builtin_binding_config(backend)
    )
    try:
        parser.parse()
        owners, names = field_owners(parser)
        declarations = {
            declaration.slot: declaration
            for name in names.values()
            for declaration in projected_field_expectations(parser, name, backend)
        }
        slot = next(iter(declarations))
        match change:
            case "missing":
                del declarations[slot]
            case "backend":
                declarations[slot] = replace(declarations[slot], backend="msgspec")
            case _:
                declarations[slot] = replace(declarations[slot], excluded_by_tag=True)
        symbol = next(iter(names))
        view = FieldOwnershipIndex(owners).project(symbol, functional_typeddict=True).value
        with pytest.raises(BindingCaptureError, match="A functional TypedDict entry"):
            view.functional_declarations(names[symbol], declarations)
    finally:
        parser.dispose()
        parser.source_lease.close()
