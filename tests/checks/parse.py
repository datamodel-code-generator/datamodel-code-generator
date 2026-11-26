"""The scopes of one parse that the checks run in: the parse itself, its segments, passes, decisions and modules.

The run of the whole pipeline (``PIPELINE``) of a parse reads the modes of its checks and, when ``FACT_FINALITY`` is
on, opens the finality check of ``finality.py`` (``PARSE_CHECKS``). When the pipeline enters ``PARSED``, the checks of
L1 run and the finality check arms; when the modules are final (``FINALIZE_SEGMENTS``), the model graph check compares
the models they write with the emitted ones; from B2 on (``render_ready``), every fact and render plan of the parse is
final. Every pass and decision runs inside the finality scope of its declaration, and every output module inside its
module checkpoint, as the pass trace (``trace.py``) records them. ``Parser.parse`` itself stays unwrapped: it remembers
the frame that called it, which warnings name.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import replace
from functools import wraps
from typing import TYPE_CHECKING, Any, Final

from scripts.pass_decls import BOUNDARY_BEFORE, DECISION_DECLS, HOOK_BODIES, PASS_DECLS, Boundary
from tests.checks import areas
from tests.checks.areas import MODES, Area, read_modes
from tests.checks.finality import (
    NO_SCOPE,
    PARSE_CHECKS,
    parse_checks,
)
from tests.checks.model_graph import check_emitted_models, check_model_owners
from tests.checks.naming import start as start_naming
from tests.checks.schema_graph import check_loaded_documents
from tests.checks.schema_locations import check_schema_locations
from tests.checks.trace import MODULE_TRACE_MARK, TRACE

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from contextlib import AbstractContextManager

    from tests.checks.areas import Mode

_IN_MODULE: Final[ContextVar[bool]] = ContextVar("in_module", default=False)
"""Whether the parse that runs processes an output module now: an override that calls its base processes it once."""
_PARSED_STEPS: Final[set[int]] = set()
"""The ids of the steps of the segments that enter ``PARSED`` (``BOUNDARY_BEFORE``)."""
_RENDER_READY_STEPS: Final[set[int]] = set()
"""The ids of the steps of the segments that enter B2 (``Boundary.RENDER_READY`` in ``BOUNDARY_BEFORE``)."""
_PAST_RENDER_READY: Final[ContextVar[bool]] = ContextVar("past_render_ready", default=False)
"""Whether the parse that runs has entered B2."""
_DECLS: Final = {decl.name: decl for decl in PASS_DECLS}
"""The declaration of each table pass, by the name of its ``PassSpec``."""
_HOOKS: Final = ("_process_single_module",)
"""The parser methods wrapped on the class that defines them in the running parser's MRO (``seams.py`` rule 2)."""


def render_ready() -> bool:
    """Return whether the parse that runs has entered B2: its facts and its fields' render plans are final from then on.

    The pipeline enters B2 where its segment ``imports`` starts (``BOUNDARY_BEFORE``), right after
    ``freeze_field_render_plans`` froze the render plans; the renders before only build what passes compare, such as
    the deduplication keys of ``--reuse-model``.
    """
    return _PAST_RENDER_READY.get()


def _traced(name: str) -> None:
    """Append ``name`` to the pass trace, when one is recorded."""
    if (trace := TRACE.get()) is not None:
        trace.append(name)


