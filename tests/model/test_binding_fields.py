"""Verify final C3 and functional TypedDict ownership through real model generation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType
from datamodel_code_generator._generation_contract import AttemptId
from datamodel_code_generator.model.binding_fields import FieldOwnershipIndex
from datamodel_code_generator.parser.openapi_contract import ContractApiOpenAPIParser
from tests.conftest import assert_output
from tests.data.python.binding_engine_observer import BindingEngineObserver
from tests.data.python.binding_inputs import builtin_binding_config, projected_field_expectations
from tests.data.python.binding_type_snapshot import ownership_snapshot
from tests.data.python.field_ownership_inputs import field_owners

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/binding"
EXPECTED = DATA / "expected/main/generation_platform/binding"


@pytest.mark.parametrize("backend", list(DataModelType))
def test_final_declaring_field_ownership(backend: DataModelType) -> None:
    """Keep C3's first-base winner distinct from functional TypedDict's last raw-key winner."""
    chain = backend == DataModelType.MsgspecStruct
    functional = backend == DataModelType.TypingTypedDict
    parser = ContractApiOpenAPIParser(
        SOURCE / ("field-ownership-chain.json" if chain else "field-ownership.json"),
        attempt_id=AttemptId(1),
        config=builtin_binding_config(backend),
    )
    previous = sys.getprofile()
    try:
        assert_output(str(parser.parse()), EXPECTED / f"field-ownership-{backend.name}.py")
        observer = BindingEngineObserver()
        sys.setprofile(observer.record)
        try:
            owners, names = field_owners(parser)
            leaf = next(symbol for symbol, name in names.items() if name == "Leaf")
            index = FieldOwnershipIndex(owners)
            projection = index.project(leaf, functional_typeddict=functional)
            cached = index.project(leaf, functional_typeddict=functional) is projection
        finally:
            sys.setprofile(previous)
        policy = "functional" if functional else "chain" if chain else "class"
        assert_output(
            json.dumps(ownership_snapshot(projection, names), indent=2) + "\n",
            EXPECTED / f"field-ownership-{policy}.txt",
        )
        assert_output(json.dumps(cached) + "\n", EXPECTED / "field-ownership-cached.txt")
        assert_output(
            json.dumps({"additional_engine_calls": sum(observer.calls.values())}) + "\n",
            EXPECTED / "type-projection-calls.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize("change", json.loads((SOURCE / "invalid-field-ownership.json").read_text()))
def test_unresolved_field_ownership_is_not_invented(change: dict[str, str]) -> None:
    """Reject damaged final inventories after an otherwise successful real generation."""
    from dataclasses import replace

    parser = ContractApiOpenAPIParser(
        SOURCE / "field-ownership.json",
        attempt_id=AttemptId(1),
        config=builtin_binding_config(DataModelType.PydanticV2BaseModel),
    )
    try:
        assert_output(str(parser.parse()), EXPECTED / "field-ownership-PydanticV2BaseModel.py")
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
            case "cycle":
                owners[base] = replace(owners[base], bases=(leaf,))
            case "inconsistent_c3":
                owners[symbols["C"]] = replace(owners[symbols["C"]], bases=(symbols["B"], base))
            case _:  # The remaining external fixture duplicates an own field.
                owners[leaf] = replace(owners[leaf], fields=(*owners[leaf].fields, *owners[leaf].fields))
        projection = FieldOwnershipIndex(owners).project(leaf)
        assert_output(
            json.dumps(ownership_snapshot(projection, names), indent=2) + "\n",
            EXPECTED / f"field-ownership-{change['reason']}.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize("change", ["unchanged", "missing", "backend", "excluded"])
def test_functional_emissions_keep_declaring_slots(change: str) -> None:
    """Corroborate inherited entry order against actual builtin output, without extra getters."""
    from dataclasses import replace

    from datamodel_code_generator._generation_contract import BindingCaptureError
    from datamodel_code_generator.imports import Import
    from datamodel_code_generator.model.binding import FrozenImportBindings, index_builtin_field_declarations

    backend = DataModelType.TypingTypedDict
    parser = ContractApiOpenAPIParser(
        SOURCE / "field-ownership.json", attempt_id=AttemptId(1), config=builtin_binding_config(backend)
    )
    previous = sys.getprofile()
    try:
        body = str(parser.parse())
        assert_output(body, EXPECTED / "field-ownership-TypingTypedDict.py")
        observer = BindingEngineObserver()
        sys.setprofile(observer.record)
        try:
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
                case "excluded":
                    declarations[slot] = replace(declarations[slot], excluded_by_tag=True)
            ownership = FieldOwnershipIndex(owners)
            views = tuple(
                next(
                    value
                    for value in (ownership.project(symbol, functional_typeddict=True).value,)
                    if value is not None
                )
                for symbol in names
            )
            if change != "unchanged":
                with pytest.raises(BindingCaptureError, match="A functional TypedDict entry"):
                    views[0].functional_declarations(names[views[0].consumer], declarations)
                return
            expected = tuple(
                declaration
                for view in views
                for declaration in view.functional_declarations(names[view.consumer], declarations)
            )
            index = index_builtin_field_declarations(
                body,
                expected=expected,
                imports=FrozenImportBindings((
                    Import(import_="TypedDict", from_="typing"),
                    Import(import_="NotRequired", from_="typing_extensions"),
                )),
            )
        finally:
            sys.setprofile(previous)
        assert_output(
            json.dumps(
                [
                    {
                        "consumer": names[field.expected.consumer],
                        "owner": names[field.expected.slot.symbol],
                        "name": field.expected.slot.name,
                        "key": field.expected.entry_key,
                        "ordinal": field.expected.entry_ordinal,
                        "annotation": field.annotation,
                    }
                    for field in index.fields
                ],
                indent=2,
            )
            + "\n",
            EXPECTED / "field-ownership-emissions.txt",
        )
        assert_output(
            json.dumps({"additional_engine_calls": sum(observer.calls.values())}) + "\n",
            EXPECTED / "type-projection-calls.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


def test_functional_inherited_keys_keep_distinct_declaring_slots() -> None:
    """Keep different wire keys when inherited fields share one Python identifier."""
    from datamodel_code_generator.imports import Import
    from datamodel_code_generator.model.binding import FrozenImportBindings, index_builtin_field_declarations
    from tests.data.python.field_ownership_inputs import functional_leaf_expectations

    backend = DataModelType.TypingTypedDict
    parser = ContractApiOpenAPIParser(
        SOURCE / "functional-name-collision.json", attempt_id=AttemptId(1), config=builtin_binding_config(backend)
    )
    try:
        body = str(parser.parse())
        assert_output(body, EXPECTED / "functional-name-collision.py")
        expected = functional_leaf_expectations(parser)
        index = index_builtin_field_declarations(
            body, expected=expected, imports=FrozenImportBindings((Import(import_="TypedDict", from_="typing"),))
        )
        assert_output(
            json.dumps(
                [[field.expected.entry_key, field.expected.native_name, field.annotation] for field in index.fields],
                indent=2,
            )
            + "\n",
            EXPECTED / "functional-name-collision.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()
