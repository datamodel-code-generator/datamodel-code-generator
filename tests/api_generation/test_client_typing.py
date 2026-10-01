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


@pytest.mark.parametrize(
    "backend",
    [
        DataModelType.PydanticV2BaseModel,
        DataModelType.PydanticV2Dataclass,
        DataModelType.DataclassesDataclass,
        DataModelType.TypingTypedDict,
    ],
)
def test_client_typing_arguments(backend: DataModelType, tmp_path: Path) -> None:
    """Check a package that allows Pydantic argument validation, with its private argument checks."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(
        client_typing_report(tmp_path, backend, "pets-arguments"),
        EXPECTED / f"{backend.value.replace('.', '-')}-arguments.txt",
    )


@pytest.mark.parametrize("case", ["fields-both", "fields-unpack"])
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
def test_client_typing_fields(backend: DataModelType, case: str, tmp_path: Path) -> None:
    """Check calls that give a body's fields: each media's overloads, the witnesses of an optional body, and views."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    suffix = case.removeprefix("fields").removeprefix("-both")
    assert_output(
        client_typing_report(tmp_path, backend, case, ("fields",)),
        EXPECTED / f"{backend.value.replace('.', '-')}-fields{suffix}.txt",
    )


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
def test_client_typing_pagination(backend: DataModelType, tmp_path: Path) -> None:
    """Check pagination helpers: typed items and pages, sync and asyncio pagers, and misuses of each."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(
        client_typing_report(tmp_path, backend, "pagination", ("pagination",)),
        EXPECTED / f"{backend.value.replace('.', '-')}-pagination.txt",
    )


@pytest.mark.parametrize(
    "case", ["pagination-targets", "pagination-querystring", "pagination-counts", "pagination-links", "streams-mixed"]
)
def test_client_typing_package(case: str, tmp_path: Path) -> None:
    """Check packages whose helpers write each kind of request target, bindings, positions, and followed URLs."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(
        client_typing_report(tmp_path, DataModelType.PydanticV2BaseModel, case, ()),
        EXPECTED / f"pydantic_v2-BaseModel-{case}.txt",
    )


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
def test_client_typing_webhooks(backend: DataModelType, tmp_path: Path) -> None:
    """Check webhook helpers: event and key types in both modes, replay stores, and misuses of each."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(
        client_typing_report(tmp_path, backend, "webhooks-typing", ("webhook_helpers",)),
        EXPECTED / f"{backend.value.replace('.', '-')}-webhooks.txt",
    )


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
def test_client_typing_caches(backend: DataModelType, tmp_path: Path) -> None:
    """Check cache helpers: typed results in both modes, stores, invalidation, mutations, and misuses of each."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(
        client_typing_report(tmp_path, backend, "caching-typing", ("cache_helpers",)),
        EXPECTED / f"{backend.value.replace('.', '-')}-caches.txt",
    )


def test_client_typing_public_keys(tmp_path: Path) -> None:
    """Check Ed25519 and RSA-PSS helpers next to an HMAC one: key wrappers, events, and misuses of each key type."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(
        client_typing_report(
            tmp_path, DataModelType.PydanticV2BaseModel, "webhooks-public-keys", ("public_key_helpers",)
        ),
        EXPECTED / "pydantic_v2-BaseModel-public-keys.txt",
    )


def test_client_typing_adapters(tmp_path: Path) -> None:
    """Check adapter, mapped, and unsigned helpers: one invariant key type, union events, and misuses of each."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(
        client_typing_report(tmp_path, DataModelType.PydanticV2BaseModel, "webhooks-adapters", ("adapter_helpers",)),
        EXPECTED / "pydantic_v2-BaseModel-adapters.txt",
    )


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
def test_client_typing_streams(backend: DataModelType, tmp_path: Path) -> None:
    """Check SSE helpers: typed events and data, unknown events, sync and asyncio streams, and misuses of each."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(
        client_typing_report(tmp_path, backend, "streams", ("streams",)),
        EXPECTED / f"{backend.value.replace('.', '-')}-streams.txt",
    )


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
def test_client_typing_ndjson(backend: DataModelType, tmp_path: Path) -> None:
    """Check NDJSON helpers: typed records and data, unknown records, sync and asyncio streams, and misuses of each."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(
        client_typing_report(tmp_path, backend, "ndjson", ("ndjson",)),
        EXPECTED / f"{backend.value.replace('.', '-')}-ndjson.txt",
    )


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
def test_client_typing_unions(backend: DataModelType, tmp_path: Path) -> None:
    """Check a package whose responses are unions of models, with each union's codec typed for its members."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(
        client_typing_report(tmp_path, backend, "unions-schema", ("unions",)),
        EXPECTED / f"{backend.value.replace('.', '-')}-unions.txt",
    )
