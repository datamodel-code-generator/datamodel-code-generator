"""Hooks the FastAPI generation tests configure: they record contexts, revise plans, and return broken patches."""

from __future__ import annotations

from typing import TYPE_CHECKING

from datamodel_code_generator._fastapi.context import FastAPIContextPatch, ImportSpec

if TYPE_CHECKING:
    from datamodel_code_generator._fastapi.context import FastAPIContext

RECORDED: list[FastAPIContext] = []
NOT_CALLABLE = 7


def record(context: FastAPIContext) -> FastAPIContextPatch:
    """Keep the context the hook receives and change nothing."""
    RECORDED.append(context)
    return FastAPIContextPatch()


def rename(context: FastAPIContext) -> FastAPIContextPatch:
    """Name every operation's handler explicitly, with a handle_ prefix."""
    return FastAPIContextPatch(
        operation_names={operation.key: f"handle_{operation.python_name}" for operation in context.operations}
    )


def reverse(context: FastAPIContext) -> FastAPIContextPatch:
    """Reverse the operations and remove the last one."""
    return FastAPIContextPatch(operation_order=tuple(operation.key for operation in context.operations[-2::-1]))


def asynchronous(context: FastAPIContext) -> FastAPIContextPatch:
    """Make every handler asynchronous."""
    return FastAPIContextPatch(handler_modes={operation.key: "async" for operation in context.operations})


def annotate(context: FastAPIContext) -> FastAPIContextPatch:
    """Add extras to every operation, router, and the context, rename routers, and add imports."""
    return FastAPIContextPatch(
        router_names={router.key: f"{router.file_stem}_routes" for router in context.routers},
        operation_extras={operation.key: {"audited": True, "method": operation.method} for operation in context.operations},
        router_extras={router.key: {"owner": ["pets", "team"]} for router in context.routers},
        extras={"generated_by": "hooks", "revision": 2},
        imports=(ImportSpec(module="logging"), ImportSpec(module="json", name="dumps", alias="dump_json")),
    )


def collide(context: FastAPIContext) -> FastAPIContextPatch:
    """Give every operation the same handler name."""
    return FastAPIContextPatch(operation_names={operation.key: "same" for operation in context.operations})


def invalid(context: FastAPIContext) -> FastAPIContextPatch:
    """Return a patch whose every field is invalid."""
    first = context.operations[0].key
    return FastAPIContextPatch(
        operation_order=(first, first),
        operation_names={"/paths/~1nope/get": "nope"},
        router_names={"tag:nope": "nope"},
        handler_modes={first: "threaded"},  # type: ignore[dict-item]
        operation_extras={first: "not a mapping"},  # type: ignore[dict-item]
        router_extras={"untagged": {"value": {1, 2}}},
        extras={"value": float("nan")},
        imports=("logging",),  # type: ignore[arg-type]
    )


def untyped(context: FastAPIContext) -> dict[str, object]:
    """Return a mapping instead of a patch."""
    return {"operation_order": tuple(operation.key for operation in context.operations)}


def failing(context: FastAPIContext) -> FastAPIContextPatch:
    """Raise an error of the hook's own, which reaches the caller unchanged."""
    message = f"The hook could not read its settings for {len(context.operations)} operations"
    raise LookupError(message)
