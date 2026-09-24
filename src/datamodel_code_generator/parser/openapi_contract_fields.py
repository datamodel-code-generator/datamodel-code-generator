"""Join final declaring fields, accepted syntax, and original producer occurrences."""

from __future__ import annotations

import operator
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from datamodel_code_generator._binding_imports import FinalImportResolver
from datamodel_code_generator._generation_contract import (
    BindingCaptureError,
    BindingDiagnostic,
    FieldSourceOrigin,
    FieldUseBinding,
    FinalModelSymbol,
)
from datamodel_code_generator.model.binding import (
    ExpectedFieldDeclaration,
    FieldProjectionContext,
    FrozenImportBindings,
    ModelProjectionContext,
    OpaqueBackendValue,
    freeze_alias_nullability,
    freeze_builtin_model_facts,
    freeze_model_field_facts,
    index_builtin_field_declarations,
)
from datamodel_code_generator.model.binding_fields import DeclaredField, FieldOwner, FieldOwnershipIndex
from datamodel_code_generator.model.binding_policies import constructor_policy, freeze_model_policy
from datamodel_code_generator.parser.openapi_contract_store import _type_recipe  # pyright: ignore[reportPrivateUsage]


@dataclass(frozen=True, slots=True)
class FinalArtifactBinding:
    """Retain the exact input for one later verification of ordinarily staged bytes."""

    address: ModelArtifactAddress
    models: tuple[SymbolId, ...]
    expected: tuple[ExpectedFieldDeclaration, ...]
    imports: FrozenImportBindings
    index: BuiltinFieldArtifactIndex


@dataclass(frozen=True, slots=True)
class FinalFieldInventory:
    """Carry graph-free field semantics, occurrence bindings, and artifact evidence."""

    symbols: tuple[FinalModelSymbol, ...]
    fields: tuple[tuple[FieldSlot, ModelFieldFacts], ...]
    bindings: tuple[FieldUseBinding, ...]
    artifacts: tuple[FinalArtifactBinding, ...]
    diagnostics: tuple[BindingDiagnostic, ...]
    source_bindings: tuple[tuple[GraphObjectId, tuple[FieldUseBinding, ...]], ...] = ()
    copied_type_sources: tuple[tuple[GraphObjectId, GraphObjectId], ...] = ()


