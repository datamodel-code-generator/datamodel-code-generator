"""Prepare target renders and generated-client calls outside the measured benchmark work."""

from __future__ import annotations

import asyncio
import importlib
import shutil
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from datamodel_code_generator import generate
from tests.data.python.client_generation import client_render_call, generate_client, model_config
from tests.data.python.generated_packages import generated_root, import_generated

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

DATA = Path(__file__).parents[1]
SMALL = DATA / "generation_platform" / "client" / "publication" / "minimal.json"
LARGE = DATA / "performance" / "openapi_large.yaml"
PACKAGE = "performance_client"
BACKEND = "pydantic_v2.BaseModel"


def target_render_call(target: str, size: str, root: Path) -> Callable[[], object]:
    """Copy the input beside the output and prepare a render of both models and target files.

    The server renders through generate() without an output, which stages its files under the working directory.
    """
    original = SMALL if size == "small" else LARGE
    source = shutil.copy2(original, root / f"input{original.suffix}")
    if target == "client":
        return client_render_call(
            source, root, PACKAGE, BACKEND, config={"server_base_url": "https://benchmark.invalid"}
        )
    return partial(
        generate,
        source,
        config=model_config(
            root / "performance_server_models.py",
            BACKEND,
            {
                "output": None,
                "generate_server": "fastapi",
                "server_package": "performance_server",
                "server_model_package": "performance_server_models",
            },
        ),
    )


@contextmanager
def generated_client_calls(root: Path) -> Iterator[dict[str, Callable[[], Any]]]:
    """Keep one copied-runtime package, native clients, payload and event loop outside measured calls."""
    import httpx2

    source = shutil.copy2(SMALL, root / "input.json")
    generate_client(source, root, PACKAGE, BACKEND)
    response_body = (DATA / "performance" / "client-response.json").read_bytes()

    def response(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, headers={"Content-Type": "application/json"}, stream=httpx2.ByteStream(response_body)
        )

    with generated_root(root, PACKAGE):
        module = import_generated(PACKAGE, copied=True)
        models = importlib.import_module(f"{PACKAGE}_models")
        body = models.Pet(id=7, name="benchmark")
        base_url = "https://benchmark.invalid"
        with (
            asyncio.Runner() as runner,
            httpx2.Client(transport=httpx2.MockTransport(response), trust_env=False) as native,
            module.Client(base_url=base_url, http_client=native) as client,
        ):
            runner.get_loop()
            async_native = httpx2.AsyncClient(transport=httpx2.MockTransport(response), trust_env=False)
            async_client = module.AsyncClient(base_url=base_url, http_client=async_native)

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
