"""The run state the passes of ``Parser.parse()`` read and write, and the shapes of output modules they build.

``parser/base.py`` re-imports these names; ``ParseConfig``, ``ModuleContext`` and ``Result`` stay there, since their
module is public, so the run state takes them as type parameters (``ConfigT``, ``ContextT``, ``ResultT``).
"""

from __future__ import annotations

import contextlib
import dataclasses
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Generic, NamedTuple, TypeAlias
from weakref import WeakKeyDictionary

from typing_extensions import Protocol, ReadOnly, Self, TypedDict, TypeVar, final

from datamodel_code_generator._model_keys import ModelKey, model_key_of
from datamodel_code_generator._records import record
from datamodel_code_generator.model.base import DataModel, _keyed_answer
from datamodel_code_generator.parser._phases import DecisionOutcome

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
    from pathlib import Path

    from datamodel_code_generator import AllExportsCollisionStrategy, AllExportsScope, ModuleSplitMode
    from datamodel_code_generator._requirements import PydanticFloor
    from datamodel_code_generator.format import CodeFormatter
    from datamodel_code_generator.imports import Import, Imports
    from datamodel_code_generator.reference import ModelResolver, Reference
    from datamodel_code_generator.types import DataType

__all__ = [
    "ConfigT",
    "ContextT",
    "FinishRun",
    "Forwarder",
    "ForwarderMap",
    "ModuleContextFields",
    "ModuleContextView",
    "ModuleModels",
    "ModulePath",
    "ParseConfigView",
    "ProcessingRun",
    "PydanticFloors",
    "RecursionFacts",
    "ResultT",
    "ResultView",
    "SettleRun",
    "SortedDataModels",
    "StructuredRun",
    "pydantic_floors_recorded",
]

ModulePath: TypeAlias = tuple[str, ...]
ModuleModels: TypeAlias = list[tuple[ModulePath, list[DataModel]]]


@final
@record
class Forwarder:
    """A module whose models a merge moved into an ``_internal`` module, written as the imports of the names it binds.

    ``names`` holds ``(module, name, bound name)``: the module that writes a model, the model's class name there and the
    name the forwarder binds it to, its own models first, from ``internal_module``. A module that a grown merge moved
    (one that only the growth joined, or the ``_internal`` module of a merge of the released layout, which a grown merge
    holds) has ``released``: its models in their order in that layout, as ``(name there, class name in
    internal_module)``, and ``released_internal_modules``, the ``_internal`` modules of that layout.
    """

    internal_module: ModulePath
    names: tuple[tuple[ModulePath, str, str], ...]
    released: tuple[tuple[str, str], ...] = ()
    released_internal_modules: frozenset[ModulePath] = frozenset()


ForwarderMap: TypeAlias = dict[ModulePath, Forwarder]
ModuleContextFields: TypeAlias = "tuple[ModulePath, ModulePath, list[DataModel], bool, Imports, ModelResolver]"
"""The fields of ``parser/base.py:ModuleContext`` in order, for the passes that unpack it."""


class ModuleContextView(Protocol):
    """What the passes read of a processed module: ``parser/base.py:ModuleContext``, whose module is public."""

    @property
    def module(self) -> ModulePath:
        """The path of the module's file."""

    @property
    def module_key(self) -> ModulePath:
        """The path of the module as the module structure grouped it."""

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

    def _replace(self, *, module: ModulePath) -> Self:
        """Return the context with another file path."""


class ResultView(Protocol):
    """What the passes read of the result of one module: ``parser/base.py:Result``, whose module is public."""

    @property
    def body(self) -> str:
        """The code of the module."""


ContextT = TypeVar("ContextT", bound=ModuleContextView)
ResultT = TypeVar("ResultT", bound=ResultView)


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


@record
class _KeptHolder:
    """A holder of a root model that the ``parent`` strategy kept, recorded where collapse left it naming the root."""

    model: DataModel
    data_type: DataType
    inner_class_name: str
    """The class name the inner model had there, which the warning for a root that stays names."""
    root_import: Import | None
    """The import of the root model that the holder's module had there."""


