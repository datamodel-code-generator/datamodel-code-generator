"""Call generated clients over TLS with path arguments at URL normalization boundaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from tests.data.python.client_pagination import Harness, users
from tests.data.python.client_runtime import Exchange, run

if TYPE_CHECKING:
    from types import ModuleType

SOURCE = Path(__file__).parents[1] / "generation_platform/fastapi"


def path_segments(package: ModuleType, lines: list[str]) -> None:
    """Preserve the path argument in ordinary calls and first pages in both execution modes."""
    harness = Harness(package)
    values = json.loads((SOURCE / "http-boundaries.json").read_text(encoding="utf-8"))["paths"]
    exchange = Exchange(lines)
    with exchange.client() as http, package.Client(http_client=http) as api:
        for call in (api.folders.list_folder, api.protocols.folders.all.page):
            for value in values:
                exchange.respond(users("1"))
                argument = harness.argument("folders", "ListFolder", "path", "folder", value)
                call(folder=argument)
                lines.append(f"path {value!r} completed")

    async def asynchronous() -> None:
        exchange = Exchange(lines)
        async with exchange.async_client() as http, package.AsyncClient(http_client=http) as api:
            for call in (api.folders.list_folder, api.protocols.folders.all.page):
                for value in values:
                    exchange.respond(users("1"))
                    argument = harness.argument("folders", "ListFolder", "path", "folder", value)
                    await call(folder=argument)
                    lines.append(f"async path {value!r} completed")

    run(asynchronous)


