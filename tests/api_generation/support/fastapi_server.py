"""Generate FastAPI servers, serve them in process, and report every request, handler call, and response."""

from __future__ import annotations

import importlib
import inspect
import json
import shutil
import sys
from functools import partial
from itertools import starmap
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi.exceptions import ResponseValidationError
from fastapi.testclient import TestClient

from datamodel_code_generator import DataModelType, generate
from datamodel_code_generator.enums import OpenAPIScope
from datamodel_code_generator.format import Formatter
from tests.api_generation.support.client_generation import formatter_refusal
from tests.api_generation.support.fastapi_generation import SOURCE, server_options
from tests.api_generation.support.generated_packages import forget_generated, import_generated

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from types import ModuleType

    import pytest
    from fastapi import FastAPI


def _models(case: dict[str, Any], package: str) -> str:
    """Return the import path of a case's models: a module beside the package, or inside it for a nested case."""
    return f"{package}.models" if case.get("nested") else f"{package}_models"


def _generate(case: dict[str, Any], backend: str, root: Path, package: str) -> None:
    for name in case.get("files", ()):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE / name, root / name)
    model = case.get("model", {})
    if isinstance(directory := model.get("custom_template_dir"), str):
        model = {**model, "custom_template_dir": SOURCE / directory}
    models = _models(case, package)
    generate(
        shutil.copy2(SOURCE / case["input"], root / case["input"]),
        **{
            "output": root / f"{models.replace('.', '/')}{'' if case.get('modular') else '.py'}",
            "input_file_type": "openapi",
            "target_python_version": "3.11",
            "openapi_scopes": [OpenAPIScope.Schemas, OpenAPIScope.Api],
            "output_model_type": DataModelType(backend),
            "disable_timestamp": True,
            "formatters": [Formatter.BUILTIN],
            **model,
        },
        **server_options(case, package, models),
        server_output=root / package,
    )


def _generated(root: Path, package: str, package_snapshots: set[str]) -> dict[tuple[str, ...], str]:
    """Return selected package snapshots and every model, leaving out the copied runtime."""
    owners = {f"{package}_models", f"{package}_models.py"} | ({package} & package_snapshots)
    return {
        parts: path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*.py"))
        if ((parts := path.relative_to(root).parts)[0] in owners or parts == (package, "models.py"))
        and "_runtime" not in parts
    }


def _import(package: str, models: str | None = None) -> tuple[ModuleType, ModuleType]:
    """Import a generated package and its models, a module beside the package unless they are named."""
    return import_generated(package), importlib.import_module(models or f"{package}_models")


class _WithoutRawPath:
    """Serve like an ASGI server that omits or rewrites the optional raw_path for requests that ask for it."""

    def __init__(self, app: FastAPI) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        headers = dict(scope.get("headers", ()))
        if b"x-without-raw-path" in headers:
            scope = {key: value for key, value in scope.items() if key != "raw_path"}
        if (raw := headers.get(b"x-raw-path")) is not None:
            scope = {**scope, "raw_path": raw}
        await self.app(scope, receive, send)


def _exchange(client: TestClient, request: dict[str, Any], calls: list[str], lines: list[str]) -> None:
    if (location := request.get("openapi")) is not None:
        path, method, status = location
        document = client.app.app.openapi()
        content = document["paths"][path][method]["responses"].get(status, {}).get("content")
        lines.extend((f"> openapi {path} {method} {status}", f"< {json.dumps(content, sort_keys=True)}"))
        return
    method, url = request["method"], request["url"]
    lines.append(f"> {method} {url}")
    options = {key: request[key] for key in ("headers", "json", "content", "data", "files") if key in request}
    match options.get("files"):
        case dict() as files:
            options["files"] = {name: (value[0], value[1].encode(), value[2]) for name, value in files.items()}
        case list() as files:
            options["files"] = [(name, (file, body.encode(), media)) for name, file, body, media in files]
        case _:
            pass
    if isinstance(content := options.get("content"), str):
        options["content"] = content.encode()
    try:
        response = client.request(method, url, **options)
    except ResponseValidationError as error:
        lines.extend(f"  {call}" for call in calls)
        lines.append(f"< ResponseValidationError: {[item['msg'] for item in error.errors()]}")
    except Exception as error:  # noqa: BLE001
        lines.extend(f"  {call}" for call in calls)
        lines.append(f"< raised {type(error).__name__}: {error}")
    else:
        lines.extend(f"  {call}" for call in calls)
        media = response.headers.get("content-type", "-")
        textual = media.startswith("text/") or media.partition(";")[0].endswith(("/json", "+json"))
        body = (
            _errors(response) if response.status_code == 422 else response.text if textual else repr(response.content)
        )
        lines.append(f"< {response.status_code} {media} {body}".rstrip())
        lines.extend(
            f"< {name}: {response.headers[name]}"
            for name in request.get("show_headers", ())
            if name in response.headers
        )
    finally:
        calls.clear()


