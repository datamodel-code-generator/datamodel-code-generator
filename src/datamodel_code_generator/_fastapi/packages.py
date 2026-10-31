"""The packages a server package lives beside: the `services` package of the user's implementations, and others.

A server package imports the user's implementations from the package `services` in its parent package, or a top-level
`services` for a top-level server package. The renderer writes its stubs there, and the API generation refuses a
`services` package that another server package beside it, or a module or the models, would also be.
"""

from __future__ import annotations

from typing import Final

from datamodel_code_generator._module_paths import dotted_name

__all__ = ["SERVICES", "beside", "services_package"]

SERVICES: Final = "services"
"""The name of the package of the user's implementations, and of its directory beside the server package."""


def beside(package: str, name: str) -> str:
    """Return the dotted name of the package `name` in the parent package of `package`, top level for a top one."""
    return dotted_name(package.rpartition(".")[0], name)


def services_package(server: str) -> str:
    """Return the package of the user's implementations that the server package `server` imports."""
    return beside(server, SERVICES)
