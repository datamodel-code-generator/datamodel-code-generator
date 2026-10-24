"""The structure passes (L3): the output modules the settled models are grouped into, deduplicated and merged.

Each pass is a function of the parser and the structure state or the run, called from the pipeline tables of
``parser/_passes/_tables.py``; the parser comes in through ``StructureHost``, which ``Parser`` satisfies structurally.
"""

from __future__ import annotations

import operator
import os.path
from collections import defaultdict
from itertools import groupby
from pathlib import Path
from typing import TYPE_CHECKING, Any

from typing_extensions import Protocol

from datamodel_code_generator import Error, ModuleSplitMode, ReuseScope
from datamodel_code_generator._model_keys import model_key_of
from datamodel_code_generator._reference_graph import EdgeKind, GraphSnapshot, graph_answer, reference_graph
from datamodel_code_generator._scc import find_circular_sccs
from datamodel_code_generator.model.base import (
    DataModel,
    _keyed_answer,
    get_resolve_reference_action_capabilities,
    remember_nested_factory_order_before_nullability,
)
from datamodel_code_generator.model.enum import Enum
from datamodel_code_generator.parser._graph_ids import keyed_models, unchecked
from datamodel_code_generator.parser._model_shapes import COLLAPSE_REUSE, SAME_NAME, TREE_SCOPE, skips_root_model
from datamodel_code_generator.parser._passes._shared import (
    BaseStandInHost,
    _get_mapping_discriminator_values,
    _runtime_validation_reference_keys,
    _sort_models_by_class_body_references,
    redirect_collapsed_reuse_users,
    replace_model_in_list,
    require_update_action,
)
from datamodel_code_generator.parser._passes._state import RecursionFacts, _ModuleNames
from datamodel_code_generator.reference import ModelResolver
from datamodel_code_generator.util import camel_to_snake

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from datamodel_code_generator._format_types import PythonVersion
    from datamodel_code_generator._internal_utils import HashableComparable
    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator.config import ParserConfig
    from datamodel_code_generator.format import CodeFormatter, Formatter
    from datamodel_code_generator.model.base import BaseClassDataType
    from datamodel_code_generator.parser._model_shapes import ModelShapes, ShapeRow
    from datamodel_code_generator.parser._passes._shared import _ReuseOptimizationContext
    from datamodel_code_generator.parser._passes._state import (
        ConfigT,
        ForwarderMap,
        ModuleModels,
        ModulePath,
        SortedDataModels,
        StructuredRun,
        _StructureState,
    )
    from datamodel_code_generator.parser.generation import GenerationStore, GraphBuilder
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import DataType

__all__ = [
    "StructureHost",
    "apply_forced_invalid_dotted_stdout_repair",
    "apply_reuse_model_tree_scope",
    "build_structure",
    "configure_code_formatter",
    "delete_duplicate_models",
    "detect_standard_generation_templates",
    "get_module_directory",
    "group_models_by_module",
    "replace_duplicate_name_in_module",
    "reset_collapsed_reuse_state",
    "resolve_module_circular_imports",
    "snapshot_union_tags",
]


