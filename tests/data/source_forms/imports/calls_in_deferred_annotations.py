"""A call in an annotation that never runs (deferred by the future import, or function-local) imports nothing.

The same call as a run-time expression is a literal dynamic import.
"""

from __future__ import annotations

from importlib import import_module

deferred: import_module("pkg.deferred")
lazy: lambda: import_module("pkg.in_lambda")
loaded = import_module("pkg.loaded")


def local() -> None:
    inner: import_module("pkg.local") = None
    return inner
