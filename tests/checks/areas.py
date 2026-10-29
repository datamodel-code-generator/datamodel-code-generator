"""The areas of the debug checks, and the modes the parse that runs gives them."""

from __future__ import annotations

import json
import os
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, cast, final

if TYPE_CHECKING:
    from collections.abc import Mapping

_OFF_VALUES: Final = frozenset({"", "0"})
_RAISE_VALUE: Final = "1"
_REPORT_PREFIX: Final = "report:"


class Area(Enum):
    """A family of debug checks; the value is the environment variable that sets its mode."""

    RENDER_MEMOS = "DATAMODEL_CODE_GENERATOR_CHECK_RENDER_MEMOS"
    """Every value a per-parse memo returns equals the value computed anew where it is read. With a report file, it
    also records the values of the tables with a legacy row (``memos.LEGACY_ROWS``: stale dedup keys, self references
    and nested models' required fields before nullability) that differ from the values computed anew."""

    SCHEMA_LOCATIONS = "DATAMODEL_CODE_GENERATOR_CHECK_SCHEMA_LOCATIONS"
    """Every model parsed from a JSON document has a schema location at the end of L1 (``schema_locations.py``)."""

    MODEL_GRAPH = "DATAMODEL_CODE_GENERATOR_CHECK_MODEL_GRAPH"
    """The model graph that the generation store records keeps its invariants (``model_graph.py``)."""

    SCHEMA_GRAPH = "DATAMODEL_CODE_GENERATOR_CHECK_SCHEMA_GRAPH"
    """No reader writes a schema object the schema graph validated once and shares (``schema_graph.py``)."""

    MODULE_IMPORTS = "DATAMODEL_CODE_GENERATOR_CHECK_MODULE_IMPORTS"
    """The imports of every module, and those all modules share, equal the replay of their import ledger when the
    module's import text is written; the module writes for each model it names what its import plan, made from the
    final names, writes, and its imports bind every model of another module the plan imports. With a report file, it
    also records how many model names of each module it compared (``module_imports.py``)."""

    RENDER_PLANS = "DATAMODEL_CODE_GENERATOR_CHECK_RENDER_PLANS"
    """Every field render plan frozen at B2 writes or omits each item its field declares, and renders, when the
    modules are written, as the plan the field's facts give then; the type expression of the field's type hint frozen
    with it renders as the type hint then (``render_plans.py``)."""

    STORE_WRITES = "DATAMODEL_CODE_GENERATOR_CHECK_STORE_WRITES"
    """After L1, data types, fields and models change their facts only inside store operations (``finality.py``)."""

    FACT_FINALITY = "DATAMODEL_CODE_GENERATOR_CHECK_FACT_FINALITY"
    """Each pass changes only the facts it declares, on the models its scope allows, before they are final; after a
    freeze point, a write to the graph of facts frozen there fails where it is made (``finality.py``)."""

    FACT_FINALITY_SWEEP = "DATAMODEL_CODE_GENERATOR_CHECK_FACT_FINALITY_SWEEP"
    """With ``FACT_FINALITY`` on, every model is compared after each global pass that transforms or repairs.

    It widens what ``FACT_FINALITY`` compares and reports nothing itself: ``1`` and ``report:<path>`` both turn it on.
    """


@final
@dataclass(frozen=True, slots=True)
class Mode:
    """How the current parse handles a violation in an area that is on."""

    area: Area
    report_path: Path | None
    """The JSON lines file that collects the violations, or None to raise at the first one."""
    state: dict[Any, Any] = field(default_factory=dict, compare=False)
    """What the area's wrappers keep for the parse."""

    def violation(self, error: type[Exception], message: str, **details: object) -> None:
        """Raise error with the message, or append the message and its details to the report as one JSON line.

        The message starts with the area's variable, so that it names the flag that found it.
        """
        text = f"{self.area.value}: {message}"
        if self.report_path is None:
            raise error(text)
        self.record(text, **details)

    def record(self, message: str, **details: object) -> None:
        """Append the message and its details to the report as one JSON line; only a report mode calls it."""
        with cast("Path", self.report_path).open("a", encoding="utf-8") as report:
            report.write(f"{json.dumps({'area': self.area.name, 'message': message, **details}, ensure_ascii=False)}\n")


MODES: Final[ContextVar[Mapping[Area, Mode] | None]] = ContextVar("check_modes", default=None)
"""The modes of the areas that are on in the parse that runs, or None outside a parse."""


def _read_mode(area: Area) -> Mode | None:
    value = os.environ.get(area.value, "")
    if value in _OFF_VALUES:
        return None
    if value == _RAISE_VALUE:
        return Mode(area, None)
    if value.startswith(_REPORT_PREFIX) and (path := value.removeprefix(_REPORT_PREFIX)):
        return Mode(area, Path(path))
    msg = f"{area.value}={value!r}: expected 0, 1 or report:<path>"
    raise ValueError(msg)


def read_modes() -> Mapping[Area, Mode]:
    """Read the mode of every area that is on, for one parse."""
    return {area: mode for area in Area if (mode := _read_mode(area)) is not None}


def mode(area: Area) -> Mode | None:
    """Return how the current parse handles violations in area, or None when the area is off or no parse runs."""
    return None if (modes := MODES.get()) is None else modes.get(area)
