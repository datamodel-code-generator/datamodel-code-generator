"""Independent dry target planners reading only accepted immutable contract values."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from datamodel_code_generator._generation_contract import LiteralMapping, LiteralScalar, LiteralSequence
from datamodel_code_generator._openapi_type_binding import require_type_bindings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from datamodel_code_generator._generation_contract import (
        FrozenLiteral,
        GeneratedTypeContractBatch,
        WireDeclaration,
    )


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


def server_plan(batch: GeneratedTypeContractBatch) -> str:
    """Consume route-facing facts from this server execution's accepted batch."""
    return wire_plan(batch)


def client_plan(batch: GeneratedTypeContractBatch) -> str:
    """Consume request-facing facts from a separate client execution's accepted batch."""
    return wire_plan(batch)
