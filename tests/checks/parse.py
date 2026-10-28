"""The scopes of one parse that the checks run in: the parse itself, its segments, passes, decisions and modules.

The run of the whole pipeline (``PIPELINE``) of a parse reads the modes of its checks and, when ``STORE_WRITES`` or
``FACT_FINALITY`` is on, opens the checks of ``finality.py`` (``PARSE_CHECKS``). When the pipeline enters ``PARSED``,
the checks of L1 run and those of ``finality.py`` arm; when the modules are final (``FINALIZE_SEGMENTS``), the model
graph check compares the models they write with the emitted ones. Every pass and decision runs inside the finality
scope of its declaration, every output module inside its module checkpoint, and every segment with a scope name inside
that pass scope, as the pass trace (``trace.py``) records them. ``Parser.parse`` itself stays unwrapped: it remembers
the frame that called it, which warnings name.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import replace
from functools import wraps
from typing import TYPE_CHECKING, Any, Final

from scripts.pass_decls import BOUNDARY_BEFORE, DECISION_DECLS, HOOK_BODIES, PASS_DECLS, SCOPE_NAMES, Boundary
from tests.checks.areas import MODES, Area, read_modes
from tests.checks.finality import (
    NO_SCOPE,
    PARSE_CHECKS,
    GuardedView,
    _Finality,
    guarded_facts,
    parse_checks,
)
from tests.checks.model_graph import check_emitted_models, check_model_owners
from tests.checks.schema_graph import check_shared_schemas
from tests.checks.schema_locations import check_schema_locations
from tests.checks.trace import MODULE_TRACE_MARK, TRACE

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from contextlib import AbstractContextManager

_IN_MODULE: Final[ContextVar[bool]] = ContextVar("in_module", default=False)
"""Whether the parse that runs processes an output module now: an override that calls its base processes it once."""
_SEGMENT_SCOPES: Final[dict[int, tuple[str, Any]]] = {}
"""The scope name (``SCOPE_NAMES``) and phase of each segment with a scope name, by the id of its steps."""
_PARSED_STEPS: Final[set[int]] = set()
"""The ids of the steps of the segments that enter ``PARSED`` (``BOUNDARY_BEFORE``)."""
_DECLS: Final = {decl.name: decl for decl in PASS_DECLS}
"""The declaration of each table pass, by the name of its ``PassSpec``."""
_HOOKS: Final = ("_process_single_module",)
"""The parser methods wrapped on the class that defines them in the running parser's MRO (``seams.py`` rule 2)."""


def viewed(value: Any) -> Any:
    """Return the object a guarded view stands for, or ``value`` itself when it is no view."""
    return value._object if isinstance(value, GuardedView) else value


def _traced(name: str) -> None:
    """Append ``name`` to the pass trace, when one is recorded."""
    if (trace := TRACE.get()) is not None:
        trace.append(name)


def _segment_scopes(steps: Iterable[Any]) -> None:
    from datamodel_code_generator.parser._passes import _tables
    from datamodel_code_generator.parser._pipeline import ForEachModule, HookCall, Segment

    for step in steps:
        if isinstance(step, Segment):
            if (scope_name := SCOPE_NAMES.get(step.name)) is not None:
                _SEGMENT_SCOPES[id(step.steps)] = (scope_name, step.phase)
            if BOUNDARY_BEFORE.get(step.name) is Boundary.PARSED:
                _PARSED_STEPS.add(id(step.steps))
            _segment_scopes(step.steps)
        elif isinstance(step, ForEachModule):
            _segment_scopes(step.body)
        elif isinstance(step, HookCall):
            _segment_scopes(getattr(_tables, HOOK_BODIES[step.name]))


def _models(_parser: object, models: Sequence[Any]) -> Sequence[Any]:
    return models


def _model_list(_parser: object, state: Any) -> Sequence[Any]:
    return state.model_list


def _model_list_before_override_removal(_parser: object, state: Any) -> Sequence[Any]:
    return state.model_list_before_override_removal


def _live_models(_parser: object, state: Any) -> Sequence[Any]:
    return state.live_models


def _all_models(_parser: object, state: Any) -> Sequence[Any]:
    return state.all_models


def _additional_properties_models(_parser: object, state: Any) -> Sequence[Any]:
    return state.additional_properties_models


def _context_models(_parser: object, state: Any) -> Sequence[Any]:
    from datamodel_code_generator.parser._passes._shared import _context_models

    return _context_models(state.contexts)


def _models_before_override_removal(parser: Any, state: Any) -> Sequence[Any]:
    from datamodel_code_generator.parser._passes._tables import _models_before_override_removal

    return _models_before_override_removal(parser, state)


