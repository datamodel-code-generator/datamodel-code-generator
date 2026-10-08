"""Prepare target renders and generated-client calls outside the measured benchmark work."""

from __future__ import annotations

import asyncio
import importlib
import shutil
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from datamodel_code_generator import DataModelType, Formatter, GenerateConfig, InputFileType, OpenAPIScope
from datamodel_code_generator.fastapi import FastAPIConfig, render_fastapi
from tests.data.python.client_generation import client_render_call, generate_client
from tests.data.python.generated_packages import generated_root, import_generated

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from datamodel_code_generator.api_types import GeneratedProject

DATA = Path(__file__).parents[1]
SMALL = DATA / "generation_platform" / "client" / "publication" / "minimal.json"
LARGE = DATA / "performance" / "openapi_large.yaml"
PACKAGE = "performance_client"


def target_render_call(target: str, size: str, root: Path) -> Callable[[], GeneratedProject]:
    """Copy the input beside the output and prepare a render of both models and target artifacts."""
    source = shutil.copy2(SMALL if size == "small" else LARGE, root / "input.yaml")
    if target == "client":
        return client_render_call(source, root, PACKAGE, "pydantic_v2.BaseModel")
    return partial(
        render_fastapi,
        source,
        model_config=GenerateConfig(
            output=root / "performance_server_models.py",
            input_file_type=InputFileType.OpenAPI,
            target_python_version="3.11",
            openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
            output_model_type=DataModelType.PydanticV2BaseModel,
            disable_timestamp=True,
            formatters=[Formatter.BUILTIN],
        ),
        config=FastAPIConfig(
            output=root / "performance_server", package="performance_server", model_package="performance_server_models"
        ),
    )


@contextmanager
def generated_client_calls(root: Path) -> Iterator[dict[str, Callable[[], Any]]]:
    """Keep one copied-runtime package, native clients, payload and event loop outside measured calls."""
    import httpx2

    source = shutil.copy2(SMALL, root / "input.json")
    generate_client(source, root, PACKAGE, "pydantic_v2.BaseModel")
    response_body = (DATA / "performance" / "client-response.json").read_bytes()

    def response(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, headers={"Content-Type": "application/json"}, stream=httpx2.ByteStream(response_body)
        )

    with generated_root(root, PACKAGE):
        module = import_generated(PACKAGE, copied=True)
        models = importlib.import_module(f"{PACKAGE}_models")
        client_options = importlib.import_module(f"{PACKAGE}.options")
        body = models.Pet(id=7, name="benchmark")
        options = client_options.ClientOptions(base_url="https://benchmark.invalid")
        with (
            asyncio.Runner() as runner,
            httpx2.Client(transport=httpx2.MockTransport(response), trust_env=False) as native,
            module.Client(options=options, http_client=native) as client,
        ):
            runner.get_loop()
            async_native = httpx2.AsyncClient(transport=httpx2.MockTransport(response), trust_env=False)
            async_client = module.AsyncClient(options=options, http_client=async_native)

            def sync_calls() -> None:
                for _ in range(200):
                    client.pets.create_pet(body=body)

            async def async_calls() -> None:
                for _ in range(200):
                    await async_client.pets.create_pet(body=body)

            def run_async_calls() -> None:
                runner.run(async_calls())

            try:
                yield {"sync": sync_calls, "async": run_async_calls}
            finally:
                runner.run(async_client.aclose())
                runner.run(async_native.aclose())
