"""Call the FastAPI target's hooks in order, checking each patch before the next hook sees its changes."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Protocol

from typing_extensions import TypeIs

from datamodel_code_generator._api_types import APIGenerationError, Diagnostic
from datamodel_code_generator._fastapi.context import FastAPIContextPatch, HookReference, ImportSpec
from datamodel_code_generator._fastapi.naming import explicit
from datamodel_code_generator._fastapi.plan import PlanError, Revision
from datamodel_code_generator._runtime.model_codecs.wire import checked_wire

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from datamodel_code_generator._fastapi.context import FastAPIContext, FastAPIHook, FrozenJSONMap
    from datamodel_code_generator._runtime.model_codecs.wire import WireValue

_MODES: Final = frozenset({"sync", "async"})


@dataclass(frozen=True, slots=True, kw_only=True)
class Extensions:
    """Context values hooks add without changing the plan: extras by operation, router, and context, and imports."""

    operation_extras: Mapping[str, FrozenJSONMap] = field(default_factory=lambda: MappingProxyType({}))
    router_extras: Mapping[str, FrozenJSONMap] = field(default_factory=lambda: MappingProxyType({}))
    extras: FrozenJSONMap = field(default_factory=lambda: MappingProxyType({}))
    imports: tuple[ImportSpec, ...] = ()


class ContextSource(Protocol):
    """Plan the server under a revision and return its context with the hooks' extensions."""

    def context(self, revision: Revision, extensions: Extensions) -> FastAPIContext:
        """Return the context of the revised plan; a plan conflict raises PlanError."""
        raise NotImplementedError


class HookRunner:
    """Call each configured hook once at each position, loading a referenced hook once per generation."""

    def __init__(self, hooks: tuple[HookReference | FastAPIHook, ...], target_id: str) -> None:
        """Keep the hooks in configuration order and the target the diagnostics name."""
        self.hooks = hooks
        self.target_id = target_id
        self.loaded: dict[HookReference, Callable[[FastAPIContext], object]] = {}

    def run(self, source: ContextSource) -> tuple[Revision, Extensions, FastAPIContext]:
        """Return the revision and extensions every hook asked for, and the context they produce."""
        revision, extensions = Revision(), Extensions()
        context = source.context(revision, extensions)
        for index, hook in enumerate(self.hooks):
            patch = _call(self.callable(index, hook), context)
            if not isinstance(patch, FastAPIContextPatch):
                raise APIGenerationError((
                    self.diagnostic("E_HOOK_CONTRACT", index, f"returned {type(patch).__name__}, not a patch"),
                ))
            if problems := tuple(self.diagnostic("E_HOOK_CONTRACT", index, text) for text in _problems(patch, context)):
                raise APIGenerationError(problems)
            revision, extensions = _revised(revision, extensions, patch)
            try:
                context = source.context(revision, extensions)
            except PlanError as error:
                raise APIGenerationError(
                    tuple(
                        self.diagnostic("E_HOOK_CONFLICT", index, f"made a conflicting change: {item.message}")
                        for item in error.diagnostics
                    )
                ) from None
        return revision, extensions, context

    def callable(self, index: int, hook: HookReference | FastAPIHook) -> Callable[[FastAPIContext], object]:
        """Return a configured callable, or the callable a hook reference names, loading its module once."""
        if not isinstance(hook, HookReference):
            return hook
        if (loaded := self.loaded.get(hook)) is None:
            function = getattr(_module(hook), hook.callable, None)
            if not callable(function):
                raise APIGenerationError((
                    self.diagnostic("E_HOOK_CONTRACT", index, f"names {hook.callable}, which is not callable"),
                ))
            loaded = self.loaded[hook] = function
        return loaded

    def diagnostic(self, code: str, index: int, message: str) -> Diagnostic:
        """Return a hook diagnostic that names the hook's position in the configuration."""
        return Diagnostic(
            code=code,
            severity="error",
            stage="hook",
            message=f"Hook {index} {message}",
            option_path=f"hooks[{index}]",
            target_id=self.target_id,
        )


def _module(hook: HookReference) -> object:
    if hook.module is not None:
        return importlib.import_module(hook.module)
    assert hook.file is not None
    path = hook.file.resolve()
    digest = hashlib.sha256(bytes(path) + b"\0" + path.read_bytes()).hexdigest()
    name = f"_dcg_fastapi_hook_{digest[:24]}"
    if (module := sys.modules.get(name)) is not None:
        return module
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    return module


