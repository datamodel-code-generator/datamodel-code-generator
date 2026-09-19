"""Freeze actual OpenAPI traversal and source uses without another resolver or parser."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from datamodel_code_generator._binding_imports import FinalImportResolver
from datamodel_code_generator._binding_literals import freeze_literal
from datamodel_code_generator._generation_contract import (
    BindingCaptureError,
    BindingDiagnostic,
    DeclarationId,
    GeneratedSymbolType,
    IgnoredDeclaration,
    OperationContract,
    OperationId,
    SourceLocation,
    TypeProjection,
    TypeUseBinding,
    TypeUseId,
    WireDeclaration,
)
from datamodel_code_generator.model.base import DataModel
from datamodel_code_generator.parser._api_reference import ApiDeclarationId
from datamodel_code_generator.parser.openapi_contract import ContractApiOpenAPIParser
from datamodel_code_generator.parser.openapi_contract_store import _type_recipe  # pyright: ignore[reportPrivateUsage]
from datamodel_code_generator.parser.openapi_media import encoding_media
from datamodel_code_generator.parser.openapi_scope import ApiIgnoredDeclaration

_PARAMETER_FACTS = (
    "name",
    "in",
    "description",
    "required",
    "deprecated",
    "allowEmptyValue",
    "style",
    "explode",
    "allowReserved",
    "example",
    "examples",
)
_OPERATION_FACTS = (
    "operationId",
    "tags",
    "summary",
    "description",
    "externalDocs",
    "deprecated",
    "security",
    "servers",
)
_LINK_FACTS = ("operationRef", "operationId", "parameters", "requestBody", "description", "server")


def _facts(raw: dict[str, YamlValue], keys: tuple[str, ...]) -> tuple[tuple[str, FrozenLiteral], ...]:
    return tuple((key, freeze_literal(value, set())) for key, value in raw.items() if key in keys)


def wire_mapping(value: YamlValue) -> dict[str, YamlValue]:
    """Read an optional wire object without constructing or validating another schema."""
    return value if isinstance(value, dict) else {}


def child_declaration(declaration: ApiDeclarationId, *tokens: str) -> ApiDeclarationId:
    """Append original declaration tokens without interpreting model naming paths."""
    return ApiDeclarationId(declaration.document, (*declaration.tokens, *tokens))


@dataclass(frozen=True, slots=True)
class FinalOperationInventory:
    """Keep only value contracts after the capture parser has been disposed."""

    operations: tuple[OperationContract, ...]
    uses: tuple[TypeUseBinding, ...]
    diagnostics: tuple[BindingDiagnostic, ...]
    security_schemes: tuple[WireDeclaration, ...] = ()


class FinalOperationBuilder:
    """Join actual object-use edges with final types inside the parser ownership boundary."""

    def __init__(
        self,
        parser: BindingCaptureMixin,
        inventory: FinalModelInventory,
        fields: FinalFieldInventory,
        projector: FinalTypeProjector,
    ) -> None:
        """Index completed observations without loading or resolving another source."""
        self.parser = parser
        self.projector = projector.with_references(dict(inventory.declaration_references))
        self.imports = FinalImportResolver(
            tuple(dict.fromkeys(value for module in inventory.imports for value in module.values)),
            parser.config.import_overrides,
        )
        self.references = {reference: binding.symbol for reference, binding in inventory.declaration_references}
        self.directional_projectors = {"neutral": self.projector}
        self.directional_references = {"neutral": self.references}
        for direction, suffix in (("request", "Request"), ("response", "Response")):
            bindings = dict(inventory.declaration_references)
            for variant in parser.binding_ledger.variants:
                if variant.suffix == suffix and (terminal := bindings.get(variant.reference)) is not None:
                    bindings[variant.base] = terminal
            self.directional_projectors[direction] = projector.with_references(bindings)
            self.directional_references[direction] = {reference: value.symbol for reference, value in bindings.items()}
        self.schemas: dict[tuple[ApiDeclarationId, str], list[SchemaUseObservation]] = {}
        for observation in parser.schema_observations if isinstance(parser, ContractApiOpenAPIParser) else ():
            self.schemas.setdefault((observation.frame.declaration, observation.frame.projection), []).append(
                observation
            )
        self.objects = {
            observation.use: observation
            for observation in (parser.object_observations if isinstance(parser, ContractApiOpenAPIParser) else ())
        }
        self.operation_frames = (
            list(parser.binding_frames.operations) if isinstance(parser, ContractApiOpenAPIParser) else []
        )
        self.ignored = list(parser.ignored_declarations) if isinstance(parser, ContractApiOpenAPIParser) else []
        self.api_scope = isinstance(parser, ContractApiOpenAPIParser)
        self.unresolved_symbols = {
            model.symbol for model in inventory.models if model.reference_path in parser.unresolved_references
        }
        self.type_results = {
            id(observation.declaration): observation.data_type
            for observation in parser.type_observations
            if observation.declaration is not None and observation.path == observation.declaration.engine_path
        }
        self.resolved_types: dict[str, list[DataType]] = {}
        self.source_ref_keys: dict[tuple[int, str], list[str]] = {}
        for observation in parser.type_observations:
            for resolved in observation.resolved_refs:
                self.resolved_types.setdefault(resolved, []).append(observation.data_type)
                if observation.declaration is not None and observation.ref is not None:
                    self.source_ref_keys.setdefault((id(observation.declaration), observation.ref), []).append(resolved)
        self.members: dict[int, list[FieldUseBinding]] = {}
        self.source_members = dict(fields.source_bindings)
        self.member_sources: dict[tuple[SourceDocumentId, str], dict[FieldSlot, None]] = {}
        for binding in (*fields.bindings, *(member for _, members in fields.source_bindings for member in members)):
            if binding.slot is None:
                continue
            for origin in binding.occurrences:
                self.member_sources.setdefault((origin.location.document, origin.location.pointer), {})[
                    binding.slot
                ] = None
        for binding in fields.bindings:
            self.members.setdefault(binding.consumer, []).append(binding)
        self.operations: dict[int, OperationId] = {}
        self.operation_uses: dict[OperationId, ApiDeclarationId] = {}
        self.uses: dict[TypeUseId, TypeUseBinding] = {}
        self.diagnostics: list[BindingDiagnostic] = []

    def location(self, declaration: ApiDeclarationId, role: Literal["declaration", "use", "schema"]) -> SourceLocation:
        """Translate an actual declaration into its leased plain-pointer identity."""
        if (document := self.parser.source_lease.document_id(declaration.document)) is None:
            msg = "An observed declaration has no source document lease"
            raise BindingCaptureError(msg)
        pointer = (
            "/" + "/".join(token.replace("~", "~0").replace("/", "~1") for token in declaration.tokens)
            if declaration.tokens
            else ""
        )
        return SourceLocation(document, pointer, role)

    def _resolve_object(
        self, declaration: ApiDeclarationId, raw: YamlValue
    ) -> tuple[ApiDeclarationId, dict[str, YamlValue]]:
        if (observed := self.objects.get(declaration)) is not None:
            return observed.target.declaration, observed.target.value
        return declaration, wire_mapping(raw)

    def _project_schema(
        self, declaration: ApiDeclarationId, projection: str, direction: str = "neutral"
    ) -> TypeProjection:
        projected = self.imports.project(self._project_schema_type(declaration, projection, direction))
        if isinstance(projected.value, GeneratedSymbolType) and projected.value.symbol in self.unresolved_symbols:
            return TypeProjection(None, "BND_UNRESOLVED_REFERENCE")
        return projected

    def _project_schema_type(self, declaration: ApiDeclarationId, projection: str, direction: str) -> TypeProjection:
        projector = self.directional_projectors[direction]
        references = self.directional_references[direction]
        observations = self.schemas.get((declaration, projection), ())
        for observation in reversed(observations):
            if (data_type := self.type_results.get(id(observation.frame))) is not None:
                return projector.project(_type_recipe(data_type, self.parser.binding_ledger, set()))
            key = self.parser.model_resolver.join_path(observation.frame.engine_path)
            if (reference := self.parser.model_resolver.references.get(key)) is not None:
                if (identity := self.parser.binding_ledger.identity(reference)) in references:
                    return projector.declaration_type(identity)
                if (
                    isinstance(model := reference.source, DataModel)
                    and (model.IS_ALIAS or model.IS_ROOT_MODEL)
                    and len(model.fields) == 1
                ):
                    return projector.project(_type_recipe(model.fields[0].data_type, self.parser.binding_ledger, set()))
                if (
                    recipe := self.parser.binding_ledger.root_recipes.get(
                        self.parser.binding_ledger.identity(reference)
                    )
                ) is not None:
                    return projector.project(recipe)
            projected = tuple(
                projector.project(_type_recipe(data_type, self.parser.binding_ledger, set()))
                for data_type in self.resolved_types.get(key, ())
            )
            if projected and all(value == projected[0] for value in projected):
                return projected[0]
        return TypeProjection(None, "BND_SYMBOL_NOT_EMITTED")

    def _use(  # ruff: ignore[too-many-arguments, too-many-positional-arguments] -- Preserve independent owner, declaration, use-site, and wire identities.
        self,
        owner: OperationId | SourceLocation,
        role: TypeUseRole,
        declaration: ApiDeclarationId,
        use_site: ApiDeclarationId,
        schema: ApiDeclarationId,
        schema_use: ApiDeclarationId,
        *,
        projection: Literal["value", "item_stream_array"] = "value",
        location: str | None = None,
        name: str | None = None,
        status: str | None = None,
        media: str | None = None,
    ) -> TypeUseId:
        direction: Literal["request", "response", "neutral"] = (
            "response" if role.startswith("response") else "neutral" if role == "schema" else "request"
        )
        use = TypeUseId(
            owner,
            role,
            self.location(use_site, "use"),
            self.location(schema_use, "schema"),
            DeclarationId(self.location(declaration, "declaration")),
            direction,
            projection,
            location,
            name,
            status,
            media,
        )
        if use in self.uses:
            return use
        projected = self._project_schema(schema, projection, direction)
        members = (
            tuple(
                replace(member, direction=direction)
                for member in self._source_members(schema, projection, projected.value.symbol)
            )
            if isinstance(projected.value, GeneratedSymbolType)
            else ()
        )
        self.uses[use] = TypeUseBinding(
            use,
            "bound"
            if projected.value is not None
            else "invalid"
            if self.api_scope or projected.reason != "BND_SYMBOL_NOT_EMITTED"
            else "not_generated",
            projected.value,
            projected.reason,
            members,
            self._helper_producers(self.location(schema, "schema")),
        )
        return use

    def _source_members(self, schema: ApiDeclarationId, projection: str, symbol: int) -> tuple[FieldUseBinding, ...]:
        for observation in reversed(self.schemas.get((schema, projection), ())):
            key = self.parser.model_resolver.join_path(observation.frame.engine_path)
            keys = [key]
            if isinstance(raw := observation.frame.raw_schema, dict) and isinstance(ref := raw.get("$ref"), str):
                keys.extend(self.source_ref_keys.get((id(observation.frame), ref), ()))
            for key in keys:
                if (
                    (reference := self.parser.model_resolver.references.get(key)) is not None
                    and (members := self.source_members.get(self.parser.binding_ledger.identity(reference))) is not None
                    and all(member.consumer == symbol for member in members)
                ):
                    return members
        return tuple(self.members.get(symbol, ()))

    def _media(  # ruff: ignore[too-many-arguments] -- Preserve independent owner, declaration, use-site, and wire identities.
        self,
        raw: YamlValue,
        declaration: ApiDeclarationId,
        use_site: ApiDeclarationId,
        owner: OperationId,
        role: TypeUseRole,
        *,
        status: str | None = None,
        parameter_name: str | None = None,
        parameter_location: str | None = None,
    ) -> tuple[WireDeclaration, ...]:
        media_values: list[WireDeclaration] = []
        for name, value in wire_mapping(raw).items():
            media = wire_mapping(value)
            media_decl, media_use = (
                child_declaration(declaration, "content", name),
                child_declaration(use_site, "content", name),
            )
            uses: list[TypeUseId] = []
            for keyword, projections in self._media_projections(media, media_decl):
                schema, schema_use = child_declaration(media_decl, keyword), child_declaration(media_use, keyword)
                uses.extend(
                    self._use(
                        owner,
                        role,
                        declaration,
                        use_site,
                        schema,
                        schema_use,
                        projection=projection,
                        name=parameter_name,
                        location=parameter_location,
                        status=status,
                        media=name,
                    )
                    for projection in projections
                )
            encodings: list[WireDeclaration] = []
            encoding_decl = child_declaration(media_decl, "encoding")
            if self._encoding_allowed(media, name, encoding_decl, owner, role):
                for property_name, encoding_value in wire_mapping(media.get("encoding")).items():
                    encoding = wire_mapping(encoding_value)
                    declared = child_declaration(encoding_decl, property_name)
                    used = child_declaration(media_use, "encoding", property_name)
                    header_role: TypeUseRole = (
                        "response_encoding_header" if role == "response_body" else "request_encoding_header"
                    )
                    headers = self._headers(
                        encoding.get("headers"),
                        child_declaration(declared, "headers"),
                        child_declaration(used, "headers"),
                        owner,
                        header_role,
                        status=status,
                        media=name,
                    )
                    encodings.append(
                        WireDeclaration(
                            "encoding",
                            property_name,
                            DeclarationId(self.location(declared, "declaration")),
                            self.location(used, "use"),
                            _facts(encoding, ("contentType", "style", "explode", "allowReserved")),
                            children=headers,
                        )
                    )
            media_values.append(
                WireDeclaration(
                    "media",
                    name,
                    DeclarationId(self.location(media_decl, "declaration")),
                    self.location(media_use, "use"),
                    _facts(media, ("example", "examples")),
                    tuple(uses),
                    tuple(encodings),
                )
            )
        return tuple(media_values)

    def _media_projections(
        self,
        media: dict[str, YamlValue],
        declaration: ApiDeclarationId,
    ) -> tuple[tuple[str, tuple[Literal["value", "item_stream_array"], ...]], ...]:
        return tuple(
            (keyword, ("value", "item_stream_array") if (schema, "item_stream_array") in self.schemas else ("value",))
            for keyword in ("schema", "itemSchema")
            if keyword in media and (schema := child_declaration(declaration, keyword), "value") in self.schemas
        )

    def _encoding_allowed(
        self,
        medium: dict[str, YamlValue],
        media: str,
        declaration: ApiDeclarationId,
        operation: OperationId,
        role: TypeUseRole,
    ) -> bool:
        if "encoding" not in medium:
            return False
        if self.api_scope:
            return not any(item.declaration == declaration for item in self.ignored)
        owner: MediaOwner = (
            "request_body"
            if role == "request_body"
            else "response"
            if role == "response_body"
            else "parameter"
            if role == "parameter"
            else "header"
        )
        use = self.operation_uses[operation]
        parsed = encoding_media(media, owner, openapi_32=self.parser.schema_features.media_item_schema)
        if isinstance(parsed, str):
            self.ignored.append(ApiIgnoredDeclaration(declaration, use, parsed, owner, media))
            return False
        if parsed.type != "multipart":
            for property_name, encoding in wire_mapping(medium["encoding"]).items():
                for name in wire_mapping(wire_mapping(encoding).get("headers")):
                    self.ignored.append(
                        ApiIgnoredDeclaration(
                            child_declaration(declaration, property_name, "headers", name),
                            use,
                            "oas_non_multipart_encoding_headers",
                            owner,
                            media,
                            name,
                        )
                    )
        return True

    def _parameter(  # ruff: ignore[too-many-arguments] -- Preserve independent owner, declaration, use-site, and wire identities.
        self,
        raw: YamlValue,
        declaration: ApiDeclarationId,
        use_site: ApiDeclarationId,
        owner: OperationId,
        role: TypeUseRole,
        *,
        name: str | None = None,
        status: str | None = None,
        media: str | None = None,
    ) -> WireDeclaration:
        declared, value = self._resolve_object(declaration, raw)
        wire_name = name if name is not None else str(value.get("name", ""))
        location = str(value["in"]) if "in" in value else None
        schemas = (
            (
                self._use(
                    owner,
                    role,
                    declared,
                    use_site,
                    child_declaration(declared, "schema"),
                    child_declaration(use_site, "schema"),
                    location=location,
                    name=wire_name,
                    status=status,
                    media=media,
                ),
            )
            if "schema" in value
            else ()
        )
        children = self._media(
            value.get("content"),
            declared,
            use_site,
            owner,
            role,
            status=status,
            parameter_name=wire_name,
            parameter_location=location,
        )
        return WireDeclaration(
            "parameter" if role == "parameter" else "header",
            wire_name,
            DeclarationId(self.location(declared, "declaration")),
            self.location(use_site, "use"),
            _facts(value, _PARAMETER_FACTS),
            schemas,
            children,
        )

    def _headers(  # ruff: ignore[too-many-arguments] -- Preserve independent owner, declaration, use-site, and wire identities.
        self,
        raw: YamlValue,
        declaration: ApiDeclarationId,
        use_site: ApiDeclarationId,
        owner: OperationId,
        role: TypeUseRole,
        *,
        status: str | None = None,
        media: str | None = None,
    ) -> tuple[WireDeclaration, ...]:
        ignored = {item.declaration for item in self.ignored}
        if declaration in ignored:
            return ()
        if not self.api_scope:
            for name, value in wire_mapping(raw).items():
                occurrence = child_declaration(declaration, name)
                if occurrence in ignored:
                    continue
                declared, resolved = self._resolve_object(occurrence, value)
                if "$ref" in resolved:
                    self.diagnostics.append(
                        BindingDiagnostic(
                            "BND_UNRESOLVED_REFERENCE",
                            operation=owner,
                            source_locations=(self.location(occurrence, "declaration"),),
                        )
                    )
                elif name.lower() == "content-type":
                    self.ignored.append(
                        ApiIgnoredDeclaration(
                            declared, self.operation_uses[owner], "oas_header_ignored", "header", media, name
                        )
                    )
                    ignored.add(occurrence)
        return tuple(
            self._parameter(
                value,
                child_declaration(declaration, name),
                child_declaration(use_site, name),
                owner,
                role,
                name=name,
                status=status,
                media=media,
            )
            for name, value in wire_mapping(raw).items()
            if child_declaration(declaration, name) not in ignored
        )

    def _response(
        self, raw: YamlValue, declaration: ApiDeclarationId, use_site: ApiDeclarationId, owner: OperationId, status: str
    ) -> WireDeclaration:
        declared, value = self._resolve_object(declaration, raw)
        content = self._media(
            value.get("content"),
            declared,
            use_site,
            owner,
            "response_body",
            status=status,
        )
        headers = self._headers(
            value.get("headers"),
            child_declaration(declared, "headers"),
            child_declaration(use_site, "headers"),
            owner,
            "response_header",
            status=status,
        )
        links = tuple(
            WireDeclaration(
                "link",
                name,
                DeclarationId(self.location(child_declaration(declared, "links", name), "declaration")),
                self.location(child_declaration(use_site, "links", name), "use"),
                _facts(wire_mapping(link), _LINK_FACTS),
            )
            for name, link in wire_mapping(value.get("links")).items()
        )
        return WireDeclaration(
            "response",
            status,
            DeclarationId(self.location(declared, "declaration")),
            self.location(use_site, "use"),
            _facts(value, ("description",)),
            children=(*content, *headers, *links),
        )

    def _parameters(self, frame: ApiDeclarationFrame, owner: OperationId) -> tuple[WireDeclaration, ...]:
        groups = frame.original_parameters
        candidates = (*groups[-1], *(declaration for group in groups[:-1] for declaration in group)) if groups else ()
        claimed: set[ApiDeclarationId] = set()
        parameters: list[WireDeclaration] = []
        ignored = {item.declaration for item in self.ignored}
        for target in frame.effective_parameters:
            occurrence = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate not in claimed
                    and candidate in self.objects
                    and self.objects[candidate].target.declaration == target
                ),
                target,
            )
            claimed.add(occurrence)
            if target in ignored:
                continue
            observed = self.objects.get(occurrence)
            used = observed.original_use if observed is not None else occurrence
            value = (
                observed.target.value
                if observed is not None
                else self.parser.source_lease.borrow(self.location(target, "declaration"))
            )
            parameters.append(self._parameter(value, target, used, owner, "parameter"))
        return tuple(parameters)

    @staticmethod
    def _path_item(declaration: ApiDeclarationId) -> ApiDeclarationId:
        tail = -2 if len(declaration.tokens) > 1 and declaration.tokens[-2] == "additionalOperations" else -1
        return ApiDeclarationId(declaration.document, declaration.tokens[:tail])

    def _operation_facts(self, frame: ApiDeclarationFrame) -> tuple[tuple[str, FrozenLiteral], ...]:
        effective = frame.effective_operation or frame.raw_operation or {}
        facts = _facts(effective, _OPERATION_FACTS)
        if "servers" in effective:
            return facts
        path_item = wire_mapping(
            self.parser.source_lease.borrow(self.location(self._path_item(frame.declaration), "declaration"))
        )
        root = wire_mapping(
            self.parser.source_lease.borrow(
                self.location(ApiDeclarationId(frame.root_use_site.document, ()), "declaration")
            )
        )
        return (*facts, *_facts(path_item if "servers" in path_item else root, ("servers",)))

    def _operation(self, frame: ApiDeclarationFrame, order: int, parent: OperationId | None) -> OperationContract:
        raw = frame.raw_operation or {}
        use_site = frame.root_use_site
        kind: Literal["path", "webhook", "callback"] = (
            "callback"
            if parent is not None
            else "webhook"
            if use_site.tokens and use_site.tokens[0] == "webhooks"
            else "path"
        )
        identity = OperationId(
            self.location(use_site, "use"),
            kind,
            parent,
            self.location(ApiDeclarationId(use_site.document, use_site.tokens[:-1]), "use")
            if parent is not None
            else None,
        )
        self.operations[id(frame)] = identity
        self.operation_uses[identity] = use_site
        parameters = self._parameters(frame, identity)
        body: WireDeclaration | None = None
        if "requestBody" in raw:
            used = child_declaration(use_site, "requestBody")
            declared, value = self._resolve_object(
                child_declaration(frame.declaration, "requestBody"), raw["requestBody"]
            )
            content = self._media(value.get("content"), declared, used, identity, "request_body")
            body = WireDeclaration(
                "request_body",
                None,
                DeclarationId(self.location(declared, "declaration")),
                self.location(used, "use"),
                _facts(value, ("required", "description")),
                children=content,
            )
        responses = tuple(
            self._response(
                value,
                child_declaration(frame.declaration, "responses", str(status)),
                child_declaration(use_site, "responses", str(status)),
                identity,
                str(status),
            )
            for status, value in wire_mapping(raw.get("responses")).items()
        )
        callbacks = tuple(
            WireDeclaration(
                "callback",
                name,
                DeclarationId(self.location(child_declaration(frame.declaration, "callbacks", name), "declaration")),
                self.location(child_declaration(use_site, "callbacks", name), "use"),
                (),
            )
            for name in wire_mapping(raw.get("callbacks"))
        )
        ignored_values = tuple(
            IgnoredDeclaration(
                self.location(item.declaration, "declaration"),
                self.location(item.use_site or use_site, "use"),
                item.owner,
                item.media,
                item.wire_name,
                item.reason,
            )
            for item in self.ignored
            if item.use_site == use_site
        )
        return OperationContract(
            identity,
            DeclarationId(self.location(frame.declaration, "declaration")),
            frame.declaration.tokens[-1],
            self._path_item(use_site).tokens[-1] if use_site.tokens else "",
            "operationId" in raw,
            "security" in raw,
            "servers" in raw,
            order,
            self._operation_facts(frame),
            parameters,
            body,
            responses,
            callbacks,
            ignored_values,
        )

    def freeze(self) -> FinalOperationInventory:
        """Build source-order operations, then schema-only occurrences for helper demands."""
        operations = tuple(
            self._operation(observation.frame, order, self.operations.get(id(observation.parent)))
            for order, observation in enumerate(self.operation_frames)
        )
        for (declaration, _projection), observations in self.schemas.items():
            source = self.location(declaration, "schema")
            self._use(
                source,
                "schema",
                declaration,
                declaration,
                declaration,
                declaration,
                projection=observations[0].frame.projection,
            )
        self._schema_helpers()
        return FinalOperationInventory(
            operations, tuple(self.uses.values()), tuple(self.diagnostics), self._security_schemes()
        )

    def _schema_helpers(self) -> None:
        """Expose only nested occurrences whose actual producer returned a captured type."""
        candidates: dict[SourceLocation, list[TypeProjection]] = {}
        for observation in self.parser.schema_types:
            locations = (
                tuple(origin.location for origin in self.parser.schema_origins.origins(observation.schema))
                if observation.schema is not None
                else observation.locations
            )
            if not locations:
                continue
            projected = self.imports.project(
                self.projector.project(_type_recipe(observation.data_type, self.parser.binding_ledger, set()))
            )
            for location in locations:
                candidates.setdefault(location, []).append(projected)
        for location, projections in candidates.items():
            use = TypeUseId(
                location,
                "schema",
                replace(location, role="use"),
                location,
                DeclarationId(replace(location, role="declaration")),
                "neutral",
            )
            if use in self.uses:
                continue
            selected = projections[0]
            if any(projection != selected for projection in projections[1:]):
                self.uses[use] = TypeUseBinding(use, "invalid", None, "BND_AMBIGUOUS_REPLACEMENT")
                continue
            self.uses[use] = TypeUseBinding(
                use,
                "bound" if selected.value is not None else "invalid",
                selected.value,
                selected.reason,
                tuple(self.members.get(selected.value.symbol, ()))
                if isinstance(selected.value, GeneratedSymbolType)
                else (),
                self._helper_producers(location),
            )

    def _helper_producers(self, location: SourceLocation) -> tuple[FieldSlot, ...]:
        pointer = location.pointer
        while True:
            if (slots := self.member_sources.get((location.document, pointer))) is not None:
                return tuple(slots)
            if not pointer:
                return ()
            pointer = pointer.rpartition("/")[0]

    def _security_schemes(self) -> tuple[WireDeclaration, ...]:
        declarations: list[WireDeclaration] = []
        for document in self.parser.source_lease.documents():
            root = wire_mapping(self.parser.source_lease.borrow(SourceLocation(document.id, "", "declaration")))
            schemes = wire_mapping(wire_mapping(root.get("components")).get("securitySchemes"))
            for name, raw in schemes.items():
                use = ApiDeclarationId(document.uri, ("components", "securitySchemes", name))
                declaration, value = self._resolve_object(use, raw)
                declarations.append(
                    WireDeclaration(
                        "security_scheme",
                        name,
                        DeclarationId(self.location(declaration, "declaration")),
                        self.location(use, "use"),
                        _facts(
                            value,
                            (
                                "$ref",
                                "type",
                                "description",
                                "name",
                                "in",
                                "scheme",
                                "bearerFormat",
                                "flows",
                                "openIdConnectUrl",
                            ),
                        ),
                    )
                )
        return tuple(declarations)


if TYPE_CHECKING:
    from datamodel_code_generator._generation_contract import (
        FieldSlot,
        FieldUseBinding,
        FrozenLiteral,
        SourceDocumentId,
        TypeUseRole,
    )
    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator.parser.openapi_contract import BindingCaptureMixin, SchemaUseObservation
    from datamodel_code_generator.parser.openapi_contract_fields import FinalFieldInventory
    from datamodel_code_generator.parser.openapi_contract_freeze import FinalModelInventory
    from datamodel_code_generator.parser.openapi_contract_types import FinalTypeProjector
    from datamodel_code_generator.parser.openapi_media import MediaOwner
    from datamodel_code_generator.parser.openapi_scope import ApiDeclarationFrame
    from datamodel_code_generator.types import DataType