class StructureHost(BaseStandInHost, Protocol):
    """The options, run state and hooks of the parser that the structure passes read and write."""

    _recursion_facts: RecursionFacts
    _reuse_union_tags: dict[DataModel, tuple[HashableComparable, ...]]
    _uses_standard_generation_templates: bool

    @property
    def _collapsed_reuse_models(self) -> dict[int, list[DataModel]]:
        """The models collapsed reuse removed, by the id of their canonical model."""

    @property
    def _collapsed_reuse_users(self) -> dict[int, tuple[DataType, Reference]]:
        """The data types collapsed reuse redirected, with the reference each named before."""

    @property
    def _configured_generation_types_are_builtin(self) -> bool:
        """Whether the configured model, field and type manager types are the builtin ones."""

    @property
    def _graph_builder(self) -> GraphBuilder:
        """The builder that changes the model graph through the generation store."""

    @property
    def _merged_copies(self) -> set[DataModel]:
        """The copies of schemas merged from another document."""

    @property
    def _reuse_optimization_context(self) -> _ReuseOptimizationContext:
        """The constraints that type overrides put on reuse."""

    @property
    def _typed_map_value_reference_ids(self) -> set[int]:
        """The ids of the references of typed map values."""

    @property
    def collapse_reuse_models(self) -> bool:
        """Whether reuse removes duplicates instead of subclassing them."""

    @property
    def collapse_root_models(self) -> bool:
        """Whether root models are collapsed into their users."""

    @property
    def config(self) -> ParserConfig:
        """The parser configuration."""

    @property
    def custom_class_name_generator(self) -> Callable[[str], str] | None:
        """The function that generates class names, if configured."""

    @property
    def custom_formatter(self) -> list[str] | None:
        """The custom formatter modules, if configured."""

    @property
    def data_model_type(self) -> type[DataModel]:
        """The model type of the output."""

    @property
    def defer_formatting(self) -> bool:
        """Whether formatting waits until every module is written."""

    @property
    def formatters(self) -> list[Formatter] | None:
        """The configured formatters."""

    @property
    def generation_store(self) -> GenerationStore:
        """The generation store of the parse."""

    @property
    def pydantic_v2_root_model_type(self) -> type[DataModel] | None:
        """The root model type whose class body evaluates its root type, if the output has one."""

    @property
    def reuse_model(self) -> bool:
        """Whether models that render the same are reused."""

    @property
    def reuse_scope(self) -> ReuseScope | None:
        """The scope of reuse."""

    @property
    def shared_module_name(self) -> str:
        """The name of the module tree-scope reuse moves shared models to."""

    @property
    def target_python_version(self) -> PythonVersion:
        """The Python version of the output."""

    def _apply_forced_invalid_dotted_stdout_repair(
        self, module_models: ModuleModels, module_split_mode: ModuleSplitMode | None
    ) -> bool:
        """Flatten the invalid dotted modules of stdout output, and return whether it did."""

    def _build_code_formatter(self, settings_path: Path | None, *, is_multi_module_output: bool) -> CodeFormatter:
        """Return the code formatter of the run."""

    def _build_module_structure(
        self, sorted_data_models: SortedDataModels, module_split_mode: ModuleSplitMode | None
    ) -> tuple[
        ModuleModels,
        set[ModulePath],
        ForwarderMap,
        dict[DataModel, tuple[ModulePath, list[DataModel]]],
        dict[str, str],
    ]:
        """Return the output modules of the sorted models."""

    def _get_duplicate_root_reference(
        self, model: DataModel, root_data_type: DataType, models_set: set[DataModel]
    ) -> Reference | None:
        """Return the model a duplicated root model names, which replaces it."""

    def _is_root_model(self, model: DataModel) -> bool:
        """Return whether a model uses the configured root model representation."""

    def _model_shapes(self, row: ShapeRow) -> ModelShapes[DataModel, Reference]:
        """Return the keys of one comparison pass of the dedup or reuse user of a row."""

    def _should_preserve_explicit_root_class_name(self, class_name: str) -> bool:
        """Return whether a class name of an explicit root keeps its leading underscore."""

    def _site_models_last(self, models: list[DataModel]) -> list[DataModel]:
        """Order the site models a reference with more assertions gets after the others."""

    def _typed_map_values_last(self, models: list[DataModel]) -> list[DataModel]:
        """Order models of typed map values after the others."""


def _module_key(
    data_model: DataModel, module_split_mode: ModuleSplitMode | None, target_python_version: PythonVersion
) -> ModulePath:
    if module_split_mode == ModuleSplitMode.Single:
        return (*data_model.module_path, camel_to_snake(data_model.class_name, target_python_version))
    return tuple(data_model.module_path)


def _group_models_by_module(
    data_models: Iterable[DataModel], module_split_mode: ModuleSplitMode | None, target_python_version: PythonVersion
) -> ModuleModels:
    """Group models by output module and include required empty package levels."""

    def sort_key(data_model: DataModel) -> tuple[int, ModulePath]:
        key = _module_key(data_model, module_split_mode, target_python_version)
        return (len(key), key)

    grouped_models = groupby(
        sorted(data_models, key=sort_key, reverse=True),
        key=lambda model: _module_key(model, module_split_mode, target_python_version),
    )
    module_models: ModuleModels = []
    previous_module: ModulePath = ()
    for module, models in ((key, [*values]) for key, values in grouped_models):
        if len(previous_module) - len(module) > 1:
            module_models.extend(
                (previous_module[:parts], []) for parts in range(len(previous_module) - 1, len(module), -1)
            )
        module_models.append((module, models))
        previous_module = module
    return module_models


def _index_module_models(
    module_models: ModuleModels, module_split_mode: ModuleSplitMode | None
) -> tuple[dict[DataModel, tuple[ModulePath, list[DataModel]]], _ModuleNames]:
    """Build model lookups for already-grouped output modules."""
    model_to_module_models: dict[DataModel, tuple[ModulePath, list[DataModel]]] = {}
    model_path_to_module_name = _ModuleNames()
    for module, models in module_models:
        for model in models:
            model_to_module_models[model] = module, models
            if module_split_mode == ModuleSplitMode.Single:
                model_path_to_module_name.assign(model, ".".join(module))
    return model_to_module_models, model_path_to_module_name


def _merged_class_names(class_names: Sequence[str]) -> tuple[str, ...]:
    """Return one distinct class name per model merged into one module, in merge order.

    A model keeps its name unless an earlier model of the merge already holds it; a later holder takes
    ``<name>_<n>`` with the smallest ``n >= 1`` that no model of the merge holds or has already taken.
    """
    taken = set(class_names)
    held: set[str] = set()
    names: list[str] = []
    for name in class_names:
        if name not in held:
            held.add(name)
            names.append(name)
            continue
        suffix = 1
        while (candidate := f"{name}_{suffix}") in taken:
            suffix += 1
        taken.add(candidate)
        names.append(candidate)
    return tuple(names)


