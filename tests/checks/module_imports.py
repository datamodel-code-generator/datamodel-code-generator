"""``MODULE_IMPORTS``: every module's imports are what its import ledger replays, and what its import plan holds.

The imports of every module, and those all modules share, equal the replay of their ledger entries when the module's
import text is written, so a write that bypasses ``ModuleImportLedger`` fails. After the last rename, each module's
import plan (``ModuleImportPlan``) is made from the final names (``NameTable``): its own models by their class names,
those of other modules through imports whose names ``allocate_bindings`` gives from the names the module asked for
through the ledger (``NameLog``). The module must write for each model it names what the plan writes, and its imports
must bind every import the plan holds. With a report file, it also records how many model names of each module it
compared.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import wraps
from typing import TYPE_CHECKING, Any, Final, TypeAlias, final

from tests.checks.areas import Area, Mode, mode
from tests.checks.render_plans import check_frozen_render_plans

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Iterator, Mapping

    from datamodel_code_generator._format_types import PythonVersion
    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator.imports import Import, Imports
    from datamodel_code_generator.parser._graph_ids import KeyedModels
    from datamodel_code_generator.reference import Reference

_NAMES: Final = "name events"
"""The key of the names each module asked for and released through the ledger, in the state of the parse."""
_PLANS: Final = "import plans"
"""The key of the import plan of each module, in the state of the parse."""


@final
@dataclass(frozen=True)
class NameRequested:
    """``ModelResolver.add`` on the resolver of a module: the path, the name asked for, as a class name or unique."""

    path: tuple[str, ...]
    original_name: str
    class_name: bool = False
    unique: bool = True


@final
@dataclass(frozen=True)
class NamesReleased:
    """``ModelResolver.delete_names`` on the resolver of a module."""

    names: frozenset[str]


NameEvent: TypeAlias = NameRequested | NamesReleased
"""One change to the names the resolver of a module holds."""


@final
@dataclass(frozen=True)
class NameLog:
    """The names the resolver of a module started with and every change to them, in the order made."""

    taken: frozenset[str]
    events: tuple[NameEvent, ...]


@final
@dataclass(frozen=True)
class BindingRequest:
    """The import of a model of another module: the resolver path of the import and the name it asks for."""

    path: tuple[str, ...]
    name: str


def allocate_bindings(
    requests: Iterable[BindingRequest], log: NameLog, *, target_python_version: PythonVersion
) -> dict[BindingRequest, str]:
    """Return the name each request binds: the name the resolver of its module holds for the request's path.

    Row ``legacy.alias-order``: a name is unique among the names taken when it was asked for, so the names are
    allocated again in the order the log records, against the names it records: the module's field names, its class
    names as of L5, the imports that L5 and the repairs asked for (also those dropped later) and the names a repair
    released. A request that the log never asked for under its path gets no name: no pass imported it.
    """
    from datamodel_code_generator.reference import ModelResolver

    resolver = ModelResolver(exclude_names=set(log.taken), target_python_version=target_python_version)
    for event in log.events:
        if isinstance(event, NamesReleased):
            resolver.delete_names(event.names)
        else:
            resolver.add(event.path, event.original_name, class_name=event.class_name, unique=event.unique)
    return {
        request: reference.name
        for request in requests
        if (reference := resolver.references.get(ModelResolver.join_path(request.path))) is not None
        and request.name in {reference.original_name, reference.name}
    }


@final
@dataclass(frozen=True)
class FinalName:
    """The class name of a model and the module that writes it, dotted and as a path, read after the last rename."""

    class_name: str
    module: str
    module_path: tuple[str, ...]


@final
@dataclass(frozen=True)
class NameTable:
    """The final name of every model the modules write, by model key."""

    names: Mapping[ModelKey, FinalName]

    def class_name(self, target: ModelKey, reference: Reference) -> str:
        """Return the name a module writes for the model ``target`` that ``reference`` names.

        Row ``legacy-keyless-reference-by-path``: a reference whose own key names no written model (one built outside
        the resolver, or the Reference of a duplicate that reuse replaced) names the model at its path, or the
        subclass that replaced it, but is written by its own name.
        """
        from datamodel_code_generator._model_keys import model_key_of

        return self.names[target].class_name if model_key_of(reference) == target else reference.short_name


@final
@dataclass(frozen=True)
class ReferenceImport:
    """A model of another module that a module imports, with the import that binds it."""

    target: ModelKey
    module: str
    """The module of the model (dotted)."""
    import_: Import
    """The import line, its alias being the name the import binds."""


@final
@dataclass(frozen=True)
class ReferenceUse:
    """One place where a module writes the name of a model: what the plan writes there and what was written."""

    target: ModelKey
    holder: str
    """The class name of the model that writes it."""
    planned: str | None
    """The class name in the model's own module, else the expression of its import; None when no import binds it.

    A reference that names no written model is planned as its own name."""
    written: str


@final
@dataclass(frozen=True)
class ModuleImportPlan:
    """The models one module names, planned from the final names: imports of other modules' models and what is written.

    The imports the module needs for other names (``symbols``), its ``__future__`` imports and the aliases that field
    names force over its bindings (``_bind_module_field_names``, pass ``field-name-bindings``) are not planned: the
    plan is compared with the module's imports without those aliases and with what the module writes.
    """

    module: tuple[str, ...]
    references: tuple[ReferenceImport, ...]
    uses: tuple[ReferenceUse, ...]

    def differences(self, imports: Imports) -> tuple[str, ...]:
        """Describe each use written differently, each planned import ``imports`` lacks and each it has extra."""
        from datamodel_code_generator.imports import Imports

        storage_key = Imports._storage_key
        planned = {storage_key(reference.import_) for reference in self.references}
        return (
            *(
                f"{use.holder} writes {use.written} where the plan "
                + (f"writes {use.planned}" if use.planned is not None else "binds no name")
                for use in self.uses
                if use.planned != use.written
            ),
            *(
                f"no import of {reference.import_.import_} from {reference.import_.from_} binds "
                f"{reference.import_.binding_name}"
                for reference in self.references
                if not imports.counter.get(key := storage_key(reference.import_))
                or imports.get_effective_name(*key) != reference.import_.binding_name
            ),
            *(
                f"the plan lacks the import of {import_.import_} from {import_.from_}"
                for import_ in imports.reference_paths.values()
                if (key := storage_key(import_)) not in planned and imports.counter.get(key)
            ),
        )


def _name_events(imports_mode: Mode, imports: object) -> list[NameEvent]:
    """Return the name events of the module whose imports are ``imports``, which the state holds with them."""
    return imports_mode.state.setdefault((_NAMES, id(imports)), (imports, []))[1]


def _record_name_event(imports: object, event: NameEvent) -> None:
    """Keep ``event`` in the ``NameLog`` of the module whose imports are ``imports``, while the area is on."""
    if (imports_mode := mode(Area.MODULE_IMPORTS)) is not None:
        _name_events(imports_mode, imports).append(event)


def add_name(original: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap ``ModuleImportLedger.add_name``: keep the name asked for as an event of the module's ``NameLog``."""

    @wraps(original)
    def add_name(
        self: Any,
        imports: object,
        resolver: object,
        path: Iterable[str],
        original_name: str,
        *,
        class_name: bool = False,
        unique: bool = True,
    ) -> Any:
        _record_name_event(imports, NameRequested(tuple(path), original_name, class_name, unique))
        return original(self, imports, resolver, path, original_name, class_name=class_name, unique=unique)

    return add_name