def _errors(response: Any) -> str:
    """Show each validation error without its message, which follows the installed pydantic-core."""
    return json.dumps([
        {key: value for key, value in error.items() if key != "msg"} for error in response.json()["detail"]
    ])


def _interfaces(server: ModuleType) -> Iterator[str]:
    """Report each service Protocol's abstract methods, and that a subclass without them cannot be instantiated."""
    module = server.services
    for name, protocol in inspect.getmembers(module, inspect.isclass):
        if protocol.__module__ == module.__name__:
            yield f"service {name}: {', '.join(sorted(protocol.__abstractmethods__))}"
            try:
                type(f"Incomplete{name}", (protocol,), {})()
            except TypeError:
                yield f"service {name} without its methods: TypeError"


def _build(label: str, build: Callable[[], object]) -> str:
    try:
        build()
    except (AttributeError, TypeError, ValueError) as error:
        return f"build {label}: {type(error).__name__}: {error}"
    return f"build {label}: ok"


def _serve(
    server: ModuleType,
    app_case: dict[str, Any],
    sets: dict[str, Any],
    settings: dict[str, Any],
    built: dict[str, FastAPI],
    lines: list[str],
) -> FastAPI:
    if (app := built.get(app_case["set"])) is None:
        options = {**settings.get(app_case["set"], {})}
        overrides = options.pop("dependency_overrides", {})
        app = server.create_app(**sets[app_case["set"]], **options)
        app.dependency_overrides.update(overrides)
    lines.extend(f"openapi {key} {json.dumps(app.openapi().get(key))}" for key in app_case.get("openapi", ()))
    return app


def fastapi_server_report(
    case_name: str, root: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, dict[str, dict[tuple[str, ...], str]]]:
    """Generate one server per backend, replay the case's builds, applications, and requests.

    A case with a `previous` document first generates the package from it, so that the generation the case serves
    leaves the modules only that document needs in place; the report says whether each `stale` module is still a
    file and whether serving imported it. Return the report and each backend's generated modules.
    """
    case = json.loads((SOURCE / "servers.json").read_text(encoding="utf-8"))[case_name]
    services = importlib.import_module(f"tests.data.python.fastapi_handlers.{case['services']}")
    lines = [f"# {case_name}"]
    packages: dict[str, dict[tuple[str, ...], str]] = {}
    monkeypatch.syspath_prepend(str(root))
    package_snapshots = {
        f"{case_name.replace('-', '_')}_{backend.rpartition('.')[2].lower()}"
        for backend in case.get("package_snapshots", case.get("backends", ["pydantic_v2.BaseModel"]))
    }
    for backend in case.get("backends", ["pydantic_v2.BaseModel"]):
        package = f"{case_name.replace('-', '_')}_{backend.rpartition('.')[2].lower()}"
        lines.append(f"serve {backend}")
        if (previous := case.get("previous")) is not None:
            _generate({**case, "input": previous}, backend, root, package)
        try:
            _generate(case, backend, root, package)
        except RuntimeError as error:
            lines.append(f"  {formatter_refusal(error)}")
            continue
        packages[backend.replace(".", "_")] = _generated(root, package, package_snapshots)
        try:
            server, models = _import(package, _models(case, package))
            calls: list[str] = []
            lines.extend(_interfaces(server))
            sets = services.services(server, models, calls)
            settings = services.settings(server, models, calls) if hasattr(services, "settings") else {}
            built = services.applications(server, sets) if hasattr(services, "applications") else {}
            lines.extend(_build(name, partial(server.build_router, **sets[name])) for name in case.get("builds", ()))
            if hasattr(services, "builds"):
                lines.extend(starmap(_build, services.builds(server, models, calls)))
            for app_case in case.get("apps", [{"set": "default", "requests": case.get("requests", [])}]):
                if "apps" in case:
                    lines.append(f"app {app_case['set']}")
                app = _serve(server, app_case, sets, settings, built, lines)
                with TestClient(_WithoutRawPath(app)) as client:
                    for request in app_case.get("requests", ()):
                        _exchange(client, request, calls, lines)
            lines.extend(
                f"stale {name}: file {(root / package / name).is_file()}; imported "
                f"{'.'.join((package, *Path(name).with_suffix('').parts)) in sys.modules}"
                for name in case.get("stale", ())
            )
        finally:
            forget_generated(package)
    return "\n".join(lines).replace(root.resolve().as_posix(), "<root>") + "\n", packages
