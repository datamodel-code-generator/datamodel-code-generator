"""Snapshot actual final graph identities for field ownership E2E inputs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamodel_code_generator._generation_contract import AttemptId, FieldSlot, SymbolId
from datamodel_code_generator.model.binding_fields import DeclaredField, FieldOwner

if TYPE_CHECKING:
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