def release_names(original: Callable[..., None]) -> Callable[..., None]:
    """Wrap ``ModuleImportLedger.release_names``: keep the released names as an event of the module's ``NameLog``."""

    @wraps(original)
    def release_names(self: Any, imports: object, resolver: object, names: Collection[str]) -> None:
        _record_name_event(imports, NamesReleased(frozenset(names)))
        return original(self, imports, resolver, names)

    return release_names


def render(original: Callable[[Any, Any], None]) -> Callable[[Any, Any], None]:
    """Wrap ``imports.render``: before the modules are written, check the frozen render plans and plan the imports."""

    @wraps(original)
    def render(parser: Any, run: Any) -> None:
        check_frozen_render_plans()
        if (imports_mode := mode(Area.MODULE_IMPORTS)) is not None:
            _plan_module_imports(imports_mode, parser, run)
        return original(parser, run)

    return render


def get_module_future_imports(original: Callable[..., str]) -> Callable[..., str]:
    """Wrap ``Parser._get_module_future_imports``: where a module's import text is written, compare its imports.

    The parser takes the module's ``__future__`` imports out first, through the ledger.
    """

    @wraps(original)
    def get_module_future_imports(self: Any, ctx: Any, config: object, future_imports_str: str) -> str:
        text = original(self, ctx, config, future_imports_str)
        if (imports_mode := mode(Area.MODULE_IMPORTS)) is not None:
            _check_module_imports(imports_mode, self, ctx)
        return text

    return get_module_future_imports


def _violation(imports_mode: Mode, message: str, **details: object) -> None:
    from datamodel_code_generator import Error

    imports_mode.violation(Error, message, **details)


