"""How the parser repairs stdout output whose dotted schema names inferred modules one stream cannot hold.

Once every module is written, ``inspect_invalid_dotted_stdout`` records whether the concatenated stdout result is
usable and, when an inferred dotted module made it unusable, the modules a retry flattens, with the model inventory
and source fingerprint the retry must match. ``apply_forced_invalid_dotted_stdout_repair`` flattens those modules in
the retry. Each function takes the parser through ``StdoutRepairHost``, which ``Parser`` satisfies structurally. The
methods stay on ``Parser`` as delegators to these functions, and these functions call them through the parser, so
subclasses still override them; ``Parser._find_stdout_defect_models`` and ``Parser._find_shadowed_stdout_models``,
which build and read the public ``StdoutBindingContext``, stay parser methods.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from typing_extensions import Protocol

from datamodel_code_generator._parse_memo import MemoTable
from datamodel_code_generator.model.base import forget_model_memos
from datamodel_code_generator.model.enum import Enum
from datamodel_code_generator.parser._model_shapes import stdout_fingerprint
from datamodel_code_generator.parser._passes._name_allocation import scheme_resolver
from datamodel_code_generator.parser._passes._shared import _normalize_result_module_path
from datamodel_code_generator.parser._passes._state import ContextT, ResultT
from datamodel_code_generator.reference import split_module_name

if TYPE_CHECKING:
    from datamodel_code_generator._format_types import PythonVersion
    from datamodel_code_generator._internal_utils import HashableComparable
    from datamodel_code_generator.enums import ModuleSplitMode
    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.model.base import DataModel
    from datamodel_code_generator.parser._model_shapes import ReuseGroups
    from datamodel_code_generator.parser._passes._state import ModuleModels, ModulePath, SortedDataModels
    from datamodel_code_generator.parser.generation import GenerationStore, GraphBuilder
    from datamodel_code_generator.reference import ModelResolver

__all__ = [
    "StdoutParseConfig",
    "StdoutRepairHost",
    "apply_forced_invalid_dotted_stdout_repair",
    "find_invalid_inferred_modules",
    "flatten_invalid_modules",
    "get_source_data_fingerprint",
    "inspect_invalid_dotted_stdout",
    "model_inventory",
    "stdout_model_fingerprint",
]

_TOP_LEVEL_FUTURE_IMPORT_PATTERN: Final = re.compile(r"(?m)^from __future__ import ")
_TOP_LEVEL_RELATIVE_IMPORT_PATTERN: Final = re.compile(r"(?m)^from \.")


class StdoutParseConfig(Protocol):
    """What the stdout repair reads of the parse configuration: ``parser/base.py:ParseConfig``."""

    @property
    def with_import(self) -> bool:
        """Whether the modules write their imports."""

    @property
    def module_split_mode(self) -> ModuleSplitMode | None:
        """How models are split into modules (``--module-split-mode``)."""


class StdoutRepairHost(Protocol[ContextT, ResultT]):
    """The options, run state and hooks of the parser that the stdout repair reads and writes."""

    invalid_dotted_stdout_repair_modules: tuple[ModulePath, ...]
    generated_model_inventory: tuple[str, ...] | None
    source_data_fingerprint: bytes | None
    stdout_result_usable: bool

    @property
    def forced_invalid_dotted_stdout_repair_modules(self) -> tuple[ModulePath, ...]:
        """The inferred modules a retry flattens, which the completed first pass recorded."""

    @property
    def generation_store(self) -> GenerationStore:
        """The generation store of the run."""

    @property
    def imports(self) -> Imports:
        """The imports every module of the output takes."""

    @property
    def model_resolver(self) -> ModelResolver:
        """The resolver of the model names of the run."""

    @property
    def repair_invalid_dotted_stdout(self) -> bool:
        """Whether an unusable stdout result of inferred dotted modules is repaired by a retry."""

    @property
    def target_python_version(self) -> PythonVersion:
        """The Python version the output targets."""

    @property
    def treat_dot_as_module(self) -> bool | None:
        """Whether dots in schema names make modules."""

    @property
    def _graph_builder(self) -> GraphBuilder:
        """The capability through which the parser changes the graph of the generation store."""

    @property
    def _reuse_groups(self) -> ReuseGroups:
        """The groups of duplicate models that deduplication and reuse found."""

    def _find_invalid_inferred_modules(self, sorted_data_models: SortedDataModels) -> set[ModulePath]:
        """Return non-canonical module paths created by automatic dotted-name inference."""

    @staticmethod
    def _find_stdout_defect_models(
        contexts: list[ContextT],
        results: dict[ModulePath, ResultT],
        common_imports: Imports,
        *,
        with_import: bool,
        treat_dot_as_module: bool | None,
    ) -> set[DataModel]:
        """Return final models involved in an unusable concatenated stdout result."""

    def _flatten_invalid_modules(
        self,
        live_models: list[DataModel],
        affected_models: list[DataModel],
    ) -> bool:
        """Flatten selected inferred modules while preserving all unaffected names."""

    def _get_source_data_fingerprint(self) -> bytes | None:
        """Hash parsed root and reference data without allocating one serialized copy."""

    def _model_inventory(self, models: list[DataModel]) -> tuple[str, ...]:
        """Return the paths of the models other than typed map values, which a retry with untyped values omits."""


def find_invalid_inferred_modules(
    parser: StdoutRepairHost[ContextT, ResultT], sorted_data_models: SortedDataModels
) -> set[ModulePath]:
    """Return non-canonical module paths created by automatic dotted-name inference."""
    invalid_modules: set[ModulePath] = set()
    for model in sorted_data_models.values():
        original_name = model.reference.original_name
        if model.file_path is not None or "." not in original_name:
            continue
        module = tuple(model.module_path)
        if (
            module
            and split_module_name(
                original_name,
                treat_dot_as_module=None,
                strict_dotted_module_names=True,
                target_python_version=parser.target_python_version,
            )
            is None
        ):
            invalid_modules.add(module)
    return invalid_modules


def stdout_model_fingerprint(model: DataModel) -> HashableComparable:
    """Return the final rendered definition without using the pre-processing dedup cache."""
    return stdout_fingerprint(model.render(class_name=model.class_name), model.imports)


def get_source_data_fingerprint(parser: StdoutRepairHost[ContextT, ResultT]) -> bytes | None:
    """Hash parsed root and reference data without allocating one serialized copy."""
    from contextlib import suppress  # noqa: PLC0415
    from hashlib import sha256  # noqa: PLC0415
    from pickle import Pickler  # noqa: PLC0415, S403

    digest = sha256()

    class DigestWriter:
        __slots__ = ()

        @staticmethod
        def write(data: bytes, /) -> int:
            digest.update(data)
            return len(data)

    source_data = (
        getattr(parser, "raw_obj", None),
        sorted(getattr(parser, "remote_object_cache", {}).items()),
        sorted((getattr(parser, "_python_type_expressions", None) or {}).items()),
    )
    # Parsed YAML may contain mixed or non-JSON scalar mapping keys. Pickle preserves
    # their types and streams directly into the digest; unsupported extension objects
    # simply disable the optional retry so a completed legacy result is never lost.
    with suppress(Exception):
        Pickler(DigestWriter(), protocol=5).dump(source_data)
        return digest.digest()
    return None


def inspect_invalid_dotted_stdout(
    parser: StdoutRepairHost[ContextT, ResultT],
    contexts: list[ContextT],
    sorted_data_models: SortedDataModels,
    config: StdoutParseConfig,
    results: dict[ModulePath, ResultT],
) -> None:
    """Record a narrow repair plan after every module transformation has completed."""
    if not (parser.repair_invalid_dotted_stdout or parser.forced_invalid_dotted_stdout_repair_modules):
        return
    invalid_modules = (
        set()
        if parser.forced_invalid_dotted_stdout_repair_modules
        else parser._find_invalid_inferred_modules(sorted_data_models)  # noqa: SLF001
    )
    if parser.repair_invalid_dotted_stdout and not invalid_modules:
        return

    defect_models = type(parser)._find_stdout_defect_models(  # noqa: SLF001
        contexts,
        results,
        parser.imports,
        with_import=config.with_import,
        treat_dot_as_module=parser.treat_dot_as_module,
    )
    live_models = [model for ctx in contexts for model in ctx.models]
    if (
        getattr(parser, "openapi_include_info_version", False)
        and getattr(parser, "openapi_info_version", None) is not None
        and any(
            _normalize_result_module_path(module, treat_dot_as_module=parser.treat_dot_as_module)
            > _normalize_result_module_path(("__init__.py",), treat_dot_as_module=parser.treat_dot_as_module)
            and _TOP_LEVEL_FUTURE_IMPORT_PATTERN.search(result.body)
            for module, result in results.items()
        )
    ):
        defect_models.update(live_models)
    parser.stdout_result_usable = not defect_models
    if parser.forced_invalid_dotted_stdout_repair_modules:
        parser.generated_model_inventory = parser._model_inventory(live_models)  # noqa: SLF001
        parser.source_data_fingerprint = parser._get_source_data_fingerprint()  # noqa: SLF001
        return
    if not defect_models or config.module_split_mode is not None:
        return

    repair_modules: set[ModulePath] = set()
    for model in defect_models:
        module = tuple(model.module_path)
        matching_module: ModulePath = ()
        for invalid_module in invalid_modules:
            if len(invalid_module) > len(matching_module) and module[: len(invalid_module)] == invalid_module:
                matching_module = invalid_module
        if matching_module:
            repair_modules.add(matching_module)

    if not repair_modules or any(model.file_path is not None for model in live_models):
        return
    if (source_fingerprint := parser._get_source_data_fingerprint()) is None:  # noqa: SLF001
        return
    # The retry flattens every stdout model. Pass every non-canonical inferred
    # module so canonical modules reserve their existing names before any of them.
    parser.invalid_dotted_stdout_repair_modules = tuple(sorted(invalid_modules))
    parser.generated_model_inventory = parser._model_inventory(live_models)  # noqa: SLF001
    parser.source_data_fingerprint = source_fingerprint


def model_inventory(parser: StdoutRepairHost[ContextT, ResultT], models: list[DataModel]) -> tuple[str, ...]:
    """Return the paths of the models other than typed map values, which a retry with untyped values omits."""
    groups = parser._reuse_groups  # noqa: SLF001
    return tuple(sorted(model.path for model in models if not groups.is_typed_map_value(model)))


def flatten_invalid_modules(
    parser: StdoutRepairHost[ContextT, ResultT],
    live_models: list[DataModel],
    affected_models: list[DataModel],
) -> bool:
    """Flatten selected inferred modules while preserving all unaffected names."""
    resolver = parser.model_resolver
    affected_model_ids = {id(model) for model in affected_models}
    unavailable_names = resolver.exclude_names | {
        model.class_name for model in live_models if id(model) not in affected_model_ids
    }
    unavailable_names.update(import_.alias or import_.import_ for model in live_models for import_ in model.imports)
    scoped_resolver = scheme_resolver(
        "flattened-module",
        exclude_names=unavailable_names,
        duplicate_name_suffix_map=resolver.duplicate_name_suffix_map,
        target_python_version=parser.target_python_version,
    )
    new_names: dict[DataModel, str] = {}
    for index, model in enumerate(affected_models):
        new_names[model] = scoped_resolver.add(
            [str(index)],
            model.class_name,
            class_name=True,
            model_type="enum" if isinstance(model, Enum) else "model",
            preserve_class_name=True,
        ).name

    generation_index = parser.generation_store.index
    models_to_clear = set(affected_models)
    for model in affected_models:
        models_to_clear.update(
            owner_model
            for fact in generation_index.data_type_facts_for_reference(model.reference)
            if (owner_model := generation_index.owner_model_for_data_type(fact.data_type)) is not None
        )

    for model, new_name in new_names.items():
        parser._graph_builder.rename_model(model, reference_name=new_name, clear_duplicate_name=True)  # noqa: SLF001
    resolver.refresh_reference_names()
    for model in models_to_clear:
        forget_model_memos(model, MemoTable.DEDUP_KEYS)
    return True


def apply_forced_invalid_dotted_stdout_repair(
    parser: StdoutRepairHost[ContextT, ResultT],
    module_models: ModuleModels,
    module_split_mode: ModuleSplitMode | None,
) -> bool:
    """Apply the repair roots proven necessary by a completed legacy pass."""
    if module_split_mode is not None or not (affected_modules := parser.forced_invalid_dotted_stdout_repair_modules):
        return False
    live_models = [model for _, models in module_models for model in models]
    originally_affected_models = [
        model
        for model in live_models
        if any(tuple(model.module_path)[: len(module)] == module for module in affected_modules)
    ]
    if not originally_affected_models or any(model.file_path is not None for model in live_models):
        return False  # pragma: no cover - first-pass inventory and source checks make this a safety fallback.
    originally_affected_ids = {id(model) for model in originally_affected_models}
    # Preserve existing root/canonical names first, then assign conflict-free flat
    # names to the invalid roots that made the completed legacy output unusable.
    unaffected_models = [model for model in live_models if id(model) not in originally_affected_ids]
    flatten_order = [model for model in unaffected_models if not model.module_path]
    flatten_order.extend(model for model in unaffected_models if model.module_path)
    flatten_order.extend(originally_affected_models)
    return parser._flatten_invalid_modules(live_models, flatten_order)  # noqa: SLF001
