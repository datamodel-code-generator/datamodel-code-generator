"""Import generated packages with this checkout's runtime sources, and remove them from the module cache again."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from datamodel_code_generator import _runtime

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType


def import_generated(package: str) -> ModuleType:
    """Import a generated package whose private runtime resolves to this checkout's runtime sources."""
    runtime = Path(_runtime.__file__).parent
    spec = importlib.util.spec_from_file_location(
        f"{package}._runtime", runtime / "__init__.py", submodule_search_locations=[str(runtime)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(package)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return importlib.import_module(package)


def forget_generated(package: str) -> None:
    """Remove a generated package, its runtime, and its models from the module cache."""
    for name in [
        name for name in sys.modules if name.startswith((f"{package}.", f"{package}_models")) or name == package
    ]:
        del sys.modules[name]


@contextmanager
def generated_root(root: Path, *packages: str) -> Iterator[None]:
    """Put a generation root on the import path, then remove it and forget the packages imported from it."""
    sys.path.insert(0, str(root))
    try:
        yield
    finally:
        sys.path.remove(str(root))
        for package in packages:
            forget_generated(package)
