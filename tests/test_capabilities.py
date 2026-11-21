"""The capability table: how a clause reads the environment facts, and the mark a test gets from it."""

from __future__ import annotations

import pytest

from tests import capabilities
from tests.conftest import assert_output, expected_environment_facts
from tests.units import condition_holds, condition_problem

FACTS = {
    "python": "3.12",
    "formatter": "external",
    "black": "24.1.0",
    "isort": "",
    "platform": "linux",
    "pydantic": "2.0.3",
}
"""A lane with black 24.1 and Pydantic 2.0.3 on Linux, without isort; set explicitly, never read from here."""
CLAUSES = (
    "platform!=win32",
    "platform==win32",
    "python>=3.12",
    "python<3.12",
    "python>3.11",
    "python<=3.11",
    "black>=24",
    "black>=24.2",
    "black==24",
    "black==22",
    "black!=22",
    "pydantic>=2.5",
    "pydantic==2.0",
    "isort>=6",
    "isort==6",
    "isort!=6",
    "formatter==builtin|black>=24",
    "formatter==builtin|black<24",
)
MALFORMED = (
    "platform>=win32",
    "python>=3.x",
    "node==20",
    "python=3.12",
    "platform==beos",
    "black>=24|formatter=builtin",
)


def test_clauses_hold_by_release_series() -> None:
    """``==`` matches leading components, an order compares the release cut to the value's length, empty is absent."""
    assert_output("".join(f"{clause}: {condition_holds(clause, FACTS)}\n" for clause in CLAUSES))


def test_malformed_clauses_say_why() -> None:
    """A clause with an unknown fact, a value of the wrong shape or an order on a fact that is no version is refused."""
    lines = [f"{clause}: {condition_problem(clause)}\n" for clause in (*MALFORMED, *CLAUSES)]
    assert_output("".join(lines))


@pytest.mark.parametrize(
    "names",
    [
        ("posix",),
        ("posix", "black-before-24", "type-statements"),
        ("black-before-24", "native-deferred-annotations"),
        ("gpu",),
    ],
)
def test_missing_names_the_capabilities_an_environment_lacks(names: tuple[str, ...]) -> None:
    """Each capability the facts lack is named with its reason; a name outside the table is refused."""
    try:
        found = capabilities.missing(names, FACTS)
    except ValueError as error:
        found = [f"ValueError: {error}"]
    assert_output("".join(f"{line}\n" for line in found) or "none missing\n")


def test_needs_skips_where_the_running_environment_lacks_a_capability() -> None:
    """The mark skips exactly where `missing` names a capability of the running environment, with its reasons."""
    names = ("posix", "black-before-24")
    mark = capabilities.needs(*names).mark
    lacking = capabilities.missing(names, expected_environment_facts())
    agrees = mark.args == (bool(lacking),) and mark.kwargs["reason"] == ("; ".join(lacking) or "every capability holds")
    assert_output(f"mark: {mark.name}; agrees with missing: {agrees}\n")
