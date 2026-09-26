"""Serve the OpenAPI documents of generated FastAPI packages in applications of every shape, and report them."""

from __future__ import annotations

import inspect
import json
import typing
import warnings
from collections.abc import Callable
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Final, TypeAlias

from fastapi import APIRouter, FastAPI, Query, Security
from fastapi.responses import PlainTextResponse
from fastapi.security import APIKeyHeader

from tests.data.python.fastapi_server import _forget, _generate, _import

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

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
EXTRACTORS: Final = ("digest", "mtls")
Scenario: TypeAlias = Callable[[dict[str, Any]], list[str]]
SCENARIOS: dict[str, tuple[tuple[str, ...], Scenario]] = {}


def _scenario(*packages: str) -> Callable[[Scenario], Scenario]:
    def register(function: Scenario) -> Scenario:
        SCENARIOS[function.__name__.replace("_", "-")] = (packages, function)
        return function

    return register


def _attempt(label: str, action: Callable[[], object]) -> str:
    try:
        result = action()
    except Exception as error:  # noqa: BLE001
        return f"{label}: {type(error).__name__}: {error}"
    return f"{label}: {result}"


def _extractors() -> dict[str, Callable[..., object]]:
    return dict.fromkeys(EXTRACTORS, lambda request: None)


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
    """Call a generated builder with a stand-in for each service it takes, and an authorizer where it needs one."""
    services = {
        name: _stand_in(typing.get_origin(parameter.annotation) or parameter.annotation)
        for name, parameter in inspect.signature(builder).parameters.items()
        if parameter.default is inspect.Parameter.empty and name != "authorizer"
    }
    if "authorizer" in inspect.signature(builder).parameters:
        settings.setdefault("authorizer", lambda context: None)
    return builder(**services, **settings)


def _factory(app: FastAPI, change: Callable[[dict[str, Any]], object]) -> None:
    """Replace the application's factory with one that changes FastAPI's own document before composition."""
    original = app.openapi

    def factory() -> dict[str, Any]:
        document = original()
        change(document)
        return document

    app.openapi = factory  # type: ignore[method-assign]


def _changed(package: ModuleType, change: Callable[[dict[str, Any]], object], **include: Any) -> FastAPI:  # noqa: ANN401
    app = FastAPI()
    app.include_router(_connected(package.build_router), **include)
    _factory(app, change)
    package.install_openapi(app)
    return app


def _bundle(package: ModuleType) -> dict[str, Any]:
    return json.loads(package._generated.openapi.PLAN.bundle)


def _fragment(package: ModuleType, key: str) -> dict[str, Any]:
    return next(operation["fragment"] for operation in _bundle(package)["operations"] if operation["key"] == key)


def _operation(document: dict[str, Any], path: str, method: str) -> dict[str, Any]:
    return document["paths"][path][method]


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
    """Serve security requirements under the package's scheme components."""
    app = _connected(packages["secured"].create_app, credential_extractors=_extractors())
    return [json.dumps(app.openapi(), indent=2)]


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
    items.install_openapi(app)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        document = app.openapi()
    lines = [f"warning {warning.message}" for warning in caught]
    lines.extend(_summary(document))
    lines.append(f"create_app {list(_connected(items.create_app, prefix='/root').openapi()['paths'])}")
    return lines


@_scenario("pets", "secured")
def subset(packages: dict[str, Any]) -> list[str]:
    """Install the operations a group router includes, then every operation, then none.

    A package that serves some of its operations adds only the components and security schemes they use.
    """
    pets, secured = packages["pets"], packages["secured"]
    app = FastAPI()
    app.include_router(_connected(pets.routers.store.build_router))
    pets.install_openapi(app, operation_keys=("/paths/~1store~1inventory/get",))
    lines = [_attempt("store", lambda: _served(app.openapi()))]
    pets.install_openapi(app)
    lines.append(_attempt("every operation", app.openapi))
    pets.install_openapi(app, operation_keys=())
    lines.append(_attempt("no operation", app.openapi))
    app = FastAPI()
    app.include_router(_connected(secured.routers.pets.build_router))
    secured.install_openapi(app, operation_keys=("/paths/~1pets/get",))
    lines.append(_attempt("secured pets", lambda: _served(app.openapi())))
    return lines


