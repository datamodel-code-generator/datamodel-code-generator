"""Install the debug checks of the test suite (``tests/checks``) in a Python that a test starts.

``tests/checks/plugin.py`` puts this directory on the ``PYTHONPATH`` of the test run and names the package directory
the checks are for in ``DATAMODEL_CODE_GENERATOR_CHECKS_PACKAGE``. In a Python that would import that package, the
seams of each module that ``tests/checks/seams.py`` names are wrapped as soon as the module has run, so that a run
imports nothing more than it would; the ``DATAMODEL_CODE_GENERATOR_CHECK_<AREA>`` variables turn the checks on as in
the test process. Another installation of the package, such as a released one, runs unchecked.
"""

from __future__ import annotations

import os
import sys
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence
    from importlib.machinery import ModuleSpec
    from types import ModuleType


class _SeamFinder:
    """Finds a module that defines or holds a seam with the finders after it, and wraps its seams once it has run."""

    def __init__(self, modules: frozenset[str]) -> None:
        self.modules = modules

    def find_spec(self, name: str, path: Sequence[str] | None, target: ModuleType | None = None) -> ModuleSpec | None:
        if name not in self.modules:
            return None
        for finder in sys.meta_path[sys.meta_path.index(self) + 1 :]:
            if (spec := finder.find_spec(name, path, target)) is not None:
                if spec.loader is not None:
                    spec.loader.exec_module = _applying(spec.loader.exec_module)
                return spec
        return None


def _applying(exec_module: Any) -> Any:
    def exec_and_wrap(module: ModuleType) -> None:
        exec_module(module)
        from tests.checks.seams import apply

        apply(module)

    return exec_and_wrap


def _install() -> None:
    if (package := os.environ.get("DATAMODEL_CODE_GENERATOR_CHECKS_PACKAGE")) is None:
        return
    spec = find_spec("datamodel_code_generator")
    if spec is None or spec.origin is None or str(Path(spec.origin).resolve().parent) != package:
        return
    sys.path.append(str(Path(__file__).resolve().parents[3]))
    from tests.checks.seams import MODULES

    sys.meta_path.insert(0, _SeamFinder(MODULES))


_install()
