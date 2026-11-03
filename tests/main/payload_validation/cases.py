"""Discover runnable schema-derived payload validation cases."""

from __future__ import annotations

import atexit
import json
import os
import pickle
import shutil
import tempfile
import time
import uuid
from contextlib import ExitStack, suppress
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any

from datamodel_code_generator import load_yaml
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, OPEN_API_DATA_PATH

from .constants import (
    EXCLUDED_CASES,
    EXCLUDED_FILES,
    JSON_SCHEMA_KEYS,
    PAYLOAD_BACKEND_EXTRA_ARGS_BY_CASE_ID,
    PAYLOAD_CLASS_NAME,
    SCHEMA_FILE_SUFFIXES,
)
from .models import SchemaCase
from .schema import (
    _apply_component_discriminator_payload_constraints,
    _apply_discriminator_payload_constraints,
    _has_dotted_openapi_component_name,
    _has_read_only_exclusion,
    _normalize_schema_dialect,
    _openapi_components,
    _referenced_openapi_components,
    _schema_exclusion_reason,
    _to_json_schema,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    CandidateFile = tuple[Path, Callable[[Path], Iterator[SchemaCase]]]


def _load_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text) if path.suffix == ".json" else load_yaml(text)
    if not isinstance(data, dict):
        msg = f"{path} did not contain an object"
        raise TypeError(msg)
    return data


def _is_openapi_document(data: dict[str, Any]) -> bool:
    return isinstance(data.get("openapi"), str) or isinstance(data.get("swagger"), str)


def _looks_like_json_schema(data: dict[str, Any]) -> bool:
    return not _is_openapi_document(data) and bool(JSON_SCHEMA_KEYS & data.keys())


def _iter_schema_files(base_path: Path) -> Iterator[Path]:
    for path in sorted(base_path.rglob("*")):
        if path.suffix in SCHEMA_FILE_SUFFIXES:
            yield path


def _relative_case_path(path: Path) -> str:
    for base_name, base_path in (("jsonschema", JSON_SCHEMA_DATA_PATH), ("openapi", OPEN_API_DATA_PATH)):
        if path.is_relative_to(base_path):
            return f"{base_name}/{path.relative_to(base_path).as_posix()}"
    return path.as_posix()


