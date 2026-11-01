"""How a module imports a model of another module and writes the expression that names it.

The rule of the import (``reference_import_target``, under an ``ImportPathRow``), the import it adds through the
module's import ledger (``register_reference_import``), the module that imports the generated base class
(``_generic_base_class_module``) and the writes of the expression a data type names its model by
(``set_reference_expression``, ``set_reference_expressions``, ``import_references``), through the generation store, and
importing again what a module imported under a stale import (``reimport_references``, under a ``ReimportRow``). The
import path text is ``_module_paths.py``'s (``relative``, ``exact_import``, ``is_ancestor_package_reference``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, TypeAlias, cast

from typing_extensions import Protocol, final

from datamodel_code_generator._module_paths import (
    dotted_name,
    exact_import,
    is_ancestor_package_reference,
    join_module,
    relative,
    written_name,
)
from datamodel_code_generator._records import record
from datamodel_code_generator.imports import Import, _iter_import_bindings
from datamodel_code_generator.model.base import BaseClassDataType
from datamodel_code_generator.parser._graph_ids import keyed_models
from datamodel_code_generator.parser._imports_plan import ImportProducer
from datamodel_code_generator.parser._passes._shared import (
    ReplacedRootsHost,
    _get_data_type_target_full_name,
    _get_model_module_name,
    module_members,
    names_left_by_replaced_roots,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping, Sequence

    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.model.base import DataModel
    from datamodel_code_generator.parser._graph_ids import KeyedModels
    from datamodel_code_generator.parser._imports_plan import ModuleImportLedger
    from datamodel_code_generator.parser._passes._shared import ModuleNamesView, ModuleScopeView
    from datamodel_code_generator.parser._passes._state import ModuleContextView, ModulePath
    from datamodel_code_generator.parser.generation import GraphBuilder
    from datamodel_code_generator.reference import ModelResolver, Reference
    from datamodel_code_generator.types import DataType


ImportPathRow: TypeAlias = Literal["reference", "legacy-collapse-import-path"]
"""How a module imports a model of another module (``reference_import_target``).