def _segment_boundaries(steps: Iterable[Any]) -> None:
    from datamodel_code_generator.parser._passes import _tables
    from datamodel_code_generator.parser._pipeline import ForEachModule, HookCall, Segment

    for step in steps:
        if isinstance(step, Segment):
            if (boundary := BOUNDARY_BEFORE.get(step.name)) is Boundary.PARSED:
                _PARSED_STEPS.add(id(step.steps))
            elif boundary is Boundary.RENDER_READY:
                _RENDER_READY_STEPS.add(id(step.steps))
            _segment_boundaries(step.steps)
        elif isinstance(step, ForEachModule):
            _segment_boundaries(step.body)
        elif isinstance(step, HookCall):
            _segment_boundaries(getattr(_tables, HOOK_BODIES[step.name]))


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
    "replace_merged_unique_list_to_set": _model_list,
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
    "flatten_narrowed_inherited_fields": _context_models,
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
    "name_final_classes_of_additional_properties": _all_models,
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
    """Wrap ``Runner.run_steps``: the pipeline of a parse runs with its checks.

    The steps after ``PARSED`` start with the checks of L1 and the arming of those of the parse, and the steps after B2
    mark the parse as past B2 (``render_ready``); the modules that the steps of ``FINALIZE_SEGMENTS`` leave are
    compared with the emitted models.
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
        if id(steps) in _RENDER_READY_STEPS:
            _PAST_RENDER_READY.set(True)
        return original(self, steps, parser, state)

    return run_steps


def _run_pipeline(original: Callable[..., None], runner: Any, steps: Any, parser: Any, state: object) -> None:
    """Run the pipeline of a parse with the modes of its checks.

    While ``FACT_FINALITY`` is on, the pipeline runs inside the state of the finality check of the parse.
    """
    if not _PARSED_STEPS:
        _segment_boundaries(steps)
    _wrap_hooks(type(parser))
    modes = read_modes()
    token, module, ready = MODES.set(modes), _IN_MODULE.set(False), _PAST_RENDER_READY.set(False)
    try:
        start_naming(parser)
        with NO_SCOPE if (finality := modes.get(Area.FACT_FINALITY)) is None else _checks(finality):
            return original(runner, steps, parser, state)
    finally:
        _PAST_RENDER_READY.reset(ready)
        _IN_MODULE.reset(module)
        MODES.reset(token)


def _checks(mode: Mode) -> AbstractContextManager[None]:
    """Open the finality check of one parse; ``finality.py`` loads only then."""
    return parse_checks(mode)


def _runs_idle(spec: Any, parser: object, state: object) -> bool:
    """Return whether the trigger check runs the pass of ``spec`` idle: ``when`` holds and ``trigger`` is false."""
    return (
        spec.trigger is not None
        and areas.mode(Area.PASS_TRIGGERS) is not None
        and (spec.when is None or spec.when(parser, state))
        and not spec.trigger(parser, state)
    )


def run_pass(original: Callable[..., None]) -> Callable[..., None]:
    """Wrap ``Runner.run_pass``: a pass that runs is traced and runs inside the finality scope of its declaration.

    ``when`` and ``trigger`` are evaluated by the original: the pass it runs is the instrumented one, which the checks
    of a parse make once for each pass spec (``ParseChecks.passes``). With ``PASS_TRIGGERS`` on, a pass whose ``when``
    holds and whose ``trigger`` is false runs untraced inside ``ParseChecks.idle_pass`` instead, so that the traces are
    those of a run with the area off.
    """

    @wraps(original)
    def run_pass(self: Any, spec: Any, parser: object, state: object) -> None:
        trace, checks = TRACE.get(), PARSE_CHECKS.get()
        if trace is None and checks is None:
            return original(self, spec, parser, state)
        if checks is not None and _runs_idle(spec, parser, state):
            with checks.idle_pass(_DECLS[spec.name], parser, state):
                spec.run(parser, state)
            return None
        passes = {} if checks is None else checks.passes
        if (known := passes.get(id(spec))) is not None and known[0] is spec:
            return original(self, known[1], parser, state)
        run, checked_models = spec.run, CHECKED_MODELS.get(spec.name)

        def instrumented(parser: object, state: object) -> None:
            models = None if checks is None or checked_models is None else checked_models(parser, state)
            _traced(spec.name)
            with NO_SCOPE if checks is None else checks.fact_pass(_DECLS[spec.name], models):
                run(parser, state)

        passes[id(spec)] = (spec, scoped := replace(spec, run=instrumented))
        return original(self, scoped, parser, state)

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
        checks, token = PARSE_CHECKS.get(), _IN_MODULE.set(True)
        try:
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
    check_loaded_documents(parser)
    if (checks := PARSE_CHECKS.get()) is not None:
        checks.arm(parser.generation_store)


def get_inherited_constructor_info(original: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap ``get_inherited_constructor_info``: record the fields constructor ordering reads from the bases.

    Ordering reads them, after the decision phase too, from the bases ``constructor_base_models`` gives (not
    ``early``); the finality check compares them at B2.
    """

    @wraps(original)
    def get_inherited_constructor_info(host: Any, model: Any, *, early: bool = False, places: str = "collected") -> Any:
        if not early and (checks := PARSE_CHECKS.get()) is not None and checks.finality is not None:
            from datamodel_code_generator.parser._passes._shared import constructor_base_models

            if model.SUPPORTS_KW_ONLY and (bases := constructor_base_models(host, model)):
                checks.finality.record_ordering(model, bases, host._base_stand_ins)
        return original(host, model, early=early, places=places)

    return get_inherited_constructor_info
