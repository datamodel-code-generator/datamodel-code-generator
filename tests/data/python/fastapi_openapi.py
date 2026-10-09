"""Serve the OpenAPI documents of generated FastAPI packages in applications of every shape, and report them."""

from __future__ import annotations

import inspect
import json
import typing
import warnings
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Final, TypeAlias

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from tests.data.python.fastapi_server import _generate, _import
from tests.data.python.generated_packages import forget_generated

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

PACKAGES: Final[dict[str, dict[str, Any]]] = {
    "pets": {"input": "pets.yaml"},
    "items": {"input": "parameters.yaml"},
    "secured": {"input": "server-security.yaml"},
    "bodies": {"input": "bodies.yaml", "config": {"body_modes": [["/paths/~1raw/post", "request"]]}},
    "calls": {"input": "callbacks.yaml"},
    "methods": {"input": "methods.yaml"},
    "names": {"input": "names.yaml"},
    "references": {
        "input": "references.yaml",
        "files": ["references-library.yaml", "references-metadata.yaml"],
        "config": {"layout": "single"},
    },
    "selected": {"input": "pets.yaml", "model": {"openapi_include_paths": ["/pets/{petId}"]}},
    "hooks": {"input": "served-hooks.yaml", "files": ["served-event.yaml", "served-library.yaml"]},
    "streams": {"input": "served-items.yaml", "files": ["served-items-library.yaml", "events/stamp.yaml"]},
    "resources": {"input": "served-resources.yaml"},
    "legacy": {
        "input": "served-legacy.yaml",
        "files": ["served-event.yaml"],
        "model": {"openapi_include_paths": ["/pets", "/uses"]},
    },
}
Scenario: TypeAlias = Callable[[dict[str, Any]], list[str]]
SCENARIOS: dict[str, tuple[tuple[str, ...], Scenario]] = {}


def _scenario(*packages: str) -> Callable[[Scenario], Scenario]:
    def register(function: Scenario) -> Scenario:
        SCENARIOS[function.__name__.replace("_", "-")] = (packages, function)
        return function

    return register


def _stand_in(protocol: type) -> object:
    """Return an instance of a service Protocol's subclass whose methods, in each method's mode, return None."""

    def method(name: str) -> Callable[..., object]:
        if inspect.iscoroutinefunction(getattr(protocol, name)):

            async def answer_later(self: object, **_: object) -> None:
                return None

            return answer_later

        def answer(self: object, **_: object) -> None:
            return None

        return answer

    methods = {name: method(name) for name in protocol.__abstractmethods__}
    return type(f"StandIn{protocol.__name__}", (protocol,), methods)()


def _connected(builder: Callable[..., Any], **settings: Any) -> Any:  # noqa: ANN401
    """Call a generated builder with a stand-in for each service it takes, and an authorize callback if it needs one."""
    services = {
        name: _stand_in(typing.get_origin(parameter.annotation) or parameter.annotation)
        for name, parameter in inspect.signature(builder).parameters.items()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind is inspect.Parameter.KEYWORD_ONLY
        and name != "authorize"
    }
    if "authorize" in inspect.signature(builder).parameters:
        settings.setdefault("authorize", lambda requirement_sets, credentials: None)
    return builder(**services, **settings)


def _summary(document: dict[str, Any]) -> list[str]:
    return [
        f"{method.upper()} {path} {operation.get('operationId')} "
        f"{[parameter['name'] for parameter in operation.get('parameters', ())]}"
        for path, item in document["paths"].items()
        for method, operation in item.items()
    ]


@_scenario("pets")
def document_pets(packages: dict[str, Any]) -> list[str]:
    """Serve the pets document: native routes, a cookie adapter, and responses without a primary."""
    return [json.dumps(_connected(packages["pets"].create_app).openapi(), indent=2)]


@_scenario("items")
def document_parameters(packages: dict[str, Any]) -> list[str]:
    """Serve adapter parameters, renamed and repeated path placeholders, and generated errors."""
    return [json.dumps(_connected(packages["items"].create_app).openapi(), indent=2)]


@_scenario("secured")
def document_security(packages: dict[str, Any]) -> list[str]:
    """Serve the security schemes and requirements of the package's FastAPI security dependencies."""
    return [json.dumps(_connected(packages["secured"].create_app).openapi(), indent=2)]


@_scenario("bodies")
def document_bodies(packages: dict[str, Any]) -> list[str]:
    """Serve adapter and raw request bodies next to FastAPI's native bodies and forms."""
    return [json.dumps(_connected(packages["bodies"].create_app).openapi(), indent=2)]


@_scenario("calls")
def document_callbacks(packages: dict[str, Any]) -> list[str]:
    """Serve callbacks, nested and reused ones included, as documentation."""
    return [json.dumps(_connected(packages["calls"].create_app).openapi(), indent=2)]


@_scenario("methods")
def document_methods(packages: dict[str, Any]) -> list[str]:
    """Serve an OpenAPI 3.2 package: QUERY and an additional operation."""
    return [json.dumps(_connected(packages["methods"].create_app).openapi(), indent=2)]


@_scenario("names")
def document_names(packages: dict[str, Any]) -> list[str]:
    """Serve renamed native path parameters under their source placeholders."""
    return [json.dumps(_connected(packages["names"].create_app).openapi(), indent=2)]


