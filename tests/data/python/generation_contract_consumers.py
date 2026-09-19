"""Independent dry target planners reading only accepted immutable contract values."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from datamodel_code_generator._generation_contract import GeneratedSymbolType, LiteralMapping, LiteralScalar, LiteralSequence
from datamodel_code_generator._openapi_type_binding import require_type_bindings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from datamodel_code_generator._generation_contract import (
        FrozenLiteral,
        GeneratedTypeContractBatch,
        ModelFieldFacts,
        OperationContract,
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


@dataclass(frozen=True, slots=True)
class RoutePlan:
    """Retain only closed shared values needed by one target's route or method."""

    operation: OperationContract
    codecs: tuple[CodecPlan, ...]
    response_dispatch: tuple[tuple[str, str], ...]
    security: tuple[tuple[tuple[str, tuple[str, ...]], ...], ...]
    servers: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]


def _codec(
    action: str, binding: TypeUseBinding, batch: GeneratedTypeContractBatch, lease: SourceLease
) -> CodecPlan:
    if not isinstance(binding.type, GeneratedSymbolType):
        raise ValueError("This dry fixture requires an emitted builtin declaration")
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
    return CodecPlan(action, binding.id, f"{'/'.join(symbol.artifact.relative_path)}:{symbol.name}", tuple(properties))


def _security(operation: OperationContract) -> tuple[tuple[tuple[str, tuple[str, ...]], ...], ...]:
    facts = {key: literal(value) for key, value in operation.facts}
    return tuple(tuple((name, tuple(scopes)) for name, scopes in alternative.items()) for alternative in facts.get("security", []))


def _servers(operation: OperationContract) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
    facts = {key: literal(value) for key, value in operation.facts}
    return tuple(
        (server["url"], tuple((name, value["default"]) for name, value in server.get("variables", {}).items()))
        for server in facts.get("servers", [])
    )


def server_plan(batch: GeneratedTypeContractBatch, lease: SourceLease) -> tuple[RoutePlan, ...]:
    """Build route decoding and response encoding independently of client dispatch."""
    uses = {binding.id: binding for binding in batch.type_uses}
    routes = []
    for operation in batch.operations:
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
    return tuple(routes)


def client_plan(batch: GeneratedTypeContractBatch, lease: SourceLease) -> tuple[RoutePlan, ...]:
    """Build outbound requests and exact/range/default response selection from frozen facts."""
    uses = {binding.id: binding for binding in batch.type_uses}
    methods = []
    for operation in batch.operations:
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
    return tuple(methods)


def plan_snapshot(routes: tuple[RoutePlan, ...]) -> str:
    """Render an already closed plan; no source lease or model engine is available here."""
    lines = []
    for route in routes:
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
    return "\n".join(lines) + "\n"
