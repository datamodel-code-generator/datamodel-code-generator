"""The areas of the debug checks, and the modes the parse that runs gives them."""

from __future__ import annotations

import json
import os
import sys
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

if sys.platform != "win32":

    def _append(path: Path, line: bytes) -> None:
        """Append the line to the file in one write, which ``O_APPEND`` puts at the end whole.

        A regular file takes the whole write, so the loop for the rest of a short write runs once.
        """
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
        try:
            while line:
                line = line[os.write(descriptor, line) :]
        finally:
            os.close(descriptor)

else:  # pragma: win32 cover
    import _winapi

    _FILE_APPEND_DATA: Final = 0x0004
    _FILE_SHARE_READ: Final = 0x0001
    _FILE_SHARE_WRITE: Final = 0x0002
    _OPEN_ALWAYS: Final = 4
    _FILE_ATTRIBUTE_NORMAL: Final = 0x0080

    def _append(path: Path, line: bytes) -> None:
        """Append the line to the file in one write through a handle that may only append.

        The CRT's ``O_APPEND`` seeks to the end and then writes, so two processes can write at the same offset; a handle
        opened with ``FILE_APPEND_DATA`` alone writes each write at the end of the file in one step. ``_winapi`` names
        neither that right nor the share, creation and attribute values, so they are the values of the Windows headers.
        A synchronous write to a file writes all of it or fails, so the loop for the rest of a short write runs once.
        """
        handle = _winapi.CreateFile(
            str(path),
            _FILE_APPEND_DATA | _winapi.SYNCHRONIZE,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE,
            _winapi.NULL,
            _OPEN_ALWAYS,
            _FILE_ATTRIBUTE_NORMAL,
            _winapi.NULL,
        )
        try:
            while line:
                written, _ = _winapi.WriteFile(handle, line)
                line = line[written:]
        finally:
            _winapi.CloseHandle(handle)


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
    """No reader writes a loaded document, every value a document record keeps for a pointer and every schema site the
    target bindings read again equal the documents then, and no string `$ref` of a site leads to no loaded schema
    (``schema_graph.py``); heki's ``MUT202`` holds the objects the schema graph validated once and shares."""

    MODULE_IMPORTS = "DATAMODEL_CODE_GENERATOR_CHECK_MODULE_IMPORTS"
    """The imports of every module, and those all modules share, equal the replay of their import ledger when the
    module's import text is written; the module writes for each model it names what its import plan, made from the
    final names, writes, and its imports bind every model of another module the plan imports. With a report file, it
    also records how many model names of each module it compared (``module_imports.py``)."""

    RENDER_PLANS = "DATAMODEL_CODE_GENERATOR_CHECK_RENDER_PLANS"
    """Every field render plan frozen at B2 writes or omits each item its field declares, and renders, when the
    modules are written, as the plan the field's facts give then; the type expression of the field's type hint frozen
    with it renders as the type hint then (``render_plans.py``)."""

    TEMPLATE_ASSIGNMENTS = "DATAMODEL_CODE_GENERATOR_CHECK_TEMPLATE_ASSIGNMENTS"
    """Every class-scope field line a model's built-in template renders after B2 binds the field's name exactly where
    the model's row of ``classify_assignment`` decides that the class body binds the field
    (``template_assignments.py``)."""

    NAMING = "DATAMODEL_CODE_GENERATOR_CHECK_NAMING"
    """The names the L1 resolver and the L2 settle give are a function of the resolver's naming calls: replayed on a
    copy of the resolver when ``name_models`` starts, they give every reference its name, and every model of the parse
    is named by a reference of the resolver (``naming.py``)."""

    FACT_FINALITY = "DATAMODEL_CODE_GENERATOR_CHECK_FACT_FINALITY"
    """Each pass changes only the facts it declares, on the models its scope allows, before they are final; after a
    freeze point, a write to the graph of facts frozen there fails where it is made (``finality.py``)."""

    PASS_TRIGGERS = "DATAMODEL_CODE_GENERATOR_CHECK_PASS_TRIGGERS"
    """A pass whose trigger is false runs anyway and changes nothing: no fact, memo revision, import, parser attribute,
    run-state field or warning (``finality.ParseChecks.idle_pass``). It needs ``FACT_FINALITY``, which opens the
    checks of a parse and compares the facts."""

    FACT_FINALITY_SWEEP = "DATAMODEL_CODE_GENERATOR_CHECK_FACT_FINALITY_SWEEP"
    """With ``FACT_FINALITY`` on, every model is compared after each global pass that transforms or repairs.

    It widens what ``FACT_FINALITY`` compares and reports nothing itself: ``1`` and ``report:<path>`` both turn it on.
    """


class CheckFailure(BaseException):
    """A violation of a debug check that raises: the one exception every area of the suite checks raises.

    It derives from ``BaseException``, neither from the package's ``Error`` nor from ``Exception``: generations
    catch ``Error`` to try another reading of the input or to write ``Error: <message>`` into their output, and
    others catch ``Exception``, so a failing check would become a stored output change that a fix run accepts
    instead of a failing test (#7184).
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

    def violation(self, message: str, **details: object) -> None:
        """Raise ``CheckFailure`` with the message, or append the message and its details to the report as a line.

        The message starts with the area's variable, so that it names the flag that found it.
        """
        text = f"{self.area.value}: {message}"
        if self.report_path is None:
            raise CheckFailure(text)
        self.record(text, **details)

    def record(self, message: str, **details: object) -> None:
        """Append the message and its details to the report as one JSON line; only a report mode calls it.

        Generations on threads and the generator processes a test starts append to one report at once, so the line is
        written whole in one append that the system puts at the end of the file.
        """
        line = f"{json.dumps({'area': self.area.name, 'message': message, **details}, ensure_ascii=False)}\n"
        _append(cast("Path", self.report_path), line.encode())


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
