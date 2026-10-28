"""The pytest plugin of the debug checks: installs them in the test process and in every Python a test starts.

Under ``--codspeed`` it installs nothing, so that benchmarks measure what production runs.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Final

import datamodel_code_generator
from tests.checks.installer import install

if TYPE_CHECKING:
    import pytest

CHILD_SITE: Final = Path(__file__).parent / "child"
"""The directory of the ``sitecustomize`` that installs the checks in a Python a test starts."""
PACKAGE_ENV: Final = "DATAMODEL_CODE_GENERATOR_CHECKS_PACKAGE"
"""The directory of the package that a Python a test starts installs the checks for, when it imports that package."""


def pytest_configure(config: pytest.Config) -> None:
    """Install the checks, and give the Pythons that tests start the ``sitecustomize`` that installs them there."""
    if config.getoption("codspeed", default=False):  # pragma: no cover - benchmark runs measure the package unchecked
        return
    install()
    os.environ[PACKAGE_ENV] = str(Path(datamodel_code_generator.__file__).resolve().parent)
    os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, (str(CHILD_SITE), os.environ.get("PYTHONPATH"))))