def _served(document: dict[str, Any]) -> list[str]:
    return [
        *document["paths"],
        *(f"{section}/{name}" for section, names in document.get("components", {}).items() for name in names),
    ]


@_scenario("pets", "methods")
def packages(packages: dict[str, Any]) -> list[str]:
    """Compose two packages in either install order, a package that includes nothing, and an unknown owner."""
    pets, methods = packages["pets"], packages["methods"]
    documents = []
    for order in ((pets, methods), (methods, pets)):
        app = FastAPI()
        for package in order:
            app.include_router(_connected(package.build_router))
        for package in order:
            package.install_openapi(app)
        documents.append(app.openapi())
    lines = [f"orders agree {documents[0] == documents[1]} version {documents[0]['openapi']}"]
    app = FastAPI()
    app.include_router(_connected(pets.build_router))
    pets.install_openapi(app)
    methods.install_openapi(app, operation_keys=())
    lines.append(_attempt("empty install", lambda: [app.openapi()["openapi"], *app.openapi()["paths"]]))
    app = FastAPI()
    app.include_router(_connected(pets.build_router))
    app.include_router(_connected(methods.build_router))
    pets.install_openapi(app)
    lines.append(_attempt("unknown owner", app.openapi))
    return lines


@_scenario("pets")
def cache(packages: dict[str, Any]) -> list[str]:
    """Count factory calls: none to install, one per evaluation, none for a cached document."""
    pets = packages["pets"]
    app = FastAPI()
    calls: list[object] = []
    _factory(app, calls.append)
    app.include_router(_connected(pets.build_router))
    pets.install_openapi(app)
    lines = [f"install calls {len(calls)}"]
    first = app.openapi()
    lines.append(f"first calls {len(calls)} markers {'x-dcg-operation' in json.dumps(first)}")
    lines.append(f"cached {app.openapi() is first} calls {len(calls)}")
    app.openapi_schema = None
    lines.append(f"invalidated same {app.openapi() == first} calls {len(calls)}")
    pets.install_openapi(app)
    lines.append(f"reinstalled schema {app.openapi_schema} calls {len(calls)}")
    lines.append(f"recomposed same {app.openapi() == first} calls {len(calls)}")
    failing = FastAPI()
    failing.include_router(_connected(pets.build_router))

    def fail(document: dict[str, Any]) -> None:
        message = f"The factory saw {len(document['paths'])} paths"
        raise LookupError(message)

    _factory(failing, fail)
    pets.install_openapi(failing)
    lines.extend((_attempt("failing factory", failing.openapi), f"failing schema {failing.openapi_schema}"))
    return lines