CHECKED_MODELS: Final[dict[str, Callable[[Any, Any], Sequence[Any]]]] = {
    "delete_duplicate_models": _models,
    "replace_duplicate_name_in_module": _models,
    "record_decimal_alias_models": _model_list,
    "alias_shadowed_imports": _model_list,
    "override_required_field": _model_list,
    "replace_unique_list_to_set": _model_list,
    "change_from_import": _model_list,
    "extract_inherited_enum": _model_list,
    "mark_unused_models_start": _model_list,
    "set_reference_default_value_to_field": _model_list,
    "reuse_model": _model_list,
    "collapse_root_models": _model_list,
    "set_default_enum_member": _model_list,
    "sort_models": _model_list,
    "change_field_name": _model_list,
    "restore_inherited_field_names_for_unique_aliases": _model_list,
    "apply_discriminator_type": _model_list,
    "type_const_narrowed_values": _model_list,
    "set_one_literal_on_default": _model_list,
    "refresh_field_config": _model_list,
    "rename_fields_hiding_types": _model_list,
    "remove_overridden_models": _model_list,
    "apply_field_type_overrides": _model_list,
    "type_dict_keys_by_root_types": _model_list,
    "fix_constructor_field_ordering_early": _model_list_before_override_removal,
    "snapshot_module_unused_models": _model_list,
    "apply_base_class_type_overrides": _model_list,
    "settle_discriminated_unions": _context_models,
    "tag_fields_of_tagged_bases": _context_models,
    "write_enum_member_tags_as_values": _context_models,
    "assign_forward_reference_fields": _model_list,
    "fix_constructor_field_ordering": _models_before_override_removal,
    "unbind_hidden_type_fields": _model_list,
    "update_type_aliases": _model_list,
    "collect_live_models": _model_list,
    "set_validate_default_on_fields": _live_models,
    "warn_required_assignments_after_defaults": _live_models,
    "fix_tag_opt_out_field_ordering": _context_models,
    "finalize_rendered_nullability": _context_models,
    "prepare_type_alias_fields": _all_models,
    "finalize_type_alias_users": _all_models,
    "mark_set_item_models_hashable": _all_models,
    "finalize_structured_imports": _all_models,
    "finalize_sequence_interface": _all_models,
    "order_constructor_fields_by_decided_nested_factories": _all_models,
    "snapshot_model_imports": _all_models,
    "append_model_imports": _all_models,
    "remove_unused_models": _all_models,
    "prepare_schema_runtime_validation_module_code": _all_models,
    "sync_schema_runtime_validation_module_imports": _all_models,
    "merge_runtime_expression_imports": _all_models,
    "remove_unused_imports": _all_models,
    "resolve_module_import_conflicts": _all_models,
    "alias_builtin_shadowing_module_imports": _all_models,
    "alias_builtins_hidden_by_submodules": _all_models,
    "bind_module_field_names": _all_models,
    "change_imported_model_name": _all_models,
    "name_final_classes_of_additional_properties": _additional_properties_models,
    "refresh_sequence_interface_after_renames": _all_models,
    "replan_schema_validators_after_renames": _all_models,
    "decide_config_items": _all_models,
}
"""The models the finality check compares when a pass ends, besides those it touched, by pass name."""


def _wrap_hooks(parser_class: type) -> None:
    """Wrap each hook on the class that defines it in the MRO of ``parser_class``."""
    from tests.checks.seams import wrap_hook

    for name in _HOOKS:
        wrap_hook(next(cls for cls in parser_class.__mro__ if name in vars(cls)), name)


def run_steps(original: Callable[..., None]) -> Callable[..., None]:
    """Wrap ``Runner.run_steps``: the pipeline of a parse runs with its checks, a segment's steps in its scope.

    The steps after ``PARSED`` start with the checks of L1 and the arming of those of the parse; the modules that the
    steps of ``FINALIZE_SEGMENTS`` leave are compared with the emitted models.
    """

    @wraps(original)
    def run_steps(self: Any, steps: Any, parser: Any, state: Any) -> None:
        from datamodel_code_generator.parser._passes._tables import FINALIZE_SEGMENTS, PIPELINE

        if steps is PIPELINE:
            return _run_pipeline(original, self, steps, parser, state)
        if steps is FINALIZE_SEGMENTS:
            original(self, steps, parser, state)
            return check_emitted_models(
                parser.generation_store, (model for ctx in state.contexts for model in ctx.models)
            )
        if id(steps) in _PARSED_STEPS:
            _enter_parsed(parser)
        if (
            (scope := _SEGMENT_SCOPES.get(id(steps))) is not None
            and (checks := PARSE_CHECKS.get()) is not None
            and checks.armed
        ):
            with checks.scope(*scope):
                return original(self, steps, parser, state)
        return original(self, steps, parser, state)

    return run_steps


def _run_pipeline(original: Callable[..., None], runner: Any, steps: Any, parser: Any, state: object) -> None:
    """Run the pipeline of a parse with the modes of its checks.

    While an area that watches writes is on, the pipeline runs inside the state of the checks of the parse.
    """
    if not _SEGMENT_SCOPES:
        _segment_scopes(steps)
    _wrap_hooks(type(parser))
    modes = read_modes()
    token, module = MODES.set(modes), _IN_MODULE.set(False)
    try:
        with NO_SCOPE if Area.STORE_WRITES not in modes and Area.FACT_FINALITY not in modes else _checks(parser):
            return original(runner, steps, parser, state)
    finally:
        _IN_MODULE.reset(module)
        MODES.reset(token)


