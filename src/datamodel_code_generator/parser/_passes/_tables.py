"""The pipeline of ``Parser.parse()``: the tables of its post-parse passes, in execution order.

Each layer's passes live in a module of ``parser/_passes/`` (``settle``, ``structure``, ``plans``, ``modules``,
``cross_module``, ``decide``, ``render_prep``, ``imports``) as functions of the parser and the state they run on; the
helpers several layers call live in ``_shared``, the run state in ``_state``. The package itself imports none of them,
so that no pass module is in an import cycle with it. ``Parser`` runs ``PIPELINE`` from
``parse()``, and its hooks ``_build_module_structure``, ``_process_single_module`` and ``_finalize_modules`` run
``STRUCTURE_BODY``, ``MODULE_BODY`` and ``FINALIZE_SEGMENTS``. The tables take the parser through ``PipelineHost``,
which ``Parser`` satisfies structurally, and call each pass through its module (``late`` or a module attribute), so
that ``--debug`` traces the passes; no module of this package imports ``parser/base.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from typing_extensions import Protocol

from datamodel_code_generator._naming_hint import collecting_class_names
from datamodel_code_generator.model.base import _refresh_custom_template_paths
from datamodel_code_generator.parser import _pass_decls as decls
from datamodel_code_generator.parser._facts import Phase
from datamodel_code_generator.parser._passes import (
    cross_module,
    decide,
    imports,
    modules,
    plans,
    render_prep,
    settle,
    structure,
)
from datamodel_code_generator.parser._passes._shared import (
    InheritedFieldHost,
    _context_models,
    alias_shadowed_imports,
    override_required_field,
)
from datamodel_code_generator.parser._pipeline import (
    Boundary,
    DecisionPhase,
    ForEachModule,
    HookCall,
    PassSpec,
    Segment,
    StepLike,
    late,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from datamodel_code_generator.imports import Import, Imports
    from datamodel_code_generator.model.base import DataModel
    from datamodel_code_generator.parser._passes._state import (
        FinishRun,
        ModulePath,
        ProcessingRun,
        SettleRun,
        StructuredRun,
        _FinalizeState,
        _ModuleState,
        _PipelineRun,
        _PreparedModule,
        _StructureState,
    )

__all__ = [
    "FINALIZE_SEGMENTS",
    "MODULE_BODY",
    "MODULE_FORWARD_REFERENCE_ASSIGNMENT",
    "MODULE_RENDER_PREPARATION_BODY",
    "PIPELINE",
    "STRUCTURE_BODY",
    "PipelineHost",
    "RunContextView",
]


class RunContextView(Protocol):
    """What the tables read of the settings of a facade-managed run: ``parser/base.py:ParserRunContext``."""

    @property
    def preserve_circular_root_models(self) -> bool:
        """Whether collapse keeps every circular root model named (the retry after a collapse recursion)."""

    @property
    def untyped_nullable_maps(self) -> bool:
        """Whether the run parses the values of nullable maps untyped, as earlier releases did."""


class PipelineHost(
    settle.SettleHost[Any],
    settle.ParseHost,
    structure.StructureHost,
    modules.RuleAliasHost,
    plans.PlansHost,
    modules.ModulesHost,
    modules.ReuseHost,
    modules.CollapseHost,
    modules.DiscriminatorHost,
    modules.ModuleLoopHost[Any, Any, Any],
    cross_module.TaggedBasesHost,
    decide.DecideHost,
    render_prep.RenderPrepHost,
    imports.ImportsHost[Any],
    imports.FinishHost,
    imports.RenderHost[Any, Any, Any],
    InheritedFieldHost,
    Protocol,
):
    """What the tables read of the parser besides the hosts of the passes they call: options, run state and hooks."""

    @property
    def _unavailable_imports(self) -> dict[tuple[str | None, str], str]:
        """The error of each import the target Python version lacks, by imported module and name."""

    @property
    def order_constructor_fields_by_final_names(self) -> bool:
        """Whether constructor ordering runs once the fields have their final names."""

    @property
    def order_constructor_fields_by_written_defaults(self) -> bool:
        """Whether constructor ordering reads the defaults the class body writes."""

    @property
    def run_context(self) -> RunContextView:
        """The settings of the facade-managed run."""

    @property
    def typed_nullable_maps(self) -> bool:
        """Whether the parse typed the values of a nullable map."""

    @property
    def use_default_factory_for_optional_nested_models(self) -> bool:
        """Whether optional nested models take a default factory."""

    @property
    def use_root_model_sequence_interface(self) -> bool:
        """Whether root models of sequences render the sequence interface."""

    def _check_model_inheritance(self, contexts: Sequence[Any]) -> None:
        """Reject invalid builtin inheritance."""

    def _finalize_modules(
        self,
        contexts: list[Any],
        unused_models: list[DataModel],
        model_to_module_models: dict[DataModel, tuple[ModulePath, list[DataModel]]],
        module_to_import: dict[ModulePath, Imports],
    ) -> None:
        """Finish the render preparation of every module and collect the imports of each module."""

    def _finalize_structured_imports(self, contexts: list[Any]) -> None:
        """Resolve the aliases that the passes after the generic base class introduced."""

    def _merge_runtime_expression_imports(
        self, contexts: list[Any], model_imports: dict[DataModel, tuple[Import, ...]]
    ) -> None:
        """Merge the imports that runtime expressions registered into the imports of each module."""

    def _reject_unavailable_imports(self, contexts: Sequence[Any]) -> None:
        """Reject output that imports a type the target lacks."""

    def _set_nested_model_default_factory_metadata(self, contexts: list[Any]) -> None:
        """Publish the nested-factory decision of render preparation on the models of every module."""

    def _set_typed_extra_annotation_mode(self, *, use_deferred_annotations: bool) -> None:
        """Let the selected output configure its annotation representation."""

    def _uses_deferred_annotations(self, with_import: bool | None, disable_future_imports: bool) -> bool:  # noqa: FBT001
        """Return whether the modules defer the evaluation of their annotations."""


def _as_settle_run(run: _PipelineRun[Any, Any, Any]) -> SettleRun[Any]:
    return run


def _as_structured_run(run: _PipelineRun[Any, Any, Any]) -> StructuredRun[Any]:
    return run


def _as_modules_run(run: _PipelineRun[Any, Any, Any]) -> ProcessingRun[Any, Any, Any]:
    return run


def _as_finish_run(run: _PipelineRun[Any, Any, Any]) -> FinishRun[Any, Any, Any]:
    return run


def _same_state(state: _FinalizeState[Any]) -> _FinalizeState[Any]:
    return state


def _module_models(_parser: object, state: _ModuleState[Any]) -> list[DataModel]:
    return state.model_list


def _prepared_models(_parser: object, state: _PreparedModule[Any]) -> list[DataModel]:
    return state.model_list


def _all_models(_parser: object, state: _FinalizeState[Any]) -> list[DataModel]:
    return state.all_models


def _refresh_field_config(models: Iterable[DataModel]) -> None:
    for model in models:
        model.refresh_field_config()


STRUCTURE_BODY: tuple[StepLike[PipelineHost, _StructureState], ...] = (
    PassSpec(decls.GROUP_MODELS_BY_MODULE, late(structure.group_models_by_module)),
    PassSpec(decls.RESET_COLLAPSED_REUSE_STATE, late(structure.reset_collapsed_reuse_state)),
    PassSpec(decls.SNAPSHOT_UNION_TAGS, late(structure.snapshot_union_tags)),
    ForEachModule(
        "deduplicate_module_models",
        lambda _, s: (models for _, models in s.module_models),
        (
            PassSpec(
                decls.DELETE_DUPLICATE_MODELS,
                late(structure.delete_duplicate_models),
                checked_models=lambda _, models: models,
            ),
            PassSpec(
                decls.REPLACE_DUPLICATE_NAME_IN_MODULE,
                late(structure.replace_duplicate_name_in_module),
                checked_models=lambda _, models: models,
            ),
        ),
    ),
    PassSpec(
        decls.APPLY_FORCED_INVALID_DOTTED_STDOUT_REPAIR,
        late(structure.apply_forced_invalid_dotted_stdout_repair),
    ),
    PassSpec(decls.REUSE_MODEL_TREE_SCOPE, late(structure.apply_reuse_model_tree_scope)),
    PassSpec(decls.RESOLVE_CIRCULAR_IMPORTS, late(structure.resolve_module_circular_imports)),
)
"""The passes of ``_build_module_structure``."""


MODULE_BODY: tuple[StepLike[PipelineHost, _ModuleState[Any]], ...] = (
    PassSpec(
        decls.RECORD_DECIMAL_ALIAS_MODELS,
        late(modules.record_decimal_alias_models),
        when=lambda _, s: "Decimal" in s.all_module_fields,
        checked_models=_module_models,
    ),
    PassSpec(
        decls.ALIAS_SHADOWED_IMPORTS,
        lambda self, s: alias_shadowed_imports(self, s.model_list, s.all_module_fields),
        checked_models=_module_models,
    ),
    PassSpec(
        decls.OVERRIDE_REQUIRED_FIELD,
        lambda self, s: override_required_field(self, s.model_list),
        checked_models=_module_models,
    ),
    PassSpec(
        decls.REPLACE_UNIQUE_LIST_TO_SET,
        lambda self, s: modules.replace_unique_list_to_set(self, s.model_list),
        checked_models=_module_models,
    ),
    PassSpec(
        decls.CHANGE_FROM_IMPORT,
        late(modules.change_module_from_import),
        checked_models=_module_models,
    ),
    PassSpec(
        decls.EXTRACT_INHERITED_ENUM,
        late(modules.extract_module_inherited_enums),
        checked_models=_module_models,
    ),
    PassSpec(
        decls.MARK_UNUSED_MODELS_START,
        lambda _, s: modules.mark_unused_models_start(s),
        checked_models=_module_models,
    ),
    PassSpec(
        decls.SET_REFERENCE_DEFAULT_VALUE_TO_FIELD,
        lambda self, s: modules.set_reference_default_value_to_field(self, s.model_list),
        checked_models=_module_models,
    ),
    PassSpec(
        decls.REUSE_MODEL,
        lambda self, s: modules.reuse_model(self, s.model_list, s.require_update_action_models),
        checked_models=_module_models,
    ),
    PassSpec(
        decls.COLLAPSE_ROOT_MODELS,
        late(modules.collapse_module_root_models),
        checked_models=_module_models,
    ),
    PassSpec(
        decls.SET_DEFAULT_ENUM_MEMBER,
        lambda self, s: modules.set_default_enum_member(self, s.model_list),
        checked_models=_module_models,
    ),
    PassSpec(
        decls.SORT_MODELS,
        lambda self, s: modules.sort_models(
            self, s.model_list, s.imports, use_deferred_annotations=s.config.use_deferred_annotations
        ),
        checked_models=_module_models,
    ),
    PassSpec(
        decls.CHANGE_FIELD_NAME,
        lambda self, s: modules.change_field_name(self, s.model_list),
        checked_models=_module_models,
    ),
    PassSpec(
        decls.RESTORE_INHERITED_FIELD_NAMES_FOR_UNIQUE_ALIASES,
        lambda self, s: modules.restore_inherited_field_names_for_unique_aliases(self, s.model_list),
        checked_models=_module_models,
    ),
    PassSpec(
        decls.APPLY_DISCRIMINATOR_TYPE,
        lambda self, s: modules.apply_discriminator_type(self, s.model_list, s.imports),
        checked_models=_module_models,
    ),
    PassSpec(
        decls.TYPE_CONST_NARROWED_VALUES,
        lambda self, s: modules.type_const_narrowed_values(self, s.model_list, s.imports),
        checked_models=_module_models,
    ),
    PassSpec(
        decls.SET_ONE_LITERAL_ON_DEFAULT,
        lambda self, s: modules.set_one_literal_on_default(self, s.model_list),
        checked_models=_module_models,
    ),
    PassSpec(
        decls.REFRESH_FIELD_CONFIG,
        lambda _, s: _refresh_field_config(s.model_list),
        checked_models=_module_models,
    ),
    PassSpec(
        decls.RENAME_FIELDS_HIDING_TYPES,
        lambda self, s: modules.rename_fields_hiding_types(
            self,
            s.model_list,
            use_deferred_annotations=s.config.use_deferred_annotations,
        ),
        checked_models=_module_models,
    ),
    PassSpec(
        decls.REMOVE_OVERRIDDEN_MODELS,
        late(modules.remove_module_overridden_models),
        checked_models=_module_models,
    ),
    PassSpec(
        decls.APPLY_FIELD_TYPE_OVERRIDES,
        lambda self, s: modules.apply_field_type_overrides(self, s.model_list),
        checked_models=_module_models,
    ),
    PassSpec(
        decls.TYPE_DICT_KEYS_BY_ROOT_TYPES,
        late(modules.type_module_dict_keys_by_root_types),
        checked_models=_module_models,
    ),
    PassSpec(
        decls.FIX_CONSTRUCTOR_FIELD_ORDERING_EARLY,
        lambda self, s: render_prep.fix_constructor_field_ordering(
            self, s.model_list_before_override_removal, early=True
        ),
        when=lambda self, _: not self.order_constructor_fields_by_final_names,
        checked_models=lambda _, s: s.model_list_before_override_removal,
    ),
    PassSpec(
        decls.SNAPSHOT_MODULE_UNUSED_MODELS,
        lambda _, s: modules.snapshot_module_unused_models(s),
        checked_models=_module_models,
    ),
    PassSpec(
        decls.APPLY_BASE_CLASS_TYPE_OVERRIDES,
        lambda self, s: modules.apply_base_class_type_overrides(self, s.model_list),
        checked_models=_module_models,
    ),
)
"""The passes of ``_process_single_module``, for one output module."""


MODULE_FORWARD_REFERENCE_ASSIGNMENT: tuple[StepLike[PipelineHost, _PreparedModule[Any]], ...] = (
    PassSpec(
        decls.ASSIGN_FORWARD_REFERENCE_FIELDS,
        lambda self, s: render_prep.assign_module_forward_reference_fields(self, s.model_list),
        when=lambda self, s: self.use_annotated and s.config.use_deferred_annotations,
        checked_models=_prepared_models,
    ),
)
"""The fields of one output module that write by assignment, which every module decides before constructor ordering."""


def _models_before_override_removal(parser: PipelineHost, _: object) -> list[DataModel]:
    return [model for prepared in parser._prepared_modules for model in prepared.model_list_before_override_removal]  # noqa: SLF001


MODULE_RENDER_PREPARATION_BODY: tuple[StepLike[PipelineHost, _PreparedModule[Any]], ...] = (
    PassSpec(
        decls.UNBIND_HIDDEN_TYPE_FIELDS,
        render_prep.unbind_hidden_type_fields,
        checked_models=_prepared_models,
    ),
    PassSpec(
        decls.UPDATE_TYPE_ALIASES,
        late(render_prep.update_module_type_aliases),
        checked_models=_prepared_models,
    ),
    PassSpec(
        decls.COLLECT_LIVE_MODELS,
        lambda _, s: render_prep.collect_live_models(s),
        checked_models=_prepared_models,
    ),
    PassSpec(
        decls.SET_VALIDATE_DEFAULT_ON_FIELDS,
        lambda self, s: render_prep.set_validate_default_on_fields(self, s.live_models),
        checked_models=lambda _, s: s.live_models,
    ),
    PassSpec(
        decls.WARN_REQUIRED_ASSIGNMENTS_AFTER_DEFAULTS,
        lambda _, s: render_prep.warn_required_assignments_after_defaults(s.live_models),
        checked_models=lambda _, s: s.live_models,
    ),
)
"""The render preparation of one output module, after the constructor order of every module."""


FINALIZE_SEGMENTS: tuple[StepLike[PipelineHost, _FinalizeState[Any]], ...] = (
    Segment(
        "finalize_render_preparation",
        Phase.RENDER_PREPARATION,
        _same_state,
        (
            PassSpec(
                decls.FINALIZE_RENDERED_NULLABILITY,
                late(render_prep.finalize_rendered_nullability),
                checked_models=lambda _, s: _context_models(s.contexts),
            ),
            PassSpec(decls.COLLECT_ALL_MODELS, lambda _, s: render_prep.collect_all_models(s)),
            PassSpec(
                decls.PREPARE_TYPE_ALIAS_FIELDS,
                lambda self, s: render_prep.prepare_type_alias_fields(self, s.contexts, s.unused_models),
                checked_models=_all_models,
            ),
            PassSpec(
                decls.FINALIZE_TYPE_ALIAS_USERS,
                lambda self, s: self.data_model_type.finalize_type_alias_users(s.all_models),
                checked_models=_all_models,
            ),
            PassSpec(
                decls.MARK_SET_ITEM_MODELS_HASHABLE,
                lambda self, s: render_prep.mark_set_item_models_hashable(self, s.all_models),
                checked_models=_all_models,
            ),
            PassSpec(
                decls.FINALIZE_STRUCTURED_IMPORTS,
                lambda self, s: self._finalize_structured_imports(s.contexts),
                checked_models=_all_models,
            ),
            PassSpec(
                decls.FINALIZE_SEQUENCE_INTERFACE,
                lambda _, s: render_prep.finalize_sequence_interface(s),
                when=lambda self, _: self.use_root_model_sequence_interface,
                checked_models=_all_models,
            ),
            PassSpec(
                decls.DECIDE_NESTED_MODEL_DEFAULT_FACTORIES,
                late(render_prep.decide_nested_model_default_factories),
                when=lambda self, _: self.use_default_factory_for_optional_nested_models,
            ),
            PassSpec(
                decls.ORDER_CONSTRUCTOR_FIELDS_BY_DECIDED_NESTED_FACTORIES,
                late(render_prep.order_constructor_fields_by_decided_nested_factories),
                when=lambda self, _: (
                    self.use_default_factory_for_optional_nested_models
                    and self.order_constructor_fields_by_written_defaults
                ),
                checked_models=_all_models,
            ),
            PassSpec(decls.FREEZE_FIELD_RENDER_PLANS, lambda _, s: render_prep.freeze_field_render_plans(s)),
        ),
        enters_phase=False,
    ),
    Segment(
        "imports",
        Phase.IMPORTS,
        _same_state,
        (
            PassSpec(
                decls.SNAPSHOT_MODEL_IMPORTS,
                lambda _, s: imports.snapshot_model_imports(s),
                checked_models=_all_models,
            ),
            PassSpec(
                decls.APPEND_MODEL_IMPORTS,
                late(imports.append_model_imports),
                checked_models=_all_models,
            ),
            PassSpec(
                decls.REMOVE_UNUSED_MODELS,
                late(imports.remove_unused_models),
                checked_models=_all_models,
            ),
            PassSpec(
                decls.VALIDATE_EXPLICIT_FIELD_ALIASES,
                late(imports.validate_explicit_field_aliases),
                when=lambda self, _: bool(self.config.aliases),
            ),
            PassSpec(
                decls.PREPARE_SCHEMA_RUNTIME_VALIDATION_MODULE_CODE,
                lambda self, s: self._prepare_schema_runtime_validation_module_code(s.contexts),
                when=lambda self, _: self.generate_schema_validators,
                checked_models=_all_models,
            ),
            PassSpec(
                decls.SYNC_SCHEMA_RUNTIME_VALIDATION_MODULE_IMPORTS,
                lambda self, s: self._sync_schema_runtime_validation_module_imports(s.contexts, s.model_imports),
                when=lambda self, _: self.generate_schema_validators,
                checked_models=_all_models,
            ),
            PassSpec(
                decls.MERGE_RUNTIME_EXPRESSION_IMPORTS,
                lambda self, s: self._merge_runtime_expression_imports(s.contexts, s.model_imports),
                when=lambda self, _: self._has_runtime_expressions,
                checked_models=_all_models,
            ),
            PassSpec(
                decls.REMOVE_UNUSED_IMPORTS,
                late(imports.remove_unused_imports),
                checked_models=_all_models,
            ),
            PassSpec(
                decls.RESOLVE_MODULE_IMPORT_CONFLICTS,
                late(imports.resolve_module_import_conflicts),
                checked_models=_all_models,
            ),
            PassSpec(
                decls.BIND_MODULE_FIELD_NAMES,
                late(imports.bind_module_field_names),
                checked_models=_all_models,
            ),
            PassSpec(
                decls.CHANGE_IMPORTED_MODEL_NAME,
                late(imports.change_module_imported_model_names),
                checked_models=_all_models,
            ),
            PassSpec(
                decls.NAME_FINAL_CLASSES_OF_ADDITIONAL_PROPERTIES,
                lambda _, s: imports.name_final_classes_of_additional_properties(s),
                when=lambda _, s: s.collect_additional_properties_models(),
                checked_models=lambda _, s: s.additional_properties_models,
            ),
            PassSpec(
                decls.REFRESH_SEQUENCE_INTERFACE_AFTER_RENAMES,
                lambda _, s: imports.refresh_sequence_interface_after_renames(s),
                when=lambda self, s: self.use_root_model_sequence_interface and s.renamed_models,
                checked_models=_all_models,
            ),
            PassSpec(
                decls.REPLAN_SCHEMA_VALIDATORS_AFTER_RENAMES,
                late(imports.replan_schema_validators_after_renames),
                when=lambda self, s: self.generate_schema_validators and s.renamed_models,
                checked_models=_all_models,
            ),
            PassSpec(
                decls.DECIDE_CONFIG_ITEMS,
                lambda _, s: imports.decide_config_items(s),
                checked_models=_all_models,
            ),
            PassSpec(
                decls.REMAP_IMPORT_OVERRIDES,
                late(imports.remap_import_overrides),
                when=lambda self, _: self._import_overrides is not None,
            ),
            PassSpec(
                decls.REJECT_UNAVAILABLE_IMPORTS,
                lambda self, s: self._reject_unavailable_imports(s.contexts),
                when=lambda self, _: bool(self._unavailable_imports),
            ),
        ),
        enter=Boundary.RENDER_READY,
    ),
)
"""The segments of ``_finalize_modules``: the render preparation over every module, then B2 and imports."""


PIPELINE: tuple[StepLike[PipelineHost, _PipelineRun[Any, Any, Any]], ...] = (
    Segment(
        "parse",
        Phase.PARSE,
        _as_settle_run,
        (
            PassSpec(
                decls.REFRESH_CUSTOM_TEMPLATE_PATHS,
                lambda self, _: _refresh_custom_template_paths(cast("Path", self.custom_template_dir)),
                when=lambda self, _: self.custom_template_dir is not None,
            ),
            PassSpec(
                decls.SET_TYPED_EXTRA_ANNOTATION_MODE,
                lambda self, run: self._set_typed_extra_annotation_mode(
                    use_deferred_annotations=self._uses_deferred_annotations(
                        run.args.with_import, run.args.disable_future_imports
                    )
                ),
            ),
            PassSpec(decls.PARSE_RAW, late(settle.parse_raw)),
            PassSpec(decls.INDEX_CONST_NARROWED_VALUES, late(settle.index_const_narrowed_values)),
        ),
        enters_phase=False,
    ),
    Segment(
        "settle",
        Phase.SETTLE,
        _as_settle_run,
        (
            PassSpec(decls.SETTLE_YIELDING_NAMES, late(settle.settle_yielding_names)),
            PassSpec(decls.REPORT_PARSE_DIAGNOSTICS, late(settle.report_parse_diagnostics)),
            PassSpec(
                decls.WARN_ABOUT_UNMATCHED_MODEL_NAME_KEYS,
                lambda self, _: settle.warn_about_unmatched_model_name_keys(self),
                when=lambda _, run: not run.suppress_parse_warnings,
            ),
            PassSpec(decls.PREPARE_PARSE_CONFIG, late(settle.prepare_run_parse_config)),
            PassSpec(decls.SORT_DATA_MODELS, late(settle.sort_parsed_models)),
            PassSpec(
                decls.RECORD_SOURCE_REFERENCE_PATHS,
                lambda _, run: settle.record_source_reference_paths(run),
                when=lambda _, run: run.args.collect_model_metadata,
            ),
            PassSpec(
                decls.SORT_BASE_CLASSES_FOR_MRO,
                lambda self, run: settle.sort_base_classes_for_mro(run.sorted_data_models, self.generation_store),
            ),
        ),
        enter=Boundary.PARSED,
    ),
    Segment(
        "structure",
        Phase.STRUCTURE,
        _as_structured_run,
        (
            HookCall(
                "build_module_structure",
                late(structure.build_structure),
                STRUCTURE_BODY,
            ),
        ),
        scope_name="Parser._build_module_structure",
    ),
    Segment(
        "output_setup",
        Phase.STRUCTURE,
        _as_structured_run,
        (
            PassSpec(
                decls.CONFIGURE_CODE_FORMATTER,
                lambda self, run: structure.configure_code_formatter(
                    self, run, default_build_code_formatter=run.default_build_code_formatter
                ),
                when=lambda _, run: bool(run.args.format_),
            ),
            PassSpec(
                decls.DETECT_STANDARD_GENERATION_TEMPLATES,
                late(structure.detect_standard_generation_templates),
            ),
        ),
        enters_phase=False,
    ),
    Segment(
        "plan",
        Phase.PLAN,
        _as_structured_run,
        (
            PassSpec(decls.RESET_PER_RUN_STATE, late(plans.reset_per_run_state)),
            PassSpec(
                decls.FLATTEN_CONFLICTING_BASE_LAYOUTS,
                lambda self, run: plans.flatten_conflicting_base_layouts(self, run.module_models),
            ),
            PassSpec(
                decls.UNCONSTRAIN_UNION_MEMBERS,
                late(plans.unconstrain_union_members_in_modules),
                when=lambda self, _: self.data_model_type.REQUIRES_TAGGED_UNION_DISCRIMINATOR,
            ),
            PassSpec(
                decls.SET_CIRCULAR_ROOT_MODEL_PATHS,
                lambda self, run: plans.set_circular_root_model_paths(
                    self,
                    run.module_models,
                    require_dict_key_edge=not self.run_context.preserve_circular_root_models,
                ),
                when=lambda self, _: self.collapse_root_models,
            ),
            PassSpec(decls.RECORD_EMITTING_MODULES, late(plans.record_emitting_modules)),
        ),
        scope_name="Parser.parse",
    ),
    Segment(
        "modules",
        Phase.MODULES,
        _as_modules_run,
        (
            HookCall(
                "process_single_module",
                late(modules.process_each_module),
                MODULE_BODY,
                per_module=True,
            ),
            PassSpec(
                decls.WRITE_RULE_ALIASES_AS_CLASSES,
                late(modules.write_rule_aliases_as_classes),
                when=lambda self, _: (
                    self.generate_schema_validators
                    and self.data_model_type.SCHEMA_RUNTIME_VALIDATION_ROOT_MODEL is not None
                ),
            ),
            PassSpec(
                decls.NAME_INNER_MODELS_IN_RULES,
                late(modules.name_inner_models_in_rules),
                when=lambda self, _: self.generate_schema_validators and bool(self._replaced_root_models),
            ),
            PassSpec(
                decls.APPLY_BASE_STAND_IN_IMPORTS,
                late(modules.apply_base_stand_in_imports),
                when=lambda self, _: bool(self._base_stand_in_imports),
            ),
            PassSpec(
                decls.SETTLE_DISCRIMINATED_UNIONS,
                late(modules.settle_discriminated_unions),
                when=lambda self, _: self.data_model_field_type.SUPPORTS_DISCRIMINATOR,
                checked_models=lambda _, run: _context_models(run.contexts),
            ),
        ),
    ),
    Segment(
        "cross_module",
        Phase.CROSS_MODULE,
        _as_finish_run,
        (
            PassSpec(
                decls.TAG_FIELDS_OF_TAGGED_BASES,
                cross_module.tag_fields_of_tagged_bases,
                when=lambda self, _: self.data_model_type.REQUIRES_TAGGED_UNION_DISCRIMINATOR,
                checked_models=lambda _, run: _context_models(run.contexts),
            ),
            PassSpec(
                decls.WRITE_ENUM_MEMBER_TAGS_AS_VALUES,
                cross_module.write_enum_member_tags_as_values,
                when=lambda self, _: not self.data_model_type._LITERAL_HOLDS_ENUM_MEMBERS,  # noqa: SLF001
                checked_models=lambda _, run: _context_models(run.contexts),
            ),
            PassSpec(
                decls.WARN_ENUMS_ADMITTING_OTHER_VALUES,
                lambda self, _: cross_module.warn_enums_admitting_other_values(self),
            ),
            PassSpec(
                decls.IMPORT_REMOVED_REUSE_CANONICALS,
                lambda self, run: cross_module.import_removed_reuse_canonicals(
                    self,
                    run.contexts,
                    internal_modules=run.internal_modules,
                    model_path_to_module_name=run.model_path_to_module_name,
                ),
            ),
            PassSpec(
                decls.IMPORT_RENAMED_TYPES,
                lambda self, run: cross_module.import_renamed_types(
                    self,
                    run.contexts,
                    internal_modules=run.internal_modules,
                    model_path_to_module_name=run.model_path_to_module_name,
                ),
            ),
            PassSpec(
                decls.IMPORT_ENUM_MEMBER_LITERAL_CLASSES,
                lambda self, run: cross_module.import_enum_member_literal_classes(
                    self,
                    run.contexts,
                    internal_modules=run.internal_modules,
                    model_path_to_module_name=run.model_path_to_module_name,
                ),
            ),
        ),
        scope_name="Parser.parse",
    ),
    Segment(
        "generic_base_class",
        Phase.CROSS_MODULE,
        _as_finish_run,
        (
            PassSpec(
                decls.APPLY_GENERIC_BASE_CLASS,
                lambda self, run: cross_module.apply_generic_base_class(self, run.contexts),
            ),
        ),
        enters_phase=False,
        scope_name="Parser.parse",
    ),
    Segment(
        "decide",
        Phase.DECIDE,
        _as_finish_run,
        (DecisionPhase("decision_passes", lambda runner, self, run: decide.decide(self, runner, run)),),
        enter=Boundary.FACTS_FINAL,
        scope_name="Parser.parse",
    ),
    Segment(
        "render_preparation",
        Phase.RENDER_PREPARATION,
        _as_finish_run,
        (
            ForEachModule(
                "assign_forward_reference_fields_of_modules",
                lambda self, _: self._prepared_modules,
                MODULE_FORWARD_REFERENCE_ASSIGNMENT,
            ),
            PassSpec(
                decls.FIX_CONSTRUCTOR_FIELD_ORDERING,
                lambda self, run: render_prep.fix_constructor_field_ordering_bases_first(
                    self,
                    _models_before_override_removal(self, run),
                    render_prep.InheritedFieldCopies(run.contexts, run.internal_modules, run.model_path_to_module_name),
                ),
                checked_models=_models_before_override_removal,
            ),
            ForEachModule(
                "prepare_module_rendering",
                lambda self, _: self._prepared_modules,
                MODULE_RENDER_PREPARATION_BODY,
            ),
            PassSpec(
                decls.FIX_TAG_OPT_OUT_FIELD_ORDERING,
                lambda _, run: render_prep.fix_tag_opt_out_field_ordering(run.opted_out, _context_models(run.contexts)),
                when=lambda self, _: self.data_model_type.REQUIRES_TAGGED_UNION_DISCRIMINATOR,
                checked_models=lambda _, run: _context_models(run.contexts),
            ),
            HookCall(
                "finalize_modules",
                lambda self, run: self._finalize_modules(
                    run.contexts, run.unused_models, run.model_to_module_models, run.module_to_import
                ),
                FINALIZE_SEGMENTS,
            ),
        ),
        enter=Boundary.DECIDED,
        scope_name="Parser.parse",
    ),
    Segment(
        "finish",
        Phase.IMPORTS,
        _as_finish_run,
        (
            PassSpec(
                decls.RECORD_GENERATED_CLASS_NAMES,
                late(imports.record_generated_class_names),
                when=lambda _, __: collecting_class_names(),
            ),
            PassSpec(
                decls.RECORD_MODEL_PLACEMENT,
                lambda self, run: imports.record_model_placement(self, run.contexts, bool(run.internal_modules)),
                when=lambda self, _: self.typed_nullable_maps or self.run_context.untyped_nullable_maps,
            ),
            PassSpec(
                decls.CHECK_MODEL_INHERITANCE,
                lambda self, run: self._check_model_inheritance(run.contexts),
            ),
            PassSpec(
                decls.REJECT_ALIAS_CYCLES_WITHOUT_SCHEMA,
                lambda self, run: imports.reject_alias_cycles_without_schema(self, run.contexts),
                when=lambda self, _: (
                    self.data_model_type._READ_BY_PYDANTIC or self.data_model_type._READ_BY_MSGSPEC  # noqa: SLF001
                ),
            ),
            PassSpec(
                decls.WARN_ABOUT_DROPPED_CONSTRAINTS,
                lambda self, run: imports.warn_about_dropped_constraints(self, run.contexts),
            ),
            PassSpec(decls.WARN_ABOUT_DECIMAL_DEFAULTS, lambda self, _: imports.warn_about_decimal_defaults(self)),
            PassSpec(
                decls.WARN_ABOUT_VARIANT_DISCRIMINATOR_TAGS,
                lambda self, _: imports.warn_about_variant_discriminator_tags(self),
            ),
            PassSpec(
                decls.WARN_ABOUT_MSGSPEC_DECODING,
                lambda self, run: imports.warn_about_msgspec_decoding(self, run.contexts),
            ),
            PassSpec(
                decls.SET_NESTED_MODEL_DEFAULT_FACTORY_METADATA,
                lambda self, run: self._set_nested_model_default_factory_metadata(run.contexts),
                when=lambda self, _: self.use_default_factory_for_optional_nested_models,
            ),
            PassSpec(decls.RECORD_PYDANTIC_FLOOR, lambda self, run: imports.record_pydantic_floor(self, run.contexts)),
            PassSpec(decls.ADD_ROOT_INIT_RESULT, lambda _, run: imports.add_root_init_result(run)),
        ),
        enters_phase=False,
        scope_name="Parser.parse",
    ),
    Segment(
        "render",
        Phase.RENDER,
        _as_finish_run,
        (PassSpec(decls.RENDER_MODULES, late(imports.render)),),
        scope_name="Parser.__render_modules",
    ),
)
"""The passes of ``parse()``, in execution order."""