@_scenario("pets", "items")
def errors(packages: dict[str, Any]) -> list[str]:
    """Reject install arguments, markers, prefixes, versions, and documents the composition cannot serve."""
    pets, items = packages["pets"], packages["items"]
    lines: list[str] = []
    app = FastAPI()
    app.include_router(_connected(pets.build_router))
    for keys in (["/paths/~1pets/get"], ("/nope",), ("/paths/~1pets/get", "/paths/~1pets/get")):
        lines.append(_attempt(f"install {keys}", lambda keys=keys: pets.install_openapi(app, operation_keys=keys)))
    missing = FastAPI()
    missing.include_router(_connected(pets.routers.store.build_router))
    pets.install_openapi(missing)
    lines.append(_attempt("missing operations", missing.openapi))
    get = ("/pets", "get")
    cases: dict[str, Callable[[dict[str, Any]], object]] = {
        "wrong marker version": lambda document: _operation(document, *get)["x-dcg-operation"].update(version=2),
        "marker is text": lambda document: _operation(document, *get).update({"x-dcg-operation": "pets"}),
        "moved method": lambda document: document["paths"]["/pets"].update(put=document["paths"]["/pets"].pop("get")),
        "not JSON": lambda document: document.update({"x-set": {1, 2}}),
        "paths not an object": lambda document: document.update(paths=[]),
        "odd path item": lambda document: document["paths"].update({"/odd": "text"}),
        "other parameter": lambda document: _operation(document, *get)["parameters"].append(
            {"name": "session", "in": "cookie", "schema": {"type": "integer"}}
        ),
        "same parameter": lambda document: _operation(document, *get)["parameters"].append(
            {"name": "session", "in": "cookie", "schema": {"type": "string"}}
        ),
        "bad security": lambda document: _operation(document, *get).update(security=[{"key": "all"}]),
        "global security": lambda document: document.update(security=[{"global": ["read"]}]),
        "other component": lambda document: document.setdefault("components", {})
        .setdefault("schemas", {})
        .update({next(iter(_bundle(pets)["components"]["schemas"])): {"type": "null"}}),
        "same component": lambda document: document.setdefault("components", {})
        .setdefault("schemas", {})
        .update(_bundle(pets)["components"]["schemas"]),
        "referenced parameter": lambda document: _operation(document, *get)["parameters"].append(
            {"$ref": "#/components/parameters/Page"}
        ),
        "same schema": lambda document: _operation(document, *get)["responses"]["200"]["content"]["application/json"]
        .update(schema=_bundle(pets)["operations"][0]["fragment"]["responses"]["200"]["content"]["application/json"]["schema"]),
        "repeated security": lambda document: _operation(document, *get).update(security=[{"key": []}, {"key": []}]),
        "parameters not an array": lambda document: _operation(document, *get).update(parameters={}),
    }
    for label, change in cases.items():
        app = _changed(pets, change)
        lines.append(
            _attempt(
                label,
                lambda app=app: [
                    _operation(app.openapi(), *get).get("security"),
                    len(app.openapi()["paths"]),
                ],
            )
        )
    lines.append(_attempt("templated prefix", _changed(pets, lambda document: None, prefix="/{tenant}").openapi))
    old = FastAPI()
    old.openapi_version = "3.0.2"
    old.include_router(_connected(pets.build_router))
    pets.install_openapi(old)
    lines.append(_attempt("OpenAPI 3.0", old.openapi))
    newer = FastAPI()
    newer.openapi_version = "3.2.1"
    newer.include_router(_connected(items.build_router))
    items.install_openapi(newer)
    lines.append(_attempt("repeated placeholders in 3.2", newer.openapi))
    clash = FastAPI()
    clash.include_router(_connected(items.build_router))
    clash.add_api_route("/items/{item-id}/parts/{part}", lambda: None, methods=["GET"])
    items.install_openapi(clash)
    lines.append(_attempt("path collision", clash.openapi))
    return lines


@_scenario("pets", "items")
def states(packages: dict[str, Any]) -> list[str]:
    """Reject an installed state of an unknown protocol or with malformed plans, and accept a foreign plan."""
    pets = packages["pets"]
    items = packages["items"]._generated.openapi.PLAN

    def foreign(app: FastAPI, bundle: str, keys: tuple[str, ...] = ()) -> SimpleNamespace:
        plan = SimpleNamespace(package="other", version="3.1.0", repeated=False, operation_keys=keys, bundle=bundle)
        return SimpleNamespace(protocol=1, original_factory=app.openapi, package_plans=(plan,))

    cases: dict[str, Callable[[FastAPI], object]] = {
        "unknown protocol": lambda app: object(),
        "malformed plan": lambda app: SimpleNamespace(protocol=1, original_factory=app.openapi, package_plans=(1,)),
        "bundle not JSON": lambda app: foreign(app, "{", ("/x",)),
        "malformed operation": lambda app: foreign(app, '{"operations": [{}], "components": {}}', ("/x",)),
        "missing fragment": lambda app: foreign(app, '{"operations": [], "components": {}}', ("/x",)),
        "foreign plan": lambda app: foreign(app, items.bundle),
    }
    lines = []
    for label, state in cases.items():
        app = FastAPI()
        app.include_router(_connected(pets.build_router))

        def installed() -> dict[str, Any]:
            return {}

        installed.__dcg_openapi_state__ = state(app)  # type: ignore[attr-defined]
        app.openapi = installed  # type: ignore[method-assign]
        lines.append(_attempt(label, lambda app=app: pets.install_openapi(app) or sorted(app.openapi()["paths"])))
    return lines


