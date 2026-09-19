"""Project already observed legacy declarations through the shared wire normalizer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from typing_extensions import override

from datamodel_code_generator import OpenAPIScope, SchemaParseError
from datamodel_code_generator._generation_contract import (
    BindingCaptureError,
    BindingDiagnostic,
    GeneratedSymbolType,
    TypeProjection,
)
from datamodel_code_generator.model.base import DataModel
from datamodel_code_generator.parser._api_reference import ApiDeclarationId, pointer_tokens
from datamodel_code_generator.parser.openapi import OPERATION_NAMES
from datamodel_code_generator.parser.openapi_contract import OperationObservation
from datamodel_code_generator.parser.openapi_contract_operations import (
    FinalOperationBuilder,
    child_declaration,
    wire_mapping,
)
from datamodel_code_generator.parser.openapi_contract_store import _type_recipe  # pyright: ignore[reportPrivateUsage]
from datamodel_code_generator.parser.openapi_scope import ApiDeclarationFrame, ApiIgnoredDeclaration


class LegacyFinalOperationBuilder(FinalOperationBuilder):
    """Retain legacy generation scopes; never run the API walker or create a model."""

    def __init__(
        self,
        parser: BindingCaptureMixin,
        inventory: FinalModelInventory,
        fields: FinalFieldInventory,
        projector: FinalTypeProjector,
    ) -> None:
        """Join actual producer returns and original source identities once."""
        super().__init__(parser, inventory, fields, projector)
        self.schema_references: dict[ApiDeclarationId, set[GraphObjectId]] = {}
        documents = {document.id: document.uri for document in parser.source_lease.documents()}
        for observation in parser.schema_references:
            for origin in parser.schema_origins.origins(observation.schema):
                tokens = (
                    tuple(
                        token.replace("~1", "/").replace("~0", "~") for token in origin.location.pointer[1:].split("/")
                    )
                    if origin.location.pointer
                    else ()
                )
                declaration = ApiDeclarationId(documents[origin.location.document], tokens)
                self.schema_references.setdefault(declaration, set()).add(observation.reference)
        self.legacy_types: dict[tuple[ApiDeclarationId, str], list[DataType]] = {}
        self.observations: dict[ApiDeclarationId, LegacyOperationObservation] = {}
        for observation in parser.legacy_operations:
            if observation.origin_state == "known":
                self.observations[observation.candidates[0].declaration] = observation
            else:
                self.diagnostics.append(
                    BindingDiagnostic(
                        "BND_OPERATION_ORIGIN_UNRESOLVED",
                        source_locations=tuple(
                            self.location(candidate.declaration, "use") for candidate in observation.candidates
                        ),
                    )
                )
        self._observed_media()
        for observation in parser.parameter_fields:
            if (origin := parser.field_origins.get(parser.binding_ledger.identity(observation.field))) is None:
                continue
            for source in origin.origins:
                declaration = ApiDeclarationId(
                    documents[source.location.document],
                    tuple(
                        token.replace("~1", "/").replace("~0", "~") for token in source.location.pointer[1:].split("/")
                    ),
                )
                self.legacy_types.setdefault((declaration, "value"), []).append(observation.field.data_type)
        for uri in parser.root_documents:
            root = wire_mapping(parser.source_lease.borrow(self.location(ApiDeclarationId(uri, ()), "declaration")))
            self._root_operations(uri, root)

    def _observed_media(self) -> None:
        for observation in self.parser.request_types:
            if observation.operation is None or observation.operation.origin_state != "known":
                continue
            operation = observation.operation.candidates[0].declaration
            body = child_declaration(operation, "requestBody")
            declaration, raw = self._resolve_object(body, observation.operation.candidates[0].raw.get("requestBody"))
            for media, data_type in observation.types.items():
                self._record_media_type(declaration, media, raw, data_type)
        for observation in self.parser.response_types:
            if observation.operation is None or observation.operation.origin_state != "known":
                continue
            operation = observation.operation.candidates[0].declaration
            responses = wire_mapping(observation.operation.candidates[0].raw.get("responses"))
            for status, media_types in observation.types.items():
                response = child_declaration(operation, "responses", str(status))
                declaration, raw = self._resolve_object(response, responses.get(str(status)))
                for media, data_type in media_types.items():
                    self._record_media_type(declaration, media, raw, data_type)

    def _record_media_type(
        self, declaration: ApiDeclarationId, media: str, raw: dict[str, YamlValue], data_type: DataType
    ) -> None:
        medium = wire_mapping(wire_mapping(raw.get("content")).get(media))
        keyword = "schema" if "schema" in medium else "itemSchema"
        if medium.get(keyword) is None:
            return
        schema = child_declaration(declaration, "content", media, keyword)
        projection = "item_stream_array" if keyword == "itemSchema" else "value"
        self.legacy_types.setdefault((schema, projection), []).append(data_type)
        if keyword != "itemSchema":
            return
        container = data_type
        if (
            data_type.reference is not None
            and isinstance(model := data_type.reference.source, DataModel)
            and (model.IS_ALIAS or model.IS_ROOT_MODEL)
            and len(model.fields) == 1
        ):
            container = model.fields[0].data_type
        if container.is_list and len(container.data_types) == 1:
            self.legacy_types.setdefault((schema, "value"), []).append(container.data_types[0])

    @override
    def _media_projections(
        self,
        media: dict[str, YamlValue],
        declaration: ApiDeclarationId,
    ) -> tuple[tuple[str, tuple[Literal["value", "item_stream_array"], ...]], ...]:
        if "schema" in media:
            return (("schema", ("value",)),) if media["schema"] is not None else ()
        if media.get("itemSchema") is not None and self.parser.schema_features.media_item_schema:
            schema = child_declaration(declaration, "itemSchema")
            projections: tuple[Literal["value", "item_stream_array"], ...] = (
                ("value", "item_stream_array") if (schema, "item_stream_array") in self.legacy_types else ("value",)
            )
            return (("itemSchema", projections),)
        return ()

    def _root_operations(self, uri: str, root: dict[str, YamlValue]) -> None:
        scopes = self.parser.open_api_scopes
        observed_only = OpenAPIScope.Paths in scopes or OpenAPIScope.Webhooks in scopes
        for scope in ("paths", "webhooks"):
            for path, path_item in wire_mapping(root.get(scope)).items():
                if path.startswith("x-") or not isinstance(path_item, dict):
                    continue
                if "$ref" in path_item:
                    if not observed_only:
                        self.diagnostics.append(
                            BindingDiagnostic(
                                "BND_OPERATION_ORIGIN_UNRESOLVED",
                                source_locations=(self.location(ApiDeclarationId(uri, (scope, path)), "use"),),
                            )
                        )
                    continue
                for method, raw in path_item.items():
                    if method not in OPERATION_NAMES or not isinstance(raw, dict):
                        continue
                    declaration = ApiDeclarationId(uri, (scope, path, method))
                    observed = self.observations.get(declaration)
                    if observed_only and observed is None:
                        continue
                    effective = observed.effective if observed is not None else raw
                    if "security" not in effective and "security" in root:
                        effective = {**effective, "security": root["security"]}
                    frame = ApiDeclarationFrame(
                        declaration,
                        root,
                        "",
                        declaration,
                        "schema",
                        "operation",
                        raw_operation=raw,
                        effective_operation=effective,
                    )
                    self._source_operation(frame, None, set())

    def _source_operation(
        self, frame: ApiDeclarationFrame, parent: ApiDeclarationFrame | None, active: set[ApiDeclarationId]
    ) -> None:
        """Retain callback declarations without parsing schemas or demanding their codecs."""
        self.operation_frames.append(OperationObservation(frame, parent))
        for name, callback in wire_mapping((frame.raw_operation or {}).get("callbacks")).items():
            declaration = child_declaration(frame.declaration, "callbacks", name)
            declared, value, _references = self._metadata_target(declaration, callback)
            if declared in active:
                continue
            active.add(declared)
            for expression, path_item in value.items():
                if expression.startswith(("$", "x-")) or not isinstance(path_item, dict):
                    continue
                item, raw_item, _references = self._metadata_target(child_declaration(declared, expression), path_item)
                document = wire_mapping(
                    self.parser.source_lease.borrow(self.location(ApiDeclarationId(item.document, ()), "declaration"))
                )
                root = wire_mapping(
                    self.parser.source_lease.borrow(
                        self.location(ApiDeclarationId(frame.root_use_site.document, ()), "declaration")
                    )
                )
                for method, raw in raw_item.items():
                    if method not in OPERATION_NAMES or not isinstance(raw, dict):
                        continue
                    effective = (
                        {**raw, "security": root["security"]} if "security" not in raw and "security" in root else raw
                    )
                    child = ApiDeclarationFrame(
                        child_declaration(item, method),
                        document,
                        "",
                        child_declaration(frame.root_use_site, "callbacks", name, expression, method),
                        "schema",
                        "operation",
                        raw_operation=raw,
                        effective_operation=effective,
                    )
                    self._source_operation(child, frame, active)
            active.remove(declared)

    def _resolve_object(
        self, declaration: ApiDeclarationId, raw: YamlValue
    ) -> tuple[ApiDeclarationId, dict[str, YamlValue]]:
        """Read only document-local pointers already present in the borrowed catalog."""
        value = wire_mapping(raw)
        seen: set[ApiDeclarationId] = set()
        while isinstance(ref := value.get("$ref"), str) and ref.startswith("#"):
            if declaration in seen:
                return declaration, value
            seen.add(declaration)
            try:
                tokens = pointer_tokens(ref)
                if tokens is None:
                    return declaration, value
                target = ApiDeclarationId(declaration.document, tokens)
                borrowed = self.parser.source_lease.borrow(self.location(target, "declaration"))
            except (BindingCaptureError, SchemaParseError):
                return declaration, value
            if not isinstance(borrowed, dict):
                return declaration, value
            declaration, value = target, borrowed
        return declaration, value

    def _schema_target(self, declaration: ApiDeclarationId) -> ApiDeclarationId:
        raw = self.parser.source_lease.borrow(self.location(declaration, "schema"))
        if isinstance(raw, dict) and set(raw) <= {"$ref", "description", "summary"}:
            return self._resolve_object(declaration, raw)[0]
        return declaration

    @override
    def _schema_helpers(self) -> None:
        super()._schema_helpers()
        for declaration in self.schema_references:
            source = self.location(declaration, "schema")
            self._use(source, "schema", declaration, declaration, declaration, declaration)

    @override
    def _project_schema_type(self, declaration: ApiDeclarationId, projection: str, direction: str) -> TypeProjection:
        projector = self.directional_projectors[direction]
        if actual := self.legacy_types.get((declaration, projection)):
            projections = tuple(
                projector.project(_type_recipe(value, self.parser.binding_ledger, set())) for value in actual
            )
            if all(value == projections[0] for value in projections):
                return projections[0]
            return TypeProjection(None, "BND_TYPE_EXPRESSION_UNSUPPORTED")
        return self._component_type(declaration, direction)

    def _component_type(self, declaration: ApiDeclarationId, direction: str) -> TypeProjection:
        """Project only a uniquely captured component's adopted reference policy."""
        projector = self.directional_projectors[direction]
        target = self._schema_target(declaration)
        references = self.schema_references.get(target, set())
        if len(references) != 1:
            return TypeProjection(None, "BND_SYMBOL_NOT_EMITTED")
        reference = next(iter(references))
        if target != declaration:
            raw = wire_mapping(self.parser.source_lease.borrow(self.location(target, "schema")))
            if raw.get("discriminator"):
                return TypeProjection(None, "BND_SYMBOL_NOT_EMITTED")
            return projector.reference_use(
                reference, serialize_as_any=self.parser.data_type_manager.use_serialize_as_any
            )
        if (symbol := self.directional_references[direction].get(reference)) is not None:
            return TypeProjection(GeneratedSymbolType(symbol))
        if (recipe := self.parser.binding_ledger.root_recipes.get(reference)) is not None:
            return projector.project(recipe)
        return TypeProjection(None, "BND_SYMBOL_NOT_EMITTED")

    @override
    def _source_members(self, schema: ApiDeclarationId, projection: str, symbol: int) -> tuple[FieldUseBinding, ...]:
        target = self._schema_target(schema)
        references = self.schema_references.get(target, set())
        if (
            len(references) == 1
            and (members := self.source_members.get(next(iter(references)))) is not None
            and all(member.consumer == symbol for member in members)
        ):
            return members
        return tuple(self.members.get(symbol, ()))

    def _parameters(self, frame: ApiDeclarationFrame, owner: OperationId) -> tuple[WireDeclaration, ...]:
        """Correlate actual effective parameter identities, or local schema-only declarations."""
        path_item = wire_mapping(
            self.parser.source_lease.borrow(self.location(self._path_item(frame.declaration), "declaration"))
        )
        root_paths = wire_mapping(frame.raw_document.get(frame.declaration.tokens[0]))
        groups = (
            (
                ApiDeclarationId(frame.declaration.document, (frame.declaration.tokens[0], "parameters")),
                root_paths.get("parameters"),
            ),
            (child_declaration(self._path_item(frame.declaration), "parameters"), path_item.get("parameters")),
            (child_declaration(frame.declaration, "parameters"), (frame.raw_operation or {}).get("parameters")),
        )
        candidates = tuple(
            (child_declaration(parent, str(index)), value)
            for parent, raw in groups
            if isinstance(raw, list)
            for index, value in enumerate(raw)
        )
        observed = self.observations.get(frame.declaration)
        if observed is not None:
            parameters: list[WireDeclaration] = []
            effective = observed.effective.get("parameters", [])
            if not isinstance(effective, list):
                self.diagnostics.append(BindingDiagnostic("BND_OPERATION_ORIGIN_UNRESOLVED", operation=owner))
                return ()
            for raw in effective:
                matches = tuple(declaration for declaration, candidate in candidates if candidate is raw)
                if len(matches) != 1:
                    self.diagnostics.append(BindingDiagnostic("BND_OPERATION_ORIGIN_UNRESOLVED", operation=owner))
                    continue
                if self._parameter_allowed(matches[0], raw, owner):
                    parameters.append(
                        self._parameter(raw, matches[0], self._parameter_use(frame, matches[0]), owner, "parameter")
                    )
            return tuple(parameters)
        operation = tuple(
            (declaration, raw)
            for declaration, raw in candidates
            if declaration.tokens[:-1] == (*frame.declaration.tokens, "parameters")
        )
        keys = {
            (value.get("name"), value.get("in"))
            for declaration, raw in operation
            for _, value in (self._resolve_object(declaration, raw),)
        }
        common = tuple(
            (declaration, raw)
            for declaration, raw in candidates
            if declaration.tokens[:-1] != (*frame.declaration.tokens, "parameters")
            and (value := self._resolve_object(declaration, raw)[1])
            and (value.get("name"), value.get("in")) not in keys
        )
        return tuple(
            self._parameter(raw, declaration, self._parameter_use(frame, declaration), owner, "parameter")
            for declaration, raw in (*operation, *common)
            if self._parameter_allowed(declaration, raw, owner)
        )

    def _parameter_use(self, frame: ApiDeclarationFrame, declaration: ApiDeclarationId) -> ApiDeclarationId:
        """Keep a referenced operation's source declaration separate from its root use."""
        for original, use in (
            (frame.declaration, frame.root_use_site),
            (self._path_item(frame.declaration), self._path_item(frame.root_use_site)),
        ):
            length = len(original.tokens)
            if declaration.document == original.document and declaration.tokens[:length] == original.tokens:
                return child_declaration(use, *declaration.tokens[length:])
        return declaration

    def _parameter_allowed(self, declaration: ApiDeclarationId, raw: YamlValue, owner: OperationId) -> bool:
        declared, value = self._resolve_object(declaration, raw)
        if "$ref" in value:
            self.diagnostics.append(
                BindingDiagnostic(
                    "BND_UNRESOLVED_REFERENCE",
                    operation=owner,
                    source_locations=(self.location(declaration, "declaration"),),
                )
            )
            return False
        if (
            value.get("in") == "header"
            and isinstance(name := value.get("name"), str)
            and name.lower() in {"accept", "content-type", "authorization"}
        ):
            self.ignored.append(
                ApiIgnoredDeclaration(
                    declared, self.operation_uses[owner], "oas_header_ignored", "parameter", wire_name=name
                )
            )
            return False
        return True


if TYPE_CHECKING:
    from datamodel_code_generator._generation_contract import (
        FieldUseBinding,
        GraphObjectId,
        OperationId,
        WireDeclaration,
    )
    from datamodel_code_generator._source import YamlValue
    from datamodel_code_generator.parser.openapi_contract import BindingCaptureMixin, LegacyOperationObservation
    from datamodel_code_generator.parser.openapi_contract_fields import FinalFieldInventory
    from datamodel_code_generator.parser.openapi_contract_freeze import FinalModelInventory
    from datamodel_code_generator.parser.openapi_contract_types import FinalTypeProjector
    from datamodel_code_generator.types import DataType
