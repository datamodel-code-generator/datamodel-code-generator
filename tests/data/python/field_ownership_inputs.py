"""Snapshot actual final graph identities for field ownership E2E inputs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamodel_code_generator._generation_contract import AttemptId, FieldSlot, SymbolId
from datamodel_code_generator.model.binding_fields import DeclaredField, FieldOwner

if TYPE_CHECKING:
    from datamodel_code_generator.model.binding import ExpectedFieldDeclaration
    from datamodel_code_generator.parser.openapi_contract import ContractApiOpenAPIParser


def field_owners(parser: ContractApiOpenAPIParser) -> tuple[dict[SymbolId, FieldOwner], dict[SymbolId, str]]:
    """Read direct attributes without calling inheritance, field or import getters."""
    symbols = {id(model.reference): SymbolId(index) for index, model in enumerate(parser.results)}
    owners: dict[SymbolId, FieldOwner] = {}
    names: dict[SymbolId, str] = {}
    for index, model in enumerate(parser.results):
        symbol = SymbolId(index)
        names[symbol] = model.name
        owners[symbol] = FieldOwner(
            symbol,
            model.reference.path,
            tuple(symbols[id(base.reference)] for base in model.base_classes if base.reference is not None),
            tuple(
                DeclaredField(
                    FieldSlot(AttemptId(1), symbol, parser.binding_ledger.identity(field), ordinal, str(field.name)),
                    field.original_name if field.original_name is not None else str(field.name),
                )
                for ordinal, field in enumerate(model.fields)
            ),
        )
    return owners, names


def functional_leaf_expectations(parser: ContractApiOpenAPIParser) -> tuple[ExpectedFieldDeclaration, ...]:
    """Prepare real inherited field ownership without merging by normalized identifier."""
    from datamodel_code_generator import DataModelType
    from datamodel_code_generator.model.binding_fields import FieldOwnershipIndex
    from tests.data.python.binding_inputs import projected_field_expectations

    owners, names = field_owners(parser)
    declarations = {
        declaration.slot: declaration
        for name in names.values()
        for declaration in projected_field_expectations(parser, name, DataModelType.TypingTypedDict)
    }
    leaf = next(symbol for symbol, name in names.items() if name == "Leaf")
    projection = FieldOwnershipIndex(owners).project(leaf, functional_typeddict=True)
    view = next(value for value in (projection.value,) if value is not None)
    return view.functional_declarations("Leaf", declarations)
