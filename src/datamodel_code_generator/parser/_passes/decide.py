"""The decision phase (L6, ``DECIDE``): the decisions of the parser's registry over the facts final at B1.

``decide`` is called from the pipeline tables of ``parser/_passes/_tables.py`` once the generic base class is
applied; the parser comes in through ``DecideHost``, which ``Parser`` satisfies structurally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from typing_extensions import Protocol

from datamodel_code_generator.parser._passes._shared import (
    GenerationStoreHost,
    ReplacedRootsHost,
    TargetPythonVersionHost,
    run_unused_models,
)
from datamodel_code_generator.parser._phases import DecisionOptions, _DecisionWriter
from datamodel_code_generator.types import _copy_data_type

if TYPE_CHECKING:
    from datamodel_code_generator.config import ParserConfig
    from datamodel_code_generator.model.output import OutputModelContext
    from datamodel_code_generator.parser._passes._state import FinishRun
    from datamodel_code_generator.parser._phases import DecisionPass
    from datamodel_code_generator.parser._pipeline import Runner
    from datamodel_code_generator.types import DataTypeManager

__all__ = ["DecideHost", "decide"]


class DecideHost(ReplacedRootsHost, GenerationStoreHost, TargetPythonVersionHost, Protocol):
    """The decision registry, the generation store and the type manager of the parser that the decision phase reads."""

    @property
    def _DECISION_PASSES(self) -> tuple[DecisionPass[Any], ...]:  # noqa: N802
        """The decision passes of the parser class, in registry order."""

    @property
    def data_type_manager(self) -> DataTypeManager:
        """The type manager of the output model."""

    @property
    def _output_model_context(self) -> OutputModelContext:
        """The capabilities of the output model types."""

    @property
    def use_tuple_for_closed_arrays(self) -> bool:
        """Whether a closed array is written as a tuple."""

    @property
    def config(self) -> ParserConfig:
        """The configuration of the parser."""


def decide(parser: DecideHost, runner: Runner, run: FinishRun[Any, Any, Any]) -> None:
    """Seal the facts final at B1 and run the decisions of the registry on them in its order, when it holds any."""
    if not (decisions := tuple(d for d in parser._DECISION_PASSES if d.when is None or d.when(parser))):  # noqa: SLF001
        return
    unused = run_unused_models(parser, run)
    modules = [(ctx.module_key, unused.written(ctx.models)) for ctx in run.contexts]
    keywords = parser.data_type_manager.member_constraint_keywords
    version = parser.target_python_version.version_key
    facts = parser.generation_store.seal_facts(
        modules,
        lambda data_type, model_of: keywords(data_type, version, model_of),
        DecisionOptions(
            keeps_tuples=parser.use_tuple_for_closed_arrays,
            user_imports=bool(parser.config.additional_imports),
            decodes_value_unions=parser._output_model_context.decodes_value_unions,  # noqa: SLF001
        ),
    )
    writer = _DecisionWriter(parser._graph_builder, _copy_data_type)  # noqa: SLF001
    for decision in decisions:
        runner.run_decision(decision, facts, writer)
    run.decisions = writer.outcome(model for _, models in modules for model in models)
