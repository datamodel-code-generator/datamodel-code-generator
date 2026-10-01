"""Exercise generated HTTP packages with independent URL and text charset boundary inputs."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx2")

from tests.conftest import assert_output
from tests.data.python.client_runtime import generated
from tests.data.python.generation_http_boundaries import path_segments, text_charsets

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/http_boundaries"


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
def test_path_segments(backend: str, tmp_path: Path) -> None:
    """Preserve dot segments through real TLS requests in sync and async operations and first pages."""
    assert_output(generated("pagination-targets", backend, tmp_path, path_segments), EXPECTED / f"paths_{backend}.txt")


def test_text_charsets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the selected Content-Type for both decoding requests and encoding responses."""
    assert_output(text_charsets(tmp_path, monkeypatch), EXPECTED / "charsets.txt")
