"""A hook loaded from a file: it names each tagged operation after its first tag and method."""

from __future__ import annotations

from datamodel_code_generator._fastapi.context import FastAPIContext, FastAPIContextPatch


def transform(context: FastAPIContext) -> FastAPIContextPatch:
    """Name tagged handlers after their first tag and method."""
    return FastAPIContextPatch(
        operation_names={
            operation.key: f"{operation.tags[0]}_{operation.method}" for operation in context.operations if operation.tags
        }
    )
