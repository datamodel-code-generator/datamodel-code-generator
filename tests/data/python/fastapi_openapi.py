"""Serve the OpenAPI documents of generated FastAPI packages in applications of every shape, and report them."""

from __future__ import annotations

import inspect
import json
import typing
import warnings
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Final, TypeAlias

from fastapi import APIRouter, FastAPI

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
    return lines


@_scenario("pets")
def later_routes(packages: dict[str, Any]) -> list[str]:
    """Serve a route added after create_app."""
    app = _connected(packages["pets"].create_app)
    app.add_api_route("/health", lambda: None, methods=["GET"])
    return [f"paths {list(app.openapi()['paths'])}"]


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
