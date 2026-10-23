"""The run state the passes of ``Parser.parse()`` read and write, and the shapes of output modules they build.

``parser/base.py`` re-imports these names; ``ParseConfig`` stays there, since its module is public, so the run views
take the configuration as a type parameter.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple, TypeAlias

from typing_extensions import Protocol, ReadOnly, Self, TypedDict, TypeVar, final

from datamodel_code_generator._model_keys import ModelKey, model_key_of
from datamodel_code_generator.model.base import DataModel, _keyed_answer

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from datamodel_code_generator import AllExportsCollisionStrategy, AllExportsScope, ModuleSplitMode
    from datamodel_code_generator.format import CodeFormatter
    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.reference import ModelResolver, Reference

__all__ = [
    "ConfigT",
    "ForwarderMap",
    "ModuleContextFields",
    "ModuleContextView",
    "ModuleModels",
    "ModulePath",
    "ParseConfigView",
    "RecursionFacts",
    "SettleRun",
    "SortedDataModels",
    "StructuredRun",
]

ModulePath: TypeAlias = tuple[str, ...]
ModuleModels: TypeAlias = list[tuple[ModulePath, list[DataModel]]]
ForwarderMap: TypeAlias = dict[ModulePath, tuple[ModulePath, list[tuple[str, str]]]]
ModuleContextFields: TypeAlias = "tuple[ModulePath, ModulePath, list[DataModel], bool, Imports, ModelResolver]"
"""The fields of ``parser/base.py:ModuleContext`` in order, for the passes that unpack it."""


class ModuleContextView(Protocol):
    """What the passes read of a processed module: ``parser/base.py:ModuleContext``, whose module is public."""

    @property
    def models(self) -> list[DataModel]:
        """The models the module writes."""

    @property
    def is_init(self) -> bool:
        """Whether the module is a package ``__init__``."""

    @property
    def imports(self) -> Imports:
        """The imports of the module."""

    @property
    def scoped_model_resolver(self) -> ModelResolver:
        """The resolver of the names the module binds."""


@final
class _ModuleNames(dict[str, str]):  # noqa: FURB189
    """The module name scope planning gives each model, by path and by model key.

    The paths are what `Parser._process_single_module` takes and a subclass of the parser may read or write; the
    lookups (`_get_model_module_name`, `_get_data_type_target_full_name`) read the model keys. Row
    ``legacy-keyless-reference-by-path``: a model with no entry by key, as for an entry a subclass wrote by path, and
    a reference that no model was built with are looked up by path. Row ``legacy-module-follows-path``: the entries
    were paths, so a model that moves to another path leaves its entry behind (`forget`), and a root model whose
    inner model takes its path takes the inner model's entry. Under the ``MODEL_GRAPH`` check, an entry by key that
    differs from the entry at the model's path is a violation.
    """

    __slots__ = ("assigned", "by_key")

    def __init__(self) -> None:
        """Start without entries."""
        super().__init__()
        self.by_key: dict[ModelKey, str] = {}
        self.assigned: dict[str, str] = {}

    def assign(self, model: DataModel, module_name: str, *, keep: bool = False) -> None:
        """Give ``model`` the module name, or with ``keep`` only when it has none yet."""
        path = model.path
        if keep:
            if path not in self:
                self[path] = self.assigned[path] = module_name
            self.by_key.setdefault(model_key_of(model.reference), module_name)
            return
        self[path] = self.assigned[path] = module_name
        self.by_key[model_key_of(model.reference)] = module_name

    def forget(self, models: Iterable[DataModel]) -> None:
        """Drop the entries by key of models that moved to another path, whose entries by path name them no more.

        The models that import cycles merge into an internal module move there after their module names were given.
        """
        for model in models:
            self.by_key.pop(model_key_of(model.reference), None)

    def of(self, reference: Reference) -> str | None:
        """Return the module name of the model ``reference`` names, or None when it has none.

        An entry that a subclass wrote by path, before or after the parser gave the name, wins over the one by key;
        under the ``MODEL_GRAPH`` check, the two differing is a violation.
        """
        path = reference.path
        by_path = self.get(path)
        written = by_path != self.assigned.get(path)
        by_key = None if reference.source is None else self.by_key.get(model_key_of(reference))
        if written:
            return by_path if by_key is None else _keyed_answer("module names", by_path, lambda: by_key)
        if by_key is None:
            return by_path
        return _keyed_answer("module names", by_key, lambda: by_path)


SortedDataModels = dict[ModelKey, DataModel]


class _RenamedType(NamedTuple):
    """A class name a type had before a rename, and the path imports of that name were added under."""

    class_name: str
    import_path: str


@dataclass(frozen=True, slots=True)
class _ConstNarrowing:
    """The const that admits one value of the enum a model is built from, read at each value typed by that model.

    ``as_member`` says whether the value is typed ``Literal[<Enum>.<member>]``, which needs members equal to their
    values and an output whose Literal holds enum members; a null const types the value None.
    """

    const: object
    location: str
    as_member: bool


class RecursionFacts(TypedDict):
    """What makes models recursive in the output, by model key, for the passes after the one that finds it."""

    require_update: ReadOnly[frozenset[ModelKey]]
    """The models whose module calls the reference update action for them: the models the sort finds on a reference
    cycle or after a model they reference, and the models that reuse and the shared module make need it later."""
    circular_roots: ReadOnly[frozenset[ModelKey]]
    """The root models on a cycle of root models, which collapsing keeps named."""


class _ParseArgs(NamedTuple):
    """The arguments of one ``Parser.parse()`` call."""

    with_import: bool | None
    format_: bool | None
    settings_path: Path | None
    disable_future_imports: bool
    all_exports_scope: AllExportsScope | None
    all_exports_collision_strategy: AllExportsCollisionStrategy | None
    module_split_mode: ModuleSplitMode | None
    collect_model_metadata: bool


class ParseConfigView(Protocol):
    """What the passes read of the parse configuration: ``parser/base.py:ParseConfig``, whose module is public."""

    @property
    def code_formatter(self) -> CodeFormatter | None:
        """The code formatter of the run, or None when the builtin defaults need none."""

    def _replace(self, *, code_formatter: CodeFormatter | None) -> Self:
        """Return the configuration with another code formatter."""


ConfigT = TypeVar("ConfigT", bound=ParseConfigView)


class SettleRun(Protocol[ConfigT]):
    """What the passes up to the sorting of models read and write."""

    args: _ParseArgs
    suppress_parse_warnings: bool
    config: ConfigT
    sorted_data_models: SortedDataModels
    model_identities: Mapping[DataModel, str] | None


class StructuredRun(SettleRun[ConfigT], Protocol[ConfigT]):
    """What the passes from the module structure on read and write."""

    module_models: ModuleModels
    internal_modules: set[ModulePath]
    forwarder_map: ForwarderMap
    model_to_module_models: dict[DataModel, tuple[ModulePath, list[DataModel]]]
    model_path_to_module_name: dict[str, str]


@final
@dataclass(slots=True)
class _StructureState:
    """The state of one ``_build_module_structure`` call."""

    sorted_data_models: SortedDataModels
    module_split_mode: ModuleSplitMode | None
    module_models: ModuleModels = dataclasses.field(init=False)
    model_to_module_models: dict[DataModel, tuple[ModulePath, list[DataModel]]] = dataclasses.field(init=False)
    model_path_to_module_name: _ModuleNames = dataclasses.field(init=False)
    internal_modules: set[ModulePath] = dataclasses.field(init=False)
    forwarder_map: ForwarderMap = dataclasses.field(init=False)
