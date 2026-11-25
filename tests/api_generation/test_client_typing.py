"""Type-check generated client packages of every backend with a positive and a negative sample."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import DataModelType
from tests.api_generation.support.client_publication import capability_arguments
from tests.api_generation.support.client_typing import (
    client_dependency_typing_report,
    client_regenerated_typing_report,
    client_typing_report,
)
from tests.conftest import assert_output
from tests.main.conftest import run_main_with_args

if TYPE_CHECKING:
    from pathlib import Path

ENABLED = "DATAMODEL_CODE_GENERATOR_CLIENT_TYPING_E2E"
PACKAGE_SAMPLES = {"tools": ("tools",), "tools-mcp": ("tools", "mcp_server")}
"""The samples a package case checks besides the package: the tool tables over each client kind."""


@pytest.mark.parametrize("case", ["no-success", "no-success-unpack", "modelless-bodies"])
@pytest.mark.parametrize("backend", list(DataModelType))
def test_client_typing_no_success(backend: DataModelType, case: str, tmp_path: Path) -> None:
    """Type-check error-only methods in both signature styles, and the codecs of a package with no model classes."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(client_typing_report(tmp_path, backend, case, ()))


@pytest.mark.parametrize(
    "profile",
    [
        "minimal",
        "minimal-pydantic-dataclass",
        "minimal-dataclass",
        "minimal-typeddict",
        "minimal-msgspec",
        "all",
        "all-pydantic-dataclass",
        "all-dataclass",
        "all-typeddict",
        "all-msgspec",
    ],
)
def test_client_typing_printed_dependencies(profile: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Check a package of each backend clean where only the dependencies its generation prints are installed.

    The other lanes check against an environment that holds every model library, where a package that imports a
    library its backend does not need still resolves.
    """
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    run_main_with_args(capability_arguments(profile, tmp_path / "project"))
    assert_output(
        client_dependency_typing_report(tmp_path / "project", tmp_path / "environment", capsys.readouterr().err)
    )


@pytest.mark.parametrize("case", ["pets-protocols", "pets-protocols-unpack"])
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
    assert_output(client_typing_report(tmp_path, backend, case))


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
    assert_output(client_typing_report(tmp_path, backend, case, ("fields",)))


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
    assert_output(client_typing_report(tmp_path, backend, "pagination", ("pagination",)))


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
def test_client_typing_polling(backend: DataModelType, tmp_path: Path) -> None:
    """Check polling helpers: typed results and polls, sync and asyncio handles, and misuses of each."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(client_typing_report(tmp_path, backend, "polling", ("polling",)))


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
def test_client_typing_uploads(backend: DataModelType, tmp_path: Path) -> None:
    """Check upload helpers: typed results, sync and asyncio handles and sources, and misuses of each."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(client_typing_report(tmp_path, backend, "uploads", ("uploads",)))


@pytest.mark.parametrize(
    "case",
    [
        "pagination-targets",
        "pagination-querystring",
        "pagination-counts",
        "pagination-links",
        "streams-mixed",
        "type-spellings-cycle",
        "type-spellings-exact",
        "type-spellings-legacy",
        "type-spellings-containers",
        "naming",
        "naming-snake",
        "type-checking-imports-on",
        "asyncapi-market-v3",
        "asyncapi-events",
        "tools",
        "tools-mcp",
    ],
)
def test_client_typing_package(case: str, tmp_path: Path) -> None:
    """Check helper target, naming, and type-checking import packages.

    The helper target packages write each kind of request target, bindings, positions, and followed URLs.
    The naming packages check methods and arguments named by the model's naming rules, with their suffixes.
    The type-checking import package imports the names its annotations alone use only for type checkers, and the
    AsyncAPI packages open WebSocket channels, one of which sends no message, and server-sent event channels. The
    tools package builds each tool table over its own client kind and types its search, schema, and call results, and
    the MCP package builds its server over the asyncio client and serves it through a factory of them.
    """
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(
        client_typing_report(tmp_path, DataModelType.PydanticV2BaseModel, case, PACKAGE_SAMPLES.get(case, ()))
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
    assert_output(client_typing_report(tmp_path, backend, "webhooks-typing", ("webhook_helpers",)))


def test_client_typing_public_keys(tmp_path: Path) -> None:
    """Check Ed25519 and RSA-PSS helpers next to an HMAC one: key wrappers, events, and misuses of each key type."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(
        client_typing_report(
            tmp_path, DataModelType.PydanticV2BaseModel, "webhooks-public-keys", ("public_key_helpers",)
        )
    )


