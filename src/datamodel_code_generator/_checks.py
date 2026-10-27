"""Debug checks that one environment variable per area turns on.

`DATAMODEL_CODE_GENERATOR_CHECK_<AREA>` unset, empty or `0` turns the area off, as in production; `1` raises at the
first violation; `report:<path>` appends every violation to `<path>` as one JSON line and goes on. `Parser.parse`
reads every variable once, at its start, with `read_check_flags`; the code it runs asks `check_mode(area)`.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, final

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping

_OFF_VALUES: Final = frozenset({"", "0"})
_RAISE_VALUE: Final = "1"
_REPORT_PREFIX: Final = "report:"


class CheckArea(Enum):
    """A family of debug checks; the value is the environment variable that sets its mode."""

    SCHEMA_LOCATIONS = "DATAMODEL_CODE_GENERATOR_CHECK_SCHEMA_LOCATIONS"
    """Every model parsed from a JSON document has a schema location at the end of L1."""
    MODULE_IMPORTS = "DATAMODEL_CODE_GENERATOR_CHECK_MODULE_IMPORTS"
    """The imports of every module, and those all modules share, equal the replay of their import ledger when the
    module's import text is written; the module writes for each model it names what its import plan, made from the
    final names, writes, and its imports bind every model of another module the plan imports. With a report file, it
    also records how many model names of each module it compared."""

    MODEL_GRAPH = "DATAMODEL_CODE_GENERATOR_CHECK_MODEL_GRAPH"
    """Collapse stops before it would build a data type cycle; the store never meets a copied Reference; every model
    parsed from a JSON document has an owner or is a root at the end of L1; the models that an order or a graph is
    built over by model key never share a key, a reference whose key names one of them names it by its path too, and
    a lookup by path, for the references whose key names none of them, never names two or more; every answer of a
    reference graph snapshot equals the computation it replaced; a refresh that records only the models store
    operations noted records the facts and reference sites a full rebuild would; the models the output writes are
    the store's emitted models. With a report file, it also records the root-collapse decisions whose answer over the
    emitted models differs."""

    SCHEMA_GRAPH = "DATAMODEL_CODE_GENERATOR_CHECK_SCHEMA_GRAPH"
    """Each `$ref` edge of the schema graph gets one key and names the target the parser loads; under request-response
    read/write models with --dedupe-request-response-variants, a model whose key names its schema splits exactly when
    the key needs variants, and no other model splits after a reference to it read that it does not; no reader writes a
    schema object the graph validated once and shares; each reserved target has the same value in the prepared tree it
    is parsed from as in a fresh load of its source. With a report file, it also records the `$dynamicRef` and
    `$recursiveRef` answers from the anchors L1 has registered that differ from the answers of the document's anchor
    tables."""

    STORE_WRITES = "DATAMODEL_CODE_GENERATOR_CHECK_STORE_WRITES"
    """After L1, data types, fields and models change their facts only inside store operations."""

    GENERATED_CODE_REQUIREMENTS = "DATAMODEL_CODE_GENERATOR_CHECK_GENERATED_CODE_REQUIREMENTS"
    """Every Pydantic ``ConfigDict`` key that the generator can write has a row in ``_requirements.py``."""

    RENDER_PLANS = "DATAMODEL_CODE_GENERATOR_CHECK_RENDER_PLANS"
    """Every field render plan frozen at B2 writes or omits each item its field declares, and renders, when the
    modules are written, as the plan the field's facts give then; the type expression of the field's type hint frozen
    with it renders as the type hint then."""

    RENDER_MEMOS = "DATAMODEL_CODE_GENERATOR_CHECK_RENDER_MEMOS"
    """Every value a per-parse memo returns equals the value computed anew where it is read. With a report file, it
    also records the values of the tables with a legacy row (``_parse_memo.LEGACY_ROWS``: stale dedup keys, self
    references and nested models' required fields before nullability) that differ from the values computed anew."""

    FACT_FINALITY = "DATAMODEL_CODE_GENERATOR_CHECK_FACT_FINALITY"
    """Each pass changes only the facts it declares, on the models its scope allows, before they are final; after a
    freeze point, a write to the graph of facts frozen there fails where it is made."""

    FACT_FINALITY_SWEEP = "DATAMODEL_CODE_GENERATOR_CHECK_FACT_FINALITY_SWEEP"
    """With ``FACT_FINALITY`` on, every model is compared after each global pass that transforms or repairs.

    It widens what ``FACT_FINALITY`` compares and reports nothing itself: ``1`` and ``report:<path>`` both turn it on.
    """


@final
@dataclass(frozen=True, slots=True)
class CheckMode:
    """How the current parse handles a violation in an area that is on."""

    area: CheckArea
    report_path: Path | None
    """The JSON lines file that collects the violations, or None to raise at the first one."""

    def violation(self, error: type[Exception], message: str, **details: object) -> None:
        """Raise error with the message, or append the message and its details to the report as one JSON line.

        The message starts with the area's variable, so that it names the flag that found it.
        """
        text = f"{self.area.value}: {message}"
        if self.report_path is None:
            raise error(text)
        self.record(text, **details)

    def record(self, message: str, **details: object) -> None:
        """Append the message and its details to the report as one JSON line; with no report file, do nothing."""
        if self.report_path is None:
            return
        with self.report_path.open("a", encoding="utf-8") as report:
            report.write(f"{json.dumps({'area': self.area.name, 'message': message, **details}, ensure_ascii=False)}\n")


_NO_CHECKS: Final[Mapping[CheckArea, CheckMode]] = MappingProxyType({})
CHECK_MODES: Final[ContextVar[Mapping[CheckArea, CheckMode]]] = ContextVar("check_modes", default=_NO_CHECKS)


def _read_mode(area: CheckArea) -> CheckMode | None:
    value = os.environ.get(area.value, "")
    if value in _OFF_VALUES:
        return None
    if value == _RAISE_VALUE:
        return CheckMode(area, None)
    if value.startswith(_REPORT_PREFIX) and (path := value.removeprefix(_REPORT_PREFIX)):
        return CheckMode(area, Path(path))
    msg = f"{area.value}={value!r}: expected 0, 1 or report:<path>"
    raise ValueError(msg)


@contextmanager
def read_check_flags() -> Generator[None, None, None]:
    """Read the mode of every area once and make those modes current for the code run inside."""
    modes = {area: mode for area in CheckArea if (mode := _read_mode(area)) is not None}
    token = CHECK_MODES.set(MappingProxyType(modes))
    try:
        yield
    finally:
        CHECK_MODES.reset(token)


def check_mode(area: CheckArea) -> CheckMode | None:
    """Return how the current parse handles violations in area, or None when the area is off or no parse runs."""
    return CHECK_MODES.get().get(area)
