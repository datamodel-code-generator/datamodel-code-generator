"""The post-parse pipeline of ``Parser.parse()``: its steps and segments, and the runner that executes them.

The tables of ``parser/_passes/_tables.py`` hold the pipeline, which ``Parser`` runs. A ``PassSpec`` runs one pass,
named as its declaration in ``scripts/pass_decls.py``; a ``ForEachModule`` runs passes once per output module; a
``HookCall`` calls an overridable parser method whose default runs a table of its own; a ``Segment`` runs the steps of
one phase, entering the phase first. The ``Runner`` is the only caller of a pass: it skips a pass whose ``when`` is
false and runs every other one. A ``DecisionPhase`` runs the decision passes of the parser's registry
(``parser/_phases.py``) in registry order. ``scripts/check_pass_pipeline.py`` checks the tables and the registries
against the declarations and the order rule. ``anchor_parse_caller`` and ``stacklevel_at_parse_caller`` let a warning
name the code that called ``Parser.parse()`` however deep in the pipeline it is emitted.
"""

from __future__ import annotations

import sys
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Final, Generic, Protocol, TypeVar, cast

from typing_extensions import ParamSpec, final

from datamodel_code_generator._records import record

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from contextvars import Token
    from types import FrameType

    from datamodel_code_generator.parser._facts import Phase
    from datamodel_code_generator.parser._phases import DecisionPass, DecisionWriter, FinalFacts

__all__ = [
    "DecisionPhase",
    "ForEachModule",
    "HookCall",
    "PassSpec",
    "Runner",
    "Segment",
    "StepLike",
    "anchor_parse_caller",
    "late",
    "release_parse_caller",
    "stacklevel_at_parse_caller",
]

P = TypeVar("P")
R = TypeVar("R")
Q = ParamSpec("Q")
S = TypeVar("S")
M = TypeVar("M")
P_contra = TypeVar("P_contra", contravariant=True)
S_contra = TypeVar("S_contra", contravariant=True)


class StepLike(Protocol[P_contra, S_contra]):
    """One entry of a pipeline table."""

    def execute(self, runner: Runner, parser: P_contra, state: S_contra) -> None:
        """Run the step on ``state``."""


def late(function: Callable[Q, R]) -> Callable[Q, R]:
    """Return ``function`` looked up on its module at call time, so a tracer that replaces module attributes sees it."""
    named = cast("Any", function)
    module = named.__module__
    name = named.__name__

    def call(*args: Q.args, **kwargs: Q.kwargs) -> R:
        return cast("Callable[Q, R]", getattr(sys.modules[module], name))(*args, **kwargs)

    return call


@final
@record
class PassSpec(Generic[P, S]):
    """One pass, named as its declaration: ``run`` runs it on the state of its segment when ``when`` (if any) holds."""

    name: str
    run: Callable[[P, S], None]
    when: Callable[[P, S], bool] | None = None

    def execute(self, runner: Runner, parser: P, state: S) -> None:
        """Run the pass through the runner."""
        runner.run_pass(self, parser, state)


@final
@record
class ForEachModule(Generic[P, S, M]):
    """Passes run module by module, in module order: every pass of ``body`` for a module before the next module."""

    name: str
    modules: Callable[[P, S], Iterable[M]]
    body: tuple[StepLike[P, M], ...]

    def execute(self, runner: Runner, parser: P, state: S) -> None:
        """Run the body once per module."""
        for module_state in self.modules(parser, state):
            runner.run_steps(self.body, parser, module_state)


@final
@record
class HookCall(Generic[P, S]):
    """A call of an overridable parser method, whose default implementation runs a table on a state of its own.

    With ``per_module``, the call calls the method once per output module.
    """

    name: str
    call: Callable[[P, S], None]
    per_module: bool = False

    def execute(self, runner: Runner, parser: P, state: S) -> None:  # noqa: ARG002
        """Call the hook."""
        self.call(parser, state)


@final
@record
class DecisionPhase(Generic[P, S]):
    """The decision passes of the parser's registry: ``run`` seals the facts and runs them through the runner."""

    name: str
    run: Callable[[Runner, P, S], None]

    def execute(self, runner: Runner, parser: P, state: S) -> None:
        """Run the decision passes."""
        self.run(runner, parser, state)


@final
@record
class Segment(Generic[P, R, S]):
    """The steps of one phase over one view of the state.

    Entering the segment enters ``phase`` on the generation store, unless ``enters_phase`` is false, for a segment that
    continues the phase of the one before. ``view`` is the identity at run time: it types the state the steps see.
    """

    name: str
    phase: Phase
    view: Callable[[R], S]
    steps: tuple[StepLike[P, S], ...]
    enters_phase: bool = True

    def execute(self, runner: Runner, parser: P, state: R) -> None:
        """Enter the phase, then run the steps."""
        if self.enters_phase:
            runner.enter_phase(self.phase)
        runner.run_steps(self.steps, parser, self.view(state))


@final
@record
class Runner:
    """Runs the steps of the tables in order; the only caller of ``PassSpec.run``.

    ``enter_phase`` marks a phase on the generation store.
    """

    enter_phase: Callable[[Phase], None]

    def run_pass(self, spec: PassSpec[P, S], parser: P, state: S) -> None:  # noqa: PLR6301
        """Run one pass, unless its ``when`` is false."""
        if spec.when is not None and not spec.when(parser, state):
            return
        spec.run(parser, state)

    def run_decision(  # noqa: PLR6301
        self, decision: DecisionPass[DecisionWriter], facts: FinalFacts, writer: DecisionWriter
    ) -> None:
        """Run one decision."""
        decision.run(facts, writer)

    def run_steps(self, steps: Iterable[StepLike[P, S]], parser: P, state: S) -> None:
        """Run ``steps`` in order on ``state``."""
        for step in steps:
            step.execute(self, parser, state)


_PARSE_CALLER: Final[ContextVar[FrameType | None]] = ContextVar("parse_caller", default=None)
"""The frame that called the running ``Parser.parse()``."""


def anchor_parse_caller() -> Token[FrameType | None]:
    """Remember the frame that called the ``Parser.parse()`` calling this, past the wrapper frames of ``--debug``.

    ``release_parse_caller`` with the returned token forgets it again.
    """
    frame = sys._getframe(2)  # noqa: SLF001
    while frame is not None and frame.f_globals.get("__name__", "").startswith("pysnooper"):
        frame = frame.f_back
    return _PARSE_CALLER.set(frame)


def release_parse_caller(token: Token[FrameType | None]) -> None:
    """Forget the frame ``anchor_parse_caller`` remembered."""
    _PARSE_CALLER.reset(token)


def stacklevel_at_parse_caller() -> int:
    """Return the ``stacklevel`` that makes a ``warn()`` in the calling function name the caller of ``Parser.parse()``.

    Outside ``Parser.parse()`` there is no such caller, and the warning names the outermost frame.
    """
    anchor = _PARSE_CALLER.get()
    frame: FrameType | None = sys._getframe(1)  # noqa: SLF001
    level = 1
    while frame is not None and frame is not anchor:
        frame = frame.f_back
        level += 1
    return level
