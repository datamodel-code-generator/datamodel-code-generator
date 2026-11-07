"""How the parser writes the exports of a package's ``__init__.py`` (``--all-exports-scope``).

``init_export_imports`` returns the imports by which an ``__init__.py`` exports the models of the modules below it:
the names of a module's models it exports (``exported_names``) of each module below the package that its scope reaches
(``exports_reach``), which ``collect_exports_for_init`` collects, ``resolve_export_collisions`` resolves the names that
collide by ``--all-exports-collision-strategy`` (``raise_collision_error`` reports them), and ``build_all_exports_code``
builds the imports; the import cycles that merge modules read the same exports (``parser/_passes/structure.py``). The
functions take the parser through ``InitExportsHost``, which ``Parser`` satisfies structurally. ``Parser`` keeps
``_init_export_imports`` as the hook that calls ``init_export_imports``, and ``_make_prefixed_name``, the staticmethod
that builds a prefixed name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeAlias

from typing_extensions import Protocol

from datamodel_code_generator import Error
from datamodel_code_generator._free_names import first_free
from datamodel_code_generator._module_paths import is_below, is_package_init, module_parts, written_names
from datamodel_code_generator.enums import AllExportsCollisionStrategy, AllExportsScope
from datamodel_code_generator.imports import Import, Imports

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    from datamodel_code_generator._format_types import PythonVersion
    from datamodel_code_generator.model.base import DataModel
    from datamodel_code_generator.parser._passes._state import ModulePath
    from datamodel_code_generator.reference import ModelResolver

    _ModuleContextRow: TypeAlias = tuple[ModulePath, ModulePath, list[DataModel], bool, Imports, ModelResolver]
    _Export: TypeAlias = tuple[str, tuple[str, ...], str]


__all__ = [
    "InitExportsConfig",
    "InitExportsHost",
    "build_all_exports_code",
    "collect_exports_for_init",
    "exported_names",
    "exports_reach",
    "init_export_imports",
    "raise_collision_error",
    "resolve_export_collisions",
]


class InitExportsConfig(Protocol):
    """What the exports read of the parse configuration: ``parser/base.py:ParseConfig``, whose module is public."""

    @property
    def all_exports_collision_strategy(self) -> AllExportsCollisionStrategy | None:
        """How names that collide in the exports are resolved (``--all-exports-collision-strategy``)."""


class InitExportsHost(Protocol):
    """The options and hooks of the parser that the exports of an ``__init__.py`` read."""

    @property
    def target_python_version(self) -> PythonVersion:
        """The Python version of the output (``--target-python-version``)."""

    @staticmethod
    def _make_prefixed_name(
        name: str,
        path: tuple[str, ...],
        used: set[str],
        *,
        minimal: bool,
        target_python_version: PythonVersion | None = None,
    ) -> str:
        """Generate a prefixed name, using minimal or full prefix."""


def exported_names(models: Iterable[DataModel]) -> Iterator[str]:
    """Return the names of ``models`` that ``--all-exports-scope`` exports: those not starting with ``_``."""
    return (ref.short_name for model in models if (ref := model.reference) and not ref.short_name.startswith("_"))


def exports_reach(depth: int, scope: AllExportsScope) -> bool:
    """Return whether the exports of a package's ``__init__.py`` reach a module ``depth`` levels below the package.

    The scope ``children`` reaches the modules directly below the package (``depth`` 1), ``recursive`` every module
    below it.
    """
    return depth == 1 or (depth > 1 and scope != AllExportsScope.Children)


def collect_exports_for_init(
    module: tuple[str, ...],
    processed_models: Sequence[_ModuleContextRow],
    scope: AllExportsScope,
) -> list[_Export]:
    """Collect exports for __init__.py based on scope."""
    exports: list[_Export] = []
    normalized_module = written_names(module)
    base = normalized_module[:-1] if is_package_init(normalized_module[-1]) else normalized_module
    base_len = len(base)

    for proc_module, _, proc_models, _, _, _ in processed_models:
        normalized_proc_module = written_names(proc_module)
        if not proc_models or normalized_proc_module == normalized_module:
            continue
        prefix = module_parts(normalized_proc_module)
        if not (is_below(prefix, base) and exports_reach(len(prefix) - base_len, scope)):
            continue
        rel = prefix[base_len:]
        exports.extend((name, rel, ".".join(rel)) for name in exported_names(proc_models))
    return exports


def resolve_export_collisions(
    cls: type[InitExportsHost],
    exports: list[_Export],
    strategy: AllExportsCollisionStrategy | None,
    reserved: set[str] | None = None,
    target_python_version: PythonVersion | None = None,
) -> dict[str, list[_Export]]:
    """Resolve name collisions in exports based on strategy, titling module names as the target does.

    A prefixed name is written over the export that holds the name, as before. The export it displaces takes the next
    free name (``Name1``, ``Name2``) once every name is assigned, so that no export is left out and every name keeps the
    export it refers to.
    """
    reserved = reserved or set()
    by_name: dict[str, list[_Export]] = {}
    for item in exports:
        by_name.setdefault(item[0], []).append(item)

    if not (colliding := {n for n, items in by_name.items() if len(items) > 1 or n in reserved}):
        return dict(by_name)
    if (effective := strategy or AllExportsCollisionStrategy.Error) == AllExportsCollisionStrategy.Error:
        raise_collision_error(by_name, colliding)

    used: set[str] = {n for n in by_name if n not in colliding} | reserved
    result = {n: items for n, items in by_name.items() if n not in colliding}
    displaced: list[tuple[str, _Export]] = []

    for name in sorted(colliding):
        for item in sorted(by_name[name], key=lambda x: len(x[1])):
            new_name = cls._make_prefixed_name(
                item[0],
                item[1],
                used,
                minimal=effective == AllExportsCollisionStrategy.MinimalPrefix,
                target_python_version=target_python_version,
            )
            if new_name in reserved:
                msg = (
                    f"Cannot resolve collision: '{new_name}' conflicts with __init__.py model. "
                    "Please rename one of the models."
                )
                raise Error(msg)
            if new_name in result:
                displaced.append((new_name, result[new_name][0]))
            result[new_name] = [item]
            used.add(new_name)
    for held, item in displaced:
        free, _ = first_free(held, used.__contains__, "joined")
        result[free] = [item]
        used.add(free)
    return result


def raise_collision_error(
    by_name: dict[str, list[_Export]],
    colliding: set[str],
) -> None:
    """Raise an error with collision details."""
    details = []
    for n in sorted(colliding):
        if len(items := by_name[n]) > 1:
            details.append(f"  '{n}' is defined in: {', '.join(f'.{s}' for _, _, s in items)}")
        else:
            details.append(f"  '{n}' conflicts with a model in __init__.py")
    raise Error(
        "Name collision detected with --all-exports-scope:\n"
        + "\n".join(details)
        + "\n\nUse --all-exports-collision-strategy minimal-prefix or full-prefix to prefix the colliding names "
        "with their module names."
    )


def build_all_exports_code(
    resolved: dict[str, list[_Export]],
) -> Imports:
    """Build import statements from resolved exports."""
    export_imports = Imports()
    for export_name, items in resolved.items():
        for orig, _, short in items:
            export_imports.append(
                Import(from_=f".{short}", import_=orig, alias=export_name if export_name != orig else None)
            )
    return export_imports


def init_export_imports(  # noqa: PLR0913, PLR0917
    parser: InitExportsHost,
    module: ModulePath,
    contexts: Sequence[_ModuleContextRow],
    scope: AllExportsScope,
    config: InitExportsConfig,
    reserved: set[str],
) -> Imports | None:
    """Return the imports by which the ``__init__.py`` of ``module`` exports its subpackages' models, or None.

    Names that collide with each other or with ``reserved`` are resolved by ``--all-exports-collision-strategy``.
    """
    if not (exports := collect_exports_for_init(module, contexts, scope)):
        return None
    return build_all_exports_code(
        resolve_export_collisions(
            type(parser), exports, config.all_exports_collision_strategy, reserved, parser.target_python_version
        )
    )
