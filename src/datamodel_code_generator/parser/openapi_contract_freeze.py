"""Project the actual emitted model inventory into attempt-owned immutable values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from datamodel_code_generator._binding_imports import FinalImportResolver
from datamodel_code_generator._generation_contract import (
    FieldSlot,
    GeneratedEnumMember,
    GraphObjectId,
    SymbolId,
)
from datamodel_code_generator.imports import Import
from datamodel_code_generator.model.binding import freeze_reference_policy
from datamodel_code_generator.model.enum import Enum
from datamodel_code_generator.parser.openapi_contract_store import _type_recipe  # pyright: ignore[reportPrivateUsage]
from datamodel_code_generator.parser.openapi_contract_types import FinalTypeProjector, ReferenceTypeBinding

if TYPE_CHECKING:
    from pathlib import Path

    from datamodel_code_generator._generation_contract import AttemptId, ModuleResultBinding, TypeProjection
    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.parser.base import Result
    from datamodel_code_generator.parser.openapi_contract import BindingCaptureMixin


@dataclass(frozen=True, slots=True)
class ModelArtifactAddress:
    """Locate real primary and secondary definitions beneath the explicit output anchor."""

    result_key: tuple[str, ...] | Literal["single"]
    relative_path: tuple[str, ...]
    model_package: str
    secondary_definitions: tuple[tuple[str, ...], ...]


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


@dataclass(frozen=True, slots=True)
class FinalModelInventory:
    """Contain graph-free identities and types for subsequent field and use assembly."""

    attempt: AttemptId
    models: tuple[FinalModelIdentity, ...]
    imports: tuple[FinalModuleImports, ...]


def _freeze_imports(collections: tuple[Imports, Imports]) -> tuple[Import, ...]:
    """Read actual storage entries without invoking model imports or rendering again."""
    values: dict[Import, None] = {}
    for imports in collections:
        for module, names in imports.items():
            aliases = imports.alias.get(module, {})
            for name in sorted(names):
                symbol, separator, independent_alias = name.partition(" as ")
                value = Import(
                    from_=module,
                    import_=symbol,
                    alias=aliases.get(name) or independent_alias or None,
                    keep_unaliased=bool(separator),
                )
                values[value] = None
    return tuple(values)


def _artifact_address(binding: ModuleResultBinding, output: Path, model_package: str) -> ModelArtifactAddress | None:
    if (primary := binding.primary) is None:
        return None
    return ModelArtifactAddress(
        primary,
        (output.name,) if primary == "single" else primary,
        model_package,
        binding.secondary,
    )


def freeze_model_inventory(  # ruff: ignore[too-many-locals] -- One bounded pass joins final identities, namespaces, and own fields.
    parser: BindingCaptureMixin,
    results: str | dict[tuple[str, ...], Result],
    *,
    output: Path,
    model_package: str,
) -> FinalModelInventory:
    """Freeze only models belonging to actual module returns, after ordinary rendering."""
    ledger = parser.binding_ledger
    outputs = tuple(observation for observation in parser.module_outputs if observation.models)
    models = tuple(model for observation in outputs for model in observation.models)
    symbols = {ledger.identity(model): SymbolId(index) for index, model in enumerate(models)}
    policies = {
        ledger.identity(model): freeze_reference_policy(
            model, serialize_as_any=parser.data_type_manager.use_serialize_as_any
        )
        for model in models
    }
    references = {
        ledger.identity(model.reference): ReferenceTypeBinding(
            symbols[ledger.identity(model)],
            policies[ledger.identity(model)].nullable,
            policies[ledger.identity(model)].is_alias,
            policies[ledger.identity(model)].serialize_as_any,
        )
        for model in models
    }
    members = {
        ledger.identity(model.reference): tuple(
            GeneratedEnumMember(symbols[ledger.identity(model)], ledger.identity(field), field.name or "")
            for field in model.fields
        )
        for model in models
        if isinstance(model, Enum)
    }
    projector = FinalTypeProjector(references, members)
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
            _freeze_imports(observation.imports),
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
                        FieldSlot(ledger.attempt_id, symbol, ledger.identity(field), index, field.name or ""),
                        field.original_name,
                        import_resolvers[symbol].project(
                            projector.project(_type_recipe(field.data_type, ledger, set()))
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
    return FinalModelInventory(
        ledger.attempt_id,
        tuple(frozen),
        module_imports,
    )
