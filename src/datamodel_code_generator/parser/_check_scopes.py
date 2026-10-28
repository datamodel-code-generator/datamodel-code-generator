"""The calls the pipeline makes for the debug checks of ``parser/_finality.py``, without importing that machinery.

``Parser.parse`` loads ``parser/_finality.py`` only when ``DATAMODEL_CODE_GENERATOR_CHECK_STORE_WRITES`` or
``DATAMODEL_CODE_GENERATOR_CHECK_FACT_FINALITY`` is on, and the checks of that parse register themselves in
``PARSE_CHECKS``. Every function here is a no-op while none is registered. ``GuardedView`` is the base of the views that
``decision_views`` gives a decision; ``viewed`` unwraps it, so that decisions and the pipeline never name the machinery.
Nothing here imports the machinery, so that both sides import it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
    from contextlib import AbstractContextManager

    from datamodel_code_generator.model._views import ModelView
    from datamodel_code_generator.model.base import DataModel
    from datamodel_code_generator.parser._facts import Phase
    from datamodel_code_generator.parser._pass_decls import PassDecl
    from datamodel_code_generator.parser.generation import GenerationStore
    from datamodel_code_generator.reference import Reference

__all__ = [
    "MODULE_TRACE_MARK",
    "NO_SCOPE",
    "PARSE_CHECKS",
    "GuardedView",
    "ParseChecksPort",
    "arm_parse_checks",
    "decision_views",
    "fact_pass",
    "module_checkpoint",
    "pass_scope",
    "record_ordering_inputs",
    "recording_pass_trace",
    "viewed",
]

MODULE_TRACE_MARK: Final = "-- module"
NO_SCOPE: Final = nullcontext()
_TRACE: Final[ContextVar[list[str] | None]] = ContextVar("pass_trace", default=None)


class ParseChecksPort(ABC):
    """What the pipeline asks of the checks of the current parse; ``parser/_finality.py`` implements it."""

    __slots__ = ()

    @property
    @abstractmethod
    def armed(self) -> bool:
        """Whether L1 is over, so that writes are watched."""

    @abstractmethod
    def arm(self, store: GenerationStore, scope: str) -> None:
        """Guard the classes, the lists of the parsed graph and the store operations, in ``scope`` of SETTLE."""

    @abstractmethod
    def scope(self, name: str, phase: Phase) -> AbstractContextManager[None]:
        """Name the scope and phase that guarded writes inside are reported with."""

    @abstractmethod
    def fact_pass(self, decl: PassDecl, models: Sequence[DataModel] | None) -> AbstractContextManager[None]:
        """Run the pass ``decl`` declares on ``models`` inside the finality check."""

    @abstractmethod
    def module_checkpoint(self, models: list[DataModel]) -> AbstractContextManager[None]:
        """Process the module whose models are ``models`` inside the finality check."""

    @abstractmethod
    def decision_views(self) -> Callable[[DataModel], ModelView] | None:
        """Return how ``FinalFacts`` gives a decision each model, or None when no view reports reads."""

    @abstractmethod
    def record_ordering_inputs(
        self, model: DataModel, bases: Iterable[DataModel], stand_ins: Mapping[int, Reference | None]
    ) -> None:
        """Record the fields constructor ordering reads from ``bases`` for ``model``."""


class GuardedView:
    """The base of a view of one graph object that a decision reads while the finality check is armed."""

    __slots__ = ("_object",)

    _object: object


PARSE_CHECKS: Final[ContextVar[ParseChecksPort | None]] = ContextVar("parse_checks", default=None)


def arm_parse_checks(store: GenerationStore, scope: str) -> None:
    """Arm the checks of the current parse at the end of L1, if they are open; ``scope`` names the SETTLE scope."""
    if (checks := PARSE_CHECKS.get()) is not None:
        checks.arm(store, scope)


def pass_scope(name: str, phase: Phase) -> AbstractContextManager[None]:
    """Name the scope and phase that guarded writes inside are reported with; a no-op unless a parse is armed."""
    if (checks := PARSE_CHECKS.get()) is None or not checks.armed:
        return NO_SCOPE
    return checks.scope(name, phase)


def fact_pass(decl: PassDecl, models: Sequence[DataModel] | None = None) -> AbstractContextManager[None]:
    """Run the pass ``decl`` declares; ``models`` are the models it works on (those of its module, for a module pass).

    A no-op unless the finality check is armed or a pass trace is recorded.
    """
    if (trace := _TRACE.get()) is not None:
        trace.append(decl.name)
    if (checks := PARSE_CHECKS.get()) is None:
        return NO_SCOPE
    return checks.fact_pass(decl, models)


def module_checkpoint(models: list[DataModel]) -> AbstractContextManager[None]:
    """Process the module whose models are ``models``; a no-op unless the finality check is armed or a trace records."""
    if (trace := _TRACE.get()) is not None:
        trace.append(MODULE_TRACE_MARK)
    if (checks := PARSE_CHECKS.get()) is None:
        return NO_SCOPE
    return checks.module_checkpoint(models)


@contextmanager
def recording_pass_trace() -> Generator[list[str], None, None]:
    """Record the name of every pass that runs while the block runs, and ``MODULE_TRACE_MARK`` before each module."""
    trace: list[str] = []
    token = _TRACE.set(trace)
    try:
        yield trace
    finally:
        _TRACE.reset(token)


def decision_views() -> Callable[[DataModel], ModelView] | None:
    """Return how ``FinalFacts`` gives a decision each model while the finality check is armed, or None when it is not.

    The views report the render facts a decision reads.
    """
    if (checks := PARSE_CHECKS.get()) is None:
        return None
    return checks.decision_views()


def viewed(value: Any) -> Any:
    """Return the object a view of ``FinalFacts`` stands for: the object of a guarded view, else ``value`` itself."""
    return value._object if isinstance(value, GuardedView) else value  # noqa: SLF001


def record_ordering_inputs(
    model: DataModel, bases: Iterable[DataModel], stand_ins: Mapping[int, Reference | None]
) -> None:
    """Record the fields constructor ordering reads from ``bases`` for ``model``, when the finality check is armed."""
    if (checks := PARSE_CHECKS.get()) is not None:
        checks.record_ordering_inputs(model, bases, stand_ins)
