"""Run the Pydantic v2 BaseModel and dataclass model codecs over really generated models."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.conftest import assert_output
from tests.data.python.model_codec_pydantic import pydantic_codec_report, pydantic_codec_startup_report

if TYPE_CHECKING:
    from pathlib import Path as PathType

DATA = Path(__file__).parents[1] / "data"
CODECS = DATA / "generation_platform/codecs/pydantic"
EXPECTED = DATA / "expected/main/generation_platform/codecs/pydantic"


@pytest.mark.parametrize(
    ("source", "cases"),
    [
        ("pets", "pets-basemodel"),
        ("pets", "pets-dataclass"),
        ("pets", "pets-aliases"),
        ("pets", "pets-noalias"),
        ("pets", "pets-noalias-forbid"),
        ("pets", "pets-generator"),
        ("pets", "pets-strict"),
        ("pets", "pets-forbid"),
        ("pets", "pets-allow"),
        ("pets", "pets-custom"),
        ("pets", "pets-mismatch"),
        ("pets", "pets-variants"),
        ("pets", "pets-schemas-scope"),
        ("pets", "pets-paths-scope"),
        ("shapes", "shapes-basemodel"),
        ("shapes", "shapes-dataclass"),
        ("shapes", "shapes-variants"),
        ("shapes", "shapes-annotated"),
        ("collisions", "collisions"),
        ("zoo", "zoo-collapsed"),
    ],
)
def test_pydantic_codecs(source: str, cases: str, tmp_path: PathType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Decode, project, snapshot, and encode real generated models with presence and direction rules."""
    assert_output(
        pydantic_codec_report(CODECS / f"{source}.yaml", CODECS / f"{cases}.json", tmp_path, monkeypatch),
        EXPECTED / f"{cases}.txt",
    )


@pytest.mark.parametrize("cases", ["pets-startup", "pets-startup-dataclass", "pets-startup-noalias"])
def test_pydantic_codec_startup(cases: str, tmp_path: PathType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse bindings, bundles, and native types that disagree before any value is processed."""
    assert_output(
        pydantic_codec_startup_report(CODECS / "pets.yaml", CODECS / f"{cases}.json", tmp_path, monkeypatch),
        EXPECTED / f"{cases}.txt",
    )
