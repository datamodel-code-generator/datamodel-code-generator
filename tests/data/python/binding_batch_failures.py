"""Corrupt completed real batches to test demand ownership and diagnostic isolation."""

from dataclasses import replace

from datamodel_code_generator._generation_contract import (
    AttemptId,
    BindingDiagnostic,
    GeneratedSymbolType,
    GeneratedTypeContractBatch,
    TypeUseId,
)


def corrupt_batch(batch: GeneratedTypeContractBatch, case: str) -> tuple[GeneratedTypeContractBatch, TypeUseId]:
    """Inject one invalid identity or failed artifact without substituting a generation path."""
    binding = next(use for use in batch.type_uses if use.id.role == "request_body")
    requested = binding.id
    symbol = next(symbol for symbol in batch.symbols if symbol.name == "Item")
    member = next(field for field in batch.fields if field.consumer == symbol.id)
    slot = member.slot
    if slot is None:
        raise ValueError("The real input must emit an Item.value field")
    foreign_slot = replace(slot, attempt=AttemptId(batch.attempt + 1))
    diagnostic = BindingDiagnostic("BND_ARTIFACT_AMBIGUOUS")
    match case:
        case "missing_binding":
            return replace(batch, type_uses=tuple(use for use in batch.type_uses if use is not binding)), requested
        case "invalid_binding":
            binding = replace(binding, state="invalid", reason=None)
        case "member_attempt":
            binding = replace(binding, members=(replace(member, slot=foreign_slot),))
        case "excluded_member_attempt":
            binding = replace(binding, members=(replace(member, slot=foreign_slot, exclusion="read_only"),))
        case "missing_member":
            binding = replace(binding, members=(replace(member, slot=None, model_facts=None),))
        case "unknown_member_origin":
            binding = replace(binding, members=(replace(member, origin_state="unavailable", origin_reason="lost"),))
        case "helper_attempt":
            binding = replace(binding, producers=(foreign_slot,))
        case "symbol_attempt":
            batch = replace(
                batch,
                symbols=tuple(
                    replace(value, fields=(foreign_slot,)) if value is symbol else value for value in batch.symbols
                ),
            )
        case "missing_symbol":
            batch = replace(batch, symbols=tuple(value for value in batch.symbols if value is not symbol))
        case "missing_artifact":
            batch = replace(
                batch,
                symbols=tuple(replace(value, artifact=None) if value is symbol else value for value in batch.symbols),
            )
        case "foreign_symbol":
            binding = replace(binding, type=GeneratedSymbolType(type(symbol.id)(9999)), members=())
        case "use_diagnostic":
            diagnostic = replace(diagnostic, type_use=requested)
        case "other_use_diagnostic":
            diagnostic = replace(diagnostic, type_use=replace(requested, name="unrelated"))
        case "operation_diagnostic":
            diagnostic = replace(diagnostic, operation=batch.operations[0].id)
        case "other_operation_diagnostic":
            diagnostic = replace(diagnostic, operation=batch.operations[1].id)
        case "source_diagnostic":
            diagnostic = replace(diagnostic, source_locations=(requested.schema_site,))
        case "other_source_diagnostic":
            diagnostic = replace(diagnostic, source_locations=(replace(requested.schema_site, pointer="/unrelated"),))
        case "field_diagnostic":
            diagnostic = replace(diagnostic, details=(("field", slot.field),))
        case "artifact_diagnostic":
            diagnostic = replace(diagnostic, details=(("artifact", "models.py"),))
        case "global_diagnostic":
            pass
        case _:
            raise ValueError(case)
    if case.endswith("diagnostic"):
        batch = replace(batch, diagnostics=(*batch.diagnostics, diagnostic))
    return replace(
        batch, type_uses=tuple(binding if use.id == requested else use for use in batch.type_uses)
    ), requested
