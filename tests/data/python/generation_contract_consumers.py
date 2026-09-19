"""Independent dry target planners reading only accepted immutable contract values."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from datamodel_code_generator._generation_contract import BuiltinType, GeneratedSymbolType, LiteralMapping, LiteralScalar, LiteralSequence
from datamodel_code_generator._openapi_type_binding import require_type_bindings
from tests.data.python.binding_type_snapshot import type_snapshot

if TYPE_CHECKING:
    from collections.abc import Iterator

    from datamodel_code_generator._generation_contract import (
        FrozenLiteral,
        FinalPythonType,
        GeneratedTypeContractBatch,
        ModelFieldFacts,
        OperationContract,
        OperationId,
        TypeUseBinding,
        TypeUseId,
        WireDeclaration,
    )
    from datamodel_code_generator._openapi_generation import SourceLease


def literal(value: FrozenLiteral) -> object:
    """Read the closed literal value algebra without borrowing or parsing source."""
    match value:
        case LiteralScalar(value=value):
            return value
        case LiteralSequence(items=items):
            return [literal(item) for item in items]
        case LiteralMapping(entries=entries):
            return {str(literal(key)): literal(item) for key, item in entries}
    raise TypeError(type(value))


def declarations(values: tuple[WireDeclaration, ...]) -> Iterator[WireDeclaration]:
    """Walk effective frozen wire declarations in their preserved source order."""
    for value in values:
        yield value
        yield from declarations(value.children)


def wire_plan(batch: GeneratedTypeContractBatch) -> str:
    """Build a route/request factsheet with no reference to the model engine or source lease."""
    lines: list[str] = []
    for scheme in batch.security_schemes:
        facts = {key: literal(value) for key, value in scheme.facts}
        lines.append(f"security {scheme.name}: {json.dumps(facts, separators=(',', ':'))}")
    uses = {binding.id: binding for binding in batch.type_uses}
    demands = []
    for operation in batch.operations:
        lines.extend((
            f"{operation.id.kind} {operation.method} {operation.path}",
            f"  source: {operation.id.use_site.pointer}",
        ))
        facts = {key: literal(value) for key, value in operation.facts}
        lines.extend(
            f"  {key}: {json.dumps(facts[key], separators=(',', ':'))}"
            for key in ("operationId", "security", "servers")
            if key in facts
        )
        lines.append(
            f"  explicit: {operation.explicit_operation_id}/{operation.security_declared}/{operation.servers_declared}"
        )
        values = (
            *operation.parameters,
            *((operation.request_body,) if operation.request_body else ()),
            *operation.responses,
            *operation.callbacks,
        )
        for declaration in declarations(values):
            facts = {key: literal(value) for key, value in declaration.facts if key != "description"}
            lines.append(f"  {declaration.kind} {declaration.name or '-'}: {json.dumps(facts, separators=(',', ':'))}")
            for use in declaration.schemas:
                demands.append(use)
                lines.append(
                    f"    {use.role} {use.direction} {use.name or '-'} {use.location or '-'} "
                    f"{use.status or '-'} {use.media or '-'}: {uses[use].state}"
                )
        lines.extend(f"  ignored: {ignored.source.pointer} {ignored.reason}" for ignored in operation.ignored)
    diagnostics = (*batch.diagnostics, *require_type_bindings(batch, tuple(demands)))
    lines.extend((f"demands: {len(demands)}", f"diagnostics: {','.join(item.code for item in diagnostics) or 'none'}"))
    return "\n".join(lines) + "\n"


@dataclass(frozen=True, slots=True)
class PropertyPlan:
    """Keep selected original wire rules separately from adopted model semantics."""

    wire_name: str
    native_name: str
    required: bool
    nullable: bool
    schema_rules: tuple[tuple[str, str], ...]
    model: ModelFieldFacts


@dataclass(frozen=True, slots=True)
class CodecPlan:
    """Plan one concrete native-model conversion without compiling a wire codec."""

    action: str
    use: TypeUseId
    definition: str
    properties: tuple[PropertyPlan, ...]
    type: FinalPythonType


@dataclass(frozen=True, slots=True)
class RoutePlan:
    """Retain only closed shared values needed by one target's route or method."""

    operation: OperationContract
    codecs: tuple[CodecPlan, ...]
    response_dispatch: tuple[tuple[str, str], ...]
    security: tuple[tuple[tuple[str, tuple[str, ...]], ...], ...]
    servers: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]


@dataclass(frozen=True, slots=True)
class DocumentationPlan:
    """Select callback source facts without asking the engine for additional types."""

    operation: OperationContract
    schemas: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class TargetPlan:
    """Keep selected routes, independent helper codecs, and callback documentation."""

    routes: tuple[RoutePlan, ...]
    helpers: tuple[CodecPlan, ...]
    documentation: tuple[DocumentationPlan, ...]