@record(eq=False)
class _KeptParentRoot:
    """A root model that the ``parent`` strategy named its inner model after and kept for its later holders."""

    root: DataModel
    inner: DataModel
    holders: list[_KeptHolder]


@record
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
    def all_exports_scope(self) -> AllExportsScope | None:
        """The scope of the ``__all__`` exports of package modules, if configured."""

    @property
    def code_formatter(self) -> CodeFormatter | None:
        """The code formatter of the run, or None when the builtin defaults need none."""

    @property
    def use_deferred_annotations(self) -> bool:
        """Whether the modules defer the evaluation of their annotations."""

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

    @property
    def default_build_code_formatter(self) -> Callable[..., CodeFormatter]:
        """``Parser._build_code_formatter``, which a subclass of the parser may override."""


@final
@dataclass(slots=True)
class _StructureState:
    """The state of one ``_build_module_structure`` call."""

    sorted_data_models: SortedDataModels
    module_split_mode: ModuleSplitMode | None
    all_exports_scope: AllExportsScope | None
    module_models: ModuleModels = dataclasses.field(init=False)
    model_to_module_models: dict[DataModel, tuple[ModulePath, list[DataModel]]] = dataclasses.field(init=False)
    model_path_to_module_name: _ModuleNames = dataclasses.field(init=False)
    internal_modules: set[ModulePath] = dataclasses.field(init=False)
    forwarder_map: ForwarderMap = dataclasses.field(init=False)


@final
class PydanticFloors:
    """The Pydantic floor that each parse of a generation records, so that the generation states its kept parse's."""

    __slots__ = ("_floors",)

    def __init__(self) -> None:
        """Start with no parse recorded."""
        self._floors: WeakKeyDictionary[object, PydanticFloor] = WeakKeyDictionary()

    def record(self, parser: object, floor: PydanticFloor | None) -> None:
        """Record the floor of the models that ``parser`` emitted, None when they need nothing above the target."""
        if floor is None:
            self._floors.pop(parser, None)
        else:
            self._floors[parser] = floor

    def of(self, parser: object) -> PydanticFloor | None:
        """Return the floor that ``parser`` recorded, or None."""
        return self._floors.get(parser)


_PYDANTIC_FLOORS: ContextVar[PydanticFloors | None] = ContextVar("pydantic_floors", default=None)


@contextlib.contextmanager
def pydantic_floors_recorded() -> Generator[PydanticFloors]:
    """Record the Pydantic floors of the parses run inside, for the generation to state the one of the parse it keeps.

    A parse a generation discards, such as a compatibility retry it does not take, is never read.
    """
    record = PydanticFloors()
    token = _PYDANTIC_FLOORS.set(record)
    try:
        yield record
    finally:
        _PYDANTIC_FLOORS.reset(token)


_NO_DECISIONS: Final = DecisionOutcome(())


@final
@dataclass(slots=True)
class _PipelineRun(Generic[ConfigT, ContextT, ResultT]):
    """The state of one ``Parser.parse()`` call, filled segment by segment; a value read before it is set raises.

    ``Parser.parse()`` gives it what the passes need of the class: its ``_build_code_formatter`` and the class of each
    module's result.
    """

    args: _ParseArgs
    suppress_parse_warnings: bool
    default_build_code_formatter: Callable[..., CodeFormatter]
    result_type: Callable[..., ResultT]
    config: ConfigT = dataclasses.field(init=False)
    sorted_data_models: SortedDataModels = dataclasses.field(init=False)
    model_identities: Mapping[DataModel, str] | None = None
    module_models: ModuleModels = dataclasses.field(init=False)
    internal_modules: set[ModulePath] = dataclasses.field(init=False)
    forwarder_map: ForwarderMap = dataclasses.field(init=False)
    model_to_module_models: dict[DataModel, tuple[ModulePath, list[DataModel]]] = dataclasses.field(init=False)
    model_path_to_module_name: dict[str, str] = dataclasses.field(init=False)
    module_results: dict[ModulePath, ResultT] = dataclasses.field(default_factory=dict)
    unused_models: list[DataModel] = dataclasses.field(default_factory=list)
    module_to_import: dict[ModulePath, Imports] = dataclasses.field(default_factory=dict)
    contexts: list[ContextT] = dataclasses.field(default_factory=list)
    opted_out: list[DataModel] = dataclasses.field(default_factory=list)
    decisions: DecisionOutcome = _NO_DECISIONS
    output: str | dict[tuple[str, ...], ResultT] = dataclasses.field(init=False)