def get_module_directory(module: tuple[str, ...]) -> tuple[str, ...]:
    """Get the directory portion of a module tuple.

    Note: Module tuples in module_models do NOT include .py extension.
    The last element is either the module name (e.g., "issuing") or empty for root.

    Examples:
        ("pkg",) -> ("pkg",) - root module
        ("pkg", "issuing") -> ("pkg",) - submodule
        ("foo", "bar", "baz") -> ("foo", "bar") - deeply nested module
    """
    if not module:
        return ()
    if len(module) == 1:
        return module
    return module[:-1]


def _union_tags(model: DataModel) -> tuple[HashableComparable, ...]:
    """Return the field index, property and the tags of each variant of each discriminated union of a model.

    Unions that render alike decode other tags when their discriminators name another property or map their
    variants to other tags. The tags a mapping gives the variants are listed by variant position, so unions that
    map the same tags to variants that reuse merges match whatever schemas their mappings name.
    """
    if not (
        discriminators := [
            (index, discriminator, field.data_type.data_types)
            for index, field in enumerate(model.fields)
            if isinstance(discriminator := field.extras.get("discriminator"), dict)
        ]
    ):
        return ()
    from datamodel_code_generator._internal_utils import to_hashable  # noqa: PLC0415

    return tuple(
        (
            index,
            str(discriminator.get("propertyName")),
            tuple(
                to_hashable(_get_mapping_discriminator_values(source, mapping))
                if (mapping := discriminator.get("mapping"))
                and (reference := data_type.reference)
                and isinstance(source := reference.source, DataModel)
                else ()
                for data_type in data_types
            ),
        )
        for index, discriminator, data_types in discriminators
    )


def _distinct_bases(row: ShapeRow, bases: Iterable[BaseClassDataType]) -> Iterable[BaseClassDataType]:
    """Return the bases of a merged model's child once each, told apart as the user of the row tells them."""
    if row.base_dedup == "by-type-hint":
        return {f"{base.module_name}.{base.type_hint}": base for base in bases}.values()
    return {id(base.reference): base for base in bases}.values()


def renders_as_duplicate(
    parser: StructureHost, model: DataModel, original_model: DataModel, shapes: ModelShapes[DataModel, Reference]
) -> bool:
    """Return whether a model renders like the first model of its class name.

    A copy of a schema merged from another document compares with referenced names masked, since the
    nested models it names only become the shared ones as they collapse in the same pass.
    """
    return shapes.key(model) == shapes.key(original_model) or (
        not parser._merged_copies.isdisjoint((model, original_model))  # noqa: SLF001
        and shapes.shape(model)[0] == shapes.shape(original_model)[0]
    )


def delete_duplicate_models(parser: StructureHost, models: list[DataModel]) -> None:  # noqa: PLR0912
    if not models:
        return

    model_class_names: dict[str, DataModel] = {}
    shapes = parser._model_shapes(SAME_NAME)  # noqa: SLF001
    model_to_duplicate_models: defaultdict[DataModel, list[DataModel]] = defaultdict(list)
    # Use set for O(1) membership checks and collect removals for batch processing
    models_set = set(models)
    models_to_remove: set[DataModel] = set()
    reuse_constraint = (
        parser._reuse_optimization_context.allows_model  # noqa: SLF001
        if parser._reuse_optimization_context.constrains  # noqa: SLF001
        else None
    )
    for model in parser._site_models_last(models):  # noqa: SLF001
        if model in models_to_remove:  # pragma: no cover
            continue
        reuse_allowed = reuse_constraint is None or reuse_constraint(model)
        if parser._is_root_model(model):  # noqa: SLF001
            root_data_type = model.fields[0].data_type

            # backward compatible
            # Remove duplicated root model
            if reuse_allowed and (
                root_reference := parser._get_duplicate_root_reference(model, root_data_type, models_set)  # noqa: SLF001
            ):
                parser._graph_builder.redirect_reference_users(model.reference, root_reference)  # noqa: SLF001
                models_to_remove.add(model)
                parser._graph_builder.detach_model_data_type_refs(model)  # noqa: SLF001
                continue

            # Remove parser from all DataModel children's base_classes
            for child in model.reference.iter_data_model_children():
                parser._graph_builder.set_base_classes(  # noqa: SLF001
                    child,
                    [
                        bc
                        for bc in child.base_classes
                        if not _keyed_answer(
                            "removed base class",
                            bc.reference is model.reference,
                            lambda bc=bc, model=model: bc.reference == model.reference,
                        )
                    ],
                )
                if not child.base_classes:  # pragma: no cover
                    parser._graph_builder.reset_base_classes(child)  # noqa: SLF001

        class_name = model.duplicate_class_name or model.class_name
        if (
            reuse_allowed
            and (original_model := model_class_names.get(class_name)) is not None
            and parser._reuse_optimization_context.allows_model(original_model)  # noqa: SLF001
            and renders_as_duplicate(parser, model, original_model, shapes)
        ):
            if shapes.references_match(original_model, model):
                model_to_duplicate_models[original_model].append(model)
            continue
        model_class_names[class_name] = model
    for model, duplicate_models in model_to_duplicate_models.items():
        for duplicate_model in duplicate_models:
            parser._graph_builder.redirect_reference_users(duplicate_model.reference, model.reference)  # noqa: SLF001
            # Deduplicate base_classes in all DataModel children
            for child in duplicate_model.reference.iter_data_model_children():
                parser._graph_builder.set_base_classes(  # noqa: SLF001
                    child,
                    _distinct_bases(shapes.row, child.base_classes),
                )
            models_to_remove.add(duplicate_model)

    if parser.reuse_model and parser.collapse_reuse_models:
        max_iterations, iteration = len(models), 0
        shapes = parser._model_shapes(COLLAPSE_REUSE)  # noqa: SLF001
        while True:
            iteration += 1
            if iteration > max_iterations:  # pragma: no cover
                msg = f"Deduplication exceeded max iterations ({max_iterations})"
                raise RuntimeError(msg)

            shapes.memo.clear()
            content_key_to_models: dict[tuple[Any, ...], list[DataModel]] = defaultdict(list)
            for model in parser._reuse_optimization_context.eligible_models(models):  # noqa: SLF001
                if model in models_to_remove or (
                    skips_root_model(shapes.row, collapse_root_models=parser.collapse_root_models)
                    and parser._is_root_model(model)  # noqa: SLF001
                ):
                    continue
                content_key_to_models[shapes.key(model)].append(model)

            if not (
                duplicates := [
                    (canonical, dup)
                    for group in content_key_to_models.values()
                    if len(group) > 1
                    for canonical, dup in shapes.iter_matching_duplicates(
                        parser._typed_map_values_last(group),  # noqa: SLF001
                        keyed=False,
                    )
                    if dup not in models_to_remove
                ]
            ):
                break

            for canonical, duplicate in duplicates:
                redirect_collapsed_reuse_users(
                    parser._graph_builder,  # noqa: SLF001
                    parser._collapsed_reuse_models,  # noqa: SLF001
                    parser._collapsed_reuse_users,  # noqa: SLF001
                    duplicate,
                    canonical,
                )
                for child in duplicate.reference.iter_data_model_children():
                    parser._graph_builder.set_base_classes(  # noqa: SLF001
                        child,
                        _distinct_bases(shapes.row, child.base_classes),
                    )
                models_to_remove.add(duplicate)

    # Batch removal: O(n) instead of O(n²)
    if models_to_remove:
        models[:] = [m for m in models if m not in models_to_remove]
        for model in models_to_remove:
            parser._graph_builder.retire_model(model)  # noqa: SLF001


