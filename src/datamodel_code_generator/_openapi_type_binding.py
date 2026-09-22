"""Pure demand checks over one immutable accepted model-generation batch."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from datamodel_code_generator._generation_contract import (
    AnnotatedType,
    BindingDiagnostic,
    GeneratedEnumMember,
    GeneratedSymbolType,
    GenericType,
    LiteralType,
    OperationId,
    UnionType,
)


def _type_symbols(value: FinalPythonType) -> Iterator[SymbolId]:
    match value:
        case GeneratedSymbolType(symbol):
            yield symbol
        case GenericType(base, arguments):
            yield from _type_symbols(base)
            for argument in arguments:
                yield from _type_symbols(argument)
        case UnionType(members):
            for member in members:
                yield from _type_symbols(member)
        case AnnotatedType(base):
            yield from _type_symbols(base)
        case LiteralType(values):
            for member in values:
                if isinstance(member, GeneratedEnumMember):
                    yield member.symbol
        case _:
            return


def _required_models(
    binding: TypeUseBinding,
    symbols: dict[SymbolId, FinalModelSymbol],
    fields: dict[SymbolId, list[FieldUseBinding]],
) -> set[SymbolId]:
    pending = list(_type_symbols(binding.type)) if binding.type is not None else []
    pending.extend(slot.symbol for slot in binding.producers)
    required: set[SymbolId] = set()
    while pending:
        symbol = pending.pop()
        if symbol in required:
            continue
        required.add(symbol)
        if (model := symbols.get(symbol)) is None:
            continue
        pending.extend(model.bases)
        if model.facts is not None and model.facts.extra_items is not None:
            pending.extend(_type_symbols(model.facts.extra_items))
        for field in fields.get(symbol, ()):
            if field.model_facts is not None and field.exclusion is None:
                pending.extend(_type_symbols(field.model_facts.type))
    return required


def _member_diagnostics(
    batch: GeneratedTypeContractBatch, members: tuple[FieldUseBinding, ...]
) -> Iterator[BindingDiagnostic]:
    for member in members:
        if member.slot is not None and member.slot.attempt != batch.attempt:
            yield BindingDiagnostic("BND_ATTEMPT_MISMATCH")
        if member.exclusion is not None:
            continue
        if member.origin_state != "known":
            yield BindingDiagnostic(
                "BND_FIELD_ORIGIN_UNRESOLVED",
                source_locations=tuple(origin.location for origin in member.occurrences),
                details=(("reason", member.origin_reason),),
            )
        if member.slot is None or member.model_facts is None:
            yield BindingDiagnostic("BND_FIELD_UNRESOLVED")


def _related(
    diagnostic: BindingDiagnostic,
    use: TypeUseId,
    required: set[SymbolId],
    symbols: dict[SymbolId, FinalModelSymbol],
    fields: tuple[FieldUseBinding, ...],
) -> bool:
    if diagnostic.type_use is not None or diagnostic.operation is not None:
        return diagnostic.type_use == use if diagnostic.type_use is not None else diagnostic.operation == use.owner
    details = dict(diagnostic.details)
    if "symbol" in details:
        return details["symbol"] in required
    if "field" in details:
        return any(field.slot is not None and field.slot.field == details["field"] for field in fields)
    if "artifact" in details:
        return any(
            (model := symbols.get(symbol)) is not None
            and (artifact := model.artifact) is not None
            and details["artifact"]
            in {"/".join(path) for path in (artifact.relative_path, *artifact.secondary_definitions)}
            for symbol in required
        )
    if diagnostic.source_locations:
        return any(
            source in {use.use_site, use.schema_site, use.declaration.location}
            for source in diagnostic.source_locations
        )
    return True


def require_type_bindings(
    batch: GeneratedTypeContractBatch, use_ids: tuple[TypeUseId, ...]
) -> tuple[BindingDiagnostic, ...]:
    """Report missing or invalid requested bindings without generating another model."""
    bindings = {binding.id: binding for binding in batch.type_uses}
    symbols = {symbol.id: symbol for symbol in batch.symbols}
    fields: dict[SymbolId, list[FieldUseBinding]] = {}
    for field in batch.fields:
        fields.setdefault(field.consumer, []).append(field)
    diagnostics: list[BindingDiagnostic] = []
    for use in dict.fromkeys(use_ids):
        binding = bindings.get(use)
        if binding is None or binding.state == "not_generated":
            diagnostics.append(
                BindingDiagnostic(
                    "BND_UNRESOLVED_REFERENCE" if batch.api_scope else "BND_MODEL_SCOPE_REQUIRED",
                    type_use=use,
                    source_locations=(use.use_site,),
                )
            )
            continue
        if binding.state == "invalid":
            diagnostics.append(
                BindingDiagnostic(
                    binding.reason or "BND_UNRESOLVED_REFERENCE", type_use=use, source_locations=(use.use_site,)
                )
            )
            continue
        required = _required_models(binding, symbols, fields)
        members = (*binding.members, *(field for symbol in required for field in fields.get(symbol, ())))
        failures = list(_member_diagnostics(batch, members))
        if any(slot.attempt != batch.attempt for slot in binding.producers):
            failures.append(BindingDiagnostic("BND_ATTEMPT_MISMATCH"))
        for symbol in required:
            if (model := symbols.get(symbol)) is None or model.artifact is None:
                failures.append(BindingDiagnostic("BND_SYMBOL_NOT_EMITTED", details=(("symbol", symbol),)))
            elif any(slot.attempt != batch.attempt for slot in model.fields):
                failures.append(BindingDiagnostic("BND_ATTEMPT_MISMATCH"))
        failures.extend(
            diagnostic for diagnostic in batch.diagnostics if _related(diagnostic, use, required, symbols, members)
        )
        diagnostics.extend(
            replace(
                diagnostic,
                type_use=use,
                operation=use.owner if isinstance(use.owner, OperationId) else None,
                source_locations=diagnostic.source_locations or (use.use_site,),
            )
            for diagnostic in failures
        )
    return tuple(dict.fromkeys(diagnostics))


if TYPE_CHECKING:
    from collections.abc import Iterator

    from datamodel_code_generator._generation_contract import (
        FieldUseBinding,
        FinalModelSymbol,
        FinalPythonType,
        GeneratedTypeContractBatch,
        SymbolId,
        TypeUseBinding,
        TypeUseId,
    )
