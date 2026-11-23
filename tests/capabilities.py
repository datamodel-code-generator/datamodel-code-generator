"""The facts of a test's environment a test may need or its output may vary with: one table of names, each a predicate.

A test that runs only where its environment has something says so by name, ``@needs("symlinks")`` (or in
``pytestmark``, or ``pytest.param(..., marks=needs(...))``), and is skipped elsewhere with the capability's reason. A
comparison whose output depends on the environment names the deciding facts the same way, ``assert_output(text,
varies_with=("windows",))``: its stored lines key on the clauses of those entries that hold (`clauses_of`). The facts
are those of `tests.units.FACTS` (``python``, ``formatter``, ``black``, ``isort``, ``platform``, ``pydantic``,
``heki``; a tool's is its installed version, empty where it is not installed), read from the running environment
(`expected_environment_facts`).

Each entry is ``name: (clauses, reason)``. The capability holds where every clause holds; a clause is one or more
alternatives joined by ``|``, each ``<fact><op><value>`` (``platform!=win32``, ``black>=24``,
``formatter==builtin|black>=24``; `tests.units.condition_problem`). The table is a literal, so
``scripts/check_source_rules.py`` reads it from the source without importing it: ``raw-skip`` counts every skip
outside this module, ``dead-capability`` fails on a capability that no lane of the CI matrix
(``.github/workflows/test.yaml``) has, and ``never-run`` on a test whose capabilities together exclude every lane.
"""

from __future__ import annotations

import os
import sys
from functools import cache
from typing import TYPE_CHECKING

import pytest

from tests.units import condition_holds

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

TEST_DEFAULT_FORMATTER_ENV = "DATAMODEL_CODE_GENERATOR_TEST_DEFAULT_FORMATTER"
BUILTIN_FORMATTER_VALUE = "builtin"
_PLATFORMS = ("linux", "darwin", "win32")

CAPABILITIES: dict[str, tuple[tuple[str, ...], str]] = {
    "posix": (
        ("platform!=win32",),
        "the test uses a POSIX facility: fork, signals, resource limits, file modes, dir_fd or named pipes",
    ),
    "symlinks": (("platform!=win32",), "Windows creates symlinks and hard links only with elevated privileges"),
    "linux": (("platform==linux",), "RLIMIT_DATA caps the memory of a run on Linux only"),
    "type-statements": (("python>=3.12",), "type statements and type parameter syntax (PEP 695) need Python 3.12"),
    "fstring-nested-syntax": (("python>=3.12",), "a comment in an f-string's replacement field needs Python 3.12"),
    "sys-monitoring": (
        ("python>=3.12",),
        "sys.monitoring, which the comparator and the legacy-row witnesses measure runs with, needs Python 3.12",
    ),
    "no-sys-monitoring": (("python<3.12",), "Python 3.11 has no sys.monitoring: the comparator measures nothing there"),
    "branch-events": (
        ("python>=3.14",),
        "sys.monitoring reports each way of a branch (BRANCH_LEFT, BRANCH_RIGHT), which keys a line's arms, from 3.14",
    ),
    "no-branch-events": (
        ("python>=3.12", "python<3.14"),
        "Python 3.12 and 3.13 report no branch's ways apart: a cell catalog keys lines there",
    ),
    "typed-dict-validation": (("python>=3.12",), "Pydantic validates typing.TypedDict from Python 3.12 on"),
    "native-deferred-annotations": (("python>=3.14",), "Python defers annotations natively from 3.14 on"),
    "target-newer-than-runtime": (("python<3.14",), "the test needs a target Python version newer than the runtime"),
    "black-before-24": (("black<24",), "the test checks how black before 24 formats the output"),
    "black-before-24.3": (
        ("black<24.3",),
        "black before 24.3.0 is below the version datamodel-code-generator will require; generation says so",
    ),
    "black-without-py311-target": (
        ("black<22.6",),
        "black before 22.6.0 has no Python 3.11 target version (TargetVersion.PY311), so it cannot format for 3.11",
    ),
    "isort-before-6": (("isort<6",), "isort before 6 is below the version datamodel-code-generator will require"),
    "pydantic-before-2.5": (
        ("pydantic<2.5",),
        (
            "Pydantic 2.0.3 builds and validates models differently from 2.5.3; no lane pins 2.1 to 2.4, so the "
            "release in between that changed it is unknown and the bound is the first pinned release that agrees "
            "with later ones"
        ),
    ),
    "pydantic-before-2.8.2": (
        ("pydantic<2.8.2",),
        "Pydantic before 2.8.2 is below the version datamodel-code-generator will require; generation says so",
    ),
    "no-type-statements": (
        ("python<3.12",),
        "Python 3.11 has no type statements (PEP 695): their errors read differently there",
    ),
    "windows": (("platform==win32",), "Windows reports paths, processes and network errors in its own way"),
    "heki": (
        ("heki>=0",),
        "the test runs the heki binary, which only the heki dependency group installs (tox run -e heki, or dev)",
    ),
}
"""Every capability a test may need or its output may vary with: its clauses over the facts and why."""


def expected_environment_facts() -> dict[str, str]:
    """Return the facts of this environment (`tests.units.FACTS`) that clauses test (``black<24``)."""
    from importlib.metadata import distributions

    builtin = os.environ.get(TEST_DEFAULT_FORMATTER_ENV) == BUILTIN_FORMATTER_VALUE
    return {
        "python": f"{sys.version_info[0]}.{sys.version_info[1]}",
        "formatter": BUILTIN_FORMATTER_VALUE if builtin else "external",
        **{tool: next((found.version for found in distributions(name=tool)), "") for tool in ("black", "isort")},
        "platform": next((name for name in _PLATFORMS if sys.platform.startswith(name)), sys.platform),
        "pydantic": next((found.version for found in distributions(name="pydantic")), ""),
        "heki": next((found.version for found in distributions(name="heki")), ""),
    }


def _known(names: Iterable[str]) -> None:
    """Raise for a name that is no capability of the table."""
    if unknown := sorted(set(names) - set(CAPABILITIES)):
        msg = f"unknown capabilities {unknown}: name one of CAPABILITIES in tests/capabilities.py, or add it there"
        raise ValueError(msg)


def missing(names: tuple[str, ...], facts: Mapping[str, str]) -> list[str]:
    """Return the capabilities of ``names`` that an environment with ``facts`` lacks, each with its reason."""
    _known(names)
    return [
        f"{name}: {CAPABILITIES[name][1]}"
        for name in names
        if not all(condition_holds(clause, facts) for clause in CAPABILITIES[name][0])
    ]


def clauses_of(names: Iterable[str]) -> tuple[str, ...]:
    """Return the condition clauses of the named capabilities, sorted and unique: what a comparison varies with."""
    names = tuple(names)
    _known(names)
    return tuple(sorted({clause for name in names for clause in CAPABILITIES[name][0]}))


@cache
def _facts() -> dict[str, str]:
    """Return the facts of the running environment, read once."""
    return expected_environment_facts()


def needs(*names: str) -> pytest.MarkDecorator:
    """Return the mark that skips a test where the running environment lacks any of the named capabilities."""
    lacking = missing(names, _facts())
    return pytest.mark.skipif(bool(lacking), reason="; ".join(lacking) or "every capability holds")
