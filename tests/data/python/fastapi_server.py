"""Generate FastAPI servers, serve them in process, and report every request, handler call, and response."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
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
    from types import ModuleType

    import pytest


def _generate(case: dict[str, Any], backend: str, root: Path, package: str) -> None:
    generate_target(
        SOURCE / case["input"],
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
    """Serve like an ASGI server that omits the optional raw_path for requests that ask for it."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:  # noqa: ANN401
        if any(name == b"x-without-raw-path" for name, _ in scope.get("headers", ())):
            scope = {key: value for key, value in scope.items() if key != "raw_path"}
        await self.app(scope, receive, send)


def _exchange(client: TestClient, request: dict[str, Any], calls: list[str], lines: list[str]) -> None:
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


def fastapi_server_report(case_name: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Generate one server per backend, then replay the case's requests, router builds, and codec selections."""
    case = json.loads((SOURCE / "servers.json").read_text(encoding="utf-8"))[case_name]
    handlers = importlib.import_module(f"tests.data.python.fastapi_handlers.{case['handlers']}")
    lines = [f"# {case_name}"]
    monkeypatch.syspath_prepend(str(root))
    for backend in case.get("backends", ["pydantic_v2.BaseModel"]):
        package = f"{case_name.replace('-', '_')}_{backend.rpartition('.')[2].lower()}"
        lines.append(f"serve {backend}")
        _generate(case, backend, root, package)
        try:
            server, models = _import(package)
            calls: list[str] = []
            sets = handlers.handlers(server, models, calls)
            for name in case.get("builds", ()):
                try:
                    server.build_router(handlers=sets[name])
                except server.HandlerConfigurationError as error:
                    lines.append(f"build {name}: HandlerConfigurationError: {error}")
                else:
                    lines.append(f"build {name}: ok")
            app = FastAPI()
            app.include_router(server.build_router(handlers=sets["default"]))
            with TestClient(_WithoutRawPath(app)) as client:
                for request in case.get("requests", ()):
                    _exchange(client, request, calls, lines)
            for selector in case.get("codecs", ()):
                _codec(server, selector, lines)
        finally:
            _forget(package)
    return "\n".join(lines).replace(root.resolve().as_posix(), "<root>") + "\n"