def replace_duplicate_name_in_module(parser: StructureHost, models: list[DataModel]) -> None:
    """Make class names unique within a module.

    Renames change the rendered type hints of referencing models, so cached dedup keys of the
    module are dropped whenever a model is renamed. Models of typed map values come last, so the other
    models keep the names they had before values were typed. A stand-in, which is never written, neither takes nor
    keeps a name from the others, and the class it stands for binds its name as an import of each model that
    inherits it.
    """
    models = all_models = parser._typed_map_values_last(models)  # noqa: SLF001
    for model in models:
        model._write_pre_render_nullability()  # noqa: SLF001
        remember_nested_factory_order_before_nullability(model)
    if stand_ins := parser._base_stand_in_imports:  # noqa: SLF001
        models = [model for model in models if model_key_of(model.reference) not in stand_ins]
    exclude_names = {i.alias or i.import_ for m in models for i in m.imports}
    if stand_ins:
        exclude_names.update(
            import_.alias or import_.import_
            for model in models
            for base_class in model.base_classes
            if base_class.reference is not None
            and (import_ := stand_ins.get(model_key_of(base_class.reference))) is not None
        )
    scoped_model_resolver = ModelResolver(
        exclude_names=exclude_names,
        duplicate_name_suffix="Model",
        custom_class_name_generator=(lambda name: name) if parser.custom_class_name_generator else None,
        target_python_version=parser.target_python_version,
    )

    model_names: dict[str, DataModel] = {}
    renamed = False
    for model in models:
        class_name: str = model.class_name
        generated_name: str = scoped_model_resolver.add(
            [model.path],
            class_name,
            unique=True,
            class_name=True,
            preserve_class_name=parser._should_preserve_explicit_root_class_name(class_name),  # noqa: SLF001
        ).name
        if class_name != generated_name:
            parser._graph_builder.rename_model(model, class_name=generated_name)  # noqa: SLF001
            renamed = True
        model_names[model.class_name] = model

    for model in models:
        duplicate_name = model.duplicate_class_name
        # check only first desired name
        if duplicate_name and duplicate_name not in model_names:
            del model_names[model.class_name]
            parser._graph_builder.rename_model(model, class_name=duplicate_name)  # noqa: SLF001
            model_names[duplicate_name] = model
            renamed = True

    if not renamed:
        return
    for model in all_models:
        model.invalidate_render_caches()


