"""Run the Pydantic v2 model codecs that generated client packages still bind their uses with."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.conftest import assert_output
from tests.data.python.model_codec_builtin import builtin_codec_report, builtin_codec_startup_report

if TYPE_CHECKING:
    from pathlib import Path as PathType

DATA = Path(__file__).parents[1] / "data"
CODECS = DATA / "generation_platform/codecs/pydantic"
EXPECTED = DATA / "expected/main/generation_platform/codecs/pydantic-client"


@pytest.mark.parametrize(
    ("source", "cases"),
    [
        ("pets", "pets-basemodel"),
        ("pets", "pets-aliases"),
        ("pets", "pets-noalias"),
        ("pets", "pets-noalias-forbid"),
        ("pets", "pets-generator"),
        ("pets", "pets-serialization"),
        ("pets", "pets-variants"),
        ("shapes", "shapes-basemodel"),
        ("collisions", "collisions"),
        ("zoo", "zoo-collapsed"),
        ("ids", "ids"),
        ("containers", "containers-noalias"),
        ("types", "types-annotated"),
        ("legacy", "legacy"),
    ],
)
def test_pydantic_codecs(source: str, cases: str, tmp_path: PathType) -> None:
    """Decode, project, snapshot, and encode through generated client codecs with presence and direction rules."""
    assert_output(
        builtin_codec_report(CODECS / f"{source}.yaml", CODECS / f"{cases}.json", tmp_path),
        EXPECTED / f"{cases}.txt",
    )


@pytest.mark.abnormal_path("generated models or bindings edited out of sync after generation")
@pytest.mark.parametrize("cases", ["pets-startup", "pets-startup-noalias"])
def test_pydantic_codec_startup(cases: str, tmp_path: PathType) -> None:
    """Refuse edited client bindings, bundles, and native types that disagree before any value is processed."""
    assert_output(
        builtin_codec_startup_report(CODECS / "pets.yaml", CODECS / f"{cases}.json", tmp_path),
        EXPECTED / f"{cases}.txt",
    )
