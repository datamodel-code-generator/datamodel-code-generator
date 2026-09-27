"""Type-check the model codec samples with mypy, Pyright, and ty in strict modes."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.conftest import assert_output
from tests.data.python.codec_typing import codec_typing_report

EXPECTED = Path(__file__).parents[1] / "data" / "expected" / "main" / "generation_platform" / "codecs"
ENABLED = "DATAMODEL_CODE_GENERATOR_CODEC_TYPING_E2E"


def test_codec_typing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Check the positive samples clean and the negative ones marked, pinning every error by line and rule."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking the model codec samples")
    assert_output(codec_typing_report(tmp_path, monkeypatch), EXPECTED / "typing.txt")