def find_duplicate_models_across_modules(
    parser: StructureHost,
    module_models: list[tuple[tuple[str, ...], list[DataModel]]],
) -> list[tuple[tuple[str, ...], DataModel, tuple[str, ...], DataModel]]:
    """Find duplicate models across all modules by comparing render output, imports and referenced models.

    Type hints render references by name before imports are resolved, so equal render output from
    different modules can still reference different models that share a name.
    """
    model_modules = {
        model: module
        for module, models in module_models
        for model in parser._reuse_optimization_context.eligible_models(models)  # noqa: SLF001
    }
    shapes = parser._model_shapes(TREE_SCOPE)  # noqa: SLF001
    return [
        (model_modules[model], model, model_modules[canonical], canonical)
        for canonical, model in shapes.iter_matching_duplicates(
            parser._typed_map_values_last(list(model_modules)),  # noqa: SLF001
            keyed=True,
        )
    ]


def replace_typed_map_value_duplicates(
    parser: StructureHost,
    module_models: list[tuple[tuple[str, ...], list[DataModel]]],
    duplicates: list[tuple[tuple[str, ...], DataModel, tuple[str, ...], DataModel]],
) -> list[tuple[tuple[str, ...], DataModel, tuple[str, ...], DataModel]]:
    """Replace typed map values by the other models they duplicate, leaving the rest to the shared module.

    Those other models stay where they are, as they did before map values were typed.
    """
    typed_map_values = parser._typed_map_value_reference_ids  # noqa: SLF001
    module_lists = dict(module_models)
    remaining = []
    for duplicate in duplicates:
        duplicate_module, duplicate_model, _, canonical = duplicate
        if id(duplicate_model.reference) in typed_map_values and id(canonical.reference) not in typed_map_values:
            parser._graph_builder.redirect_reference_users(duplicate_model.reference, canonical.reference)  # noqa: SLF001
            module_lists[duplicate_module].remove(duplicate_model)
            parser._graph_builder.retire_model(duplicate_model)  # noqa: SLF001
        else:
            remaining.append(duplicate)
    return remaining


def validate_shared_module_name(
    parser: StructureHost,
    module_models: list[tuple[tuple[str, ...], list[DataModel]]],
) -> None:
    """Validate that the shared module name doesn't conflict with existing modules."""
    shared_module = parser.shared_module_name
    existing_module_names = {module[0] for module, _ in module_models if module}
    if shared_module in existing_module_names:
        msg = (
            f"Schema file or directory '{shared_module}' conflicts with the shared module name. "
            f"Use --shared-module-name to specify a different name."
        )
        raise Error(msg)


def create_shared_module_from_duplicates(  # noqa: PLR0912, PLR0915
    parser: StructureHost,
    module_models: list[tuple[tuple[str, ...], list[DataModel]]],
    duplicates: list[tuple[tuple[str, ...], DataModel, tuple[str, ...], DataModel]],
    reference_models: list[DataModel] | None,
) -> tuple[tuple[str, ...], list[DataModel]]:
    """Create shared module with canonical models and replace duplicates with inherited models.

    A duplicate that is removed rather than replaced hands every user to the shared model, since
    users in other modules would otherwise import it from a module that no longer defines it.
    """
    shared_module = parser.shared_module_name

    shared_models: list[DataModel] = []
    canonical_to_shared_ref: dict[DataModel, Reference] = {}
    canonical_models_seen: set[DataModel] = set()

    # Process in order of first appearance in duplicates to ensure stable ordering
    for _, _, _, canonical in duplicates:
        if canonical in canonical_models_seen:
            continue
        canonical_models_seen.add(canonical)
        parser._graph_builder.update_model_reference(canonical, new_file_path=Path(f"{shared_module}.py"))  # noqa: SLF001
        canonical_to_shared_ref[canonical] = canonical.reference
        shared_models.append(canonical)

    supports_inheritance = parser.data_model_type.SUPPORTS_TREE_SCOPE_REUSE_MODEL_INHERITANCE
    require_update = parser._recursion_facts["require_update"]  # noqa: SLF001
    updated_models: list[DataModel] = []

    module_models_sets: dict[tuple[str, ...], set[DataModel]] = {
        module: set(models) for module, models in module_models
    }
    models_to_remove: dict[tuple[str, ...], set[DataModel]] = defaultdict(set)

    for duplicate_module, duplicate_model, _, canonical_model in duplicates:
        shared_ref = canonical_to_shared_ref[canonical_model]
        models_set = module_models_sets.get(duplicate_module)
        if not models_set or duplicate_model not in models_set:  # pragma: no cover
            msg = f"Duplicate model {duplicate_model.name} not found in module {duplicate_module}"
            raise RuntimeError(msg)

        for module, models in module_models:  # pragma: no branch
            if module != duplicate_module:
                continue
            if isinstance(duplicate_model, Enum) or not supports_inheritance or parser.collapse_reuse_models:
                redirect_collapsed_reuse_users(
                    parser._graph_builder,  # noqa: SLF001
                    parser._collapsed_reuse_models,  # noqa: SLF001
                    parser._collapsed_reuse_users,  # noqa: SLF001
                    duplicate_model,
                    canonical_model,
                )
                models_to_remove[module].add(duplicate_model)
                parser._graph_builder.retire_model(duplicate_model)  # noqa: SLF001
            else:
                inherited_model = duplicate_model.create_reuse_model(shared_ref)
                parser._graph_builder.redirect_model_reference_users(  # noqa: SLF001
                    duplicate_model,
                    reference_models if reference_models is not None else models,
                    inherited_model.reference,
                )
                if model_key_of(shared_ref) in require_update:
                    updated_models.append(inherited_model)
                replace_model_in_list(models, duplicate_model, inherited_model)
                parser._graph_builder.replace_model(duplicate_model, inherited_model)  # noqa: SLF001
            break

    for canonical in canonical_models_seen:
        for module, models_set in module_models_sets.items():
            if canonical in models_set:
                models_to_remove[module].add(canonical)
                break
        else:  # pragma: no cover
            msg = f"Canonical model {canonical.name} not found in any module"
            raise RuntimeError(msg)

    for module, models in module_models:
        to_remove = models_to_remove.get(module)
        if to_remove:
            models[:] = [m for m in models if m not in to_remove]

    if reference_models is not None:
        # Relocation can introduce forward references after the initial sort.
        keyed = keyed_models(shared_models, "shared module forward references")
        later_shared_keys: set[ModelKey] = set()
        for model in reversed(shared_models):
            if parser.generation_store.index.referenced_keys_for_model(model, keyed) & later_shared_keys:
                updated_models.append(model)
            later_shared_keys.add(model_key_of(model.reference))
    if updated_models:
        require_update_action(parser, updated_models)

    return (shared_module,), shared_models