def _replay(ledger: Any, imports: Imports, *, without: str | None = None) -> Imports:
    """Return the imports that the ledger's entries for ``imports`` write, without those of the pass ``without``."""
    from datamodel_code_generator.parser._imports_plan import replay

    entries = ledger.entries(imports)
    return replay((entry for entry in entries if entry.producer.pass_ != without), use_exact=imports.use_exact)


def _differences(live: Imports, replayed: Imports) -> tuple[str, ...]:
    """Name the parts of two imports that differ: names per module (in module order), counts, aliases, reference paths.

    Reading ``Imports.alias`` creates empty alias maps, which bind nothing and are not compared.
    """
    return tuple(
        name
        for name, differs in (
            ("names", list(live.items()) != list(replayed.items())),
            ("counter", live.counter != replayed.counter),
            ("alias", _bound_aliases(live) != _bound_aliases(replayed)),
            ("reference_paths", live.reference_paths != replayed.reference_paths),
        )
        if differs
    )


def _bound_aliases(imports: Imports) -> dict[str | None, dict[str, str]]:
    return {module: aliases for module, aliases in imports.alias.items() if aliases}


def _check_module_imports(imports_mode: Mode, parser: Any, ctx: Any) -> None:
    """Report the shared imports and the module's imports where they differ from the replay of their ledger.

    Such a difference is a write that did not go through ``ModuleImportLedger``. The module's import plan must also
    hold what the module writes for each model it names and every import of another module's model; a report file
    also gets how many it compared.
    """
    ledger = parser._import_ledger
    for label, imports in (("shared imports", parser.imports), (f"imports of {'/'.join(ctx.module)}", ctx.imports)):
        if differences := _differences(imports, _replay(ledger, imports)):
            _violation(
                imports_mode,
                f"{label} differ from the replay of their ledger in {', '.join(differences)}",
                imports=label,
                parts=list(differences),
            )
    module, plan = "/".join(ctx.module), imports_mode.state[_PLANS, id(ctx.imports)][1]
    if differences := plan.differences(_replay(ledger, ctx.imports, without="field-name-bindings")):
        _violation(
            imports_mode,
            f"the models {module} names differ from its import plan: {'; '.join(differences)}",
            imports=module,
            parts=list(differences),
        )
    if imports_mode.report_path is not None:
        imports_mode.record(
            f"{Area.MODULE_IMPORTS.value}: compared {len(plan.uses)} model names of {module} with its import plan, "
            f"which imports {len(plan.references)}",
            kind="import-plan",
            imports=module,
            uses=len(plan.uses),
            references=len(plan.references),
        )


def _plan_module_imports(imports_mode: Mode, parser: Any, run: Any) -> None:
    """Compute the import plan of every module from the final names, after the last rename.

    A model's final module is the module that writes it.
    """
    from datamodel_code_generator._model_keys import model_key_of
    from datamodel_code_generator.parser._graph_ids import keyed_models, unchecked
    from datamodel_code_generator.parser._passes._shared import _module_name_from_module_path

    emitted = unchecked(keyed_models((model for ctx in run.contexts for model in ctx.models), "module import plan"))
    names = NameTable({
        model_key_of(model.reference): FinalName(
            model.reference.short_name, _module_name_from_module_path(ctx.module_key), ctx.module
        )
        for ctx in run.contexts
        for model in ctx.models
    })
    reused = {
        model_key_of(model.reused_reference): key
        for key, model in emitted.models.items()
        if model.reused_reference is not None
    }
    for ctx in run.contexts:
        imports_mode.state[_PLANS, id(ctx.imports)] = (
            ctx.imports,
            _module_import_plan(
                imports_mode,
                parser,
                ctx,
                names=names,
                emitted=emitted,
                reused=reused,
                module_names=run.model_path_to_module_name,
                internal_modules=run.internal_modules,
            ),
        )


def _written_key_of(keyed: KeyedModels, reused: Mapping[ModelKey, ModelKey], reference: Reference) -> ModelKey:
    """Return the key of the model among ``keyed`` that ``reference`` names: by its key, else by its path.

    A duplicate that reuse replaced by an empty subclass keeps its Reference in the types that named it; the reference
    names the subclass, which reuses it (``reused``: the key of each reused reference to the key of its subclass).
    """
    from datamodel_code_generator._model_keys import model_key_of

    if (key := model_key_of(reference)) not in keyed.models and reused.get(key) in keyed.models:
        return reused[key]
    return keyed.key_of(reference)


