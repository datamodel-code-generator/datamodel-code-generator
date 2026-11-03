"""Generate the client and server targets from a document whose Responses Object carries specification extensions."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import OPEN_API_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "target",
    [
        pytest.param(
            [
                *("--generate-client", "httpx2", "--client-output", "client"),
                *("--client-package", "client", "--client-model-package", "models"),
            ],
            id="client",
        ),
        pytest.param(
            [
                *("--generate-server", "fastapi", "--server-output", "server"),
                *("--server-package", "server", "--server-model-package", "models"),
            ],
            id="server",
        ),
    ],
)
def test_target_generation_with_responses_object_extensions(
    target: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generate a client or a server from a document with `x-` extensions beside the responses of an operation.

    OpenAPI lets a Responses Object be extended with specification extensions. Up to 0.88.0 the walk of the `api`
    scope read `x-note: internal` as a response and refused the document.
    """
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "responses_extensions.yaml",
        output_path=tmp_path / "models.py",
        input_file_type="openapi",
        extra_args=[
            *("--openapi-scopes", "schemas", "api", "--target-python-version", "3.11", "--disable-timestamp"),
            *target,
        ],
    )