def reuse_model_tree_scope(
    parser: StructureHost,
    module_models: list[tuple[tuple[str, ...], list[DataModel]]],
    module_split_mode: ModuleSplitMode | None,
) -> tuple[tuple[str, ...], list[DataModel]] | None:
    """Deduplicate models across all modules, placing shared models in shared.py."""
    if not parser.reuse_model or parser.reuse_scope != ReuseScope.Tree:
        return None

    duplicates = find_duplicate_models_across_modules(parser, module_models)
    if parser._typed_map_value_reference_ids:  # noqa: SLF001
        duplicates = replace_typed_map_value_duplicates(parser, module_models, duplicates)
    if not duplicates:
        return None

    validate_shared_module_name(parser, module_models)
    # Single-model splitting puts users outside the duplicate's original module.
    reference_models = (
        [model for _, models in module_models for model in models]
        if module_split_mode == ModuleSplitMode.Single
        else None
    )
    return create_shared_module_from_duplicates(parser, module_models, duplicates, reference_models)


def compute_internal_module_path(
    scc_modules: set[tuple[str, ...]],
    existing_modules: set[tuple[str, ...]],
    *,
    base_name: str = "_internal",
) -> tuple[str, ...]:
    """Compute the internal module path for an SCC."""
    directories = [get_module_directory(m) for m in sorted(scc_modules)]

    if not directories or any(not d for d in directories):
        prefix: tuple[str, ...] = ()
    else:
        path_strings = ["/".join(d) for d in directories]
        common = os.path.commonpath(path_strings)
        prefix = tuple(common.split("/")) if common else ()

    base_module = (base_name,) if not prefix else (*prefix, base_name)

    if base_module in existing_modules:
        counter = 1
        while True:
            candidate = (*prefix, f"{base_name}_{counter}") if prefix else (f"{base_name}_{counter}",)
            if candidate not in existing_modules:
                return candidate
            counter += 1

    return base_module


def collect_scc_models(
    scc: set[tuple[str, ...]],
    result_modules: dict[tuple[str, ...], list[DataModel]],
) -> tuple[list[DataModel], dict[int, tuple[str, ...]]]:
    """Collect all models from SCC modules.

    Returns:
        - List of all models in the SCC
        - Mapping from model id to its original module
    """
    all_models: list[DataModel] = []
    model_to_module: dict[int, tuple[str, ...]] = {}
    for scc_module in sorted(scc):
        for model in result_modules[scc_module]:
            all_models.append(model)
            model_to_module[id(model)] = scc_module
    return all_models, model_to_module


def rename_and_relocate_scc_models(
    parser: StructureHost,
    all_scc_models: list[DataModel],
    model_to_original_module: dict[int, tuple[str, ...]],
    internal_module: tuple[str, ...],
    internal_path: Path,
) -> defaultdict[tuple[str, ...], list[tuple[str, str]]]:
    """Rename duplicate classes and relocate models to internal module.

    Class names follow ``_merged_class_names``; forwarders re-export each model under its original name. A stand-in
    moves with its module, but takes no part in the names and is re-exported nowhere.

    Returns:
        Mapping from original module to list of (original_name, new_name) tuples.
    """
    internal_module_str = ".".join(internal_module)
    module_class_mappings: defaultdict[tuple[str, ...], list[tuple[str, str]]] = defaultdict(list)
    stand_ins = parser._base_stand_in_imports  # noqa: SLF001
    named_models = [model for model in all_scc_models if model_key_of(model.reference) not in stand_ins]
    original_class_names = [model.class_name for model in named_models]
    new_class_names = dict(zip(map(id, named_models), _merged_class_names(original_class_names), strict=True))

    with parser._graph_builder.defer_refresh():  # noqa: SLF001
        for model in all_scc_models:
            original_class_name = model.class_name
            new_class_name = new_class_names.get(id(model), original_class_name)
            parser._graph_builder.rename_model(  # noqa: SLF001
                model,
                reference_name=new_class_name,
                new_path=f"{internal_module_str}.{new_class_name}",
                new_file_path=internal_path,
            )
            if id(model) in new_class_names:
                module_class_mappings[model_to_original_module[id(model)]].append((original_class_name, new_class_name))

    return module_class_mappings


