"""The post-parse pipeline of ``Parser.parse()``: its steps, segments and boundaries, and the runner that executes them.

The tables of ``parser/_passes/_tables.py`` hold the pipeline, which ``Parser`` runs. A ``PassSpec`` runs one declared
pass (``parser/_pass_decls.py``);
a ``ForEachModule`` runs passes once per output module; a ``HookCall`` calls an overridable parser method whose default
runs a sub-table; a ``Segment`` runs the steps of one phase, entering the phase and the boundary before it. The
``Runner`` is the only caller of a pass: it skips a pass whose ``when`` is false and runs every other one inside
``fact_pass`` (``parser/_check_scopes.py``), with the models the finality check compares after it. A ``DecisionPhase``
runs the decision passes of the parser's registry (``parser/_phases.py``), in the order ``decision_order`` computes.

``check_order`` is the order rule of the tables. For two passes of one segment, a pass that reads what an earlier pass
writes, where nothing else overlaps, runs after it (a derived edge); every other overlap needs an explicit ``After``
edge on the later pass, which replaces the derived edge of the pair. An explicit edge that puts a reader before a writer
of the same fact is a provisional read. ``anchor_parse_caller`` and ``stacklevel_at_parse_caller`` let a warning name
the code that called ``Parser.parse()`` however deep in the pipeline it is emitted.
"""

from __future__ import annotations

import sys
from contextlib import nullcontext
from contextvars import ContextVar
from enum import Enum, unique
from typing import TYPE_CHECKING, Any, Final, Generic, Protocol, TypeVar, cast

from typing_extensions import ParamSpec, final

from datamodel_code_generator._records import record
from datamodel_code_generator.parser._check_scopes import fact_pass, pass_scope

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
    from contextvars import Token
    from types import FrameType

    from datamodel_code_generator.model.base import DataModel
    from datamodel_code_generator.parser._facts import Fact, Phase, RenderFact
    from datamodel_code_generator.parser._pass_decls import PassDecl
    from datamodel_code_generator.parser._phases import DecisionPass, DecisionWriter, FinalFacts

__all__ = [
    "Boundary",
    "DecisionPhase",
    "ForEachModule",
    "HookCall",
    "OrderCheck",
    "PassSpec",
    "Runner",
    "Segment",
    "StepLike",
    "anchor_parse_caller",
    "check_order",
    "decision_order",
    "flatten",
    "late",
    "release_parse_caller",
    "segment_bodies",
    "stacklevel_at_parse_caller",
]

P = TypeVar("P")
R = TypeVar("R")
Q = ParamSpec("Q")
S = TypeVar("S")
M = TypeVar("M")
P_contra = TypeVar("P_contra", contravariant=True)
S_contra = TypeVar("S_contra", contravariant=True)

_NO_SCOPE: Final = nullcontext()


@unique
class Boundary(Enum):
    """A point between segments where what passes may read or write changes; only the runner enters one."""

    PARSED = "parsed"
    """L1 is over: the write guards are armed."""
    MODULE_DONE = "module-done"
    """The passes of one output module are over."""
    FACTS_FINAL = "B1"
    """Every fact is final; decisions start."""
    DECIDED = "decided"
    """Every decision is written; render preparation starts."""
    RENDER_READY = "B2"
    """Render preparation is over; import collection starts."""


class StepLike(Protocol[P_contra, S_contra]):
    """One entry of a pipeline table."""

    def execute(self, runner: Runner, parser: P_contra, state: S_contra) -> None:
        """Run the step on ``state``."""

    def passes(self) -> Iterator[PassDecl]:
        """Yield the declarations of the passes the step runs, in table order."""


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
    """One declared pass: ``run`` runs it on the state of its segment, when ``when`` (if any) is true.

    ``checked_models`` returns the models the finality check compares when the pass ends, besides those it touched.
    """

    decl: PassDecl
    run: Callable[[P, S], None]
    when: Callable[[P, S], bool] | None = None
    checked_models: Callable[[P, S], Sequence[DataModel]] | None = None

    def execute(self, runner: Runner, parser: P, state: S) -> None:
        """Run the pass through the runner."""
        runner.run_pass(self, parser, state)

    def passes(self) -> Iterator[PassDecl]:
        """Yield the declaration."""
        yield self.decl


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

    def passes(self) -> Iterator[PassDecl]:
        """Yield the declarations of the body."""
        for step in self.body:
            yield from step.passes()


@final
@record
class HookCall(Generic[P, S]):
    """A call of an overridable parser method whose default implementation runs ``body`` on a state of its own.

    With ``per_module``, the call calls the method once per output module.
    """

    name: str
    call: Callable[[P, S], None]
    body: tuple[StepLike[P, Any], ...]
    per_module: bool = False

    def execute(self, runner: Runner, parser: P, state: S) -> None:  # noqa: ARG002
        """Call the hook."""
        self.call(parser, state)

    def passes(self) -> Iterator[PassDecl]:
        """Yield the declarations of the body that the default implementation runs."""
        for step in self.body:
            yield from step.passes()


