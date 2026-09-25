"""Generate FastAPI servers, serve them in process, and report every request, handler call, and response."""

from __future__ import annotations

import importlib
import importlib.util
import json
import shutil
import sys
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.exceptions import ResponseValidationError
from fastapi.testclient import TestClient

from datamodel_code_generator import DataModelType, GenerateConfig, _runtime
from datamodel_code_generator._api_generation import generate_target
from datamodel_code_generator._fastapi.target import FastAPITarget
from datamodel_code_generator.enums import OpenAPIScope
from datamodel_code_generator.format import Formatter
from tests.data.python.fastapi_generation import SOURCE, fastapi_config

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    import pytest


def _generate(case: dict[str, Any], backend: str, root: Path, package: str) -> None:
    generate_target(
        shutil.copy2(SOURCE / case["input"], root / case["input"]),
        model_config=GenerateConfig(
            output=root / f"{package}_models.py",
            input_file_type="openapi",
            openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
            output_model_type=DataModelType(backend),
            disable_timestamp=True,
            formatters=[Formatter.BUILTIN],
            **case.get("model", {}),
        ),
        config=fastapi_config(
            {"output": package, "package": package, "model_package": f"{package}_models", **case.get("config", {})},
            root,
        ),
        generator=FastAPITarget(),
    )


def _import(package: str) -> tuple[ModuleType, ModuleType]:
    """Import a generated package whose private runtime resolves to this checkout's runtime sources."""
    runtime = Path(_runtime.__file__).parent
    spec = importlib.util.spec_from_file_location(
        f"{package}._runtime", runtime / "__init__.py", submodule_search_locations=[str(runtime)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(package)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return importlib.import_module(package), importlib.import_module(f"{package}_models")


def _forget(package: str) -> None:
    for name in [name for name in sys.modules if name.startswith((f"{package}.", f"{package}_models")) or name == package]:
        del sys.modules[name]


class _WithoutRawPath:
    """Serve like an ASGI server that omits or rewrites the optional raw_path for requests that ask for it."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:  # noqa: ANN401
        headers = dict(scope.get("headers", ()))
        if b"x-without-raw-path" in headers:
            scope = {key: value for key, value in scope.items() if key != "raw_path"}
        if (raw := headers.get(b"x-raw-path")) is not None:
            scope = {**scope, "raw_path": raw}
        await self.app(scope, receive, send)


def _exchange(
    client: TestClient, request: dict[str, Any], calls: list[str], lines: list[str], errors: tuple[type[Exception], ...]
) -> None:
    method, url = request["method"], request["url"]
    lines.append(f"> {method} {url}")
    options = {key: request[key] for key in ("headers", "json", "content", "data", "files") if key in request}
    if isinstance(files := options.get("files"), dict):
        options["files"] = {name: (value[0], value[1].encode(), value[2]) for name, value in files.items()}
    if isinstance(content := options.get("content"), str):
        options["content"] = content.encode()
    try:
        response = client.request(method, url, **options)
    except ResponseValidationError as error:
        lines.extend(f"  {call}" for call in calls)
        lines.append(f"< ResponseValidationError: {[item['msg'] for item in error.errors()]}")
    except errors as error:
        lines.extend(f"  {call}" for call in calls)
        lines.append(f"< {type(error).__name__}: {error}")
    else:
        lines.extend(f"  {call}" for call in calls)
        media = response.headers.get("content-type", "-")
        textual = media.startswith("text/") or media.partition(";")[0].endswith(("/json", "+json"))
        body = response.text if textual else repr(response.content)
        lines.append(f"< {response.status_code} {media} {body}".rstrip())
        lines.extend(
            f"< {name}: {response.headers[name]}" for name in request.get("show_headers", ()) if name in response.headers
        )
    finally:
        calls.clear()


def _codec(server: ModuleType, selector: dict[str, Any], lines: list[str]) -> None:
    facade = getattr(server.responses, selector["facade"])
    arguments = {key: value for key, value in selector.items() if key != "facade"}
    try:
        codec = facade.part(**arguments) if "name" in arguments else facade.body(**arguments)
    except importlib.import_module(f"{server.__name__}.model_codecs").CodecSelectionError as error:
        lines.append(f"codec {selector['facade']} {arguments}: CodecSelectionError: {error}")
        return
    lines.append(f"codec {selector['facade']} {arguments}: {type(codec).__name__}")


def _build(label: str, build: Callable[[], object], errors: tuple[type[Exception], ...]) -> str:
    try:
        build()
    except errors as error:
        return f"build {label}: {type(error).__name__}: {error}"
    return f"build {label}: ok"


def _serve(
    server: ModuleType, app_case: dict[str, Any], sets: dict[str, Any], settings: dict[str, Any], lines: list[str]
) -> FastAPI:
    options = {**settings.get(app_case["set"], {})}
    if "options" in app_case:
        options["fastapi_options"] = app_case["options"]
    app = server.create_app(**sets[app_case["set"]], **options)
    lines.extend(f"openapi {key} {json.dumps(app.openapi().get(key))}" for key in app_case.get("openapi", ()))
    return app


def fastapi_server_report(case_name: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Generate one server per backend, then replay the case's builds, applications, requests, and codecs."""
    case = json.loads((SOURCE / "servers.json").read_text(encoding="utf-8"))[case_name]
    services = importlib.import_module(f"tests.data.python.fastapi_handlers.{case['services']}")
    lines = [f"# {case_name}"]
    monkeypatch.syspath_prepend(str(root))
    for backend in case.get("backends", ["pydantic_v2.BaseModel"]):
        package = f"{case_name.replace('-', '_')}_{backend.rpartition('.')[2].lower()}"
        lines.append(f"serve {backend}")
        _generate(case, backend, root, package)
        try:
            server, models = _import(package)
            calls: list[str] = []
            sets = services.services(server, models, calls)
            settings = services.settings(server, models, calls) if hasattr(services, "settings") else {}
            errors = (server.HandlerConfigurationError, server.AuthConfigurationError, server.OpenAPIConfigurationError)
            lines.extend(
                _build(name, partial(server.build_router, **sets[name]), errors)
                for name in case.get("builds", ())
            )
            if hasattr(services, "builds"):
                lines.extend(_build(label, build, errors) for label, build in services.builds(server, models, calls))
            for app_case in case.get("apps", [{"set": "default", "requests": case.get("requests", [])}]):
                if "apps" in case:
                    lines.append(f"app {app_case['set']}")
                app = _serve(server, app_case, sets, settings, lines)
                with TestClient(_WithoutRawPath(app)) as client:
                    for request in app_case.get("requests", ()):
                        _exchange(client, request, calls, lines, errors)
            for selector in case.get("codecs", ()):
                _codec(server, selector, lines)
        finally:
            _forget(package)
    return "\n".join(lines).replace(root.resolve().as_posix(), "<root>") + "\n"
