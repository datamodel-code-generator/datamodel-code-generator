"""Services of the secured server whose custom application template leaves the authorize callback out."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from tests.data.python.fastapi_handlers import security

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from types import ModuleType

services = security.services


def builds(server: ModuleType, models: ModuleType, calls: list[str]) -> Iterator[tuple[str, Callable[[], object]]]:
    """Yield the builders, which never reach a request: the secured routes find no authorize callback."""
    default = services(server, models, calls)["default"]
    authorize = security.settings(server, models, calls)["default"]["authorize"]
    yield "build_router", partial(server.build_router, **default, authorize=authorize)
    yield "create_app", partial(server.create_app, **default, authorize=authorize)
    yield "group public", partial(server.routers.public.build_router, public=default["public"])
