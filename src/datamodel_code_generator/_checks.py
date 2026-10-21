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
    module's import text is written."""

    MODEL_GRAPH = "DATAMODEL_CODE_GENERATOR_CHECK_MODEL_GRAPH"
    """Collapse stops before it would build a data type cycle; the store never meets a copied Reference; every model
    parsed from a JSON document has an owner or is a root at the end of L1; the models that an order or a graph is
    built over by model key never share a key, a reference whose key names one of them names it by its path too, and
    a lookup by path, for the references whose key names none of them, never names two or more; every answer of a
    reference graph snapshot equals the computation it replaced; a refresh that records only the models store
    operations noted records the facts a full rebuild would."""

    SCHEMA_GRAPH = "DATAMODEL_CODE_GENERATOR_CHECK_SCHEMA_GRAPH"
    """The schema graph registers the resources of each document `Parser.parse` prepares as the old walk did, and each
    `$ref` edge gets one key and names the target the parser loads."""

    STORE_WRITES = "DATAMODEL_CODE_GENERATOR_CHECK_STORE_WRITES"
    """After L1, data types, fields and models change their facts only inside store operations."""

    GENERATED_CODE_REQUIREMENTS = "DATAMODEL_CODE_GENERATOR_CHECK_GENERATED_CODE_REQUIREMENTS"
    """Every Pydantic ``ConfigDict`` key that the generator can write has a row in ``_requirements.py``."""

    FACT_FINALITY = "DATAMODEL_CODE_GENERATOR_CHECK_FACT_FINALITY"
    """Each pass changes only the facts it declares, on the models its scope allows, before they are final."""

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
_CHECK_MODES: Final[ContextVar[Mapping[CheckArea, CheckMode]]] = ContextVar("check_modes", default=_NO_CHECKS)


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
    token = _CHECK_MODES.set(MappingProxyType(modes))
    try:
        yield
    finally:
        _CHECK_MODES.reset(token)


def check_mode(area: CheckArea) -> CheckMode | None:
    """Return how the current parse handles violations in area, or None when the area is off or no parse runs."""
    return _CHECK_MODES.get().get(area)
