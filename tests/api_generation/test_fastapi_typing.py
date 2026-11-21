"""Type-check generated FastAPI packages, application samples, and a user's service across a regeneration."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import DataModelType
from tests.api_generation.support.fastapi_typing import (
    fastapi_bodies_report,
    fastapi_extras_report,
    fastapi_missing_report,
    fastapi_parameters_report,
    fastapi_typing_report,
    fastapi_update_report,
)
from tests.conftest import assert_output

if TYPE_CHECKING:
    from pathlib import Path

ENABLED = "DATAMODEL_CODE_GENERATOR_FASTAPI_TYPING_E2E"


@pytest.mark.parametrize(
    "backend",
    [
        pytest.param(DataModelType.PydanticV2BaseModel, id="applications-basemodel"),
        pytest.param(DataModelType.PydanticV2Dataclass, id="applications-dataclass"),
    ],
)
def test_fastapi_typing_applications(backend: DataModelType, tmp_path: Path) -> None:
    """Check the secured package and its samples: no error in the positive one, one on each marked negative line."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(fastapi_typing_report(tmp_path, backend))


def test_fastapi_typing_missing(tmp_path: Path) -> None:
    """Check a service of Pydantic dataclasses that reads and leaves out unset members through MISSING."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(fastapi_missing_report(tmp_path))


def test_fastapi_typing_bodies(tmp_path: Path) -> None:
    """Check the package of a server that reads its bodies through adapters, with its copied request runtime."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(fastapi_bodies_report(tmp_path))


def test_fastapi_typing_extras(tmp_path: Path) -> None:
    """Check the package of a server that writes the extra members of Pydantic dataclasses, with its copied writer."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(fastapi_extras_report(tmp_path))


def test_fastapi_typing_parameters(tmp_path: Path) -> None:
    """Check the package of a server whose routes have renamed path parameters, with its copied route classes."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(fastapi_parameters_report(tmp_path))


def test_fastapi_typing_updates(tmp_path: Path) -> None:
    """Check a user's service against the first document, then against the package regenerated from the second."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(fastapi_update_report(tmp_path))