def module_dependency_cycles(
    parser: StructureHost,
    module_models_list: list[tuple[tuple[str, ...], list[DataModel]]],
) -> list[set[tuple[str, ...]]]:
    """Find the modules that import each other, from the snapshot of the references between their models.

    A stand-in is no member of its module: its module imports nothing for it, and nothing imports it.
    """
    if stand_ins := parser._base_stand_in_imports:  # noqa: SLF001
        module_models_list = [
            (module, [model for model in models if model_key_of(model.reference) not in stand_ins])
            for module, models in module_models_list
        ]
    snapshot = GraphSnapshot.MODULE_DEPENDENCIES
    keyed = keyed_models((model for _, models in module_models_list for model in models), snapshot.value)
    module_of: dict[ModelKey, tuple[str, ...]] = {}
    for module, models in module_models_list:
        for model in models:
            module_of[model_key_of(model.reference)] = module
    index = parser.generation_store.index
    graph = reference_graph(
        snapshot,
        keyed.models,
        {
            EdgeKind.REFERENCE: {
                key: index.referenced_keys_for_model(model, keyed) for key, model in keyed.models.items()
            }
        },
    )

    def old() -> list[set[tuple[str, ...]]]:
        silent = unchecked(keyed)
        module_graph: dict[tuple[str, ...], set[tuple[str, ...]]] = {}
        for module, models in module_models_list:
            module_graph[module] = set()
            for model in models:
                for reference_key in index.referenced_keys_for_model(model, silent):
                    if (target_module := module_of.get(reference_key)) is not None and target_module != module:
                        module_graph[module].add(target_module)
        return find_circular_sccs(module_graph)

    return graph_answer(snapshot, find_circular_sccs(graph.quotient(module_of, (EdgeKind.REFERENCE,))), old)


def resolve_circular_imports(
    parser: StructureHost,
    module_models_list: list[tuple[tuple[str, ...], list[DataModel]]],
) -> tuple[
    list[tuple[tuple[str, ...], list[DataModel]]],
    set[tuple[str, ...]],
    dict[tuple[str, ...], tuple[tuple[str, ...], list[tuple[str, str]]]],
]:
    """Resolve circular imports by merging all SCCs into _internal.py modules.

    Uses Tarjan's algorithm to find strongly connected components (SCCs) in the
    module dependency graph. All modules in each SCC are merged into a single
    _internal.py module to break import cycles. Original modules become thin
    forwarders that re-export their classes from _internal.

    Returns:
        - Updated module_models_list with models moved to _internal modules
        - Set of _internal modules created
        - Forwarder map: original_module -> (internal_module, [(original_name, new_name)])
    """
    circular_sccs = module_dependency_cycles(parser, module_models_list)

    forwarder_map: dict[tuple[str, ...], tuple[tuple[str, ...], list[tuple[str, str]]]] = {}

    if not circular_sccs:
        return module_models_list, set(), forwarder_map

    # All circular SCCs are problematic and should be merged into _internal.py
    # to break the import cycles.
    problematic_sccs = circular_sccs

    existing_modules = {module for module, _ in module_models_list}
    internal_modules_created: set[tuple[str, ...]] = set()

    result_modules: dict[tuple[str, ...], list[DataModel]] = {
        module: list(models) for module, models in module_models_list
    }

    for scc in problematic_sccs:
        internal_module = compute_internal_module_path(scc, existing_modules | internal_modules_created)
        internal_modules_created.add(internal_module)
        internal_path = Path("/".join(internal_module))

        all_scc_models, model_to_original_module = collect_scc_models(scc, result_modules)
        module_class_mappings = rename_and_relocate_scc_models(
            parser, all_scc_models, model_to_original_module, internal_module, internal_path
        )
        all_scc_models = _sort_models_by_class_body_references(
            all_scc_models,
            parser.pydantic_v2_root_model_type,
            _runtime_validation_reference_keys(all_scc_models, same_module=False),
        )

        for scc_module in scc:
            if scc_module in result_modules:  # pragma: no branch
                result_modules[scc_module] = []
            if scc_module in module_class_mappings:  # pragma: no branch
                sorted_mappings = sorted(module_class_mappings[scc_module], key=operator.itemgetter(0))
                forwarder_map[scc_module] = (internal_module, sorted_mappings)
        result_modules[internal_module] = all_scc_models

    new_module_models: list[tuple[tuple[str, ...], list[DataModel]]] = [
        (internal_module, result_modules[internal_module])
        for internal_module in sorted(internal_modules_created)
        if internal_module in result_modules  # pragma: no branch
    ]

    for module, _ in module_models_list:
        if module not in internal_modules_created:  # pragma: no branch
            new_module_models.append((module, result_modules.get(module, [])))

    return new_module_models, internal_modules_created, forwarder_map