def _written_model_names(model: Any) -> Iterator[tuple[Reference, str, bool]]:
    """Yield each model name a model writes: the reference, what the model writes for it, and whether as a base class.

    A type that names a model writes its import's expression (unquoted) or the class name; an enum member literal
    writes the class name of its enum.
    """
    from datamodel_code_generator.model.base import BaseClassDataType
    from datamodel_code_generator.parser._passes._shared import _named_data_types

    for data_type in _named_data_types(model):
        if (reference := data_type.reference) is not None:
            yield (
                reference,
                (data_type.alias or reference.short_name).strip('"'),
                isinstance(data_type, BaseClassDataType),
            )
        literal_reference = data_type._enum_member_literal_reference
        if literal_reference is not None and not data_type.alias:
            yield from ((literal_reference, enum_class, False) for enum_class, _ in data_type.enum_member_literals)


def _module_import_plan(
    imports_mode: Mode,
    parser: Any,
    ctx: Any,
    *,
    names: NameTable,
    emitted: KeyedModels,
    reused: Mapping[ModelKey, ModelKey],
    module_names: Mapping[str, str],
    internal_modules: set[tuple[str, ...]],
) -> ModuleImportPlan:
    """Plan the models a module names: its own by their final class names, the others through their imports.

    A reference names a model of the module first, then one of any module (``_written_key_of``); one that names
    no written model is written by its own name, without an import, as today. The generated base class is
    imported by its class name from the module that writes it, as ``cross_module.apply_generic_base_class``
    imports it; any other model of another module through the name ``allocate_bindings`` gives its import. An
    import of a name that ``--import-overrides`` lists comes from the module it names.
    """
    from datamodel_code_generator._model_keys import model_key_of
    from datamodel_code_generator.imports import Import
    from datamodel_code_generator.parser._graph_ids import keyed_models, unchecked
    from datamodel_code_generator.parser._passes._shared import (
        _generic_base_class_module,
        _get_model_module_name,
        reference_import_target,
    )

    members = unchecked(keyed_models(ctx.models, "module import plan"))
    overrides = parser._import_overrides or {}
    generic_base = None if parser._generic_base_model is None else model_key_of(parser._generic_base_model.reference)
    uses: list[ReferenceUse] = []
    pending: list[tuple[ModelKey, str, str, str, Any, str]] = []
    references: dict[tuple[str | None, str], ReferenceImport] = {}

    def add_reference(target: ModelKey, import_: Import) -> None:
        if (module := overrides.get(import_.import_)) is not None:
            import_ = replace(import_, from_=module)
        references.setdefault(
            (import_.from_, import_.import_), ReferenceImport(target, names.names[target].module, import_)
        )

    for model in ctx.models:
        module_name = _get_model_module_name(model, module_names)
        for reference, written, base_class in _written_model_names(model):
            if (target := _written_key_of(members, reused, reference)) in members.models:
                uses.append(ReferenceUse(target, model.class_name, names.class_name(target, reference), written))
            elif (final := names.names.get(target := _written_key_of(emitted, reused, reference))) is None:
                uses.append(ReferenceUse(target, model.class_name, reference.short_name, written))
            elif target == generic_base:
                uses.append(ReferenceUse(target, model.class_name, final.class_name, written))
                add_reference(
                    target,
                    Import(from_=_generic_base_class_module(ctx.module, final.module_path), import_=final.class_name),
                )
            else:
                name = names.class_name(target, reference)
                import_target = reference_import_target(
                    name,
                    module_name,
                    f"{final.module}.{name}" if final.module else name,
                    use_exact=ctx.imports.use_exact,
                    init=ctx.is_init,
                    internal_modules=internal_modules,
                    base_class=base_class,
                    treat_dot_as_module=parser.treat_dot_as_module,
                )
                pending.append((target, model.class_name, name, written, import_target, reference.path))
    log = NameLog(frozenset(ctx.scoped_model_resolver.exclude_names), tuple(_name_events(imports_mode, ctx.imports)))
    bindings = allocate_bindings(
        (BindingRequest(t.full_path, t.import_) for *_, t, _ in pending),
        log,
        target_python_version=parser.target_python_version,
    )
    for target, holder, name, written, import_target, reference_path in pending:
        if (binding := bindings.get(BindingRequest(import_target.full_path, import_target.import_))) is None:
            uses.append(ReferenceUse(target, holder, None, written))
            continue
        add_reference(
            target,
            Import(
                from_=import_target.written_from,
                import_=import_target.import_,
                alias=binding,
                reference_path=reference_path,
            ),
        )
        uses.append(ReferenceUse(target, holder, import_target.expression(name, binding) or name, written))
    return ModuleImportPlan(ctx.module_key, tuple(references.values()), tuple(uses))
