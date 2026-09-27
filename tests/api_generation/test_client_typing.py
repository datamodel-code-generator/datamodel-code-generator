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


@pytest.mark.parametrize("case", ["pets", "pets-unpack"])
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
def test_client_typing(backend: DataModelType, case: str, tmp_path: Path) -> None:
    """Check the package and positive samples clean, and each checker's errors on the marked lines of the negatives.

    Both signature styles take the same calls; the negatives pin where a checker treats unpacked keywords differently.
    """
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    suffix = case.removeprefix("pets")
    assert_output(
        client_typing_report(tmp_path, backend, case), EXPECTED / f"{backend.value.replace('.', '-')}{suffix}.txt"
    )
