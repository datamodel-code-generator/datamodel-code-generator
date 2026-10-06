"""Generate servers and clients with registered codec adapters, and run cases through their imported bindings."""

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
        ("types", "listed"),
        ("plans-accepted", "plans"),
        ("enums", "enums"),
        ("leaks", "leaks"),
        ("media", "media"),
        ("enums", "structural"),
        ("enums", "structural-typeddict"),
        ("enums", "isolated"),
        ("enums", "structural-msgspec"),
        ("unplanned", "unplanned"),
        ("unplanned", "unplanned-client"),
    ],
)
def test_codec_adapters(source: str, cases: str, tmp_path: PathType) -> None:
    """Select, render, and run registered adapters under the core's direction and boundary rules, or refuse them."""
    assert_output(
        adapter_codec_report(ADAPTERS / f"{source}.yaml", ADAPTERS / f"{cases}.json", tmp_path),
        EXPECTED / f"{cases}.txt",
    )
