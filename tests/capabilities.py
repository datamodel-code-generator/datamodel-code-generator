"""The capabilities a test may need of its environment: one table of names, each a predicate over facts with its reason.

A test that runs only where its environment has something says so by name, ``@needs("symlinks")`` (or in
``pytestmark``, or ``pytest.param(..., marks=needs(...))``), and is skipped elsewhere with the capability's reason.
The facts are those that expected-output overrides select (`tests.units.FACTS`: ``python``, ``formatter``, ``black``,
``isort``, ``platform``, ``pydantic``), read once from the running environment.

Each entry is ``name: (clauses, reason)``. The capability holds where every clause holds; a clause is one or more
alternatives joined by ``|``, each ``<fact><op><value>`` (``platform!=win32``, ``black>=24``,
``formatter==builtin|black>=24``; `tests.units.condition_problem`). The table is a literal, so
``scripts/check_source_rules.py`` reads it from the source without importing it: ``raw-skip`` counts every skip
outside this module, ``dead-capability`` fails on a capability that no lane of the CI matrix
(``.github/workflows/test.yaml``) has, and ``never-run`` on a test whose capabilities together exclude every lane.
"""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING

import pytest

from tests.conftest import expected_environment_facts
from tests.units import condition_holds

if TYPE_CHECKING:
    from collections.abc import Mapping

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
}
"""Every capability a test may need: its clauses over the environment facts and why a test needs it."""


def missing(names: tuple[str, ...], facts: Mapping[str, str]) -> list[str]:
    """Return the capabilities of ``names`` that an environment with ``facts`` lacks, each with its reason."""
    if unknown := sorted(set(names) - set(CAPABILITIES)):
        msg = f"unknown capabilities {unknown}: name one of CAPABILITIES in tests/capabilities.py, or add it there"
        raise ValueError(msg)
    return [
        f"{name}: {CAPABILITIES[name][1]}"
        for name in names
        if not all(condition_holds(clause, facts) for clause in CAPABILITIES[name][0])
    ]


@cache
def _facts() -> dict[str, str]:
    """Return the facts of the running environment, read once."""
    return expected_environment_facts()


def needs(*names: str) -> pytest.MarkDecorator:
    """Return the mark that skips a test where the running environment lacks any of the named capabilities."""
    lacking = missing(names, _facts())
    return pytest.mark.skipif(bool(lacking), reason="; ".join(lacking) or "every capability holds")
