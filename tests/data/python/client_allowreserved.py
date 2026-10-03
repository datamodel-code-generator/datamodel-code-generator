"""Send version-scoped path parameters through generated clients and the existing TLS fixture server."""

from __future__ import annotations

import ast
import importlib
import json
import sys
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

import datamodel_code_generator
from tests.data.python.client_generation import SOURCE, Modules, generate_client, render_client
from tests.data.python.client_runtime import _CALL_ID, Exchange, arecord, raw_response, record, run
from tests.data.python.generated_packages import forget_generated, import_generated

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import ModuleType

    import httpx2


def reserved_version_report(case: str, version: str, backends: Sequence[str], root: Path) -> tuple[Modules, str]:
    """Render and execute the existing path fixture with a shorthand or unknown YAML root version."""
    source = root / f"{case}.yaml"
    document = (SOURCE / source.name).read_text(encoding="utf-8")
    document = document.replace(document.partition("\n")[0], f"openapi: {version}", 1)
    source.write_text(document, encoding="utf-8")
    _, modules = render_client(source, root / "render", backends[0], {}, {})
    plans = {("client", "_operations.py"): modules["client", "_operations.py"]}
    reports: list[str] = []
    for backend in backends:
        package = f"{case.replace('-', '_')}_shorthand_{backend.replace('.', '_').lower()}"
        destination = root / backend.replace(".", "_")
        destination.mkdir()
        generate_client(source, destination, package, backend)
        paths = [str(destination), str(destination / package / "src")]
        sys.path[:0] = paths
        lines = [f"# {case} {backend}"]
        try:
            reserved_paths(import_generated(package), lines)
        finally:
            del sys.path[: len(paths)]
            forget_generated(package)
        reports.append(_CALL_ID.sub("<call>", "\n".join(lines)) + "\n")
    return plans, "".join(reports)


def reserved_adapted_report(version: str, root: Path) -> str:
    """Render and send fixed reserved-path vectors with public parameter adapter registrations."""
    recipe = json.loads((SOURCE / "allowreserved-path-adapters.json").read_text(encoding="utf-8"))
    source = root / "allowreserved-path-32.yaml"
    document = (SOURCE / source.name).read_text(encoding="utf-8")
    source.write_text(document.replace(document.partition("\n")[0], f"openapi: {version}", 1), encoding="utf-8")
    diagnostics, modules = render_client(source, root / "render", "pydantic_v2.BaseModel", {}, recipe["config"])
    lines = [f"# adapted paths OpenAPI {version}", f"  render diagnostics {diagnostics}"]
    for node in ast.walk(ast.parse(modules["client", "_generated", "model_bindings.py"])):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ParameterPlanView":
            values = {
                item.arg: ast.literal_eval(item.value)
                for item in node.keywords
                if item.arg in {"location", "name", "oas_version", "allow_reserved"}
            }
            lines.append(f"  rendered adapted plan {values}")
    package = "reserved_adapted"
    destination = root / "generated"
    destination.mkdir()
    generate_client(source, destination, package, "pydantic_v2.BaseModel", config=recipe["config"])
    generated_models = (destination / f"{package}_models.py").read_text(encoding="utf-8")
    lines.append(f"  rendered and generated models equal {modules['models.py',] == generated_models}")
    paths = [str(destination), str(destination / package / "src")]
    sys.path[:0] = paths
    try:
        api = import_generated(package)
        models = importlib.import_module(f"{package}_models")
        runtime = Path(importlib.import_module(f"{package}._runtime").__file__).resolve().parent
        lines.extend((
            f"  generated model origin {models.__file__ == str(destination / f'{package}_models.py')}",
            f"  generated client origin {Path(api.__file__).is_relative_to(destination)}",
            (
                f"  active distribution runtime origin "
                f"{runtime == Path(datamodel_code_generator.__file__).resolve().parent / '_runtime'}"
            ),
        ))
        data = json.loads((SOURCE / "allowreserved-path-vectors.json").read_text(encoding="utf-8"))
        data["vectors"] = [vector for vector in data["vectors"] if vector["label"] in recipe["labels"]]
        _paths(api, lines, data)
    finally:
        del sys.path[: len(paths)]
        forget_generated(package)
    return _CALL_ID.sub("<call>", "\n".join(lines)) + "\n"


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
    _paths(package, lines, data)


def _paths(package: ModuleType, lines: list[str], data: dict[str, Any]) -> None:
    """Send the supplied existing vectors through sync and async generated clients."""
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