class FinalFieldBuilder:
    """Own temporary graph lookups only until the enclosing attempt freeze returns."""

    def __init__(
        self,
        parser: openapi_contract.BindingCaptureMixin,
        inventory: openapi_contract_freeze.FinalModelInventory,
        projector: FinalTypeProjector,
    ) -> None:
        """Build bounded identity indexes without reading derived model getters."""
        self.parser = parser
        self.inventory = inventory
        self.projector = projector
        self.model_nodes = {
            parser.binding_ledger.identity(model): model for output in parser.module_outputs for model in output.models
        }
        self.identities = {model.symbol: model for model in inventory.models}
        self.field_nodes = {
            field.slot: actual
            for model in inventory.models
            for field, actual in zip(model.fields, self.model_nodes[model.model].fields, strict=True)
        }
        self.sources = parser._resolve_field_sources()  # pyright: ignore[reportPrivateUsage] # ruff: ignore[private-member-access]
        self.policies = {
            model.symbol: freeze_model_policy(self.model_nodes[model.model], parser.data_model_type)
            for model in inventory.models
        }
        self.ownership = FieldOwnershipIndex({
            model.symbol: FieldOwner(
                model.symbol,
                model.reference_path,
                model.bases,
                tuple(
                    DeclaredField(field.slot, field.wire_name if field.wire_name is not None else field.slot.name)
                    for field in model.fields
                ),
                bool(model.unknown_bases),
            )
            for model in inventory.models
        })
        self.facts: dict[FieldSlot, ModelFieldFacts] = {}
        self.model_facts: dict[SymbolId, BackendModelFacts] = {}
        self.diagnostics: list[BindingDiagnostic] = []
        self.synthetics: dict[GraphObjectId, list[openapi_contract.SyntheticFieldObservation]] = {}
        for observation in parser.synthetic_fields:
            self.synthetics.setdefault(observation.field, []).append(observation)
        self.discriminator_fields: dict[GraphObjectId, tuple[SourceLocation, ...]] = {}
        if parser.discriminator_types:
            reference_sources: dict[GraphObjectId, list[SourceLocation]] = {}
            for source in parser.schema_references:
                reference_sources.setdefault(source.reference, []).extend(
                    origin.location for origin in parser.schema_origins.origins(source.schema)
                )
            produced_types = {
                parser.binding_ledger.identity(item.data_type): item for item in parser.discriminator_types
            }
            for target, original in parser.binding_ledger.enum_copies.items():
                if (produced := produced_types.get(original)) is not None:
                    produced_types[target] = produced
            for slot, field in self.field_nodes.items():
                if slot.field in parser.field_origins or slot.field in parser.field_constructions:
                    continue
                if (produced := produced_types.get(parser.binding_ledger.identity(field.data_type))) is None:
                    continue
                reference = produced.reference
                if reference is None and (model := self.model_nodes.get(produced.model)) is not None:
                    reference = parser.binding_ledger.identity(model.reference)
                self.discriminator_fields[slot.field] = tuple(
                    dict.fromkeys(reference_sources.get(reference, ())) if reference is not None else ()
                )

    def _extra_items(self, model: DataModel, imports: FinalImportResolver) -> FinalPythonType | None:
        for observation in reversed(self.parser.additional_types):
            if observation.frame.path != model.reference.path:
                continue
            projected = self.projector.project(_type_recipe(observation.data_type, self.parser.binding_ledger, set()))
            return imports.project(projected).value
        return None

    def _model_facts(self) -> None:
        for module in self.inventory.imports:
            imports: FinalImportResolver | None = None
            for symbol in module.models:
                identity = self.identities[symbol]
                policy = self.policies[identity.symbol]
                if policy.backend is None or not policy.builtin_semantics:
                    self.diagnostics.append(
                        BindingDiagnostic("BND_CUSTOM_BINDING_REQUIRED", details=(("symbol", identity.symbol),))
                    )
                    continue
                model = self.model_nodes[identity.model]
                extra_items = None
                if policy.extra_items_present:
                    if imports is None:
                        imports = FinalImportResolver(module.values, self.parser.config.import_overrides)
                    extra_items = self._extra_items(model, imports)
                try:
                    self.model_facts[identity.symbol] = facts = freeze_builtin_model_facts(
                        model,
                        projection=ModelProjectionContext(
                            policy.backend,
                            policy.builtin_semantics,
                            policy.functional_typeddict,
                            extra_items,
                        ),
                    )
                    if any(
                        setting.present is None or isinstance(setting.value, OpaqueBackendValue)
                        for setting in (*facts.parameters, *facts.configuration)
                    ):
                        self.diagnostics.append(
                            BindingDiagnostic("BND_CUSTOM_BINDING_REQUIRED", details=(("symbol", identity.symbol),))
                        )
                except BindingCaptureError:
                    self.diagnostics.append(
                        BindingDiagnostic("BND_TYPE_EXPRESSION_UNSUPPORTED", details=(("symbol", identity.symbol),))
                    )

    def _declarations(self) -> dict[SymbolId, tuple[ExpectedFieldDeclaration, ...]]:
        own: dict[FieldSlot, ExpectedFieldDeclaration] = {}
        for model in self.inventory.models:
            policy = self.policies[model.symbol]
            if policy.backend is None or not policy.builtin_semantics or policy.kind == "enum":
                continue
            for field in model.fields:
                if (projected := field.projection.value) is None:
                    self.diagnostics.append(
                        BindingDiagnostic(
                            field.projection.reason or "BND_TYPE_EXPRESSION_UNSUPPORTED",
                            details=(("field", field.slot.field),),
                        )
                    )
                    continue
                own[field.slot] = ExpectedFieldDeclaration(
                    self.inventory.attempt,
                    model.symbol,
                    field.slot,
                    model.name,
                    field.slot.name,
                    policy.backend,
                    projected,
                    excluded_by_tag=policy.backend == "msgspec"
                    and self.field_nodes[field.slot].extras.get("is_classvar") is True,
                    form="alias_value"
                    if policy.kind == "alias"
                    else "root_alias_value"
                    if policy.kind == "root" and model.is_alias
                    else "class_field",
                )
        declarations: dict[SymbolId, tuple[ExpectedFieldDeclaration, ...]] = {}
        for model in self.inventory.models:
            if self.policies[model.symbol].functional_typeddict:
                projection = self.ownership.project(model.symbol, functional_typeddict=True)
                if projection.value is None:
                    self.diagnostics.append(
                        BindingDiagnostic(
                            projection.reason or "BND_FIELD_UNRESOLVED", details=(("symbol", model.symbol),)
                        )
                    )
                    continue
                if any(field.slot not in own for field in projection.value.fields):
                    continue
                declarations[model.symbol] = projection.value.functional_declarations(model.name, own)
            else:
                declarations[model.symbol] = tuple(own[field.slot] for field in model.fields if field.slot in own)
        return declarations

    def _field_constructions(self, slot: FieldSlot) -> tuple[openapi_contract.FieldConstructionObservation, ...]:
        return tuple(
            construction
            for source in self.sources.get(slot.field, (slot.field,))
            if (construction := self.parser.field_constructions.get(source)) is not None
        )

    def _field_projection(self, slot: FieldSlot, alias_nullable: dict[SymbolId, bool | None]) -> FieldProjectionContext:
        original_ids = self.sources.get(slot.field, (slot.field,))
        constructions = self._field_constructions(slot)
        origins = tuple(
            origin for source in original_ids if (origin := self.parser.field_origins.get(source)) is not None
        )
        policies = tuple(
            observation.default_policy for observation in constructions if observation.default_policy is not None
        )
        overrides = tuple(
            inherited.resolution.producer
            for source in original_ids
            if (inherited := self.parser.inherited_defaults.get(source)) is not None
        ) + tuple(policy.resolution.producer for policy in policies)
        nullable = tuple(
            self.projector.preexisting_null(observation.preexisting_null, alias_nullable=alias_nullable)
            for observation in constructions
        )
        policy = self.policies[slot.symbol]
        facts = self.model_facts.get(slot.symbol)
        return FieldProjectionContext(
            any(origin.required_by_node for origin in origins) if origins else None,
            any(policy.has_default for policy in policies) if policies else None,
            None if not overrides or "opaque" in overrides else "override" in overrides,
            any(observation.schema is not None and observation.schema.nullable is True for observation in constructions)
            if constructions
            else None,
            None if not nullable or any(value is None for value in nullable) else any(nullable),
            self.parser.force_optional_for_required_fields,
            policy.builtin_semantics,
            policy.backend,
            constructor_policy(facts, "init") if facts is not None else None,
            constructor_policy(facts, "kw_only") if facts is not None else None,
        )

    def _alias_nulls(  # ruff: ignore[too-many-branches]
        self, artifacts: list[FinalArtifactBinding]
    ) -> dict[SymbolId, bool | None]:
        """Resolve producer-owned alias nulls in linear time, leaving opaque cycles unknown."""
        nullable: dict[SymbolId, bool | None] = {
            model.symbol: None for model in self.inventory.models if model.is_alias
        }
        if not nullable:
            return nullable
        dependencies: dict[SymbolId, set[SymbolId]] = {}
        dependents: dict[SymbolId, list[SymbolId]] = {}
        opaque: set[SymbolId] = set()
        ready: list[SymbolId] = []
        declarations = {
            declaration.expected.consumer: declaration
            for artifact in artifacts
            for declaration in artifact.index.fields
            if declaration.expected.form in {"alias_value", "root_alias_value"}
        }
        for symbol, declaration in declarations.items():
            if declaration.expected.form == "root_alias_value":
                nullable[symbol] = False
                ready.append(symbol)
                continue
            constructions = self._field_constructions(declaration.expected.slot)
            value, references = freeze_alias_nullability(
                self.field_nodes[declaration.expected.slot],
                type_value=declaration.expected.type,
                emitted=declaration.facts,
                aliases=nullable,
                opaque_type=not constructions or any(item.preexisting_null.opaque for item in constructions),
            )
            if value is True or not references:
                nullable[symbol] = value
                ready.append(symbol)
                continue
            if value is None:
                opaque.add(symbol)
            dependencies[symbol] = references
            for reference in references:
                dependents.setdefault(reference, []).append(symbol)
        while ready:
            symbol = ready.pop()
            for dependent in dependents.get(symbol, ()):
                if (remaining := dependencies.get(dependent)) is None:
                    continue
                remaining.remove(symbol)
                if nullable[symbol] is True:
                    nullable[dependent] = True
                else:
                    if nullable[symbol] is None:
                        opaque.add(dependent)
                    if remaining:
                        continue
                    nullable[dependent] = None if dependent in opaque else False
                del dependencies[dependent]
                ready.append(dependent)
        return nullable

    def _artifacts(
        self, declarations: dict[SymbolId, tuple[ExpectedFieldDeclaration, ...]]
    ) -> tuple[FinalArtifactBinding, ...]:
        artifacts: list[FinalArtifactBinding] = []
        outputs = (output for output in self.parser.module_outputs if output.models)
        for output, module in zip(outputs, self.inventory.imports, strict=True):
            expected = tuple(field for symbol in module.models for field in declarations.get(symbol, ()))
            imports = FrozenImportBindings(
                module.values,
                module.symbol_names,
            )
            address = self.identities[module.models[0]].artifact
            if address is None:
                self.diagnostics.append(
                    BindingDiagnostic(self.identities[module.models[0]].artifact_reason or "BND_SYMBOL_NOT_EMITTED")
                )
                continue
            try:
                index = index_builtin_field_declarations(
                    output.result.body, expected=expected, imports=imports, collect_errors=True
                )
            except BindingCaptureError:
                self.diagnostics.append(
                    BindingDiagnostic(
                        "BND_TYPE_EXPRESSION_UNSUPPORTED", details=(("artifact", "/".join(address.relative_path)),)
                    )
                )
                continue
            definitions = {definition.name: definition.kind for definition in index.definitions}
            for symbol in module.models:
                if definitions.get(self.identities[symbol].name) not in {"class", "type_alias", "assignment"}:
                    self.diagnostics.append(BindingDiagnostic("BND_SYMBOL_NOT_EMITTED", details=(("symbol", symbol),)))
                if self.identities[symbol].name in index.invalid_models:
                    self.diagnostics.append(
                        BindingDiagnostic("BND_TYPE_EXPRESSION_UNSUPPORTED", details=(("symbol", symbol),))
                    )
            artifacts.append(FinalArtifactBinding(address, module.models, expected, imports, index))
        alias_nullable = self._alias_nulls(artifacts)
        for artifact in artifacts:
            for declaration in artifact.index.fields:
                slot = declaration.expected.slot
                if slot in self.facts:
                    continue
                self.facts[slot] = freeze_model_field_facts(
                    self.field_nodes[slot],
                    type_value=declaration.expected.type,
                    emitted=declaration.facts,
                    projection=self._field_projection(slot, alias_nullable),
                )
        return tuple(artifacts)

    def _bindings(self) -> tuple[FieldUseBinding, ...]:
        bindings: list[FieldUseBinding] = []
        for identity in self.inventory.models:
            if self.policies[identity.symbol].kind == "enum":
                continue
            projection = self.ownership.project(
                identity.symbol, functional_typeddict=self.policies[identity.symbol].functional_typeddict
            )
            if projection.value is None:
                continue
            for declared in projection.value.fields:
                slot = declared.slot
                original_ids = self.sources.get(slot.field, (slot.field,))
                discriminator = any(source in self.discriminator_fields for source in original_ids)
                if discriminator:
                    original_ids = tuple(
                        dict.fromkeys((
                            *original_ids,
                            *(
                                source
                                for override in projection.value.overrides
                                if override.replacement == slot
                                for source in self.sources.get(override.original.field, (override.original.field,))
                            ),
                        ))
                    )
                origins = tuple(
                    dict.fromkeys(
                        FieldSourceOrigin(origin.location, origin.relation)
                        for source in original_ids
                        if (observation := self.parser.field_origins.get(source)) is not None
                        for origin in observation.origins
                    )
                )
                synthetic = tuple(
                    observation for source in original_ids for observation in self.synthetics.get(source, ())
                )
                if synthetic:
                    origins = tuple(
                        dict.fromkeys((
                            *origins,
                            *(
                                FieldSourceOrigin(location, observation.kind)
                                for observation in synthetic
                                for location in observation.locations
                            ),
                        ))
                    )
                if discriminator:
                    origins = tuple(
                        dict.fromkeys((
                            *origins,
                            *(
                                FieldSourceOrigin(location, "discriminator_synthetic")
                                for source in original_ids
                                for location in self.discriminator_fields.get(source, ())
                            ),
                        ))
                    )
                wire_name = declared.wire_name
                if discriminator:
                    wire_names = {
                        observed.wire_name
                        for source in original_ids
                        if (observed := self.parser.field_origins.get(source)) is not None
                    }
                    if len(wire_names) == 1:
                        wire_name = next(iter(wire_names))
                facts = self.facts.get(slot)
                bindings.append(
                    FieldUseBinding(
                        "known" if origins else "unavailable",
                        None if origins else "producer_unobserved",
                        "discriminator_synthetic"
                        if discriminator
                        else synthetic[-1].kind
                        if synthetic
                        else "root_value"
                        if identity.is_alias
                        else "property",
                        origins,
                        None if synthetic and synthetic[-1].kind == "root_value" else wire_name,
                        identity.symbol,
                        slot,
                        facts,
                        origins[0].location if origins else None,
                        "neutral",
                        "tag" if facts is not None and not facts.backend.emitted.emitted else None,
                    )
                )
        return self._variant_bindings(bindings)

    def _variant_bindings(self, bindings: list[FieldUseBinding]) -> tuple[FieldUseBinding, ...]:
        references = dict(self.inventory.declaration_references)
        variants = {
            (variant.base, variant.suffix): variant.reference for variant in self.parser.binding_ledger.variants
        }
        members_by_symbol: dict[SymbolId, list[FieldUseBinding]] = {}
        for binding in bindings:
            members_by_symbol.setdefault(binding.consumer, []).append(binding)
        for observation in self.parser.variant_fields:
            if (reference := variants.get((observation.base, observation.suffix))) is None or (
                terminal := references.get(reference)
            ) is None:
                continue
            source_origins: dict[str, tuple[int, tuple[FieldSourceOrigin, ...]]] = {}
            excluded = dict(observation.excluded)
            for position, field_id in enumerate(observation.fields):
                observed = tuple(
                    self.parser.field_origins[source]
                    for source in self.sources.get(field_id, (field_id,))
                    if source in self.parser.field_origins
                )
                names = tuple(dict.fromkeys(field.wire_name for field in observed))
                origins = tuple(
                    dict.fromkeys(
                        FieldSourceOrigin(origin.location, origin.relation)
                        for field in observed
                        for origin in field.origins
                    )
                )
                if len(names) == 1:
                    source_origins[names[0]] = position, origins
                if field_id not in excluded:
                    continue
                members_by_symbol.setdefault(terminal.symbol, []).append(
                    FieldUseBinding(
                        "known" if origins and len(names) == 1 else "unavailable",
                        None if origins and len(names) == 1 else "producer_unobserved",
                        "property",
                        origins,
                        names[0] if len(names) == 1 else None,
                        terminal.symbol,
                        None,
                        None,
                        origins[0].location if origins else None,
                        "request" if observation.suffix == "Request" else "response",
                        excluded[field_id],
                    )
                )
            ordered: list[tuple[int, FieldUseBinding]] = []
            for member in members_by_symbol.get(terminal.symbol, ()):
                position, origins = source_origins.get(member.wire_name or "", (len(source_origins), ()))
                projected_member = (
                    replace(
                        member,
                        occurrences=origins,
                        schema=origins[0].location,
                        origin_state="known",
                        origin_reason=None,
                    )
                    if origins
                    else member
                )
                ordered.append((position, projected_member))
            members_by_symbol[terminal.symbol] = [member for _, member in sorted(ordered, key=operator.itemgetter(0))]
        return tuple(member for model in self.inventory.models for member in members_by_symbol.get(model.symbol, ()))

    def freeze(self) -> FinalFieldInventory:
        """Finish immutable values before parser disposal and release of graph anchors."""
        self._model_facts()
        artifacts = self._artifacts(self._declarations())
        symbols = tuple(
            FinalModelSymbol(
                model.symbol,
                model.model,
                model.reference,
                self.policies[model.symbol].backend,
                self.policies[model.symbol].kind,
                model.name,
                model.artifact,
                model.order,
                model.bases,
                tuple(field.slot for field in model.fields),
                model.is_alias,
                model.nullable,
                self.model_facts.get(model.symbol),
            )
            for model in self.inventory.models
        )
        bindings = self._bindings()
        return FinalFieldInventory(
            symbols,
            tuple(self.facts.items()),
            bindings,
            artifacts,
            tuple(self.diagnostics),
            self._source_bindings(bindings),
            self._copied_type_sources(),
        )

    def _copied_type_sources(self) -> tuple[tuple[GraphObjectId, GraphObjectId], ...]:
        """Join removed field producers to completed copies without overriding surviving owners."""
        emitted = {slot.field for slot in self.field_nodes}
        return tuple(
            (self.parser.binding_ledger.identity(construction.data_type), slot.field)
            for slot in self.field_nodes
            for source in self.sources.get(slot.field, ())
            if source not in emitted and (construction := self.parser.field_constructions.get(source)) is not None
        )

    def _source_bindings(
        self, bindings: tuple[FieldUseBinding, ...]
    ) -> tuple[tuple[GraphObjectId, tuple[FieldUseBinding, ...]], ...]:
        """Retain original wire occurrences when independent schemas reuse one final model."""
        by_symbol: dict[SymbolId, list[FieldUseBinding]] = {}
        for binding in bindings:
            by_symbol.setdefault(binding.consumer, []).append(binding)
        references = dict(self.inventory.declaration_references)
        sources: dict[GraphObjectId, tuple[FieldUseBinding, ...]] = {}
        for registration in self.parser.binding_ledger.registrations:
            if registration.reference in sources or (terminal := references.get(registration.reference)) is None:
                continue
            if registration.reference == self.identities[terminal.symbol].reference:
                sources[registration.reference] = tuple(by_symbol.get(terminal.symbol, ()))
                continue
            origins: dict[str, list[FieldSourceOrigin]] = {}
            for field in registration.fields:
                for source in self.sources.get(field, (field,)):
                    if (observed := self.parser.field_origins.get(source)) is not None:
                        origins.setdefault(observed.wire_name, []).extend(
                            FieldSourceOrigin(origin.location, origin.relation) for origin in observed.origins
                        )
                    for synthetic in self.synthetics.get(source, ()):
                        origins.setdefault(synthetic.name or "", []).extend(
                            FieldSourceOrigin(location, synthetic.kind) for location in synthetic.locations
                        )
            sources[registration.reference] = tuple(
                replace(
                    member,
                    origin_state="known"
                    if (
                        occurrences := tuple(
                            dict.fromkeys(
                                origins.get("" if member.member_kind == "root_value" else member.wire_name or "", ())
                            )
                        )
                    )
                    else "unavailable",
                    origin_reason=None if occurrences else "producer_unobserved",
                    occurrences=occurrences,
                    schema=occurrences[0].location if occurrences else None,
                )
                for member in by_symbol.get(terminal.symbol, ())
            )
        return tuple(sources.items())


if TYPE_CHECKING:
    from datamodel_code_generator._generation_contract import (
        FieldSlot,
        FinalPythonType,
        GraphObjectId,
        ModelArtifactAddress,
        ModelFieldFacts,
        SourceLocation,
        SymbolId,
    )
    from datamodel_code_generator.model.base import DataModel
    from datamodel_code_generator.model.binding import BackendModelFacts, BuiltinFieldArtifactIndex
    from datamodel_code_generator.parser import openapi_contract, openapi_contract_freeze
    from datamodel_code_generator.parser.openapi_contract_types import FinalTypeProjector