def _codec(
    action: str, binding: TypeUseBinding, batch: GeneratedTypeContractBatch, lease: SourceLease
) -> CodecPlan:
    if binding.type is None:
        raise ValueError("A required codec has no bound type")
    if not isinstance(binding.type, GeneratedSymbolType):
        definition = (
            f"builtins.{binding.type.name}"
            if isinstance(binding.type, BuiltinType)
            else json.dumps(type_snapshot(binding.type), separators=(",", ":"))
        )
        return CodecPlan(action, binding.id, definition, (), binding.type)
    symbol = next(value for value in batch.symbols if value.id == binding.type.symbol)
    if symbol.artifact is None:
        raise ValueError("A required codec has no emitted definition")
    properties = []
    for field in binding.members:
        if field.member_kind != "property" or field.exclusion is not None:
            continue
        if field.schema is None or field.slot is None or field.model_facts is None:
            raise ValueError("A required property has no final field or original schema")
        raw = lease.borrow(field.schema)
        parent = lease.borrow(replace(field.schema, pointer=field.schema.pointer.rsplit("/properties/", 1)[0]))
        if not isinstance(raw, dict) or not isinstance(parent, dict):
            raise ValueError("The dry fixture requires object property declarations")
        schema_type = raw.get("type")
        nullable = raw.get("nullable") is True or schema_type == "null" or (
            isinstance(schema_type, list) and "null" in schema_type
        )
        rules = tuple(
            (name, json.dumps(raw[name], separators=(",", ":")))
            for name in ("default", "format", "readOnly", "writeOnly", "minimum", "maximum", "pattern")
            if name in raw
        )
        properties.append(PropertyPlan(
            field.wire_name or "",
            field.wire_name or "" if symbol.backend == "typeddict" else field.slot.name,
            field.wire_name in parent.get("required", []),
            nullable,
            rules,
            field.model_facts,
        ))
    return CodecPlan(action, binding.id, f"{'/'.join(symbol.artifact.relative_path)}:{symbol.name}", tuple(properties), binding.type)


def _security(operation: OperationContract) -> tuple[tuple[tuple[str, tuple[str, ...]], ...], ...]:
    facts = {key: literal(value) for key, value in operation.facts}
    return tuple(tuple((name, tuple(scopes)) for name, scopes in alternative.items()) for alternative in facts.get("security", []))


def _servers(operation: OperationContract) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
    facts = {key: literal(value) for key, value in operation.facts}
    return tuple(
        (server["url"], tuple((name, value["default"]) for name, value in server.get("variables", {}).items()))
        for server in facts.get("servers", [])
    )


def _metadata_demands(batch: GeneratedTypeContractBatch, operations: tuple[OperationContract, ...]) -> None:
    """Reject required unavailable metadata without turning references into empty facts."""
    names = {
        (operation.declaration.location.document if operation.security_declared else operation.id.use_site.document, name)
        for operation in operations for alternative in _security(operation) for name, _scopes in alternative
    }
    selected = (
        *(scheme for scheme in batch.security_schemes if (scheme.use_site.document, scheme.name) in names),
        *(declaration for operation in operations for declaration in declarations(operation.responses) if declaration.kind == "link"),
    )
    failures = tuple((reference.state, reference.reference) for declaration in selected for reference in declaration.references if reference.state != "resolved")
    if failures:
        raise ValueError(failures)


def _finish_plan(
    batch: GeneratedTypeContractBatch, lease: SourceLease, routes: tuple[RoutePlan, ...], helpers: tuple[TypeUseId, ...], action: str
) -> TargetPlan:
    uses = {binding.id: binding for binding in batch.type_uses}
    if errors := require_type_bindings(batch, helpers):
        raise ValueError(tuple(error.code for error in errors))
    selected = {route.operation.id for route in routes}
    documentation = []
    for operation in batch.operations:
        if operation.id in selected or operation.id.kind != "callback":
            continue
        values = (*operation.parameters, *((operation.request_body,) if operation.request_body else ()), *operation.responses)
        schemas = []
        for declaration in declarations(values):
            for use in declaration.schemas:
                source = uses[use].schema
                if source is None:
                    raise ValueError("A documented schema has no source declaration")
                raw = lease.borrow(source)
                if isinstance(raw, dict):
                    facts = {key: value for key, value in raw.items() if key in {"$ref", "type", "required", "description"}}
                    if isinstance(properties := raw.get("properties"), dict):
                        facts["properties"] = {key: value.get("type") for key, value in properties.items() if isinstance(value, dict)}
                    schemas.append((source.pointer, json.dumps(facts, separators=(",", ":"))))
        documentation.append(DocumentationPlan(operation, tuple(schemas)))
    return TargetPlan(routes, tuple(_codec(action, uses[use], batch, lease) for use in helpers), tuple(documentation))


