"""Match accepted builtin declarations to actual field identities through real generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType, OpenAPIScope, PythonVersionMin
from datamodel_code_generator._generation_contract import AttemptId, BuiltinType, FieldSlot, GenericType, SymbolId
from datamodel_code_generator.imports import Import
from datamodel_code_generator.model import get_data_model_types
from datamodel_code_generator.model.binding import (
    ExpectedFieldDeclaration,
    FieldProjectionContext,
    FrozenImportBindings,
    freeze_none_default_provenance,
    index_builtin_field_declarations,
)
from datamodel_code_generator.parser.openapi_contract import ContractApiOpenAPIParser
from tests.conftest import assert_output
from tests.data.python.binding_type_snapshot import type_snapshot

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/binding"
EXPECTED = DATA / "expected/main/generation_platform/binding"


@pytest.mark.parametrize("total_false", [False, True])
def test_functional_typed_dict_field_index(*, total_false: bool) -> None:
    """Keep actual empty/escaped/Unicode keys and Required/ReadOnly placement."""
    types = get_data_model_types(DataModelType.TypingTypedDict, target_python_version=PythonVersionMin)
    parser = ContractApiOpenAPIParser(
        SOURCE / "functional-fields.json",
        attempt_id=AttemptId(1),
        data_model_type=types.data_model,
        data_model_root_type=types.root_model,
        data_model_field_type=types.field_model,
        data_type_manager_type=types.data_type_manager,
        dump_resolve_reference_action=types.dump_resolve_reference_action,
        openapi_scopes=[OpenAPIScope.Api],
        use_total_false_for_typed_dict=total_false,
        use_frozen_field=True,
        formatters=[],
    )
    try:
        body = str(parser.parse())
        assert_output(body, EXPECTED / f"functional-fields-{total_false}.py")
        model = parser.results[0]
        expected = tuple(
            ExpectedFieldDeclaration(
                AttemptId(1),
                SymbolId(0),
                FieldSlot(AttemptId(1), SymbolId(0), parser.binding_ledger.identity(field)),
                model.name,
                field.name,
                "typeddict",
                BuiltinType("str"),
                form="typeddict_entry",
                entry_key=field.original_name if field.original_name is not None else field.name,
                entry_ordinal=index,
            )
            for index, field in enumerate(model.fields)
        )
        index = index_builtin_field_declarations(
            body,
            expected=expected,
            imports=FrozenImportBindings((
                Import(import_="TypedDict", from_="typing_extensions" if total_false else "typing"),
                *(Import(import_=name, from_="typing_extensions") for name in ("ReadOnly", "Required", "NotRequired")),
            )),
        )
        assert_output(
            json.dumps(
                [
                    {
                        "key": field.expected.entry_key,
                        "ordinal": field.expected.entry_ordinal,
                        "annotation": field.annotation,
                        "assignment": field.assignment,
                        "facts": type_snapshot(field.facts),
                    }
                    for field in index.fields
                ],
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            EXPECTED / f"functional-fields-{total_false}.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()


@pytest.mark.parametrize("backend", list(DataModelType))
@pytest.mark.parametrize("annotated", [False, True])
def test_emitted_default_facts(backend: DataModelType, *, annotated: bool) -> None:
    """Observe real mutable-default factories, explicit null and builtin missing sentinels."""
    from tests.data.python.binding_inputs import binding_backend_name, builtin_binding_config, builtin_field_imports

    parser = ContractApiOpenAPIParser(
        SOURCE / "emitted-defaults.json",
        attempt_id=AttemptId(1),
        config=builtin_binding_config(backend, annotated=annotated, missing_sentinel=True),
    )
    try:
        body = str(parser.parse())
        assert_output(body, EXPECTED / f"emitted-defaults-{backend.name}-{annotated}.py")
        model = parser.results[0]
        field_types = {
            "missing": BuiltinType("str"),
            "explicit_null": BuiltinType("str"),
            "literal": BuiltinType("int"),
            "items": GenericType(BuiltinType("list"), (BuiltinType("int"),)),
            "mapping": GenericType(BuiltinType("dict"), (BuiltinType("str"), BuiltinType("bool"))),
            "factory": GenericType(BuiltinType("list"), (BuiltinType("str"),)),
            "required": BuiltinType("bool"),
        }
        expected = tuple(
            ExpectedFieldDeclaration(
                AttemptId(1),
                SymbolId(0),
                FieldSlot(AttemptId(1), SymbolId(0), parser.binding_ledger.identity(field)),
                model.name,
                field.name,
                binding_backend_name(backend),
                field_types[field.name],
            )
            for field in model.fields
        )
        field_module = "msgspec" if backend == DataModelType.MsgspecStruct else "dataclasses"
        index = index_builtin_field_declarations(
            body,
            expected=expected,
            imports=FrozenImportBindings((*builtin_field_imports(), Import(import_="field", from_=field_module))),
        )
        assert_output(
            json.dumps({field.expected.native_name: type_snapshot(field.facts) for field in index.fields}, indent=2)
            + "\n",
            EXPECTED / f"emitted-defaults-{backend.name}-{annotated}.txt",
        )
        fields_by_id = {parser.binding_ledger.identity(field): field for field in model.fields}
        provenance = {}
        for declaration in index.fields:
            slot = declaration.expected.slot
            origin = parser.field_origins[slot.field]
            projection = FieldProjectionContext(
                original_required=origin.required_by_node,
                schema_default=any(
                    isinstance(source.raw, dict) and "default" in source.raw for source in origin.origins
                ),
                explicit_model_default=False,
                explicit_nullable=False,
                preexisting_null=False,
                configuration_nullable=False,
                builtin_semantics=True,
            )
            provenance[declaration.expected.native_name] = type_snapshot(
                freeze_none_default_provenance(
                    fields_by_id[slot.field], emitted=declaration.facts, projection=projection
                )
            )
        assert_output(
            json.dumps(provenance, indent=2) + "\n",
            EXPECTED / f"none-provenance-{backend.name}-{annotated}.txt",
        )
    finally:
        parser.dispose()
        parser.source_lease.close()
