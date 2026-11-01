"""The decision phase (L6, ``DECIDE``): the decisions of the parser's registry over the facts final at B1.

``decide`` is called from the pipeline tables of ``parser/_passes/_tables.py`` once the generic base class is
applied; the parser comes in through ``DecideHost``, which ``Parser`` satisfies structurally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from typing_extensions import Protocol

from datamodel_code_generator.parser._passes._shared import GenerationStoreHost, run_unused_models
from datamodel_code_generator.parser._phases import _DecisionWriter
from datamodel_code_generator.types import _copy_data_type

if TYPE_CHECKING:
    from datamodel_code_generator.parser._passes._state import FinishRun
    from datamodel_code_generator.parser._phases import DecisionPass
    from datamodel_code_generator.parser._pipeline import Runner

__all__ = ["DecideHost", "decide"]


class DecideHost(GenerationStoreHost, Protocol):
    """The decision registry and the generation store of the parser that the decision phase reads and writes."""

    @property
    def _DECISION_PASSES(self) -> tuple[DecisionPass[Any], ...]:  # noqa: N802
        """The decision passes of the parser class, in registry order."""


def decide(parser: DecideHost, runner: Runner, run: FinishRun[Any, Any, Any]) -> None:
    """Seal the facts final at B1 and run the decisions of the registry on them in its order, when it holds any."""
    if not (decisions := tuple(d for d in parser._DECISION_PASSES if d.when is None or d.when(parser))):  # noqa: SLF001
        return
    unused = run_unused_models(run)
    modules = [(ctx.module_key, unused.written(ctx.models)) for ctx in run.contexts]
    facts = parser.generation_store.seal_facts(modules)
    writer = _DecisionWriter(parser._graph_builder, _copy_data_type)  # noqa: SLF001
    for decision in decisions:
        runner.run_decision(decision, facts, writer)
    run.decisions = writer.outcome(model for _, models in modules for model in models)
