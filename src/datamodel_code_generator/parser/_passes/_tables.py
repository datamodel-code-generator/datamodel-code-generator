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
    DecisionPhase,
    ForEachModule,
    HookCall,
    PassSpec,
    Segment,
    StepLike,
    late,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Sequence
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

    def _finalize_modules(  # noqa: PLR0913, PLR0917
        self,
        contexts: list[Any],
        unused_models: list[DataModel],
        model_to_module_models: dict[DataModel, tuple[ModulePath, list[DataModel]]],
        module_to_import: dict[ModulePath, Imports],
        forwarder_modules: Collection[ModulePath],
        with_import: bool,  # noqa: FBT001
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


def _refresh_field_config(models: Iterable[DataModel]) -> None:
    for model in models:
        model.refresh_field_config()


STRUCTURE_BODY: tuple[StepLike[PipelineHost, _StructureState], ...] = (
    PassSpec("group_models_by_module", late(structure.group_models_by_module)),
    PassSpec("reset_collapsed_reuse_state", late(structure.reset_collapsed_reuse_state)),
    PassSpec("snapshot_union_tags", late(structure.snapshot_union_tags)),
    ForEachModule(
        "deduplicate_module_models",
        lambda _, s: (models for _, models in s.module_models),
        (
            PassSpec("delete_duplicate_models", late(structure.delete_duplicate_models)),
            PassSpec("replace_duplicate_name_in_module", late(structure.replace_duplicate_name_in_module)),
        ),
    ),
    PassSpec("apply_forced_invalid_dotted_stdout_repair", late(structure.apply_forced_invalid_dotted_stdout_repair)),
    PassSpec("reuse_model_tree_scope", late(structure.apply_reuse_model_tree_scope)),
    PassSpec("resolve_circular_imports", late(structure.resolve_module_circular_imports)),
)
"""The passes of ``_build_module_structure``."""


MODULE_BODY: tuple[StepLike[PipelineHost, _ModuleState[Any]], ...] = (
    PassSpec(
        "record_decimal_alias_models",
        late(modules.record_decimal_alias_models),
        when=lambda _, s: "Decimal" in s.all_module_fields,
    ),
    PassSpec("alias_shadowed_imports", lambda self, s: alias_shadowed_imports(self, s.model_list, s.all_module_fields)),
    PassSpec("override_required_field", lambda self, s: override_required_field(self, s.model_list)),
    PassSpec("replace_unique_list_to_set", lambda self, s: modules.replace_unique_list_to_set(self, s.model_list)),
    PassSpec("change_from_import", late(modules.change_module_from_import)),
    PassSpec("extract_inherited_enum", late(modules.extract_module_inherited_enums)),
    PassSpec("mark_unused_models_start", lambda _, s: modules.mark_unused_models_start(s)),
    PassSpec(
        "set_reference_default_value_to_field",
        lambda self, s: modules.set_reference_default_value_to_field(self, s.model_list),
    ),
    PassSpec("reuse_model", lambda self, s: modules.reuse_model(self, s.model_list, s.require_update_action_models)),
    PassSpec("collapse_root_models", late(modules.collapse_module_root_models)),
    PassSpec("set_default_enum_member", lambda self, s: modules.set_default_enum_member(self, s.model_list)),
    PassSpec(
        "sort_models",
        lambda self, s: modules.sort_models(
            self, s.model_list, s.imports, use_deferred_annotations=s.config.use_deferred_annotations
        ),
    ),
    PassSpec("change_field_name", lambda self, s: modules.change_field_name(self, s.model_list)),
    PassSpec(
        "restore_inherited_field_names_for_unique_aliases",
        lambda self, s: modules.restore_inherited_field_names_for_unique_aliases(self, s.model_list),
    ),
    PassSpec(
        "apply_discriminator_type", lambda self, s: modules.apply_discriminator_type(self, s.model_list, s.imports)
    ),
    PassSpec(
        "type_const_narrowed_values", lambda self, s: modules.type_const_narrowed_values(self, s.model_list, s.imports)
    ),
    PassSpec("set_one_literal_on_default", lambda self, s: modules.set_one_literal_on_default(self, s.model_list)),
    PassSpec("refresh_field_config", lambda _, s: _refresh_field_config(s.model_list)),
    PassSpec(
        "rename_fields_hiding_types",
        lambda self, s: modules.rename_fields_hiding_types(
            self,
            s.model_list,
            use_deferred_annotations=s.config.use_deferred_annotations,
        ),
    ),
    PassSpec("remove_overridden_models", late(modules.remove_module_overridden_models)),
    PassSpec("apply_field_type_overrides", lambda self, s: modules.apply_field_type_overrides(self, s.model_list)),
    PassSpec("type_dict_keys_by_root_types", late(modules.type_module_dict_keys_by_root_types)),
    PassSpec(
        "fix_constructor_field_ordering_early",
        lambda self, s: render_prep.fix_constructor_field_ordering(
            self, s.model_list_before_override_removal, early=True
        ),
        when=lambda self, _: not self.order_constructor_fields_by_final_names,
    ),
    PassSpec("snapshot_module_unused_models", lambda _, s: modules.snapshot_module_unused_models(s)),
    PassSpec(
        "apply_base_class_type_overrides", lambda self, s: modules.apply_base_class_type_overrides(self, s.model_list)
    ),
)
"""The passes of ``_process_single_module``, for one output module."""


MODULE_FORWARD_REFERENCE_ASSIGNMENT: tuple[StepLike[PipelineHost, _PreparedModule[Any]], ...] = (
    PassSpec(
        "assign_forward_reference_fields",
        lambda self, s: render_prep.assign_module_forward_reference_fields(self, s.model_list),
        when=lambda self, s: self.use_annotated and s.config.use_deferred_annotations,
    ),
)
"""The fields of one output module that write by assignment, which every module decides before constructor ordering."""


def _models_before_override_removal(parser: PipelineHost, _: object) -> list[DataModel]:
    return [model for prepared in parser._prepared_modules for model in prepared.model_list_before_override_removal]  # noqa: SLF001


MODULE_RENDER_PREPARATION_BODY: tuple[StepLike[PipelineHost, _PreparedModule[Any]], ...] = (
    PassSpec("unbind_hidden_type_fields", render_prep.unbind_hidden_type_fields),
    PassSpec("update_type_aliases", late(render_prep.update_module_type_aliases)),
    PassSpec("collect_live_models", lambda _, s: render_prep.collect_live_models(s)),
    PassSpec(
        "set_validate_default_on_fields",
        lambda self, s: render_prep.set_validate_default_on_fields(self, s.live_models),
    ),
    PassSpec(
        "warn_required_assignments_after_defaults",
        lambda _, s: render_prep.warn_required_assignments_after_defaults(s.live_models),
    ),
)
"""The render preparation of one output module, after the constructor order of every module."""


FINALIZE_SEGMENTS: tuple[StepLike[PipelineHost, _FinalizeState[Any]], ...] = (
    Segment(
        "finalize_render_preparation",
        Phase.RENDER_PREPARATION,
        _same_state,
        (
            PassSpec("finalize_rendered_nullability", late(render_prep.finalize_rendered_nullability)),
            PassSpec("collect_all_models", lambda _, s: render_prep.collect_all_models(s)),
            PassSpec(
                "prepare_type_alias_fields",
                lambda self, s: render_prep.prepare_type_alias_fields(self, s.contexts, s.unused_models),
            ),
            PassSpec(
                "finalize_type_alias_users",
                lambda self, s: self.data_model_type.finalize_type_alias_users(s.all_models),
            ),
            PassSpec(
                "mark_set_item_models_hashable",
                lambda self, s: render_prep.mark_set_item_models_hashable(self, s.all_models),
            ),
            PassSpec("finalize_structured_imports", lambda self, s: self._finalize_structured_imports(s.contexts)),
            PassSpec(
                "finalize_sequence_interface",
                lambda _, s: render_prep.finalize_sequence_interface(s),
                when=lambda self, _: self.use_root_model_sequence_interface,
            ),
            PassSpec(
                "decide_nested_model_default_factories",
                late(render_prep.decide_nested_model_default_factories),
                when=lambda self, _: self.use_default_factory_for_optional_nested_models,
            ),
            PassSpec(
                "order_constructor_fields_by_decided_nested_factories",
                late(render_prep.order_constructor_fields_by_decided_nested_factories),
                when=lambda self, _: (
                    self.use_default_factory_for_optional_nested_models
                    and self.order_constructor_fields_by_written_defaults
                ),
            ),
            PassSpec("freeze_field_render_plans", lambda _, s: render_prep.freeze_field_render_plans(s)),
        ),
        enters_phase=False,
    ),
    Segment(
        "imports",
        Phase.IMPORTS,
        _same_state,
        (
            PassSpec("snapshot_model_imports", lambda _, s: imports.snapshot_model_imports(s)),
            PassSpec("append_model_imports", late(imports.append_model_imports)),
            PassSpec("remove_unused_models", late(imports.remove_unused_models)),
            PassSpec(
                "validate_explicit_field_aliases",
                late(imports.validate_explicit_field_aliases),
                when=lambda self, _: bool(self.config.aliases),
            ),
            PassSpec(
                "prepare_schema_runtime_validation_module_code",
                lambda self, s: self._prepare_schema_runtime_validation_module_code(s.contexts),
                when=lambda self, _: self.generate_schema_validators,
            ),
            PassSpec(
                "sync_schema_runtime_validation_module_imports",
                lambda self, s: self._sync_schema_runtime_validation_module_imports(s.contexts, s.model_imports),
                when=lambda self, _: self.generate_schema_validators,
            ),
            PassSpec(
                "merge_runtime_expression_imports",
                lambda self, s: self._merge_runtime_expression_imports(s.contexts, s.model_imports),
                when=lambda self, _: self._has_runtime_expressions,
            ),
            PassSpec("remove_unused_imports", late(imports.remove_unused_imports)),
            PassSpec("resolve_module_import_conflicts", late(imports.resolve_module_import_conflicts)),
            PassSpec("alias_builtin_shadowing_module_imports", late(imports.alias_builtin_shadowing_module_imports)),
            PassSpec(
                "alias_builtins_hidden_by_submodules",
                late(imports.alias_builtins_hidden_by_submodules),
                when=lambda _, state: state.with_import,
            ),
            PassSpec("bind_module_field_names", late(imports.bind_module_field_names)),
            PassSpec("change_imported_model_name", late(imports.change_module_imported_model_names)),
            PassSpec(
                "name_final_classes_of_additional_properties",
                lambda _, s: imports.name_final_classes_of_additional_properties(s),
                when=lambda _, s: s.collect_additional_properties_models(),
            ),
            PassSpec(
                "refresh_sequence_interface_after_renames",
                lambda _, s: imports.refresh_sequence_interface_after_renames(s),
                when=lambda self, s: self.use_root_model_sequence_interface and s.renamed_models,
            ),
            PassSpec(
                "replan_schema_validators_after_renames",
                late(imports.replan_schema_validators_after_renames),
                when=lambda self, s: self.generate_schema_validators and s.renamed_models,
            ),
            PassSpec("decide_config_items", lambda _, s: imports.decide_config_items(s)),
            PassSpec(
                "remap_import_overrides",
                late(imports.remap_import_overrides),
                when=lambda self, _: self._import_overrides is not None,
            ),
            PassSpec(
                "reject_unavailable_imports",
                lambda self, s: self._reject_unavailable_imports(s.contexts),
                when=lambda self, _: bool(self._unavailable_imports),
            ),
        ),
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
                "refresh_custom_template_paths",
                lambda self, _: _refresh_custom_template_paths(cast("Path", self.custom_template_dir)),
                when=lambda self, _: self.custom_template_dir is not None,
            ),
            PassSpec(
                "set_typed_extra_annotation_mode",
                lambda self, run: self._set_typed_extra_annotation_mode(
                    use_deferred_annotations=self._uses_deferred_annotations(
                        run.args.with_import, run.args.disable_future_imports
                    )
                ),
            ),
            PassSpec("parse_raw", late(settle.parse_raw)),
            PassSpec("index_const_narrowed_values", late(settle.index_const_narrowed_values)),
        ),
        enters_phase=False,
    ),
    Segment(
        "settle",
        Phase.SETTLE,
        _as_settle_run,
        (
            PassSpec("settle_yielding_names", late(settle.settle_yielding_names)),
            PassSpec("report_parse_diagnostics", late(settle.report_parse_diagnostics)),
            PassSpec(
                "warn_about_unmatched_model_name_keys",
                lambda self, _: settle.warn_about_unmatched_model_name_keys(self),
                when=lambda _, run: not run.suppress_parse_warnings,
            ),
            PassSpec("prepare_parse_config", late(settle.prepare_run_parse_config)),
            PassSpec("sort_data_models", late(settle.sort_parsed_models)),
            PassSpec(
                "record_source_reference_paths",
                lambda _, run: settle.record_source_reference_paths(run),
                when=lambda _, run: run.args.collect_model_metadata,
            ),
            PassSpec(
                "sort_base_classes_for_mro",
                lambda self, run: settle.sort_base_classes_for_mro(run.sorted_data_models, self.generation_store),
            ),
        ),
    ),
    Segment(
        "structure",
        Phase.STRUCTURE,
        _as_structured_run,
        (
            HookCall(
                "build_module_structure",
                late(structure.build_structure),
            ),
        ),
    ),
    Segment(
        "output_setup",
        Phase.STRUCTURE,
        _as_structured_run,
        (
            PassSpec(
                "configure_code_formatter",
                lambda self, run: structure.configure_code_formatter(
                    self, run, default_build_code_formatter=run.default_build_code_formatter
                ),
                when=lambda _, run: bool(run.args.format_),
            ),
            PassSpec("detect_standard_generation_templates", late(structure.detect_standard_generation_templates)),
        ),
        enters_phase=False,
    ),
    Segment(
        "plan",
        Phase.PLAN,
        _as_structured_run,
        (
            PassSpec("reset_per_run_state", late(plans.reset_per_run_state)),
            PassSpec(
                "flatten_conflicting_base_layouts",
                lambda self, run: plans.flatten_conflicting_base_layouts(self, run.module_models),
            ),
            PassSpec(
                "unconstrain_union_members",
                late(plans.unconstrain_union_members_in_modules),
                when=lambda self, _: self._tag_capabilities.struct_tags,
            ),
            PassSpec(
                "set_circular_root_model_paths",
                lambda self, run: plans.set_circular_root_model_paths(
                    self,
                    run.module_models,
                    require_dict_key_edge=not self.run_context.preserve_circular_root_models,
                ),
                when=lambda self, _: self.collapse_root_models,
            ),
            PassSpec("record_emitting_modules", late(plans.record_emitting_modules)),
        ),
    ),
    Segment(
        "modules",
        Phase.MODULES,
        _as_modules_run,
        (
            HookCall(
                "process_single_module",
                late(modules.process_each_module),
                per_module=True,
            ),
            PassSpec(
                "settle_kept_parent_roots",
                late(modules.settle_kept_parent_roots),
                when=lambda self, _: bool(self._kept_parent_roots),
            ),
            PassSpec(
                "write_rule_aliases_as_classes",
                late(modules.write_rule_aliases_as_classes),
                when=lambda self, _: (
                    self.generate_schema_validators
                    and self.data_model_type.SCHEMA_RUNTIME_VALIDATION_ROOT_MODEL is not None
                ),
            ),
            PassSpec(
                "name_inner_models_in_rules",
                late(modules.name_inner_models_in_rules),
                when=lambda self, _: self.generate_schema_validators and bool(self._replaced_root_models),
            ),
            PassSpec(
                "apply_base_stand_in_imports",
                late(modules.apply_base_stand_in_imports),
                when=lambda self, _: bool(self._base_stand_in_imports),
            ),
            PassSpec(
                "settle_discriminated_unions",
                late(modules.settle_discriminated_unions),
                when=lambda self, _: self._tag_capabilities.writes_argument,
            ),
        ),
    ),
    Segment(
        "cross_module",
        Phase.CROSS_MODULE,
        _as_finish_run,
        (
            PassSpec(
                "tag_fields_of_tagged_bases",
                cross_module.tag_fields_of_tagged_bases,
                when=lambda self, _: self._tag_capabilities.struct_tags,
            ),
            PassSpec(
                "write_enum_member_tags_as_values",
                cross_module.write_enum_member_tags_as_values,
                when=lambda self, _: not self._tag_capabilities.literal_holds_members,
            ),
            PassSpec(
                "warn_enums_admitting_other_values",
                lambda self, _: cross_module.warn_enums_admitting_other_values(self),
            ),
            PassSpec(
                "import_removed_reuse_canonicals",
                lambda self, run: cross_module.import_removed_reuse_canonicals(
                    self,
                    run.contexts,
                    internal_modules=run.internal_modules,
                    model_path_to_module_name=run.model_path_to_module_name,
                ),
            ),
            PassSpec(
                "import_renamed_types",
                lambda self, run: cross_module.import_renamed_types(
                    self,
                    run.contexts,
                    internal_modules=run.internal_modules,
                    model_path_to_module_name=run.model_path_to_module_name,
                ),
            ),
            PassSpec(
                "import_enum_member_literal_classes",
                lambda self, run: cross_module.import_enum_member_literal_classes(
                    self,
                    run.contexts,
                    internal_modules=run.internal_modules,
                    model_path_to_module_name=run.model_path_to_module_name,
                ),
            ),
        ),
    ),
    Segment(
        "generic_base_class",
        Phase.CROSS_MODULE,
        _as_finish_run,
        (
            PassSpec(
                "apply_generic_base_class", lambda self, run: cross_module.apply_generic_base_class(self, run.contexts)
            ),
        ),
        enters_phase=False,
    ),
    Segment(
        "decide",
        Phase.DECIDE,
        _as_finish_run,
        (DecisionPhase("decision_passes", lambda runner, self, run: decide.decide(self, runner, run)),),
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
                "fix_constructor_field_ordering",
                lambda self, run: render_prep.fix_constructor_field_ordering_bases_first(
                    self,
                    _models_before_override_removal(self, run),
                    render_prep.InheritedFieldCopies(run.contexts, run.internal_modules, run.model_path_to_module_name),
                ),
            ),
            ForEachModule(
                "prepare_module_rendering",
                lambda self, _: self._prepared_modules,
                MODULE_RENDER_PREPARATION_BODY,
            ),
            PassSpec(
                "fix_tag_opt_out_field_ordering",
                lambda self, run: render_prep.fix_tag_opt_out_field_ordering(
                    self._tag_plan.opted_out, _context_models(run.contexts)
                ),
                when=lambda self, _: self._tag_capabilities.struct_tags,
            ),
            HookCall(
                "finalize_modules",
                lambda self, run: self._finalize_modules(
                    run.contexts,
                    run.unused_models,
                    run.model_to_module_models,
                    run.module_to_import,
                    run.forwarder_map,
                    bool(run.args.with_import),
                ),
            ),
        ),
    ),
    Segment(
        "finish",
        Phase.IMPORTS,
        _as_finish_run,
        (
            PassSpec(
                "record_generated_class_names",
                late(imports.record_generated_class_names),
                when=lambda _, __: collecting_class_names(),
            ),
            PassSpec(
                "record_model_placement",
                lambda self, run: imports.record_model_placement(self, run.contexts, bool(run.internal_modules)),
                when=lambda self, _: self.typed_nullable_maps or self.run_context.untyped_nullable_maps,
            ),
            PassSpec("check_model_inheritance", lambda self, run: self._check_model_inheritance(run.contexts)),
            PassSpec(
                "reject_alias_cycles_without_schema",
                lambda self, run: imports.reject_alias_cycles_without_schema(
                    self, run.contexts, evaluates_annotations=not run.config.use_deferred_annotations
                ),
                when=lambda self, _: (
                    imports.alias_loop_failure(self.data_model_type) is not None or bool(self._self_named_root_models)
                ),
            ),
            PassSpec(
                "warn_about_dropped_constraints",
                lambda self, run: imports.warn_about_dropped_constraints(self, run.contexts),
            ),
            PassSpec("warn_about_decimal_defaults", lambda self, _: imports.warn_about_decimal_defaults(self)),
            PassSpec(
                "warn_about_variant_discriminator_tags",
                lambda self, _: imports.warn_about_variant_discriminator_tags(self),
            ),
            PassSpec(
                "warn_about_msgspec_decoding", lambda self, run: imports.warn_about_msgspec_decoding(self, run.contexts)
            ),
            PassSpec(
                "set_nested_model_default_factory_metadata",
                lambda self, run: self._set_nested_model_default_factory_metadata(run.contexts),
                when=lambda self, _: self.use_default_factory_for_optional_nested_models,
            ),
            PassSpec("record_pydantic_floor", lambda self, run: imports.record_pydantic_floor(self, run.contexts)),
            PassSpec("add_root_init_result", lambda _, run: imports.add_root_init_result(run)),
        ),
        enters_phase=False,
    ),
    Segment(
        "render",
        Phase.RENDER,
        _as_finish_run,
        (PassSpec("render_modules", late(imports.render)),),
    ),
)
"""The passes of ``parse()``, in execution order."""
