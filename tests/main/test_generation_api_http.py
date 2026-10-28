"""Run API scope against real HTTP sources and ordinary remote-lock publication."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from datamodel_code_generator import InputFileType, OpenAPIScope, generate
from tests.conftest import assert_output
from tests.test_http import _SchemaHandler, local_http_server  # noqa: F401 - Register the existing fixture.

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/api_scope"
EXPECTED = DATA / "expected/main/generation_platform/api_scope"


def test_api_url_path_item(local_http_server: str) -> None:  # noqa: F811 - Request the imported fixture.
    """Keep external Path Item reference context across actual HTTP fetches."""
    for filename in ("external.json", "library.json"):
        _SchemaHandler.routes[f"/{filename}"] = (
            200,
            {"content-type": "application/json"},
            (SOURCE / filename).read_bytes(),
        )
    try:
        result = generate(
            urlparse(f"{local_http_server}/external.json"),
            input_file_type=InputFileType.OpenAPI,
            openapi_scopes=[OpenAPIScope.Api],
            allow_private_network=True,
            allow_remote_refs=True,
            input_filename="external.json",
            use_union_operator=False,
            use_standard_collections=False,
            disable_timestamp=True,
            formatters=[],
        )
        assert_output(result, EXPECTED / "external-generate.py")
    finally:
        for filename in ("external.json", "library.json"):
            del _SchemaHandler.routes[f"/{filename}"]


def test_api_empty_remote_lock(local_http_server: str, tmp_path: Path) -> None:  # noqa: F811
    """Publish and verify a real empty API lock without creating a model artifact."""
    _SchemaHandler.routes["/empty-api.json"] = (
        200,
        {"content-type": "application/json"},
        (SOURCE / "contentless.json").read_bytes(),
    )
    lockfile = tmp_path / "remote.lock"
    output = tmp_path / "models.py"
    try:
        generate(
            urlparse(f"{local_http_server}/empty-api.json"),
            input_file_type=InputFileType.OpenAPI,
            openapi_scopes=[OpenAPIScope.Api],
            allow_private_network=True,
            lockfile=lockfile,
            update_lock=True,
            output=output,
            formatters=[],
            disable_timestamp=True,
        )
        generate(
            urlparse(f"{local_http_server}/empty-api.json"),
            input_file_type=InputFileType.OpenAPI,
            openapi_scopes=[OpenAPIScope.Api],
            allow_private_network=True,
            lockfile=lockfile,
            locked=True,
            output=output,
            formatters=[],
            disable_timestamp=True,
        )
        assert_output(str(output.exists()) + "\n", EXPECTED / "absent-output.txt")
    finally:
        del _SchemaHandler.routes["/empty-api.json"]