def _call(function: Callable[[FastAPIContext], object], context: FastAPIContext) -> object:
    return function(context)


def _problems(patch: FastAPIContextPatch, context: FastAPIContext) -> Iterator[str]:
    keys = {operation.key for operation in context.operations}
    groups = {router.key for router in context.routers}
    order: object = patch.operation_order
    if order is not None and not (
        _is_tuple(order) and len(set(order)) == len(order) and all(key in keys for key in order)
    ):
        yield "returned an operation_order that is not a tuple of distinct operation keys of the context"
    checks: tuple[tuple[str, object, set[str], Callable[[object], bool]], ...] = (
        ("operation_names", patch.operation_names, keys, _identifier),
        ("router_names", patch.router_names, groups, _identifier),
        ("handler_modes", patch.handler_modes, keys, _MODES.__contains__),
        ("operation_extras", patch.operation_extras, keys, _json_map),
        ("router_extras", patch.router_extras, groups, _json_map),
    )
    for name, value, known, valid in checks:
        if not _keyed(value, known, valid):
            yield f"returned {name} that do not map keys of the context to valid values"
    if not _json_map(patch.extras):
        yield "returned extras that are not a mapping of names to JSON values"
    imports: object = patch.imports
    if not (_is_tuple(imports) and all(_import(item) for item in imports)):
        yield "returned imports that are not ImportSpec records of dotted modules and identifiers"


def _identifier(value: object) -> bool:
    return isinstance(value, str) and explicit(value)


def _keyed(value: object, known: set[str], valid: Callable[[object], bool]) -> bool:
    return _is_mapping(value) and all(key in known and valid(item) for key, item in value.items())


def _is_tuple(value: object) -> TypeIs[tuple[object, ...]]:
    return isinstance(value, tuple)


def _is_mapping(value: object) -> TypeIs[Mapping[object, object]]:
    return isinstance(value, Mapping)


def _json_map(value: object) -> bool:
    if not _is_mapping(value):
        return False
    try:
        checked_wire(value)
    except (TypeError, ValueError):
        return False
    return True


def _import(value: object) -> bool:
    return (
        isinstance(value, ImportSpec)
        and _dotted(value.module)
        and _optional_identifier(value.name)
        and _optional_identifier(value.alias)
        and type(value.type_checking) is bool
    )


def _dotted(value: object) -> bool:
    return isinstance(value, str) and all(part.isidentifier() for part in value.split("."))


def _optional_identifier(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.isidentifier())


def _frozen(value: Mapping[str, object]) -> FrozenJSONMap:
    frozen: WireValue = checked_wire(value)
    assert isinstance(frozen, Mapping)
    return frozen


def _revised(revision: Revision, extensions: Extensions, patch: FastAPIContextPatch) -> tuple[Revision, Extensions]:
    return (
        Revision(
            order=revision.order if patch.operation_order is None else patch.operation_order,
            operation_names=MappingProxyType({**revision.operation_names, **patch.operation_names}),
            router_names=MappingProxyType({**revision.router_names, **patch.router_names}),
            handler_modes=MappingProxyType({**revision.handler_modes, **patch.handler_modes}),
        ),
        Extensions(
            operation_extras=MappingProxyType({
                **extensions.operation_extras,
                **{key: _frozen(value) for key, value in patch.operation_extras.items()},
            }),
            router_extras=MappingProxyType({
                **extensions.router_extras,
                **{key: _frozen(value) for key, value in patch.router_extras.items()},
            }),
            extras=MappingProxyType({**extensions.extras, **_frozen(patch.extras)}),
            imports=tuple(dict.fromkeys((*extensions.imports, *patch.imports))),
        ),
    )


def extended(context: FastAPIContext, extensions: Extensions) -> FastAPIContext:
    """Return a context with the hooks' extras on its operations, routers, and itself, and their imports."""
    return replace(
        context,
        operations=tuple(
            replace(operation, extras=extras)
            if (extras := extensions.operation_extras.get(operation.key)) is not None
            else operation
            for operation in context.operations
        ),
        routers=tuple(
            replace(router, extras=extras)
            if (extras := extensions.router_extras.get(router.key)) is not None
            else router
            for router in context.routers
        ),
        extras=extensions.extras,
        imports=(*context.imports, *extensions.imports),
    )
