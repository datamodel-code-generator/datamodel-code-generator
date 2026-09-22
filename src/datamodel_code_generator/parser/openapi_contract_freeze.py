"""Project the actual emitted model inventory into attempt-owned immutable values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from datamodel_code_generator._binding_imports import FinalImportResolver, freeze_imports
from datamodel_code_generator._generation_contract import (
    FieldSlot,
    GeneratedEnumMember,
    GeneratedTypeContractBatch,
    GraphObjectId,
    ModelArtifactAddress,
    SymbolId,
)
from datamodel_code_generator.model.binding import freeze_reference_policy
from datamodel_code_generator.model.binding_policies import final_field_name
from datamodel_code_generator.model.enum import Enum
from datamodel_code_generator.parser.openapi_contract_store import (
    FieldCopy,
    _type_recipe,  # pyright: ignore[reportPrivateUsage]
)
from datamodel_code_generator.parser.openapi_contract_types import FinalTypeProjector, ReferenceTypeBinding


@dataclass(frozen=True, slots=True)
class FinalFieldType:
    """Retain an actual own-field identity and final pure type projection."""

    slot: FieldSlot
    wire_name: str | None
    projection: TypeProjection


@dataclass(frozen=True, slots=True)
class FinalModelIdentity:
    """Freeze final graph identities without retaining a model, field, or reference."""

    symbol: SymbolId
    model: GraphObjectId
    reference: GraphObjectId
    reference_path: str
    name: str
    order: int
    bases: tuple[SymbolId, ...]
    unknown_bases: tuple[GraphObjectId, ...]
    fields: tuple[FinalFieldType, ...]
    is_alias: bool
    nullable: bool
    is_enum: bool
    artifact: ModelArtifactAddress | None
    artifact_reason: Literal["BND_ARTIFACT_AMBIGUOUS", "BND_SYMBOL_NOT_EMITTED"] | None


@dataclass(frozen=True, slots=True)
class FinalModuleImports:
    """Keep effective module import identities, including independently aliased names."""

    models: tuple[SymbolId, ...]
    values: tuple[Import, ...]
    symbol_names: tuple[tuple[SymbolId, str], ...] = ()


@dataclass(frozen=True, slots=True)
class FinalModelInventory:
    """Contain graph-free identities and types for subsequent field and use assembly."""

    attempt: AttemptId
    models: tuple[FinalModelIdentity, ...]
    imports: tuple[FinalModuleImports, ...]
    references: tuple[tuple[GraphObjectId, ReferenceTypeBinding], ...]
    declaration_references: tuple[tuple[GraphObjectId, ReferenceTypeBinding], ...] = ()
    import_sources: tuple[Import, ...] = ()


def _artifact_address(binding: ModuleResultBinding, output: Path, model_package: str) -> ModelArtifactAddress | None:
    if (primary := binding.primary) is None:
        return None
    return ModelArtifactAddress(
        primary,
        (output.name,) if primary == "single" else primary,
        model_package,
        binding.secondary,
    )


def _module_symbol_names(
    parser: openapi_contract.BindingCaptureMixin,
    models: tuple[DataModel, ...],
    symbols: dict[GraphObjectId, SymbolId],
    references: dict[GraphObjectId, ReferenceTypeBinding],
) -> tuple[tuple[SymbolId, str], ...]:
    """Keep the actual final aliases used for generated references in this module."""
    ledger = parser.binding_ledger
    names = dict.fromkeys(
        (symbols[ledger.identity(model)], model.reference.name.rsplit(".", 1)[-1]) for model in models
    )
    pending = [field.data_type for model in models for field in model.fields]
    seen: set[int] = set()
    while pending:
        data_type = pending.pop()
        if id(data_type) in seen:
            continue
        seen.add(id(data_type))
        if (reference := data_type.reference) is not None and (
            binding := references.get(ledger.identity(reference))
        ) is not None:
            name = data_type.alias or reference.name.rsplit(".", 1)[-1]
            if len(name) > 1 and name[0] == name[-1] and name[0] in {"'", '"'}:
                name = name[1:-1]
            names[binding.symbol, name] = None
        pending.extend(data_type.data_types)
        if data_type.dict_key is not None:
            pending.append(data_type.dict_key)
    return tuple(names)


def _reference_terminals(
    parser: openapi_contract.BindingCaptureMixin, emitted: set[GraphObjectId], *, declarations: bool = False
) -> dict[GraphObjectId, GraphObjectId]:
    """Follow completed global redirects; contextual or ambiguous edges never guess a winner."""
    redirects: dict[GraphObjectId, set[GraphObjectId]] = {}
    owners = {registration.model: registration.reference for registration in parser.binding_ledger.registrations}
    for replacement in parser.binding_ledger.replacements:
        declaration_owner = (
            declarations
            and replacement.kind == "scoped_reference"
            and replacement.owner is not None
            and owners.get(replacement.owner) == replacement.original
        )
        if (
            (replacement.kind == "reference" or declaration_owner)
            and replacement.original is not None
            and replacement.replacement is not None
        ):
            redirects.setdefault(replacement.original, set()).add(replacement.replacement)
    resolved: dict[GraphObjectId, GraphObjectId] = {reference: reference for reference in emitted}
    for original in redirects:
        pending = [original]
        seen: set[GraphObjectId] = set()
        terminals: set[GraphObjectId] = set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            if current in emitted:
                terminals.add(current)
            else:
                pending.extend(redirects.get(current, ()))
        if len(terminals) == 1:
            resolved[original] = next(iter(terminals))
    return resolved


def _final_projector(
    parser: openapi_contract.BindingCaptureMixin,
    models: tuple[DataModel, ...],
    symbols: dict[GraphObjectId, SymbolId],
    policies: dict[GraphObjectId, FinalReferencePolicy],
) -> tuple[FinalTypeProjector, dict[GraphObjectId, ReferenceTypeBinding]]:
    ledger = parser.binding_ledger
    references = {
        ledger.identity(model.reference): ReferenceTypeBinding(
            symbols[ledger.identity(model)],
            policies[ledger.identity(model)].nullable,
            policies[ledger.identity(model)].is_alias,
            policies[ledger.identity(model)].serialize_as_any,
        )
        for model in models
    }
    emitted = set(references)
    terminals = _reference_terminals(parser, emitted)
    references.update((original, references[terminal]) for original, terminal in terminals.items())
    enum_fields = {
        ledger.identity(model.reference): {
            field.name: GeneratedEnumMember(symbols[ledger.identity(model)], ledger.identity(field), field.name or "")
            for field in model.fields
        }
        for model in models
        if isinstance(model, Enum)
    }
    members: dict[GraphObjectId, tuple[GeneratedEnumMember, ...]] = {}
    for observation in parser.discriminator_types:
        if observation.reference is None or (terminal := terminals.get(observation.reference)) is None:
            continue
        actual = enum_fields.get(terminal, {})
        names = tuple(name for _, name in observation.data_type.enum_member_literals)
        if names and all(name in actual for name in names):
            members[ledger.identity(observation.data_type)] = tuple(actual[name] for name in names)
    for target, source in ledger.enum_copies.items():
        if source in members:
            members[target] = members[source]
    unresolved = frozenset(
        ledger.identity(reference)
        for key in parser.unresolved_references
        if (reference := parser.model_resolver.references.get(key)) is not None
    ) | frozenset(
        ledger.identity(model.reference) for model in models if model.reference.path in parser.unresolved_references
    )
    return FinalTypeProjector(
        references, members, ledger.root_recipes, unresolved, _unresolved_nodes(parser, unresolved, emitted)
    ), references


def _field_dependencies(
    parser: openapi_contract.BindingCaptureMixin, emitted: set[GraphObjectId]
) -> dict[GraphObjectId, list[GraphObjectId]]:
    """Connect observed nested types to original fields and removed declarations."""
    ledger = parser.binding_ledger
    edges: dict[GraphObjectId, list[GraphObjectId]] = {}
    pending_types = [observation.data_type for observation in parser.type_observations]
    for construction in parser.field_constructions.values():
        edges.setdefault(ledger.identity(construction.data_type), []).append(construction.field)
        pending_types.append(construction.data_type)
    seen_types: set[GraphObjectId] = set()
    while pending_types:
        data_type = pending_types.pop()
        node = ledger.identity(data_type)
        if node in seen_types:
            continue
        seen_types.add(node)
        if data_type.reference is not None:
            edges.setdefault(ledger.identity(data_type.reference), []).append(node)
        children = (
            (*data_type.data_types, data_type.dict_key) if data_type.dict_key is not None else data_type.data_types
        )
        for child in children:
            edges.setdefault(ledger.identity(child), []).append(node)
        pending_types.extend(children)
    for registration in ledger.registrations:
        if registration.reference not in emitted:
            for field in registration.fields:
                edges.setdefault(field, []).append(registration.reference)
    return edges


def _unresolved_nodes(
    parser: openapi_contract.BindingCaptureMixin, unresolved: frozenset[GraphObjectId], emitted: set[GraphObjectId]
) -> frozenset[GraphObjectId]:
    """Propagate actual failed-reference identities through completed copy/collapse edges."""
    if not unresolved:
        return frozenset()
    ledger = parser.binding_ledger
    edges = _field_dependencies(parser, emitted)
    for copy in ledger.copies:
        for source in copy.sources if isinstance(copy, FieldCopy) else (copy.source,):
            edges.setdefault(source, []).append(copy.target)
    for collapse in ledger.collapses:
        targets = (collapse.owner, collapse.original, collapse.replacement)
        edges.setdefault(ledger.identity(collapse.reference), []).extend(targets)
        pending = [collapse.recipe]
        while pending:
            recipe = pending.pop()
            edges.setdefault(recipe.node, []).extend(targets)
            if recipe.reference is not None:
                edges.setdefault(recipe.reference, []).extend(targets)
            pending.extend(recipe.data_types)
            if recipe.dict_key is not None:
                pending.append(recipe.dict_key)
    nodes: set[GraphObjectId] = set()
    pending_nodes = [
        *unresolved,
        *(
            ledger.identity(observation.data_type)
            for observation in parser.type_observations
            if any(reference in parser.unresolved_references for reference in observation.resolved_refs)
        ),
    ]
    while pending_nodes:
        node = pending_nodes.pop()
        if node in nodes:
            continue
        nodes.add(node)
        pending_nodes.extend(edges.get(node, ()))
    return frozenset(nodes)


def _freeze_inventory(  # ruff: ignore[too-many-locals] -- One bounded pass joins final identities, namespaces, and own fields.
    parser: openapi_contract.BindingCaptureMixin,
    results: str | dict[tuple[str, ...], Result],
    *,
    output: Path,
    model_package: str,
) -> tuple[FinalModelInventory, FinalTypeProjector]:
    """Freeze only models belonging to actual module returns, after ordinary rendering."""
    ledger = parser.binding_ledger
    outputs = tuple(observation for observation in parser.module_outputs if observation.models)
    models = tuple(model for observation in outputs for model in observation.models)
    symbols = {ledger.identity(model): SymbolId(index) for index, model in enumerate(models)}
    manager = parser.data_type_manager
    serialize_as_any = manager.use_serialize_as_any and manager.data_type.SUPPORTS_SERIALIZE_AS_ANY
    policies = {
        ledger.identity(model): freeze_reference_policy(model, serialize_as_any=serialize_as_any) for model in models
    }
    projector, references = _final_projector(parser, models, symbols, policies)
    addresses: dict[
        GraphObjectId,
        tuple[ModelArtifactAddress | None, Literal["BND_ARTIFACT_AMBIGUOUS", "BND_SYMBOL_NOT_EMITTED"] | None],
    ] = {
        model: (address, binding.reason)
        for binding in parser.resolve_module_results(results)
        for address in (_artifact_address(binding, output, model_package),)
        for model in binding.models
    }
    module_imports = tuple(
        FinalModuleImports(
            tuple(symbols[ledger.identity(model)] for model in observation.models),
            freeze_imports(observation.imports),
            _module_symbol_names(parser, observation.models, symbols, references),
        )
        for observation in outputs
    )
    import_resolvers = {
        symbol: resolver
        for module in module_imports
        for resolver in (FinalImportResolver(module.values, parser.config.import_overrides),)
        for symbol in module.models
    }
    frozen: list[FinalModelIdentity] = []
    for model in models:
        identity = ledger.identity(model)
        symbol = symbols[identity]
        bases = tuple(ledger.identity(base.reference) for base in model.base_classes if base.reference is not None)
        address, reason = addresses[identity]
        frozen.append(
            FinalModelIdentity(
                symbol,
                identity,
                ledger.identity(model.reference),
                model.reference.path,
                model.reference.name.rsplit(".", 1)[-1],
                len(frozen),
                tuple(references[base].symbol for base in bases if base in references),
                tuple(base for base in bases if base not in references),
                tuple(
                    FinalFieldType(
                        FieldSlot(
                            ledger.attempt_id,
                            symbol,
                            ledger.identity(field),
                            index,
                            final_field_name(model, field.name),
                        ),
                        field.original_name,
                        import_resolvers[symbol].project(
                            projector.project_field(
                                ledger.identity(field), _type_recipe(field.data_type, ledger, set())
                            )
                        ),
                    )
                    for index, field in enumerate(model.fields)
                ),
                policies[identity].is_alias,
                policies[identity].nullable,
                isinstance(model, Enum),
                address,
                reason,
            )
        )
    declaration_references = {
        original: references[terminal]
        for original, terminal in _reference_terminals(
            parser, {ledger.identity(model.reference) for model in models}, declarations=True
        ).items()
    }
    return FinalModelInventory(
        ledger.attempt_id,
        tuple(frozen),
        module_imports,
        tuple(references.items()),
        tuple(declaration_references.items()),
        tuple(dict.fromkeys(source for observation in outputs for source in observation.import_sources)),
    ), projector


def freeze_model_inventory(
    parser: openapi_contract.BindingCaptureMixin,
    results: str | dict[tuple[str, ...], Result],
    *,
    output: Path,
    model_package: str,
) -> FinalModelInventory:
    """Freeze real emitted identities independently of later field and use demands."""
    return _freeze_inventory(parser, results, output=output, model_package=model_package)[0]


@dataclass(frozen=True, slots=True)
class FrozenGenerationAttempt:
    """Retain completed value contracts and accepted-artifact evidence outside the graph."""

    batch: GeneratedTypeContractBatch
    artifacts: tuple[FinalArtifactBinding, ...]


def freeze_generation_attempt(
    parser: openapi_contract.BindingCaptureMixin,
    results: str | dict[tuple[str, ...], Result],
    *,
    output: Path,
    model_package: str,
    root_selector_document: str,
) -> FrozenGenerationAttempt:
    """Freeze one completed ordinary parse without deciding which retry the driver accepts."""
    from datamodel_code_generator.parser.openapi_contract import ContractApiOpenAPIParser  # noqa: PLC0415
    from datamodel_code_generator.parser.openapi_contract_fields import FinalFieldBuilder  # noqa: PLC0415
    from datamodel_code_generator.parser.openapi_contract_legacy import LegacyFinalOperationBuilder  # noqa: PLC0415
    from datamodel_code_generator.parser.openapi_contract_operations import FinalOperationBuilder  # noqa: PLC0415

    inventory, projector = _freeze_inventory(parser, results, output=output, model_package=model_package)
    fields = FinalFieldBuilder(parser, inventory, projector).freeze()
    operations = (
        FinalOperationBuilder(parser, inventory, fields, projector).freeze()
        if isinstance(parser, ContractApiOpenAPIParser)
        else LegacyFinalOperationBuilder(parser, inventory, fields, projector).freeze()
    )
    batch = GeneratedTypeContractBatch(
        inventory.attempt,
        root_selector_document,
        parser.source_lease.documents(),
        operations.operations,
        operations.uses,
        fields.symbols,
        tuple(artifact.address for artifact in fields.artifacts),
        fields.bindings,
        (*fields.diagnostics, *operations.diagnostics),
        operations.security_schemes,
        isinstance(parser, ContractApiOpenAPIParser),
    )
    return FrozenGenerationAttempt(batch, fields.artifacts)


if TYPE_CHECKING:
    from pathlib import Path

    from datamodel_code_generator._generation_contract import AttemptId, ModuleResultBinding, TypeProjection
    from datamodel_code_generator.imports import Import
    from datamodel_code_generator.model.base import DataModel
    from datamodel_code_generator.model.binding import FinalReferencePolicy
    from datamodel_code_generator.parser import openapi_contract
    from datamodel_code_generator.parser.base import Result
    from datamodel_code_generator.parser.openapi_contract_fields import FinalArtifactBinding