@final
@record
class DecisionPhase(Generic[P, S]):
    """The decision passes of the parser's registry: ``run`` seals the facts and runs them through the runner.

    The decisions are no table passes: their declarations live in the registry, not in ``PASS_DECLS``.
    """

    name: str
    run: Callable[[Runner, P, S], None]

    def execute(self, runner: Runner, parser: P, state: S) -> None:
        """Run the decision passes."""
        self.run(runner, parser, state)

    def passes(self) -> Iterator[PassDecl]:  # noqa: PLR6301
        """Yield nothing: the registry, not the table, declares the decisions."""
        yield from ()


@final
@record
class Segment(Generic[P, R, S]):
    """The steps of one phase over one view of the state.

    Entering the segment enters ``phase`` on the generation store (unless ``enters_phase`` is false, for a segment that
    continues the phase of the one before) and then the boundary ``enter``. ``view`` is the identity at run time: it
    types the state the steps see. Guarded writes inside are reported in the pass scope ``scope_name``.
    """

    name: str
    phase: Phase
    view: Callable[[R], S]
    steps: tuple[StepLike[P, S], ...]
    enter: Boundary | None = None
    enters_phase: bool = True
    scope_name: str | None = None

    def execute(self, runner: Runner, parser: P, state: R) -> None:
        """Enter the phase and the boundary, then run the steps."""
        if self.enters_phase:
            runner.enter_phase(self.phase)
        if self.enter is not None:
            runner.enter_boundary(self.enter)
        with _NO_SCOPE if self.scope_name is None else pass_scope(self.scope_name, self.phase):
            runner.run_steps(self.steps, parser, self.view(state))

    def passes(self) -> Iterator[PassDecl]:
        """Yield the declarations of the steps."""
        for step in self.steps:
            yield from step.passes()


@final
@record
class Runner:
    """Runs the steps of the tables in order; the only caller of ``PassSpec.run``.

    ``enter_phase`` marks a phase on the generation store; ``boundary_hooks`` run when the pipeline enters a boundary.
    """

    enter_phase: Callable[[Phase], None]
    boundary_hooks: Mapping[Boundary, Callable[[], None]]

    def run_pass(self, spec: PassSpec[P, S], parser: P, state: S) -> None:  # noqa: PLR6301
        """Run one pass inside its ``fact_pass`` scope, unless its ``when`` is false."""
        if spec.when is not None and not spec.when(parser, state):
            return
        with fact_pass(spec.decl, None if spec.checked_models is None else spec.checked_models(parser, state)):
            spec.run(parser, state)

    def run_decision(  # noqa: PLR6301
        self, decision: DecisionPass[DecisionWriter], facts: FinalFacts, writer: DecisionWriter
    ) -> None:
        """Run one decision inside the ``fact_pass`` scope of its declaration."""
        with fact_pass(decision.spec["decl"]):
            decision.run(facts, writer)

    def run_steps(self, steps: Iterable[StepLike[P, S]], parser: P, state: S) -> None:
        """Run ``steps`` in order on ``state``."""
        for step in steps:
            step.execute(self, parser, state)

    def run_module_body(self, body: Iterable[StepLike[P, M]], parser: P, state: M) -> None:
        """Run the passes of one output module, then enter ``MODULE_DONE``."""
        self.run_steps(body, parser, state)
        self.enter_boundary(Boundary.MODULE_DONE)

    def enter_boundary(self, boundary: Boundary) -> None:
        """Run the hook of ``boundary``, if the parser gave one."""
        if (hook := self.boundary_hooks.get(boundary)) is not None:
            hook()


def flatten(steps: Iterable[StepLike[Any, Any]]) -> Iterator[PassDecl]:
    """Yield the declarations of every pass of ``steps``, with hook and module bodies expanded, in table order."""
    for step in steps:
        yield from step.passes()


def segment_bodies(
    segments: Iterable[StepLike[Any, Any]],
) -> Iterator[tuple[Segment[Any, Any, Any], tuple[PassDecl, ...]]]:
    """Yield each segment with the passes of its body: its own steps, with hook and module bodies expanded.

    ``segments`` holds segments only; the segments in the body of a hook (``_finalize_modules``) are bodies of their
    own.
    """
    for step in segments:
        segment = cast("Segment[Any, Any, Any]", step)
        own: list[PassDecl] = []
        nested: list[tuple[Segment[Any, Any, Any], tuple[PassDecl, ...]]] = []
        for inner in segment.steps:
            if isinstance(inner, HookCall) and any(isinstance(part, Segment) for part in inner.body):
                nested.extend(segment_bodies(inner.body))
            else:
                own.extend(inner.passes())
        yield segment, tuple(own)
        yield from nested


@final
@record
class OrderCheck:
    """What ``check_order`` found: the order, its problems, and the provisional reads the explicit edges record."""

    order: tuple[str, ...]
    problems: tuple[str, ...]
    provisional_reads: tuple[str, ...]


