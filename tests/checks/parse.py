"""The scopes of one parse that the checks run in: the parse itself, its segments, passes, decisions and modules.

The run of the whole pipeline (``PIPELINE``) of a parse reads the modes of its checks and, when ``STORE_WRITES`` or
``FACT_FINALITY`` is on, opens the checks of ``finality.py`` (``PARSE_CHECKS``), which arm when the pipeline enters
``PARSED``. Every pass and decision then runs inside the finality scope of its declaration, every output module inside
its module checkpoint, and every segment with a scope name inside that pass scope, as the pass trace (``trace.py``)
records them. ``Parser.parse`` itself stays unwrapped: it remembers the frame that called it, which warnings name.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import replace
from functools import wraps
from typing import TYPE_CHECKING, Any, Final

from tests.checks.areas import MODES, Area, read_modes
from tests.checks.finality import (
    NO_SCOPE,
    PARSE_CHECKS,
    GuardedView,
    _Finality,
    guarded_facts,
    parse_checks,
)
from tests.checks.trace import MODULE_TRACE_MARK, TRACE

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from contextlib import AbstractContextManager

_IN_MODULE: Final[ContextVar[bool]] = ContextVar("in_module", default=False)
"""Whether the parse that runs processes an output module now: an override that calls its base processes it once."""
_SEGMENT_SCOPES: Final[dict[int, tuple[str, Any]]] = {}
"""The scope name and phase of each segment with a scope name, by the id of its steps."""
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
    from datamodel_code_generator.parser._pipeline import ForEachModule, HookCall, Segment

    for step in steps:
        if isinstance(step, Segment):
            if step.scope_name is not None:
                _SEGMENT_SCOPES[id(step.steps)] = (step.scope_name, step.phase)
            _segment_scopes(step.steps)
        elif isinstance(step, (HookCall, ForEachModule)):
            _segment_scopes(step.body)


def _wrap_hooks(parser_class: type) -> None:
    """Wrap each hook on the class that defines it in the MRO of ``parser_class``."""
    from tests.checks.seams import wrap_hook

    for name in _HOOKS:
        wrap_hook(next(cls for cls in parser_class.__mro__ if name in vars(cls)), name)


def run_steps(original: Callable[..., None]) -> Callable[..., None]:
    """Wrap ``Runner.run_steps``: the pipeline of a parse runs with its checks, a segment's steps in its scope."""

    @wraps(original)
    def run_steps(self: Any, steps: Any, parser: Any, state: object) -> None:
        from datamodel_code_generator.parser._passes._tables import PIPELINE

        if steps is PIPELINE:
            return _run_pipeline(original, self, steps, parser, state)
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
        run, checked_models = spec.run, spec.checked_models

        def instrumented(parser: object, state: object) -> None:
            models = None if checks is None or checked_models is None else checked_models(parser, state)
            _traced(spec.decl.name)
            with NO_SCOPE if checks is None else checks.fact_pass(spec.decl, models):
                run(parser, state)

        return original(self, replace(spec, run=instrumented), parser, state)

    return run_pass


def run_decision(original: Callable[..., None]) -> Callable[..., None]:
    """Wrap ``Runner.run_decision``: a decision is traced and runs inside the finality scope of its declaration."""

    @wraps(original)
    def run_decision(self: Any, decision: Any, facts: object, writer: object) -> None:
        decl = decision.spec["decl"]
        _traced(decl.name)
        with NO_SCOPE if (checks := PARSE_CHECKS.get()) is None else checks.fact_pass(decl, None):
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


def enter_parsed(original: Callable[[Any], None]) -> Callable[[Any], None]:
    """Wrap ``Parser.__enter_parsed``: after the checks of L1, the checks of the parse arm, in the SETTLE scope."""

    @wraps(original)
    def enter_parsed(self: Any) -> None:
        original(self)
        if (checks := PARSE_CHECKS.get()) is not None:
            checks.arm(self.generation_store, "Parser.parse")

    return enter_parsed


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
