"""Run the structural model codecs that client packages generate for the standard library backends."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.conftest import assert_output
from tests.data.python.model_codec_builtin import builtin_codec_report, builtin_codec_startup_report

if TYPE_CHECKING:
    from pathlib import Path as PathType

DATA = Path(__file__).parents[1] / "data"
CODECS = DATA / "generation_platform/codecs"
EXPECTED = DATA / "expected/main/generation_platform/codecs/structural"


@pytest.mark.parametrize(
    ("source", "cases"),
    [
        ("structural/native-containers", "native-containers-typeddict"),
        ("structural/native-leaves", "native-temporal-dataclass"),
        ("structural/native-leaves", "native-temporal-typeddict"),
        ("structural/native-leaves", "native-leaves-dataclass"),
        ("structural/native-leaves", "native-leaves-typeddict"),
        ("pydantic/pets", "pets-dataclass"),
        ("pydantic/pets", "pets-typeddict"),
        ("pydantic/pets", "pets-msgspec"),
        ("pydantic/shapes", "shapes-msgspec"),
        ("structural/structs", "structs-msgspec"),
        ("structural/structs", "structs-options"),
        ("pydantic/shapes", "shapes-dataclass"),
        ("pydantic/shapes", "shapes-aliases"),
        ("pydantic/loose", "loose-dataclass"),
        ("pydantic/loose", "loose-typeddict"),
        ("structural/stdlib", "stdlib-dataclass"),
        ("structural/stdlib", "stdlib-frozen"),
        ("structural/stdlib", "stdlib-generic"),
        ("structural/stdlib", "stdlib-strip"),
        ("structural/unsupported", "unsupported"),
        ("structural/records", "records-typeddict"),
        ("structural/records", "records-total"),
        ("structural/records", "records-open"),
        ("structural/stdlib", "stdlib-typeddict"),
        ("structural/untyped", "untyped"),
        ("structural/aliases", "aliases-dataclass"),
        ("structural/aliases", "aliases-type"),
        ("structural/aliases", "aliases-typeddict"),
        ("structural/aliases", "aliases-msgspec"),
        ("pydantic/names", "names-msgspec"),
    ],
)
def test_structural_codecs(source: str, cases: str, tmp_path: PathType) -> None:
    """Decode, construct, snapshot, and encode generated models through their final annotations."""
    assert_output(
        builtin_codec_report(CODECS / f"{source}.yaml", CODECS / "structural" / f"{cases}.json", tmp_path),
        EXPECTED / f"{cases}.txt",
    )


@pytest.mark.abnormal_path("generated models or bindings edited out of sync after generation")
@pytest.mark.parametrize(
    ("source", "cases"),
    [
        ("structural/stdlib", "stdlib-startup"),
        ("structural/records", "records-startup"),
        ("structural/structs", "structs-startup"),
    ],
)
def test_structural_codec_startup(source: str, cases: str, tmp_path: PathType) -> None:
    """Refuse edited bindings, bundles, and native types that disagree, and native failures past the wire schema."""
    assert_output(
        builtin_codec_startup_report(CODECS / f"{source}.yaml", CODECS / "structural" / f"{cases}.json", tmp_path),
        EXPECTED / f"{cases}.txt",
    )