def _facts_text(facts: Iterable[Fact | RenderFact]) -> str:
    return ", ".join(sorted(fact.name for fact in facts))


def check_order(decls: Sequence[PassDecl], *, computed: bool = False) -> OrderCheck:
    """Check the order of one segment's passes against the order rule; ``computed`` derives the order from it.

    Two passes conflict when one writes what the other reads or both write the same fact. Where only one of them writes
    what the other reads, a derived edge runs the reader after the writer; every other conflicting pair must be
    connected by explicit ``After`` edges, directly or through other edges. An explicit edge names another pass of the
    segment that the pass conflicts with, and replaces the derived edge of its pair. In a table segment (``computed``
    false) every edge must follow the table order; a computed segment runs its passes in topological order, the order
    of ``decls`` breaking ties, and a cycle is a problem.
    """
    successors: dict[str, set[str]] = {decl.name: set() for decl in decls}
    problems, provisional = _add_explicit_edges(decls, successors, computed=computed)
    explicit = {frozenset((earlier, later)) for earlier, laters in successors.items() for later in laters}
    unordered: list[tuple[PassDecl, PassDecl, frozenset[Fact | RenderFact]]] = []
    for index, later in enumerate(decls):
        for earlier in decls[:index]:
            if frozenset((earlier.name, later.name)) in explicit or not (shared := _conflict(earlier, later)):
                continue
            if _flows(earlier, later):
                successors[earlier.name].add(later.name)
            elif computed and _flows(later, earlier):
                successors[later.name].add(earlier.name)
            else:
                unordered.append((earlier, later, shared))
    problems.extend(
        f"{later.name} conflicts with {earlier.name} on {_facts_text(shared)} and needs an explicit edge"
        for earlier, later, shared in unordered
        if not _reaches(successors, earlier.name, later.name) and not _reaches(successors, later.name, earlier.name)
    )
    order = tuple(successors)
    if computed:
        order, cycle = _topological_order(order, successors)
        if cycle:
            problems.append(f"the edges of {', '.join(cycle)} form a cycle")
    return OrderCheck(order, tuple(problems), tuple(provisional))


def decision_order(decisions: Sequence[DecisionPass[Any]]) -> tuple[DecisionPass[Any], ...]:
    """Return ``decisions`` in the order the order rule computes from their declarations, registration breaking ties."""
    by_name = {decision.spec["decl"].name: decision for decision in decisions}
    order = check_order([decision.spec["decl"] for decision in decisions], computed=True).order
    return tuple(by_name[name] for name in order)


def _add_explicit_edges(
    decls: Sequence[PassDecl], successors: dict[str, set[str]], *, computed: bool
) -> tuple[list[str], list[str]]:
    """Add the explicit edges of ``decls`` to ``successors``; return the problems and the provisional reads."""
    position = {decl.name: index for index, decl in enumerate(decls)}
    problems: list[str] = []
    provisional: list[str] = []
    for later in decls:
        for edge in later.after:
            if (index := position.get(edge.pass_name)) is None:
                problems.append(f"{later.name} runs after {edge.pass_name}, which is not a pass of the segment")
            elif not _conflict(earlier := decls[index], later):
                problems.append(f"{later.name} runs after {edge.pass_name}, which it does not conflict with")
            elif not computed and index > position[later.name]:
                problems.append(f"{later.name} runs after {edge.pass_name}, which the table runs later")
            else:
                successors[earlier.name].add(later.name)
                if read_first := earlier.reads & later.writes:
                    provisional.append(
                        f"{earlier.name} reads {_facts_text(read_first)} before {later.name} writes it ({edge.reason})"
                    )
    return problems, provisional


def _flows(writer: PassDecl, reader: PassDecl) -> bool:
    """Return whether ``writer`` writes what ``reader`` reads and the pair has no other overlap."""
    return bool(writer.writes & reader.reads) and not writer.reads & reader.writes and not writer.writes & reader.writes


def _topological_order(names: tuple[str, ...], successors: Mapping[str, set[str]]) -> tuple[tuple[str, ...], list[str]]:
    """Return the names in an order every edge follows, ``names`` breaking ties, and the names a cycle leaves."""
    pending = list(names)
    order: list[str] = []
    while ready := [name for name in pending if not any(name in successors[other] for other in pending)]:
        order.append(ready[0])
        pending.remove(ready[0])
    return (*order, *pending), pending


def _conflict(earlier: PassDecl, later: PassDecl) -> frozenset[Fact | RenderFact]:
    """Return the facts that one of the passes writes and the other reads or writes."""
    return (earlier.writes & (later.reads | later.writes)) | (earlier.reads & later.writes)


def _reaches(successors: Mapping[str, set[str]], start: str, goal: str) -> bool:
    pending = [start]
    seen: set[str] = set()
    while pending:
        if (name := pending.pop()) == goal:
            return True
        if name not in seen:
            seen.add(name)
            pending.extend(successors[name])
    return False


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