def server_plan(
    batch: GeneratedTypeContractBatch, lease: SourceLease, *, selected: tuple[OperationId, ...] | None = None, helpers: tuple[TypeUseId, ...] = ()
) -> TargetPlan:
    """Build route decoding and response encoding independently of client dispatch."""
    uses = {binding.id: binding for binding in batch.type_uses}
    operations = tuple(operation for operation in batch.operations if selected is None or operation.id in selected)
    _metadata_demands(batch, operations)
    routes = []
    for operation in operations:
        request = (*operation.parameters, *((operation.request_body,) if operation.request_body else ()))
        decode = tuple(use for declaration in declarations(request) for use in declaration.schemas)
        encode = tuple(use for declaration in declarations(operation.responses) for use in declaration.schemas)
        if errors := require_type_bindings(batch, (*decode, *encode)):
            raise ValueError(tuple(error.code for error in errors))
        routes.append(RoutePlan(
            operation,
            (*(_codec("decode", uses[use], batch, lease) for use in decode), *(_codec("encode", uses[use], batch, lease) for use in encode)),
            tuple((response.name or "", "emit") for response in operation.responses),
            _security(operation),
            _servers(operation),
        ))
    return _finish_plan(batch, lease, tuple(routes), helpers, "decode")


def client_plan(
    batch: GeneratedTypeContractBatch, lease: SourceLease, *, selected: tuple[OperationId, ...] | None = None, helpers: tuple[TypeUseId, ...] = ()
) -> TargetPlan:
    """Build outbound requests and exact/range/default response selection from frozen facts."""
    uses = {binding.id: binding for binding in batch.type_uses}
    operations = tuple(operation for operation in batch.operations if selected is None or operation.id in selected)
    _metadata_demands(batch, operations)
    methods = []
    for operation in operations:
        codecs = []
        for parameter in operation.parameters:
            for declaration in declarations((parameter,)):
                codecs.extend(_codec("encode", uses[use], batch, lease) for use in declaration.schemas)
        if operation.request_body is not None:
            for declaration in declarations((operation.request_body,)):
                codecs.extend(_codec("encode", uses[use], batch, lease) for use in declaration.schemas)
        dispatch = []
        for response in operation.responses:
            status = response.name or ""
            dispatch.append((status, "fallback" if status == "default" else "class" if status.endswith("XX") else "exact"))
            for declaration in declarations((response,)):
                codecs.extend(_codec("decode", uses[use], batch, lease) for use in declaration.schemas)
        if errors := require_type_bindings(batch, tuple(codec.use for codec in codecs)):
            raise ValueError(tuple(error.code for error in errors))
        methods.append(RoutePlan(operation, tuple(codecs), tuple(dispatch), _security(operation), _servers(operation)))
    return _finish_plan(batch, lease, tuple(methods), helpers, "encode")


def plan_snapshot(plan: TargetPlan) -> str:
    """Render an already closed plan; no source lease or model engine is available here."""
    lines = []
    for route in plan.routes:
        operation = route.operation
        lines.append(f"{operation.id.kind} {operation.method} {operation.path}")
        lines.append(f"  security: {json.dumps(route.security, separators=(',', ':'))}")
        lines.append(f"  servers: {json.dumps(route.servers, separators=(',', ':'))}")
        lines.append(f"  responses: {json.dumps(route.response_dispatch, separators=(',', ':'))}")
        for codec in route.codecs:
            use = codec.use
            lines.append(f"  {codec.action} {use.role} {use.name or '-'} {use.status or '-'} {use.media or '-'}: {codec.definition}")
            for field in codec.properties:
                lines.append(f"    {field.wire_name} -> {field.native_name}: required={field.required}, nullable={field.nullable}")
        for response in operation.responses:
            for link in response.children:
                if link.kind == "link":
                    facts = {key: literal(value) for key, value in link.facts}
                    lines.append(f"  link {link.name}: {json.dumps(facts, separators=(',', ':'))}")
        lines.extend(f"  callback {callback.name}" for callback in operation.callbacks)
    for helper in plan.helpers:
        lines.append(f"helper {helper.action} {helper.use.schema_site.pointer}: {helper.definition}")
    for document in plan.documentation:
        operation = document.operation
        lines.append(f"documentation {operation.method} {operation.path}")
        for declaration in declarations((*operation.parameters, *((operation.request_body,) if operation.request_body else ()), *operation.responses)):
            lines.append(f"  {declaration.kind} {declaration.name or '-'}")
        lines.extend(f"  schema {pointer}: {facts}" for pointer, facts in document.schemas)
    return "\n".join(lines) + "\n"


def metadata_snapshot(batch: GeneratedTypeContractBatch) -> str:
    """Inspect retained reference-only facts independently of the selected planner."""
    selected = (
        *(scheme for scheme in batch.security_schemes if scheme.name == "Key" and scheme.use_site.document == 0),
        *(item for operation in batch.operations for item in declarations(operation.responses) if item.kind == "link"),
    )
    documents = {document.id: document.uri for document in batch.documents}
    lines = []
    for declaration in selected:
        facts = {key: literal(value) for key, value in declaration.facts}
        lines.append(f"{declaration.kind} {declaration.name}: {json.dumps(facts, separators=(',', ':'))}")
        for reference in declaration.references:
            target = f"{documents[reference.target.document]}#{reference.target.pointer}" if reference.target else "-"
            lines.append(f"  {reference.reference}: {reference.state} -> {target}")
    return "\n".join(lines) + "\n"
