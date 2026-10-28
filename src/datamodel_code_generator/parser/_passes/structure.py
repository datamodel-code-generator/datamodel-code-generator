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

from datamodel_code_generator import AllExportsScope, Error, ModuleSplitMode, ReuseScope
from datamodel_code_generator._model_keys import model_key_of
from datamodel_code_generator._reference_graph import EdgeKind, GraphSnapshot, graph_answer, reference_graph
from datamodel_code_generator._scc import find_circular_sccs, strongly_connected_components
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
    _parsed_rule_data_types,
    _runtime_validation_reference_keys,
    _sort_models_by_class_body_references,
    redirect_collapsed_reuse_users,
    replace_model_in_list,
    require_update_action,
)
from datamodel_code_generator.parser._passes._state import Forwarder, RecursionFacts, _ModuleNames
from datamodel_code_generator.reference import ModelResolver
from datamodel_code_generator.util import camel_to_snake

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence

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
    def _declared_class_stand_ins(self) -> set[ModelKey]:
        """The stand-ins of schemas whose ``x-python-import`` declares the class, which no root-model rule drops."""

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
    def _self_named_root_models(self) -> list[DataModel]:
        """The root models naming themselves that the deduplication of root models dropped, whose users name them."""

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

    @property
    def treat_dot_as_module(self) -> bool | None:
        """Whether dots in schema names make modules."""

    def _apply_forced_invalid_dotted_stdout_repair(
        self, module_models: ModuleModels, module_split_mode: ModuleSplitMode | None
    ) -> bool:
        """Flatten the invalid dotted modules of stdout output, and return whether it did."""

    def _build_code_formatter(self, settings_path: Path | None, *, is_multi_module_output: bool) -> CodeFormatter:
        """Return the code formatter of the run."""

    def _build_module_structure(
        self,
        sorted_data_models: SortedDataModels,
        module_split_mode: ModuleSplitMode | None,
        all_exports_scope: AllExportsScope | None,
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


def _drop_root_model_bases(parser: StructureHost, model: DataModel) -> None:
    """Remove a root model from the bases of the models that inherit it, which then keep their default base."""
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
        if not child.base_classes:
            parser._graph_builder.reset_base_classes(child)  # noqa: SLF001


def _drop_duplicate_root_model(parser: StructureHost, model: DataModel, root_reference: Reference) -> None:
    """Redirect the users of a duplicated root model to the reference its root names, and detach its own types.

    A root model whose root names the model itself has no other model to redirect them to: the models that inherit
    it drop it from their bases, as they drop every other root model, and the fields that use it keep naming it,
    which ``reject_alias_cycles_without_schema`` reads from ``_self_named_root_models``.
    """
    parser._graph_builder.redirect_reference_users(model.reference, root_reference)  # noqa: SLF001
    parser._graph_builder.detach_model_data_type_refs(model)  # noqa: SLF001
    if root_reference is model.reference:
        parser._self_named_root_models.append(model)  # noqa: SLF001
        _drop_root_model_bases(parser, model)


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
        if parser._is_root_model(model) and (  # noqa: SLF001
            model_key_of(model.reference) not in parser._declared_class_stand_ins  # noqa: SLF001
        ):
            root_data_type = model.fields[0].data_type

            # backward compatible
            # Remove duplicated root model
            if reuse_allowed and (
                root_reference := parser._get_duplicate_root_reference(model, root_data_type, models_set)  # noqa: SLF001
            ):
                _drop_duplicate_root_model(parser, model, root_reference)
                models_to_remove.add(model)
                continue
            _drop_root_model_bases(parser, model)

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
            parser._graph_builder.redirect_reference_users(  # noqa: SLF001
                duplicate_model.reference, model.reference, merged=True
            )
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
            parser._graph_builder.redirect_reference_users(  # noqa: SLF001
                duplicate_model.reference, canonical.reference, merged=True
            )
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


def _is_below(path: tuple[str, ...], package: tuple[str, ...]) -> bool:
    """Return whether the module at ``path`` lies below ``package``, whose ``__init__`` module Python runs first."""
    return len(path) > len(package) and path[: len(package)] == package


def _failing_cycles(
    nodes: set[tuple[tuple[str, ...], int]],
    imports: dict[tuple[tuple[str, ...], int], set[tuple[tuple[str, ...], int]]],
    packages: dict[tuple[tuple[str, ...], int], set[tuple[tuple[str, ...], int]]],
    entered: frozenset[tuple[tuple[str, ...], int]] = frozenset(),
) -> Iterator[set[tuple[tuple[str, ...], int]]]:
    """Yield the import cycles among ``nodes`` that fail when one of their modules is imported first.

    A cycle of a package and modules below it runs the package first, whichever of them is imported first, so it
    imports unless a module of it imports from the package: the cycles left without running the packages of
    ``entered`` are checked again. Any other cycle finds one of its modules half built.
    """
    graph = {node: (imports[node] | (packages[node] - entered)) & nodes for node in nodes}
    for component in strongly_connected_components(graph):
        if len(component) == 1:
            continue
        top = min(component, key=lambda node: len(node[0]))
        if top[1] < 0 and top not in entered and all(node == top or _is_below(node[0], top[0]) for node in component):
            yield from _failing_cycles(component, imports, packages, entered | {top})
        else:
            yield component


def _exported_imports(
    exporters: Iterable[tuple[tuple[str, ...], int]], scope: AllExportsScope
) -> defaultdict[tuple[str, ...], set[tuple[tuple[str, ...], int]]]:
    """Return the exporters that the ``__init__`` module of each package imports to re-export their names.

    A package re-exports the modules directly below it, or all modules below it with the recursive scope.
    """
    imported: defaultdict[tuple[str, ...], set[tuple[tuple[str, ...], int]]] = defaultdict(set)
    for node in exporters:
        path = node[0]
        first = max(len(path) - 1, 0) if scope == AllExportsScope.Children else 0
        for size in range(first, len(path)):
            imported[path[:size]].add(node)
    return imported


def _expanded_path(module: tuple[str, ...]) -> tuple[str, ...]:
    """Return the package layout of a module whose dotted names ``treat_dot_as_module`` turns into packages."""
    return tuple(part for name in module for part in name.split("."))


def _same_path(module: tuple[str, ...]) -> tuple[str, ...]:
    """Return the module path as it is."""
    return module


def _failing_import_cycles(
    merges: list[set[tuple[str, ...]]],
    module_graph: dict[tuple[str, ...], set[tuple[str, ...]]],
    exporting: set[tuple[str, ...]],
    all_exports_scope: AllExportsScope | None,
    *,
    dotted: bool,
) -> list[set[tuple[tuple[str, ...], int]]]:
    """Return the import cycles through an ``_internal`` module that fail once each merge moves into its own.

    A node is a module path with ``-1``, or the path of the ``_internal`` module of a merge with the index of the
    merge. A merged module imports its ``_internal`` module, which imports what the merged modules import; a module
    that uses a merged model imports the ``_internal`` module. Importing a module runs the ``__init__`` module of each
    package above it first. With ``all_exports_scope``, the ``__init__`` module of a package that is no merged module
    imports the modules below it that ``exporting`` names, and the ``_internal`` modules, to re-export their names;
    ``dotted`` places a module in the packages that the dots of its name make.
    """
    place = _expanded_path if dotted else _same_path
    internal_nodes = [(place(compute_internal_module_path(merge, set())), index) for index, merge in enumerate(merges)]
    forwarded = {module: internal_nodes[index] for index, merge in enumerate(merges) for module in merge}

    def node_of(module: tuple[str, ...]) -> tuple[tuple[str, ...], int]:
        return forwarded.get(module, (place(module), -1))

    imports = {
        (place(module), -1): {forwarded[module]} if module in forwarded else set(map(node_of, targets))
        for module, targets in module_graph.items()
    }
    for node, merge in zip(internal_nodes, merges, strict=True):
        imports[node] = set(map(node_of, {target for module in merge for target in module_graph[module]} - merge))
    exports: dict[tuple[tuple[str, ...], int], set[tuple[tuple[str, ...], int]]] = {}
    if all_exports_scope is not None:
        exporters = [(place(module), -1) for module in exporting if module not in forwarded]
        merged_paths = set(map(place, forwarded))
        exports = {
            (package, -1): targets
            for package, targets in _exported_imports([*exporters, *internal_nodes], all_exports_scope).items()
            if package not in merged_paths
        }
    edges = {node: imports.get(node, set()) | exports.get(node, set()) for node in imports.keys() | exports.keys()}
    packages = {
        (path, index): {(path[:size], -1) for size in range(len(path)) if (path[:size], -1) in edges} - targets
        for (path, index), targets in edges.items()
    }
    module_at = {place(module): module for module in module_graph}
    exported = set().union(*exports.values())

    def moves(node: tuple[tuple[str, ...], int], cycle: set[tuple[tuple[str, ...], int]]) -> bool:
        """Return whether the module of ``node`` joins a merge.

        A package without a module of ``module_graph`` has an empty ``__init__`` module that stays. A module that a
        package imports to re-export it, and a package that does so, join only if the cycle holds an import of
        theirs: the package runs first and imports the module, whichever of them is imported first.
        """
        path, index = node
        if index >= 0:
            return True
        if path not in module_at:
            return False
        if node not in exports and node not in exported:
            return True
        return bool(imports[node] & cycle) or any(node in imports.get(held, ()) for held in cycle)

    return [
        {(module_at[path] if index < 0 else path, index) for path, index in cycle if moves((path, index), cycle)}
        for cycle in _failing_cycles(set(edges), edges, packages)
        if any(index >= 0 for _, index in cycle)
    ]


def _joined_merges(
    merges: list[set[tuple[str, ...]]], cycles: list[set[tuple[tuple[str, ...], int]]]
) -> list[set[tuple[str, ...]]]:
    """Join each import cycle with its modules and the merges whose ``_internal`` module or modules it holds.

    A joined merge takes the place of the first merge it holds; the other merges keep their order.
    """
    merge_of = {module: index for index, merge in enumerate(merges) for module in merge}
    group_of = list(range(len(merges)))
    joined = dict(enumerate(map(set, merges)))
    for cycle in cycles:
        first, *rest = sorted({
            group_of[index if index >= 0 else merge_of[path]] for path, index in cycle if index >= 0 or path in merge_of
        })
        for group in rest:
            joined[first] |= joined.pop(group)
            group_of = [first if held == group else held for held in group_of]
        joined[first] |= {path for path, index in cycle if index < 0}
    return list(joined.values())


def module_dependency_cycles(
    parser: StructureHost,
    module_models_list: list[tuple[tuple[str, ...], list[DataModel]]],
    all_exports_scope: AllExportsScope | None = None,
) -> list[set[tuple[str, ...]]]:
    """Find the modules to merge into ``_internal`` modules (``_module_merges``)."""
    return _module_merges(parser, module_models_list, all_exports_scope)[1]


def _module_merges(
    parser: StructureHost,
    module_models_list: list[tuple[tuple[str, ...], list[DataModel]]],
    all_exports_scope: AllExportsScope | None,
) -> tuple[list[set[tuple[str, ...]]], list[set[tuple[str, ...]]]]:
    """Find the modules to merge into ``_internal`` modules, from the snapshot of the references between models.

    Return the merges of the released layout, the modules that import each other, and the merges they grow into.

    A merge starts as modules that import each other. Importing a module runs the ``__init__`` module of each package
    above it first, so ``_internal`` can still import itself back: through a forwarder package of the merge above a
    module it imports, or through a module it imports that uses a merged model. The modules of each such import cycle
    that fails join the merge, with the merges whose modules it holds, until none is left; the import cycles that are
    there without the merges stay as they are.
    With ``all_exports_scope``, the ``__init__`` module of a package that is no merged module imports the modules
    below it that have a name to export, so the import cycles through it fail too.
    The models that the schema validator rules of a model name count as its field types do: its module imports them
    for the class body that evaluates the rules, an alias written as a class for its rules included.
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
    rule_references = {
        key: keyed.keys_of(
            reference for data_type in _parsed_rule_data_types(model) if (reference := data_type.reference) is not None
        )
        for key, model in keyed.models.items()
    }
    graph = reference_graph(
        snapshot,
        keyed.models,
        {
            EdgeKind.REFERENCE: {
                key: index.referenced_keys_for_model(model, keyed) for key, model in keyed.models.items()
            },
            EdgeKind.RULE_TYPE: rule_references,
        },
    )

    def old() -> list[set[tuple[str, ...]]]:
        silent = unchecked(keyed)
        module_graph: dict[tuple[str, ...], set[tuple[str, ...]]] = {}
        for module, models in module_models_list:
            module_graph[module] = set()
            for model in models:
                for reference_key in (
                    index.referenced_keys_for_model(model, silent) | rule_references[model_key_of(model.reference)]
                ):
                    if (target_module := module_of.get(reference_key)) is not None and target_module != module:
                        module_graph[module].add(target_module)
        return find_circular_sccs(module_graph)

    module_graph = graph.quotient(module_of, (EdgeKind.REFERENCE, EdgeKind.RULE_TYPE))
    merges = graph_answer(snapshot, find_circular_sccs(module_graph), old)
    exporting = {
        module
        for module, models in module_models_list
        if any(model.reference and not model.reference.short_name.startswith("_") for model in models)
    }
    released = merges
    while merges and (
        failing := _failing_import_cycles(
            merges,
            module_graph,
            exporting,
            all_exports_scope,
            dotted=all_exports_scope is not None and bool(parser.treat_dot_as_module),
        )
    ):
        merges = _joined_merges(merges, failing)
    return released, merges


def _released_internal_modules(
    released: list[set[tuple[str, ...]]], existing_modules: set[tuple[str, ...]]
) -> dict[frozenset[tuple[str, ...]], tuple[str, ...]]:
    """Return the ``_internal`` module that the released layout gives each of its merges, numbered in merge order."""
    paths: dict[frozenset[tuple[str, ...]], tuple[str, ...]] = {}
    for merge in released:
        paths[frozenset(merge)] = compute_internal_module_path(merge, existing_modules | set(paths.values()))
    return paths


def _released_internal_forwarder(  # noqa: PLR0913, PLR0917
    parser: StructureHost,
    merge: set[tuple[str, ...]],
    grown_models: list[DataModel],
    model_to_original_module: dict[int, tuple[str, ...]],
    released_names: dict[int, str],
    internal_module: tuple[str, ...],
    released_internal_modules: frozenset[tuple[str, ...]],
) -> Forwarder:
    """Return the forwarder of the ``_internal`` module that the released layout writes for a merge a grown merge holds.

    It binds the merge's models to the class names that module gave them (``_merged_class_names`` over the merge alone),
    in the order it wrote them.
    """
    stand_ins = parser._base_stand_in_imports  # noqa: SLF001
    models = [model for model in grown_models if model_to_original_module[id(model)] in merge]
    named = [model for model in models if model_key_of(model.reference) not in stand_ins]
    merged_names = dict(
        zip(map(id, named), _merged_class_names([released_names[id(model)] for model in named]), strict=True)
    )
    order = _sort_models_by_class_body_references(
        models, parser.pydantic_v2_root_model_type, _runtime_validation_reference_keys(models, same_module=False)
    )
    return Forwarder(
        internal_module,
        tuple(sorted((internal_module, model.class_name, merged_names[id(model)]) for model in named)),
        tuple((merged_names[id(model)], model.class_name) for model in order if id(model) in merged_names),
        released_internal_modules,
    )


def resolve_circular_imports(  # noqa: PLR0914
    parser: StructureHost,
    module_models_list: list[tuple[tuple[str, ...], list[DataModel]]],
    all_exports_scope: AllExportsScope | None,
) -> tuple[list[tuple[tuple[str, ...], list[DataModel]]], set[tuple[str, ...]], ForwarderMap]:
    """Merge the modules of each import cycle into an ``_internal`` module; the modules become forwarders.

    A merge of the released layout (``_module_merges``) keeps the ``_internal`` module that layout gives it. A merge
    that grew takes an ``_internal`` module of its own beside every module and released ``_internal`` module, and each
    module of the released layout that it moves keeps what that layout bound there: a module that only the growth
    joined, and the ``_internal`` module of each released merge it holds, become forwarders that carry their models in
    that layout (``Forwarder.released``), whose imports render adds.

    Returns:
        - Updated module_models_list with models moved to _internal modules
        - Set of _internal modules created
        - Forwarder map: original_module -> ``Forwarder``
    """
    released, merges = _module_merges(parser, module_models_list, all_exports_scope)
    forwarder_map: ForwarderMap = {}
    if not merges:
        return module_models_list, set(), forwarder_map

    existing_modules = {module for module, _ in module_models_list}
    released_paths = _released_internal_modules(released, existing_modules)
    released_internal_modules = frozenset(released_paths.values())
    internal_modules_created: set[tuple[str, ...]] = set()
    released_forwarders: list[tuple[str, ...]] = []
    stand_ins = parser._base_stand_in_imports  # noqa: SLF001

    result_modules: dict[tuple[str, ...], list[DataModel]] = {
        module: list(models) for module, models in module_models_list
    }

    for merge in merges:
        grown = (internal_module := released_paths.get(frozenset(merge))) is None
        if internal_module is None:
            internal_module = compute_internal_module_path(
                merge, existing_modules | released_internal_modules | internal_modules_created
            )
        internal_modules_created.add(internal_module)
        internal_path = Path("/".join(internal_module))

        all_scc_models, model_to_original_module = collect_scc_models(merge, result_modules)
        released_names = {id(model): model.class_name for model in all_scc_models}
        module_class_mappings = rename_and_relocate_scc_models(
            parser, all_scc_models, model_to_original_module, internal_module, internal_path
        )
        joined = set(merge)
        if grown:
            for released_merge in (released_merge for released_merge in released if released_merge <= merge):
                joined -= released_merge
                released_path = released_paths[frozenset(released_merge)]
                forwarder_map[released_path] = _released_internal_forwarder(
                    parser,
                    released_merge,
                    all_scc_models,
                    model_to_original_module,
                    released_names,
                    internal_module,
                    released_internal_modules,
                )
                released_forwarders.append(released_path)
        all_scc_models = _sort_models_by_class_body_references(
            all_scc_models,
            parser.pydantic_v2_root_model_type,
            _runtime_validation_reference_keys(all_scc_models, same_module=False),
        )

        for scc_module in merge:
            module_models = result_modules[scc_module]
            result_modules[scc_module] = []
            if scc_module in module_class_mappings:  # pragma: no branch
                sorted_mappings = sorted(module_class_mappings[scc_module], key=operator.itemgetter(0))
                moved = grown and scc_module in joined
                forwarder_map[scc_module] = Forwarder(
                    internal_module,
                    tuple((internal_module, new_name, original_name) for original_name, new_name in sorted_mappings),
                    tuple(
                        (released_names[id(model)], model.class_name)
                        for model in module_models
                        if moved and model_key_of(model.reference) not in stand_ins
                    ),
                    released_internal_modules if moved else frozenset(),
                )
        result_modules[internal_module] = all_scc_models

    new_module_models: list[tuple[tuple[str, ...], list[DataModel]]] = [
        (internal_module, result_modules[internal_module])
        for internal_module in sorted(internal_modules_created)
        if internal_module in result_modules  # pragma: no branch
    ]
    new_module_models.extend((module, []) for module in sorted(released_forwarders))

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
    """Forget the models collapsed reuse removed, and the root models deduplication dropped, in an earlier parse."""
    parser._collapsed_reuse_models.clear()  # noqa: SLF001
    parser._collapsed_reuse_users.clear()  # noqa: SLF001
    parser._self_named_root_models.clear()  # noqa: SLF001


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
        parser, state.module_models, state.all_exports_scope
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
    ) = parser._build_module_structure(  # noqa: SLF001
        run.sorted_data_models, run.args.module_split_mode, run.args.all_exports_scope
    )


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