@_scenario("secured", "bodies", "pets", "calls")
def composition(packages: dict[str, Any]) -> list[str]:
    """Compose generated security with the application's, and meet user declarations of generated fragments."""
    secured, bodies, pets = packages["secured"], packages["bodies"], packages["pets"]
    user = APIKeyHeader(name="X-User", auto_error=False)
    app = _connected(secured.create_app, credential_extractors=_extractors(), dependencies=[Security(user)])
    document = app.openapi()
    lines = [
        f"{method.upper()} {path} security {operation.get('security')}"
        for path, item in document["paths"].items()
        for method, operation in item.items()
    ]
    lines.append(f"schemes {sorted(document['components']['securitySchemes'])}")
    header = {"schema": {"type": "integer"}}
    for label, value in (("other header", header), ("same header", None)):

        def change(document: dict[str, Any], value: dict[str, Any] | None = value) -> None:
            declared = _fragment(secured, "/paths/~1pets/get")["responses"]["401"]["headers"]["WWW-Authenticate"]
            response = _operation(document, "/pets", "get")["responses"].setdefault("401", {"description": "Denied."})
            response["headers"] = {"www-authenticate": value or declared}

        changed = FastAPI()
        changed.include_router(_connected(secured.build_router, credential_extractors=_extractors()))
        _factory(changed, change)
        secured.install_openapi(changed)
        lines.append(_attempt(label, lambda changed=changed: _operation(changed.openapi(), "/pets", "get")["responses"]["401"]))
    for label, body in (("other body", {"content": {}}), ("same body", None)):
        declared = _fragment(bodies, "/paths/~1raw/post")["requestBody"]

        def change(document: dict[str, Any], body: dict[str, Any] | None = body, declared: Any = declared) -> None:  # noqa: ANN401
            _operation(document, "/raw", "post")["requestBody"] = body or declared

        app = _changed(bodies, change)
        lines.append(_attempt(label, lambda app=app: sorted(_operation(app.openapi(), "/raw", "post")["requestBody"])))

    def limit(value: int = Query(alias="session", default=0)) -> int:
        return value

    dependent = FastAPI()
    dependent.include_router(_connected(pets.build_router, operation_dependencies={"/paths/~1pets/get": [Security(limit)]}))
    pets.install_openapi(dependent)
    lines.append(_attempt("user query named like the cookie adapter", lambda: _summary(dependent.openapi())[:1]))
    calls = packages["calls"]
    example = _changed(
        calls,
        lambda document: _operation(document, "/events", "get")["responses"]["200"]
        .setdefault("content", {})
        .setdefault("application/json", {})
        .update(example=[]),
    )
    lines.append(f"factory example {_operation(example.openapi(), '/events', 'get')['responses']['200']}")
    plain = _connected(pets.create_app, fastapi_options={"default_response_class": PlainTextResponse})
    lines.append(f"plain text {json.dumps(_operation(plain.openapi(), '/pets', 'get')['responses']['200'])}")
    return lines


def fastapi_openapi_report(case_name: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Generate the scenario's packages, then report the documents and failures of its applications."""
    names, scenario = SCENARIOS[case_name]
    lines = [f"# {case_name}"]
    monkeypatch.syspath_prepend(str(root))
    imported: dict[str, Any] = {}
    try:
        for name in names:
            _generate(PACKAGES[name], "pydantic_v2.BaseModel", root, f"docs_{name}")
            imported[name] = _import(f"docs_{name}")[0]
        lines.extend(scenario(imported))
    finally:
        for name in names:
            _forget(f"docs_{name}")
    return "\n".join(lines).replace(root.resolve().as_posix(), "<root>") + "\n"
