"""Type-check generated FastAPI packages, application samples, and a user's service across a regeneration."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType
from tests.conftest import assert_output
from tests.data.python.fastapi_typing import fastapi_typing_report, fastapi_update_report

EXPECTED = Path(__file__).parents[1] / "data" / "expected" / "main" / "generation_platform" / "fastapi" / "typing"
ENABLED = "DATAMODEL_CODE_GENERATOR_FASTAPI_TYPING_E2E"


@pytest.mark.parametrize(
    ("backend", "name"),
    [
        (DataModelType.PydanticV2BaseModel, "applications-basemodel"),
        (DataModelType.PydanticV2Dataclass, "applications-dataclass"),
    ],
)
def test_fastapi_typing_applications(backend: DataModelType, name: str, tmp_path: Path) -> None:
    """Check the secured package and its samples: no error in the positive one, one on each marked negative line."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(fastapi_typing_report(tmp_path, backend), EXPECTED / f"{name}.txt")


def test_fastapi_typing_updates(tmp_path: Path) -> None:
    """Check a user's service against the first document, then against the package regenerated from the second."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(fastapi_update_report(tmp_path), EXPECTED / "updates.txt")
