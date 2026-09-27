"""Compare the builtin codecs of all five model backends over one set of wire cases."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import assert_output
from tests.data.python.model_codec_builtin import backend_comparison_report

DATA = Path(__file__).parents[1] / "data"
CODECS = DATA / "generation_platform/codecs"
EXPECTED = DATA / "expected/main/generation_platform/codecs/structural"


@pytest.mark.parametrize(
    ("source", "cases"),
    [
        ("pydantic/pets", "backends"),
        ("pydantic/containers", "containers"),
    ],
)
def test_backend_comparisons(source: str, cases: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Decode and echo the same wire values through every backend, grouping the backends whose outcome agrees."""
    assert_output(
        backend_comparison_report(
            CODECS / f"{source}.yaml", CODECS / "structural" / f"{cases}.json", tmp_path, monkeypatch
        ),
        EXPECTED / f"{cases}.txt",
    )