def _iter_openapi_json_body_schemas(document: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    match document.get("paths"):
        case dict() as paths:
            for path_name, path_item in paths.items():
                if not isinstance(path_item, dict):
                    continue
                for method, operation in path_item.items():
                    if method.startswith("x-") or not isinstance(operation, dict):
                        continue
                    if isinstance(request_body := operation.get("requestBody"), dict):
                        yield from _iter_media_type_schemas(f"paths.{path_name}.{method}.requestBody", request_body)
                    if isinstance(responses := operation.get("responses"), dict):
                        for status_code, response in responses.items():
                            if isinstance(response, dict):
                                yield from _iter_media_type_schemas(
                                    f"paths.{path_name}.{method}.responses.{status_code}",
                                    response,
                                )


def _iter_media_type_schemas(prefix: str, container: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    match container.get("content"):
        case dict() as content:
            for media_type, media_type_object in content.items():
                if (
                    media_type == "application/json"
                    and isinstance(media_type_object, dict)
                    and isinstance(schema := media_type_object.get("schema"), dict)
                ):
                    yield f"{prefix}.content.application/json", schema


def _make_jsonschema_case(path: Path, schema: dict[str, Any]) -> SchemaCase:
    case_id = _relative_case_path(path)
    normalized_schema = _normalize_schema_dialect(schema)
    return SchemaCase(
        id=case_id,
        input_file_type="jsonschema",
        source_path=path,
        source_schema=deepcopy(normalized_schema),
        codegen_schema=deepcopy(normalized_schema),
        temp_input_suffix=path.suffix,
        backend_extra_args=PAYLOAD_BACKEND_EXTRA_ARGS_BY_CASE_ID.get(case_id, {}),
    )


def _make_openapi_case(path: Path, document: dict[str, Any], candidate_name: str, schema: dict[str, Any]) -> SchemaCase:
    components = _openapi_components(document)
    referenced_components = _referenced_openapi_components(schema, components)
    json_schema = {
        **_to_json_schema(schema),
        "components": {"schemas": _to_json_schema(referenced_components)},
    }
    _apply_discriminator_payload_constraints(json_schema, schema)
    _apply_component_discriminator_payload_constraints(json_schema)
    codegen_schema = {
        "openapi": document.get("openapi", "3.0.0"),
        "info": (
            document.get("info") if isinstance(document.get("info"), dict) else {"title": "Payload", "version": "1"}
        ),
        "paths": {},
        "components": {
            "schemas": {
                PAYLOAD_CLASS_NAME: schema,
                **referenced_components,
            }
        },
    }
    case_id = f"{_relative_case_path(path)}::{candidate_name}"
    return SchemaCase(
        id=case_id,
        input_file_type="openapi",
        source_path=path,
        source_schema=json_schema,
        codegen_schema=codegen_schema,
        temp_input_suffix=".yaml",
        backend_extra_args=PAYLOAD_BACKEND_EXTRA_ARGS_BY_CASE_ID.get(case_id, {}),
    )


_SUPPORTED: dict[tuple[bytes, bool], bool] = {}
"""Whether each schema met in this process has no exclusion reason, by its pickled form: equal schemas, one verdict."""


def _schema_is_supported(schema: dict[str, Any], *, is_openapi: bool = False) -> bool:
    """Return whether ``_schema_exclusion_reason`` finds no reason, once per distinct schema of the process.

    The checks that only read the schema run first: a schema one of them excludes has some reason in any order, so
    it skips the metaschema validation. A check that fails on a schema leaves the verdict to the full order.
    """
    if (supported := _SUPPORTED.get(key := (pickle.dumps(schema), is_openapi))) is None:
        with suppress(Exception):
            if _has_read_only_exclusion(schema, is_openapi=is_openapi):
                _SUPPORTED[key] = False
                return False
        supported = _SUPPORTED[key] = _schema_exclusion_reason(schema, is_openapi=is_openapi) is None
    return supported


def _case_is_supported(case: SchemaCase) -> bool:
    return _schema_is_supported(case.source_schema, is_openapi=case.input_file_type == "openapi")


def _iter_openapi_schema_candidates(document: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for name, schema in _openapi_components(document).items():
        if isinstance(schema, dict):
            yield f"components.schemas.{name}", schema
    yield from _iter_openapi_json_body_schemas(document)


def _jsonschema_file_candidates(path: Path) -> Iterator[SchemaCase]:
    if _looks_like_json_schema(schema := _load_mapping(path)) and _schema_is_supported(schema):
        yield _make_jsonschema_case(path, schema)


def _openapi_file_candidates(path: Path) -> Iterator[SchemaCase]:
    document = _load_mapping(path)
    if not _is_openapi_document(document) or _has_dotted_openapi_component_name(document):
        return
    for name, schema in _iter_openapi_schema_candidates(document):
        if not _schema_is_supported(schema, is_openapi=True):
            continue
        case = _make_openapi_case(path, document, name, schema)
        if _case_is_supported(case):
            yield case


def _candidate_files() -> list[CandidateFile]:
    """Return each schema fixture file that may hold cases, in discovery order, with the reader of its cases."""
    return [
        (path, read)
        for base_path, read in (
            (JSON_SCHEMA_DATA_PATH, _jsonschema_file_candidates),
            (OPEN_API_DATA_PATH, _openapi_file_candidates),
        )
        for path in _iter_schema_files(base_path)
        if _relative_case_path(path) not in EXCLUDED_FILES
    ]


def _iter_case_candidates() -> Iterator[SchemaCase]:
    for path, read in _candidate_files():
        yield from read(path)


_CHUNKS_PER_WORKER = 4
_CHUNK_TIMEOUT_SECONDS = 600.0
"""How long a worker waits for the cases of a chunk that another worker claimed, for a worker that died reading it."""


def _claim(path: Path) -> bool:
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return False
    return True


def _produce_chunk(result: Path, claim: Path, files: list[CandidateFile]) -> None:
    """Write the cases of a chunk's files to ``result`` unless another process has claimed the chunk.

    A claim stays once the cases are written; reading that fails releases it, so that a waiting process reads the
    chunk itself and fails alike instead of waiting.
    """
    if _claim(claim):
        with ExitStack() as release:
            release.callback(claim.unlink)
            partial = result.with_suffix(f".{os.getpid()}.partial")
            partial.write_bytes(pickle.dumps([list(read(path)) for path, read in files], pickle.HIGHEST_PROTOCOL))
            partial.replace(result)
            release.pop_all()


def _chunk_cases(result: Path, claim: Path, files: list[CandidateFile]) -> list[list[SchemaCase]]:
    deadline = time.monotonic() + _CHUNK_TIMEOUT_SECONDS
    while not result.exists() and time.monotonic() < deadline:
        _produce_chunk(result, claim, files)
        time.sleep(0.005)
    return pickle.loads(result.read_bytes())


def _discover_cases() -> list[SchemaCase]:
    """Return the cases of every candidate file, reading each chunk of the files once among the workers of a run.

    The pytest-xdist workers of one run (one test run id, one parent process) read the chunks no worker has claimed,
    their own first, and take the cases of the other chunks from the files their readers wrote. A process outside
    such a run, or started by a test, reads every chunk itself. Either way each file's cases come from the same
    readers, in the order of the files.
    """
    worker = int(os.environ.get("PYTEST_XDIST_WORKER", "gw0").removeprefix("gw"))
    chunks = int(os.environ.get("PYTEST_XDIST_WORKER_COUNT", "1")) * _CHUNKS_PER_WORKER
    run = os.environ.get("PYTEST_XDIST_TESTRUNUID") or uuid.uuid4().hex
    directory = Path(tempfile.gettempdir()) / f"dcg-payload-cases-{run}-{os.getppid()}"
    directory.mkdir(exist_ok=True)
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    files = _candidate_files()
    first = worker * _CHUNKS_PER_WORKER
    for chunk in (*range(first, chunks), *range(first)):
        _produce_chunk(directory / f"{chunk}.pickle", directory / f"{chunk}.claim", files[chunk::chunks])
    by_file: list[list[SchemaCase]] = [[] for _ in files]
    for chunk in range(chunks):
        by_file[chunk::chunks] = _chunk_cases(
            directory / f"{chunk}.pickle", directory / f"{chunk}.claim", files[chunk::chunks]
        )
    return [case for cases in by_file for case in cases if case.id not in EXCLUDED_CASES]


def discover_unaccounted_files(cases: list[SchemaCase]) -> list[str]:
    """Return schema fixture files that are runnable but neither tested nor excluded."""
    accounted = {_relative_case_path(case.source_path) for case in cases} | set(EXCLUDED_FILES)
    excluded_case_files = {case_id.split("::", 1)[0] for case_id in EXCLUDED_CASES}
    accounted.update(excluded_case_files)
    return sorted({
        case_path
        for case in _iter_case_candidates()
        if (case_path := _relative_case_path(case.source_path)) not in accounted
    })


SCHEMA_CASES = _discover_cases()