def _checks(parser: object) -> AbstractContextManager[None]:
    """Open the checks of one parse of ``parser``; ``finality.py`` loads only then."""
    from datamodel_code_generator import Error

    return parse_checks(parser, Error)


def run_pass(original: Callable[..., None]) -> Callable[..., None]:
    """Wrap ``Runner.run_pass``: a pass that runs is traced and runs inside the finality scope of its declaration.

    ``when`` is evaluated once, by the original: the pass it runs is the instrumented one.
    """

    @wraps(original)
    def run_pass(self: Any, spec: Any, parser: object, state: object) -> None:
        trace, checks = TRACE.get(), PARSE_CHECKS.get()
        if trace is None and checks is None:
            return original(self, spec, parser, state)
        run, checked_models = spec.run, CHECKED_MODELS.get(spec.name)

        def instrumented(parser: object, state: object) -> None:
            models = None if checks is None or checked_models is None else checked_models(parser, state)
            _traced(spec.name)
            with NO_SCOPE if checks is None else checks.fact_pass(_DECLS[spec.name], models):
                run(parser, state)

        return original(self, replace(spec, run=instrumented), parser, state)

    return run_pass


def run_decision(original: Callable[..., None]) -> Callable[..., None]:
    """Wrap ``Runner.run_decision``: a decision is traced and runs inside the finality scope of its declaration.

    The declaration is the entry of ``DECISION_DECLS`` that the decision names.
    """

    @wraps(original)
    def run_decision(self: Any, decision: Any, facts: object, writer: object) -> None:
        name = decision.spec["name"]
        _traced(name)
        checks = PARSE_CHECKS.get()
        with NO_SCOPE if checks is None else checks.fact_pass(DECISION_DECLS[name], None):
            return original(self, decision, facts, writer)

    return run_decision


def process_single_module(original: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap ``_process_single_module``: an output module is traced and processed inside its module checkpoint."""

    @wraps(original)
    def process_single_module(self: Any, module: object, models: Any, *args: Any, **kwargs: Any) -> Any:
        if _IN_MODULE.get():
            return original(self, module, models, *args, **kwargs)
        from datamodel_code_generator.parser._facts import Phase

        checks, token = PARSE_CHECKS.get(), _IN_MODULE.set(True)
        try:
            armed = checks is not None and checks.armed
            with checks.scope("Parser._process_single_module", Phase.MODULES) if armed else NO_SCOPE:
                _traced(MODULE_TRACE_MARK)
                with NO_SCOPE if checks is None else checks.module_checkpoint(models):
                    return original(self, module, models, *args, **kwargs)
        finally:
            _IN_MODULE.reset(token)

    return process_single_module


def _enter_parsed(parser: Any) -> None:
    """Run the checks of L1, then arm the checks of the parse, in the SETTLE scope."""
    check_schema_locations(parser)
    check_model_owners(parser)
    check_shared_schemas(parser)
    if (checks := PARSE_CHECKS.get()) is not None:
        checks.arm(parser.generation_store, "Parser.parse")


def seal_facts(original: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap ``GenerationStore.seal_facts``: while the finality check runs, a decision reads the models as guarded views.

    The views report the render facts a decision reads.
    """

    @wraps(original)
    def seal_facts(self: Any, modules: Any) -> Any:
        facts = original(self, modules)
        checks = PARSE_CHECKS.get()
        return facts if checks is None or checks.finality is None else _views(checks.finality, facts)

    return seal_facts


def _views(finality: _Finality, facts: Any) -> Any:

    return guarded_facts(finality, facts)


def drop_held_null(original: Callable[..., None]) -> Callable[..., None]:
    """Wrap ``_DecisionWriter.drop_held_null``: it writes the objects that the guarded views a decision read name."""

    @wraps(original)
    def drop_held_null(self: Any, model: Any, field: Any, data_types: Any) -> None:
        return original(self, viewed(model), viewed(field), [viewed(data_type) for data_type in data_types])

    return drop_held_null


def get_inherited_constructor_info(original: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap ``get_inherited_constructor_info``: record the fields constructor ordering reads from the bases.

    Ordering reads them, after the decision phase too, from the bases ``constructor_base_models`` gives (not
    ``early``); the finality check compares them at B2.
    """

    @wraps(original)
    def get_inherited_constructor_info(host: Any, model: Any, *, early: bool = False) -> Any:
        if not early and (checks := PARSE_CHECKS.get()) is not None and checks.finality is not None:
            from datamodel_code_generator.parser._passes._shared import constructor_base_models

            if model.SUPPORTS_KW_ONLY and (bases := constructor_base_models(host, model)):
                checks.finality.record_ordering(model, bases, host._base_stand_ins)
        return original(host, model, early=early)

    return get_inherited_constructor_info
