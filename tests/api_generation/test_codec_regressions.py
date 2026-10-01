"""Exercise split model codecs and parameter adapters through generated clients and servers."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("httpx2")

from tests.conftest import assert_output
from tests.data.python.codec_regressions import BACKENDS, codec_regression_report

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/regressions"


@pytest.mark.parametrize("case", ["split", "adapters"])
@pytest.mark.parametrize(
    ("target", "backend"),
    [("client", backend) for backend in BACKENDS] + [("fastapi", backend) for backend in BACKENDS[:2]],
)
def test_codec_regressions(
    case: str, target: str, backend: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decode each directional union member and honor registered adapters at the HTTP boundary."""
    if target == "fastapi":
        pytest.importorskip("fastapi")
    assert_output(
        codec_regression_report(case, target, backend, tmp_path, monkeypatch),
        EXPECTED / f"{case}-{target}-{backend}.txt",
    )