def test_client_typing_adapters(tmp_path: Path) -> None:
    """Check adapter, mapped, and unsigned helpers: one invariant key type, union events, and misuses of each."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(
        client_typing_report(tmp_path, DataModelType.PydanticV2BaseModel, "webhooks-adapters", ("adapter_helpers",))
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
    assert_output(client_typing_report(tmp_path, backend, "caching-typing", ("cache_helpers",)))


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
    assert_output(client_typing_report(tmp_path, backend, "streams", ("streams",)))


def test_client_typing_stream_resume(tmp_path: Path) -> None:
    """Check resumable stream helpers: checkpoints, sync and asyncio resumes, and misuses of each."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(
        client_typing_report(tmp_path, DataModelType.PydanticV2BaseModel, "stream-resume", ("stream_resume",))
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
    assert_output(client_typing_report(tmp_path, backend, "ndjson", ("ndjson",)))


@pytest.mark.parametrize(
    "backend",
    [DataModelType.PydanticV2BaseModel, DataModelType.PydanticV2Dataclass, DataModelType.MsgspecStruct],
)
def test_client_typing_sockets(backend: DataModelType, tmp_path: Path) -> None:
    """Check WebSocket helpers: typed sessions, messages, and receipts, sync and asyncio, and misuses of each.

    Stdlib dataclasses and TypedDicts cannot convert the received union, whose members only their schemas tell apart.
    """
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(client_typing_report(tmp_path, backend, "sockets", ("sockets",)))


def test_client_typing_missing(tmp_path: Path) -> None:
    """Check a package of Pydantic dataclasses whose unset optional members hold MISSING, and the misuses of them."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(client_typing_report(tmp_path, DataModelType.PydanticV2Dataclass, "missing-sentinel", ("missing",)))


@pytest.mark.parametrize("backend", [DataModelType.PydanticV2BaseModel, DataModelType.PydanticV2Dataclass])
def test_client_typing_unions(backend: DataModelType, tmp_path: Path) -> None:
    """Check a package whose responses are unions of models, with each union's codec typed for its members.

    Only Pydantic converts every union of the package, whose members other backends tell apart only by their schemas.
    """
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(client_typing_report(tmp_path, backend, "unions", ("unions",)))


def test_client_typing_compression(tmp_path: Path) -> None:
    """Check request coding settings on client, view, call, and helper options."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(client_typing_report(tmp_path, DataModelType.PydanticV2BaseModel, "compression", ("compression",)))


def test_client_typing_auth(tmp_path: Path) -> None:
    """Check constructor credentials of each scheme kind, OAuth providers, and native Auth settings."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(client_typing_report(tmp_path, DataModelType.PydanticV2BaseModel, "auth", ("auth",)))


@pytest.mark.parametrize("case", ["media", "pets"])
def test_client_typing_media_ranges(case: str, tmp_path: Path) -> None:
    """Keep exact response overloads narrow while accepting concrete types within success ranges."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    sample = "media_ranges" if case == "media" else "media_ranges_only"
    target = "pets/resources/files" if case == "media" else "pets/resources/pets/photos"
    assert_output(
        client_typing_report(tmp_path, DataModelType.PydanticV2BaseModel, case, (sample,), package_targets=(target,))
    )


def test_client_typing_regenerated_user_code(tmp_path: Path) -> None:
    """Check user code clean against one API version, then each checker's errors where the next version breaks it."""
    if not os.environ.get(ENABLED):
        pytest.skip(f"{ENABLED} enables type checking generated packages")
    assert_output(client_regenerated_typing_report(tmp_path))
