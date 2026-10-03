"""Send version-scoped path parameters through generated clients and the existing TLS fixture server."""

from __future__ import annotations

import importlib
import json
from functools import partial
from typing import TYPE_CHECKING, Any

from tests.data.python.client_generation import SOURCE
from tests.data.python.client_runtime import Exchange, arecord, raw_response, record, run

if TYPE_CHECKING:
    from types import ModuleType

    import httpx2


def _arguments(package: ModuleType, vector: dict[str, Any]) -> dict[str, object]:
    """Construct public parameter snapshots with each generated backend's request codec."""
    types = importlib.import_module(f"{package.__name__}.types.wire")
    codec = getattr(types, f"{''.join(part.title() for part in vector['method'].split('_'))}RequestCodecs")
    return {
        item["argument"]: codec.parameter(location=item["location"], name=item["name"]).from_wire(item["value"])
        for item in vector["parameters"]
    }


def _response(lines: list[str], request: httpx2.Request) -> httpx2.Response:
    """Record the received logical authority and target before returning the declared bodyless success."""
    lines.append(
        f"  received url {request.url} host {request.headers['host']} target {request.url.raw_path!r} status 204"
    )
    return raw_response(204)(request)


def reserved_paths(package: ModuleType, lines: list[str]) -> None:
    """Exercise schema/style flags and unchanged query, content, header and cookie controls in both clients."""
    data = json.loads((SOURCE / "allowreserved-path-vectors.json").read_text(encoding="utf-8"))
    lines.append(f"  configured origin {data['origin']}")
    vectors = [(vector, _arguments(package, vector)) for vector in data["vectors"]]
    exchange = Exchange(lines)
    with exchange.client() as native, package.Client(http_client=native) as api:
        for vector, arguments in vectors:
            exchange.respond(partial(_response, lines))
            record(lines, f"sync {vector['label']}", partial(getattr(api.wire, vector["method"]), **arguments))
            lines.append(f"  requests {1 - len(exchange.responders)}")
            exchange.responders.clear()
    run(lambda: _async_paths(package, vectors, lines))


async def _async_paths(
    package: ModuleType, vectors: list[tuple[dict[str, Any], dict[str, object]]], lines: list[str]
) -> None:
    exchange = Exchange(lines)
    async with exchange.async_client() as native, package.AsyncClient(http_client=native) as api:
        for vector, arguments in vectors:
            exchange.respond(partial(_response, lines))
            await arecord(lines, f"async {vector['label']}", partial(getattr(api.wire, vector["method"]), **arguments))
            lines.append(f"  requests {1 - len(exchange.responders)}")
            exchange.responders.clear()