class ProcessingRun(StructuredRun[ConfigT], Protocol[ConfigT, ContextT, ResultT]):
    """What the processing of output modules reads and writes."""

    module_results: dict[ModulePath, ResultT]
    unused_models: list[DataModel]
    module_to_import: dict[ModulePath, Imports]
    contexts: list[ContextT]


class FinishRun(ProcessingRun[ConfigT, ContextT, ResultT], Protocol[ConfigT, ContextT, ResultT]):
    """What the passes after the output modules are processed read and write."""

    opted_out: list[DataModel]
    decisions: DecisionOutcome
    """What the decision phase changed."""
    output: str | dict[tuple[str, ...], ResultT]

    @property
    def result_type(self) -> Callable[..., ResultT]:
        """The class of each module's result."""


@final
@dataclass(slots=True, kw_only=True)
class _ModuleState(Generic[ConfigT]):
    """The state of one ``_process_single_module`` call.

    ``model_list`` is the list of the module's models: after model-level type overrides removed models, the kept
    list, and ``model_list_before_override_removal`` the list before.
    """

    is_init: bool
    model_list: list[DataModel]
    imports: Imports
    scoped_model_resolver: ModelResolver
    all_module_fields: set[str]
    config: ConfigT
    internal_modules: set[ModulePath]
    model_path_to_module_name: dict[str, str]
    require_update_action_models: list[str]
    unused_models: list[DataModel]
    unused_models_start: int = dataclasses.field(init=False)
    model_list_before_override_removal: list[DataModel] = dataclasses.field(init=False)
    current_unused_models: Sequence[DataModel] = ()
    live_models: list[DataModel] = dataclasses.field(init=False)


@final
@dataclass(slots=True, kw_only=True)
class _PreparedModule(Generic[ConfigT]):
    """One output module as ``_process_single_module`` left it, for its render preparation after the decision phase.

    ``model_list`` holds the models of the module that render, ``model_list_before_override_removal`` also those a
    model-level type override replaces, which constructor ordering still orders, and ``current_unused_models`` the
    unused models the module added.
    """

    model_list: list[DataModel]
    model_list_before_override_removal: list[DataModel]
    config: ConfigT
    current_unused_models: Sequence[DataModel]
    live_models: list[DataModel] = dataclasses.field(init=False)

    @classmethod
    def of(cls, state: _ModuleState[ConfigT]) -> _PreparedModule[ConfigT]:
        """Take the module of ``state`` with its model lists as they are now."""
        return cls(
            model_list=list(state.model_list),
            model_list_before_override_removal=list(state.model_list_before_override_removal),
            config=state.config,
            current_unused_models=state.current_unused_models,
        )


@final
@dataclass(slots=True)
class _FinalizeState(Generic[ContextT]):
    """The state of one ``_finalize_modules`` call.

    ``all_models`` are the models of every module as they were before unused models left them.
    """

    contexts: list[ContextT]
    unused_models: list[DataModel]
    model_to_module_models: dict[DataModel, tuple[ModulePath, list[DataModel]]]
    module_to_import: dict[ModulePath, Imports]
    all_models: list[DataModel] = dataclasses.field(init=False)
    model_imports: dict[DataModel, tuple[Import, ...]] = dataclasses.field(init=False)
    renamed_models: bool = False
    additional_properties_models: list[DataModel] = dataclasses.field(default_factory=list)

    def collect_additional_properties_models(self) -> bool:
        """Take the models whose additional properties reference classes; return whether there is any."""
        self.additional_properties_models = [
            model for model in self.all_models if len(model.additional_properties_reference_classes)
        ]
        return bool(self.additional_properties_models)
