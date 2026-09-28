"""Type-check generated client packages of every backend with a positive and a negative sample."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType
from tests.conftest import assert_output
from tests.data.python.client_typing import client_typing_report

EXPECTED = Path(__file__).parents[1] / "data" / "expected" / "main" / "generation_platform" / "client" / "typing"
ENABLED = "DATAMODEL_CODE_GENERATOR_CLIENT_TYPING_E2E"


@pytest.mark.parametrize(
    "backend",
    [
        DataModelType.PydanticV2BaseModel,
        DataModelType.PydanticV2Dataclass,
        DataModelType.DataclassesDataclass,
        DataModelType.TypingTypedDict,
        DataModelType.MsgspecStruct,
    ],
)
def test_client_typing(backend: DataModelType, tmp_path: Path) -> None:
    """Check the package and positive sample clean, and one error on each marked line of the negative sample."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(client_typing_report(tmp_path, backend), EXPECTED / f"{backend.value.replace('.', '-')}.txt")