def group_models_by_module(parser: StructureHost, state: _StructureState) -> None:
    """Group the sorted models into output modules and index them."""
    state.module_models = _group_models_by_module(
        state.sorted_data_models.values(), state.module_split_mode, parser.target_python_version
    )
    state.model_to_module_models, state.model_path_to_module_name = _index_module_models(
        state.module_models, state.module_split_mode
    )


def reset_collapsed_reuse_state(parser: StructureHost, _state: _StructureState) -> None:
    """Forget the models collapsed reuse removed in an earlier parse."""
    parser._collapsed_reuse_models.clear()  # noqa: SLF001
    parser._collapsed_reuse_users.clear()  # noqa: SLF001


def snapshot_union_tags(parser: StructureHost, state: _StructureState) -> None:
    """Record the tags of each discriminated union before reuse merges any model."""
    parser._reuse_union_tags = (  # noqa: SLF001
        {model: tags for _, models in state.module_models for model in models if (tags := _union_tags(model))}
        if parser.reuse_model
        else {}
    )


def apply_forced_invalid_dotted_stdout_repair(parser: StructureHost, state: _StructureState) -> None:
    """Apply the repair of invalid dotted stdout modules, and group the models again when it applied."""
    if not parser._apply_forced_invalid_dotted_stdout_repair(state.module_models, state.module_split_mode):  # noqa: SLF001
        return
    live_model_ids = {id(model) for _, models in state.module_models for model in models}
    state.module_models = _group_models_by_module(
        (model for model in state.sorted_data_models.values() if id(model) in live_model_ids),
        state.module_split_mode,
        parser.target_python_version,
    )
    state.model_to_module_models, state.model_path_to_module_name = _index_module_models(
        state.module_models, state.module_split_mode
    )


def apply_reuse_model_tree_scope(parser: StructureHost, state: _StructureState) -> None:
    """Move the duplicates of tree-scope reuse into a shared module first in the module list."""
    if not (shared_module_entry := reuse_model_tree_scope(parser, state.module_models, state.module_split_mode)):
        return
    state.module_models.insert(0, shared_module_entry)
    if state.module_split_mode == ModuleSplitMode.Single:
        state.model_to_module_models, state.model_path_to_module_name = _index_module_models(
            state.module_models, state.module_split_mode
        )


def resolve_module_circular_imports(parser: StructureHost, state: _StructureState) -> None:
    """Break the import cycles between modules."""
    state.module_models, state.internal_modules, state.forwarder_map = resolve_circular_imports(
        parser, state.module_models
    )
    if state.internal_modules:
        state.model_path_to_module_name.forget(
            model for module, models in state.module_models if module in state.internal_modules for model in models
        )


def build_structure(parser: StructureHost, run: StructuredRun[Any]) -> None:
    """Group the models into output modules through ``_build_module_structure``."""
    (
        run.module_models,
        run.internal_modules,
        run.forwarder_map,
        run.model_to_module_models,
        run.model_path_to_module_name,
    ) = parser._build_module_structure(run.sorted_data_models, run.args.module_split_mode)  # noqa: SLF001


def configure_code_formatter(
    parser: StructureHost, run: StructuredRun[ConfigT], *, default_build_code_formatter: Callable[..., CodeFormatter]
) -> None:
    """Build the code formatter of the run, unless the builtin defaults need none.

    ``default_build_code_formatter`` is ``Parser._build_code_formatter``, which a subclass of the parser may override.
    """
    match parser.formatters:
        case [] if (
            not parser.custom_formatter
            and "_build_code_formatter" not in parser.__dict__
            and type(parser)._build_code_formatter is default_build_code_formatter  # noqa: SLF001
        ):
            pass
        case _:
            run.config = run.config._replace(
                code_formatter=parser._build_code_formatter(  # noqa: SLF001
                    run.args.settings_path,
                    is_multi_module_output=parser.defer_formatting or len(run.module_models) > 1,
                ),
            )


def detect_standard_generation_templates(parser: StructureHost, run: StructuredRun[ConfigT]) -> None:
    """Decide whether the run renders with the standard templates and the builtin formatter only."""
    parser._uses_standard_generation_templates = bool(  # noqa: SLF001
        (code_formatter := run.config.code_formatter)
        and code_formatter.use_builtin_formatter
        and parser._configured_generation_types_are_builtin  # noqa: SLF001
        and not (parser_config := parser.config).custom_template_dir
        and not any((
            parser_config.additional_imports,
            parser_config.class_decorators,
            parser_config.base_class,
            parser_config.base_class_map,
            parser_config.extra_template_data,
            parser_config.validators,
            parser_config.generate_schema_validators,
            parser_config.alias_generator,
            parser_config.custom_class_name_generator,
            parser_config.dump_resolve_reference_action is not None
            and not get_resolve_reference_action_capabilities(
                parser_config.dump_resolve_reference_action
            ).generated_formatter_safe,
            parser_config.type_mappings,
            parser_config.type_overrides,
            parser_config.import_overrides,
        ))
    )
