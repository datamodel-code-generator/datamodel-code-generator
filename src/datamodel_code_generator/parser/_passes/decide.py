"""The decision phase (L6, ``DECIDE``): the decisions of the parser's registry over the facts final at B1.

``decide`` is called from the pipeline tables of ``parser/_passes/_tables.py`` once the generic base class is
applied; the parser comes in through ``DecideHost``, which ``Parser`` satisfies structurally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from typing_extensions import Protocol

from datamodel_code_generator.parser._check_scopes import decision_views, viewed
from datamodel_code_generator.parser._passes._shared import _copy_data_type
from datamodel_code_generator.parser._phases import _DecisionWriter
from datamodel_code_generator.parser._pipeline import decision_order

if TYPE_CHECKING:
    from datamodel_code_generator.parser._passes._state import FinishRun
    from datamodel_code_generator.parser._phases import DecisionPass
    from datamodel_code_generator.parser._pipeline import Runner
    from datamodel_code_generator.parser.generation import GenerationStore, GraphBuilder

__all__ = ["DecideHost", "decide"]


class DecideHost(Protocol):
    """The decision registry and the generation store of the parser that the decision phase reads and writes."""

    @property
    def _DECISION_PASSES(self) -> tuple[DecisionPass[Any], ...]:  # noqa: N802
        """The decision passes of the parser class, in registry order."""

    @property
    def _graph_builder(self) -> GraphBuilder:
        """The builder that changes the model graph through the generation store."""

    @property
    def generation_store(self) -> GenerationStore:
        """The generation store of the parse."""


def decide(parser: DecideHost, runner: Runner, run: FinishRun[Any, Any, Any]) -> None:
    """Seal the facts final at B1 and run the decisions of the registry on them, when it holds any."""
    if not (decisions := tuple(d for d in parser._DECISION_PASSES if d.when is None or d.when(parser))):  # noqa: SLF001
        return
    unused = {id(model) for model in run.unused_models}
    modules = [(ctx.module_key, [model for model in ctx.models if id(model) not in unused]) for ctx in run.contexts]
    facts = parser.generation_store.seal_facts(modules, decision_views())
    writer = _DecisionWriter(parser._graph_builder, viewed, _copy_data_type)  # noqa: SLF001
    for decision in decision_order(decisions):
        runner.run_decision(decision, facts, writer)
    run.decisions = writer.outcome(model for _, models in modules for model in models)