@_scenario("items")
def prefixes(packages: dict[str, Any]) -> list[str]:
    """Include one package under two prefixes, a nested prefix, and create_app's prefix."""
    items = packages["items"]
    app = FastAPI()
    router = _connected(items.build_router)
    app.include_router(router, prefix="/v1")
    app.include_router(router, prefix="/v2")
    outer = APIRouter(prefix="/outer")
    outer.include_router(_connected(items.build_router, prefix="/inner"))
    app.include_router(outer, prefix="/api")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        document = app.openapi()
    lines = [f"warning {warning.message}" for warning in caught]
    lines.extend(_summary(document))
    lines.append(f"create_app {list(_connected(items.create_app, prefix='/root').openapi()['paths'])}")
    app = FastAPI()
    app.include_router(_connected(items.build_router, prefix="/v3"))
    items.serve_source_openapi(app, prefix="/v3")
    lines.append(f"serve_source_openapi {list(app.openapi()['paths'])}")
    return lines


@_scenario("pets")
def later_routes(packages: dict[str, Any]) -> list[str]:
    """Serve a route added after create_app."""
    app = _connected(packages["pets"].create_app)
    app.add_api_route("/health", lambda: None, methods=["GET"])
    return [f"paths {list(app.openapi()['paths'])}"]


@_scenario("references")
def document_references(packages: dict[str, Any]) -> list[str]:
    """Serve a document whose references into other documents the package bundles into its components."""
    return [json.dumps(_connected(packages["references"].create_app).openapi(), indent=2)]


@_scenario("selected")
def document_selection(packages: dict[str, Any]) -> list[str]:
    """Serve only the selected operations' paths, with only the components they reference."""
    return [json.dumps(_connected(packages["selected"].create_app).openapi(), indent=2)]


@_scenario("hooks")
def document_webhooks(packages: dict[str, Any]) -> list[str]:
    """Bundle the path items, discriminator mapping, and schemas that webhooks and callbacks name elsewhere."""
    return [json.dumps(_connected(packages["hooks"].create_app).openapi(), indent=2)]


@_scenario("streams")
def document_streams(packages: dict[str, Any]) -> list[str]:
    """Bundle an OpenAPI 3.2 item schema and media type, resolving a schema's references against its $id."""
    return [json.dumps(_connected(packages["streams"].create_app).openapi(), indent=2)]


@_scenario("legacy")
def document_legacy(packages: dict[str, Any]) -> list[str]:
    """Serve an OpenAPI 3.0 subset: discriminator subtypes kept, references into left-out paths bundled."""
    return [json.dumps(_connected(packages["legacy"].create_app).openapi(), indent=2)]


@_scenario("resources")
def document_resources(packages: dict[str, Any]) -> list[str]:
    """Serve schema resources by `$id`, `$anchor`, and `$dynamicAnchor`, and references into paths under a prefix."""
    resources = packages["resources"]
    unset = _connected(resources.create_app, summary=None, contact=None, servers=None, openapi_tags=None).openapi()
    return [
        json.dumps(_connected(resources.create_app, prefix="/api").openapi(), indent=2),
        f"unset {sorted(unset)} {sorted(unset['info'])}",
    ]


@_scenario("bodies")
def fastapi_document(packages: dict[str, Any]) -> list[str]:
    """Serve FastAPI's own document instead of the source document."""
    return [json.dumps(_connected(packages["bodies"].create_app, source_openapi=False).openapi(), indent=2)]


@_scenario("items")
def own_applications(packages: dict[str, Any]) -> list[str]:
    """Serve FastAPI's document in an application of your own, then the source document behind a root path."""
    items = packages["items"]
    app = FastAPI()
    app.include_router(_connected(items.build_router))
    lines = [f"fastapi {app.openapi()['openapi']} {list(app.openapi()['paths'])}"]
    app = FastAPI(root_path="/api")
    app.include_router(_connected(items.build_router))
    app.openapi()
    items.serve_source_openapi(app)
    served = TestClient(app).get("/openapi.json").json()
    first = app.openapi()
    lines.extend((
        f"source {served['openapi']} {list(served['paths'])}",
        f"servers {served.get('servers')}",
        f"kept {app.openapi() is first}",
    ))
    described = _connected(
        items.create_app, title="Mine", version="2", servers=[{"url": "https://prod.example.com"}]
    ).openapi()
    lines.append(f"metadata {described['info']} {described['servers']}")
    return lines


def fastapi_openapi_report(case_name: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Generate the scenario's packages, then report the documents and failures of its applications."""
    names, scenario = SCENARIOS[case_name]
    lines = [f"# {case_name}"]
    monkeypatch.syspath_prepend(str(root))
    imported: dict[str, Any] = {}
    try:
        for name in names:
            with warnings.catch_warnings(record=True) as recorded:
                warnings.simplefilter("always", UserWarning)
                _generate(PACKAGES[name], "pydantic_v2.BaseModel", root, f"docs_{name}")
            lines.extend(f"{item.category.__name__}: {item.message}" for item in recorded)
            imported[name] = _import(f"docs_{name}")[0]
        lines.extend(scenario(imported))
    finally:
        for name in names:
            forget_generated(f"docs_{name}")
    return "\n".join(lines).replace(root.resolve().as_posix(), "<root>") + "\n"
