"""Find the callback operations of each operation, including those of callback objects several operations share."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING

from datamodel_code_generator._runtime.model_codecs.wire import escape_pointer_token, pointer_tokens

if TYPE_CHECKING:
    from collections.abc import Iterator

    from datamodel_code_generator._generation_contract import (
        GeneratedTypeContractBatch,
        OperationContract,
        SourceLocation,
        TypeUseId,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class CallbackNode:
    """One callback operation under an operation or callback: its keys, name, expression, method, and callbacks."""

    key: str
    parent_key: str
    name: str
    tokens: tuple[str, ...]
    operation: OperationContract
    callbacks: tuple[CallbackNode, ...]

    @property
    def expression(self) -> str:
        """Return the runtime expression the callback operation is sent to."""
        return self.tokens[0]


class CallbackIndex:
    """Index the callback operations the parser walked by the callback object they belong to."""

    def __init__(self, batch: GeneratedTypeContractBatch) -> None:
        """Keep the batch whose operations include every walked callback operation."""
        self.batch = batch

    @cached_property
    def walked(self) -> dict[SourceLocation, list[tuple[tuple[str, ...], OperationContract]]]:
        """Return each callback object's operations by expression and method, from where the parser walked it.

        The parser walks a callback object once, so every operation that uses the object finds them here.
        """
        contracts = {operation.id: operation for operation in self.batch.operations}
        walked: dict[SourceLocation, list[tuple[tuple[str, ...], OperationContract]]] = {}
        for operation in self.batch.operations:
            if (parent := operation.id.parent) is None:
                continue
            tokens = pointer_tokens(operation.id.use_site.pointer)[len(pointer_tokens(parent.use_site.pointer)) :]
            callback = next(item for item in contracts[parent].callbacks if item.name == tokens[1])
            walked.setdefault(callback.declaration.location, []).append((tuple(tokens[2:]), operation))
        return walked

    def nodes(
        self, contract: OperationContract, key: str, active: frozenset[SourceLocation] = frozenset()
    ) -> tuple[CallbackNode, ...]:
        """Return an operation's callback operations under its key; a callback inside itself is not repeated."""
        nodes: list[CallbackNode] = []
        for callback in contract.callbacks:
            if (location := callback.declaration.location) in active:
                continue
            name = str(callback.name)
            for tokens, operation in self.walked.get(location, ()):
                child = "/".join((key, "callbacks", *(escape_pointer_token(token) for token in (name, *tokens))))
                nodes.append(
                    CallbackNode(
                        key=child,
                        parent_key=key,
                        name=name,
                        tokens=tokens,
                        operation=operation,
                        callbacks=self.nodes(operation, child, active | {location}),
                    )
                )
        return tuple(nodes)


def operation_uses(operation: OperationContract) -> tuple[TypeUseId, ...]:
    """Return the type uses of an operation's parameters, responses, and body, without encoding headers."""
    pending = [*operation.parameters, *operation.responses]
    if operation.request_body is not None:
        pending.append(operation.request_body)
    found: list[TypeUseId] = []
    while pending:
        declaration = pending.pop(0)
        found.extend(declaration.schemas)
        pending.extend(child for child in declaration.children if child.kind != "encoding")
    return tuple(found)


def flattened(nodes: tuple[CallbackNode, ...]) -> Iterator[CallbackNode]:
    """Yield callback operations depth first, each before the callbacks it contains."""
    for node in nodes:
        yield node
        yield from flattened(node.callbacks)
