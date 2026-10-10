"""Import generated packages with this checkout's runtime sources, and remove them from the module cache again.

A generated package's model codec runtime is this checkout's model codec runtime itself, under its own module names,
so coverage measured by package name records it however a test imports the package. `import_generated_codecs`
imports only the model codec modules, without running the package module, so a server package needs no framework.
"""

from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from datamodel_code_generator import _runtime

if TYPE_CHECKING:
    from collections.abc import Iterator


def import_generated(package: str, *, copied: bool = False) -> ModuleType:
    """Import a generated package with its copied runtime or this checkout's covered runtime sources."""
    if copied:
        return importlib.import_module(package)
    runtime = Path(_runtime.__file__).parent
    spec = importlib.util.spec_from_file_location(
        f"{package}._runtime", runtime / "__init__.py", submodule_search_locations=[str(runtime)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(package)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _share_codecs(package)
    return importlib.import_module(package)


def _shell(name: str, path: Path) -> None:
    module = ModuleType(name)
    module.__path__ = [str(path)]
    sys.modules[name] = module


def _codec_runtime() -> Iterator[tuple[str, ModuleType]]:
    """Yield this checkout's model codec runtime modules with their names relative to the runtime package."""
    name = f"{_runtime.__name__}.model_codecs"
    codecs = importlib.import_module(name)
    yield "model_codecs", codecs
    for item in pkgutil.walk_packages(codecs.__path__, f"{name}."):
        yield item.name.removeprefix(f"{_runtime.__name__}."), importlib.import_module(item.name)


def import_generated_codecs(package: str, root: Path) -> None:
    """Make a generated package's model codec modules importable without running its package module.

    The package and its runtime become bare packages over their directories, so `<package>._generated.model_bindings`
    and `<package>.model_codecs` import against this checkout's model codec runtime.
    """
    _shell(package, root / package)
    _shell(f"{package}._runtime", Path(_runtime.__file__).parent)
    _share_codecs(package)


def _share_codecs(package: str) -> None:
    """Resolve a generated package's model codec runtime modules to this checkout's own modules."""
    for name, module in _codec_runtime():
        sys.modules[f"{package}._runtime.{name}"] = module


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
