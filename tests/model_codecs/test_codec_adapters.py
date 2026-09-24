"""Register codec adapters, render generated model bindings, and run them from an imported generated package."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.conftest import assert_output
from tests.data.python.model_codec_adapters import adapter_codec_report

if TYPE_CHECKING:
    from pathlib import Path as PathType

DATA = Path(__file__).parents[1] / "data"
ADAPTERS = DATA / "generation_platform/codecs/adapters"
EXPECTED = DATA / "expected/main/generation_platform/codecs/adapters"


@pytest.mark.parametrize(
    ("source", "cases"),
    [
        ("adapters", "adapters"),
        ("rogue", "rogue"),
        ("scripted", "scripted"),
        ("types", "types"),
        ("plans", "plans"),
        ("adapters", "exports"),
        ("modular", "modular"),
        ("enums", "enums"),
        ("leaks", "leaks"),
        ("blanket", "blanket"),
        ("media", "media"),
    ],
)
def test_codec_adapters(source: str, cases: str, tmp_path: PathType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Select, check, render, and run registered adapters with the core's direction and boundary rules."""
    assert_output(
        adapter_codec_report(ADAPTERS / f"{source}.yaml", ADAPTERS / f"{cases}.json", tmp_path, monkeypatch),
        EXPECTED / f"{cases}.txt",
    )