* ``reference``: the import a module writes for the models its own types name (``change_from_import``).
* ``legacy-collapse-import-path``: the import of a model that a type inlined into the module names (collapsed root
  types, dict keys typed by root types), as these wrote it since 0.25.9 (#2043): the module path alone, without the
  ``_`` of a hyphenated module, the import from an ``_internal`` module or from a module named like its class, or the
  leading dot of a package ``__init__``; a model of the module itself imports nothing and keeps the expression it was
  copied with, and every other one writes its binding even where it is the class name.
"""


@final
@record
class ReferenceImportTarget:
    """How a module imports a model of another module: the resolver path of the import and its module and name.

    ``from_`` is the module as the expression rule reads it, ``written_from`` the module the import line writes (a
    package ``__init__`` reaches a model outside its package one level up).
    """

    full_path: tuple[str, str]
    from_: str
    import_: str
    written_from: str
    row: ImportPathRow = "reference"

    @property
    def same_module(self) -> bool:
        """Return whether the model is one of the module itself, which the module names without an import."""
        return not (self.from_ and self.import_)

    def expression(self, name: str, binding: str) -> str | None:
        """Return what the module writes for the model ``name`` under the binding, or None for the class name."""
        if self.same_module or (binding == name and self.row == "reference"):
            return None
        return binding if name == self.import_ else f"{binding}.{name}"


def reference_import_target(  # ruff: ignore[too-many-arguments]
    name: str,
    current_module_name: str,
    target_full_name: str,
    *,
    use_exact: bool,
    init: bool,
    internal_modules: Collection[tuple[str, ...]],
    base_class: bool,
    treat_dot_as_module: bool | None,
    row: ImportPathRow = "reference",
) -> ReferenceImportTarget:
    """Return how the module ``current_module_name`` imports the model ``name`` at ``target_full_name``.

    ``use_exact`` imports the model from its module, except from an ancestor package, which keeps the package import.
    """
    if base_class and row == "reference":
        left, right = relative(current_module_name, target_full_name)
        from_ = (
            left if is_ancestor_package_reference(current_module_name, target_full_name) else join_module(left, right)
        )
        import_ = name
        full_path = from_, import_
    else:
        from_, import_ = full_path = relative(current_module_name, target_full_name)
        if use_exact and not is_ancestor_package_reference(current_module_name, target_full_name):
            from_, import_ = full_path = exact_import(from_, import_, name)
        if row == "legacy-collapse-import-path":
            return ReferenceImportTarget(full_path, from_, import_, from_, row)
        import_ = written_name(import_)
        current_module_path = tuple(current_module_name.split(".")) if current_module_name else ()
        if (  # pragma: no cover
            len(current_module_path) > 1 and current_module_path[-1].count(".") > 0 and not treat_dot_as_module
        ):
            rel_path_depth = current_module_path[-1].count(".")
            from_ = from_[rel_path_depth:]

        ref_module = tuple(target_full_name.split(".")[:-1])

        is_module_class_collision = ref_module and import_ == name and ref_module[-1] == import_

        if from_ and not use_exact and (ref_module in internal_modules or is_module_class_collision):
            from_ = join_module(from_, import_)
            import_ = name
            full_path = from_, import_
    written_from = "." + from_ if init and not target_full_name.startswith(current_module_name + ".") else from_
    return ReferenceImportTarget(full_path, from_, import_, written_from)


def register_reference_import(  # noqa: PLR0913
    reference: Reference,
    current_module_name: str,
    target_full_name: str,
    imports: Imports,
    scoped_model_resolver: ModelResolver,
    *,
    init: bool,
    internal_modules: set[tuple[str, ...]],
    base_class: bool = False,
    producer: ImportProducer,
    treat_dot_as_module: bool | None,
    import_ledger: ModuleImportLedger,
    row: ImportPathRow = "reference",
) -> str | None:
    """Import a reference from another module and return its expression when it is not the class name.

    Under row ``legacy-collapse-import-path`` a model of the module itself imports nothing.
    """
    name = reference.short_name
    target = reference_import_target(
        name,
        current_module_name,
        target_full_name,
        use_exact=imports.use_exact,
        init=init,
        internal_modules=internal_modules,
        base_class=base_class,
        treat_dot_as_module=treat_dot_as_module,
        row=row,
    )
    if row == "legacy-collapse-import-path" and target.same_module:
        return None
    alias = import_ledger.add_name(imports, scoped_model_resolver, target.full_path, target.import_).name
    import_ledger.append(
        imports,
        Import(
            from_=target.written_from,
            import_=target.import_,
            alias=alias,
            reference_path=reference.path,
        ),
        producer,
    )
    return target.expression(name, alias)


def _generic_base_class_module(module: ModulePath, base_module: ModulePath) -> str:
    """Return the module that ``module`` imports the generated base class from, which ``base_module`` writes."""
    from_ = relative(".".join(module[:-1]) if module else "", ".".join(base_module[:-1]) if base_module else "")[0]
    return join_module(from_, base_module[-1].replace(".py", ""))


class ReferenceExpressionHost(Protocol):
    """The option, the import ledger and the builder that writing how a data type names its reference uses."""

    @property
    def _graph_builder(self) -> GraphBuilder:
        """The builder that writes the expression through the generation store."""

    @property
    def _import_ledger(self) -> ModuleImportLedger:
        """The ledger of the imports each module adds."""

    @property
    def treat_dot_as_module(self) -> bool | None:
        """Whether dots in schema names make modules."""


def set_reference_expression(  # noqa: PLR0913, PLR0917
    host: ReferenceExpressionHost,
    data_type: DataType,
    current_module_name: str,
    target_full_name: str,
    imports: Imports,
    scoped_model_resolver: ModelResolver,
    *,
    same_module: bool,
    init: bool,
    internal_modules: set[ModulePath],
    producer: ImportProducer,
    row: ImportPathRow = "reference",
) -> bool:
    """Write how a data type names its reference in the current module, and return whether that changed it.

    A model the module emits is written by its class name; any other is imported, and written by the expression
    the import gives when that is not the class name. Under row ``legacy-collapse-import-path`` a model of the module
    itself keeps the expression the data type has.
    """
    expression = (
        None
        if same_module
        else register_reference_import(
            cast("Reference", data_type.reference),
            current_module_name,
            target_full_name,
            imports,
            scoped_model_resolver,
            init=init,
            internal_modules=internal_modules,
            base_class=isinstance(data_type, BaseClassDataType),
            producer=producer,
            treat_dot_as_module=host.treat_dot_as_module,
            import_ledger=host._import_ledger,  # noqa: SLF001
            row=row,
        )
    )
    if (expression is None and row != "reference") or expression == data_type.alias:
        return False
    host._graph_builder.name_reference(data_type, expression)  # noqa: SLF001
    return True


def set_reference_expressions(  # noqa: PLR0913
    host: ReferenceExpressionHost,
    ctx: ModuleContextView,
    naming: ModuleNamesView,
    model: DataModel,
    data_types: Iterable[DataType],
    *,
    members: KeyedModels | None,
    producer: ImportProducer,
) -> None:
    """Write how each of the ``data_types`` of ``model`` names its reference in the module of ``ctx``.

    A model ``members`` holds is written by its class name, any other is imported (``set_reference_expression``);
    without ``members`` every one is. ``naming`` gives the module names of ``model`` and of the models named.
    """
    import_references(
        host,
        model,
        data_types,
        ctx.imports,
        ctx.scoped_model_resolver,
        naming.model_path_to_module_name,
        init=ctx.is_init,
        internal_modules=naming.internal_modules,
        members=members,
        producer=producer,
    )


def import_references(  # noqa: PLR0913, PLR0917
    host: ReferenceExpressionHost,
    model: DataModel,
    data_types: Iterable[DataType],
    imports: Imports,
    scoped_model_resolver: ModelResolver,
    model_path_to_module_name: Mapping[str, str],
    *,
    init: bool,
    internal_modules: set[ModulePath],
    members: KeyedModels | None,
    producer: ImportProducer,
    row: ImportPathRow = "reference",
) -> bool:
    """Write how each of the ``data_types`` of ``model`` names its reference in the module of ``imports``.

    ``set_reference_expressions`` with the module's imports, resolver and module names given one by one, under
    ``row`` (``ImportPathRow``). Return whether an expression changed.
    """
    changed = False
    current_module_name = _get_model_module_name(model, model_path_to_module_name)
    for data_type in data_types:
        if (reference := data_type.reference) is None:
            continue
        changed |= set_reference_expression(
            host,
            data_type,
            current_module_name,
            _get_data_type_target_full_name(data_type, reference, model_path_to_module_name),
            imports,
            scoped_model_resolver,
            same_module=members is not None and members.key_of(reference) in members.models,
            init=init,
            internal_modules=internal_modules,
            producer=producer,
            row=row,
        )
    return changed


ReimportRow: TypeAlias = Literal["collapse-root-models", "import-renamed-types", "import-removed-reuse-canonicals"]
"""How a module imports again the models that its data types name under stale imports (``reimport_references``).

Each row is the pass that does it, which the imports are recorded under (``ImportProducer``).

* ``collapse-root-models``: holders that collapse redirected to the inner model of a root model; their module drops
  the imports of the root model at once, releases the names that no import binds any more, and imports the inner model
  from the module whose live model list holds it (``emitting_module_name``), the holder's own module writing it by
  its class name.
* ``import-renamed-types``: data types whose module imported a renamed type by an old name; each stale import is
  dropped for its model, every name the stale imports bound is released, and the type is imported by its path
  (``import_references``).
* ``import-removed-reuse-canonicals``: data types whose module imported a duplicate that reuse removed; each stale
  import is dropped for its model, no name is released, and the canonical is imported by its path, a model of
  ``members`` written by its class name.

The releasing rows also release the names that the module's roots replaced by their inner models leave
(``names_left_by_replaced_roots``).
"""


class EmittingModulesHost(Protocol):
    """The live model lists of the output modules, which ``emitting_module_name`` reads and remembers models in."""

    @property
    def _emitting_module_lists(self) -> list[tuple[str, list[DataModel]]]:
        """The live model list of each output module, by module name."""

    @property
    def _emitting_module_names(self) -> dict[int, str]:
        """The module that emits each model found in the live lists, by model id."""


class ReimportHost(ReferenceExpressionHost, ReplacedRootsHost, EmittingModulesHost, Protocol):
    """What importing again the models a module imported under stale imports reads of the parser."""


def emitting_module_name(
    parser: EmittingModulesHost, model: DataModel, model_path_to_module_name: dict[str, str]
) -> str:
    """Return the name of the module whose live model list holds a model, found by identity.

    A model that a module's processing puts into its list after modules are structured, such as a reuse subclass,
    is looked up in the live lists and remembered.
    """
    if (module_name := parser._emitting_module_names.get(id(model))) is not None:  # noqa: SLF001
        return module_name
    for module_name, module_models in parser._emitting_module_lists:  # noqa: SLF001
        if any(module_model is model for module_model in module_models):
            parser._emitting_module_names[id(model)] = module_name  # noqa: SLF001
            return module_name
    return _get_model_module_name(model, model_path_to_module_name)


def reimport_references(  # noqa: PLR0913
    host: ReimportHost,
    ctx: ModuleContextView,
    naming: ModuleScopeView,
    stale_users: Sequence[tuple[DataModel, DataType, Import | None]],
    *,
    row: ReimportRow,
    members: KeyedModels | None = None,
) -> None:
    """Import again into the module of ``ctx`` what the ``stale_users`` name, under ``row`` (``ReimportRow``).

    ``reimport_module_references`` with the module's models, imports and resolver taken from ``ctx`` and its module
    names from ``naming``.
    """
    reimport_module_references(
        host,
        ctx.models,
        stale_users,
        ctx.imports,
        ctx.scoped_model_resolver,
        naming.model_path_to_module_name,
        init=ctx.is_init,
        internal_modules=naming.internal_modules,
        row=row,
        members=members,
    )


def reimport_module_references(  # noqa: PLR0913, PLR0917
    host: ReimportHost,
    models: list[DataModel],
    stale_users: Sequence[tuple[DataModel, DataType, Import | None]],
    imports: Imports,
    scoped_model_resolver: ModelResolver,
    model_path_to_module_name: dict[str, str],
    *,
    init: bool,
    internal_modules: set[ModulePath],
    row: ReimportRow,
    members: KeyedModels | None = None,
) -> None:
    """Drop the stale imports of a module, release the names they leave and write how each data type names its model.

    The module's ``models`` give the names its replaced roots leave; ``row`` (``ReimportRow``) says for whom the
    imports are dropped, which names are released and where the models are imported from.
    """
    if not stale_users:
        return
    ledger = host._import_ledger  # noqa: SLF001
    stale = [(model, stale_import) for model, _, stale_import in stale_users if stale_import is not None]
    if row == "collapse-root-models":
        ledger.remove(imports, [stale_import for _, stale_import in stale], ImportProducer(row))
    else:
        for model, stale_import in stale:
            ledger.remove(imports, stale_import, ImportProducer(row, model))
    if row != "import-removed-reuse-canonicals":
        released = {stale_import.binding_name for _, stale_import in stale}
        if row == "collapse-root-models":
            released.difference_update(_iter_import_bindings(imports))
        ledger.release_names(
            imports,
            scoped_model_resolver,
            released.union(
                names_left_by_replaced_roots(host, models, keyed_models(module_members(host, models), "module members"))
            ),
        )
    for model, data_type, _ in stale_users:
        producer = ImportProducer(row, model)
        if row != "collapse-root-models":
            import_references(
                host,
                model,
                (data_type,),
                imports,
                scoped_model_resolver,
                model_path_to_module_name,
                init=init,
                internal_modules=internal_modules,
                members=members,
                producer=producer,
            )
            continue
        reference = cast("Reference", data_type.reference)
        holder_module_name = _get_model_module_name(model, model_path_to_module_name)
        inner_module_name = (
            holder_module_name
            if (inner_model := reference.source) is None
            else emitting_module_name(host, cast("DataModel", inner_model), model_path_to_module_name)
        )
        set_reference_expression(
            host,
            data_type,
            holder_module_name,
            dotted_name(inner_module_name, reference.short_name),
            imports,
            scoped_model_resolver,
            same_module=inner_module_name == holder_module_name,
            init=init,
            internal_modules=internal_modules,
            producer=producer,
        )
