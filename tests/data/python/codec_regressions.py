"""Generate clients and servers and exercise directional unions and registered parameter adapters."""

from __future__ import annotations

import importlib
import json
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from datamodel_code_generator import DataModelType, GenerateConfig, OpenAPIScope
from datamodel_code_generator.fastapi import generate_fastapi
from datamodel_code_generator.format import Formatter
from tests.data.python.client_runtime import (
    _CALL_ID,
    Exchange,
    json_response,
    record,
    run,
)
from tests.data.python.client_runtime import (
    _generate as generate_client,
)
from tests.data.python.fastapi_generation import fastapi_config
from tests.data.python.generated_packages import forget_generated, import_generated

if TYPE_CHECKING:
    import pytest

SOURCE = Path(__file__).parents[1] / "generation_platform/regressions"
BACKENDS = (
    "pydantic_v2.BaseModel",
    "pydantic_v2.dataclass",
    "dataclasses.dataclass",
    "typing.TypedDict",
    "msgspec.Struct",
)


def _plain(value: object) -> object:
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if hasattr(value, "wire"):
        return _plain(value.value)
    if hasattr(value, "root"):
        return _plain(value.root)
    if hasattr(value, "__struct_fields__"):
        return {key: _plain(getattr(value, key)) for key in value.__struct_fields__}
    if hasattr(value, "__dict__"):
        return {key: _plain(item) for key, item in vars(value).items() if not key.startswith("_")}
    return value


def _generate(case: str, target: str, backend: str, root: Path) -> tuple[object, dict[str, Any]]:
    spec = json.loads((SOURCE / "cases.json").read_text())[case]
    model = GenerateConfig(
        output=root / "regression_models.py",
        input_file_type="openapi",
        target_python_version="3.11",
        openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
        output_model_type=DataModelType(backend),
        disable_timestamp=True,
        formatters=[Formatter.BUILTIN],
        **spec.get("model", {}),
    )
    config = {
        "output": "regression",
        "package": "regression",
        "model_package": "regression_models",
        "codec_adapters": spec.get("codec_adapters", []),
    }
    source = shutil.copy2(SOURCE / spec["input"], root / spec["input"])
    if target == "client":
        config["validation"] = {"request": "schema", "response": "schema"}
        generate_client({**spec, "config": config}, backend, root, "regression", source=SOURCE)
    else:
        config["layout"] = "single"
        generate_fastapi(source, model_config=model, config=fastapi_config(config, root))
    shutil.copy2(SOURCE / "app_codecs.py", root / "app_codecs.py")
    return import_generated("regression"), spec


def _client(package: Any, case: str, spec: dict[str, Any], lines: list[str]) -> None:
    facades = importlib.import_module("regression.types.default")

    def parameters(arguments: dict[str, object]) -> dict[str, object]:
        names = {"x_values": ("header", "X-Values"), "path": ("path", "path")}
        return {
            name: facades.ParametersRequestCodecs.parameter(
                location=names.get(name, ("cookie" if name in {"tags", "required", "content"} else "query", name))[0],
                name=names.get(name, ("", name))[1],
            ).from_wire(value)
            for name, value in arguments.items()
        }

    def body(payload: dict[str, object]) -> object:
        return facades.ShapeRequestCodecs.body().from_wire(payload)

    def response(payload: dict[str, object]) -> object:
        return json_response(200, {key: value for key, value in payload.items() if key != "secret"})

    exchange = Exchange(lines)
    with exchange.client() as http, package.Client(http_client=http) as api:
        if case == "split":
            for payload in spec["payloads"]:
                exchange.respond(response(payload))
                record(lines, f"shape {payload}", lambda payload=payload: api.default.shape(body=body(payload)))
        else:
            for arguments in spec["calls"]:
                exchange.respond(json_response(200, {}))
                record(
                    lines,
                    f"parameters {arguments}",
                    lambda arguments=arguments: api.default.parameters(**parameters(arguments)),
                )

    async def asynchronous() -> None:
        exchange = Exchange(lines)
        async with exchange.async_client() as http, package.AsyncClient(http_client=http) as api:
            if case == "split":
                for payload in spec["payloads"]:
                    exchange.respond(response(payload))
                    result = await api.default.shape(body=body(payload))
                    lines.append(f"async shape {payload}: {_plain(result)}")
            else:
                for arguments in spec["calls"]:
                    exchange.respond(json_response(200, {}))
                    result = await api.default.parameters(**parameters(arguments))
                    lines.append(f"async parameters {arguments}: {_plain(result)}")

    run(asynchronous)


def _server(package: Any, case: str, spec: dict[str, Any], lines: list[str]) -> None:
    from fastapi.testclient import TestClient

    class Service:
        def shape(self, *, body: object) -> object:
            lines.append(f"shape received {type(body).__name__}: {_plain(body)}")
            return package.responses.ShapeResponseCodecs.body(status_code=200).from_wire({
                key: value for key, value in _plain(body).items() if value is not None
            })

        def parameters(self, **arguments: object) -> object:
            public = importlib.import_module("regression.model_codecs")
            wire = {name: _plain(value) for name, value in arguments.items() if value is not public.UNSET}
            lines.append(f"parameters received {wire}")
            return package.responses.ParametersResponseCodecs.body(status_code=200).from_wire(wire)

    with TestClient(package.create_app(service=Service())) as api:
        requests = ({"json": payload} for payload in spec["payloads"]) if case == "split" else iter(spec["requests"])
        for request in requests:
            response = api.post("/shapes", **request) if case == "split" else api.get(**request)
            lines.append(f"response {response.status_code}: {response.json()}")


def codec_regression_report(case: str, target: str, backend: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Exercise generated public operations using real client TLS exchanges and FastAPI request handling."""
    monkeypatch.syspath_prepend(str(root))
    lines = [f"# {case} {target} {backend}"]
    try:
        package, spec = _generate(case, target, backend, root)
        (_client if target == "client" else _server)(package, case, spec, lines)
    finally:
        forget_generated("regression")
        sys.modules.pop("app_codecs", None)
    return _CALL_ID.sub("<call>", "\n".join(lines)) + "\n"
