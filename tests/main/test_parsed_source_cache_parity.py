"""Parity tests for the process-local parsed source cache."""

from __future__ import annotations

import marshal
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import (
    InputFileType,
    _clear_parser_source_data_cache,
    _is_parsed_source_cache_enabled,
    _parser_source_data_cache,
    enable_parsed_source_cache,
    load_data,
    load_data_from_path,
    load_yaml,
)
from datamodel_code_generator import _source as source_loading
from tests.conftest import assert_mutable_copy_is_isolated, assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, OPEN_API_DATA_PATH, run_main_with_args

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Any


def test_source_loading_facade_keeps_compatible_exports() -> None:
    """Keep public source helpers identical while parsers use their neutral owner."""
    exports = {
        "enable_parsed_source_cache": (enable_parsed_source_cache, source_loading.enable_parsed_source_cache),
        "load_data": (load_data, source_loading.load_data),
        "load_data_from_path": (load_data_from_path, source_loading.load_data_from_path),
        "load_yaml": (load_yaml, source_loading.load_yaml),
    }
    report = "\n".join(
        f"{name}: identity={public is neutral}, module={public.__module__}"
        for name, (public, neutral) in exports.items()
    )

    assert_output(
        report + "\n", Path(__file__).parents[1] / "data" / "expected" / "main" / "source_loading_boundary.txt"
    )


def _input_file_type_option(input_file_type: InputFileType) -> str:
    match input_file_type:
        case InputFileType.JsonSchema:
            return "jsonschema"
        case InputFileType.OpenAPI:
            return "openapi"
        case _:  # pragma: no cover
            msg = f"Unsupported parsed source cache parity input type: {input_file_type}"
            raise AssertionError(msg)


def _build_generate_args(
    input_path: Path,
    output_path: Path,
    input_file_type: InputFileType,
    extra_args: Sequence[str] | None,
) -> list[str]:
    args = [
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--input-file-type",
        _input_file_type_option(input_file_type),
        "--disable-timestamp",
        "--formatters",
        "builtin",
    ]
    if extra := list(extra_args or ()):
        return [*args, *extra]
    return args


def _run_generate_with_parsed_source_cache(
    input_path: Path,
    output_path: Path,
    input_file_type: InputFileType,
    *,
    use_cache: bool,
    extra_args: Sequence[str] | None,
) -> int:
    _clear_parser_source_data_cache()
    run_main_with_args(
        _build_generate_args(input_path, output_path, input_file_type, extra_args),
        use_parsed_source_cache=use_cache,
        use_builtin_default_formatter=False,
    )
    return len(_parser_source_data_cache)


@pytest.mark.allow_direct_assert
def test_parsed_source_cache_scope_restore_order_and_idempotence() -> None:
    """Keep cache enabled until every active scope has been restored."""
    restore_outer = enable_parsed_source_cache()
    restore_inner = enable_parsed_source_cache()

    try:
        restore_outer()
        assert _is_parsed_source_cache_enabled()
        restore_outer()
        assert _is_parsed_source_cache_enabled()
    finally:
        restore_outer()
        restore_inner()
    assert not _is_parsed_source_cache_enabled()

    restore_outer = enable_parsed_source_cache()
    restore_inner = enable_parsed_source_cache()
    try:
        restore_inner()
        assert _is_parsed_source_cache_enabled()
    finally:
        restore_inner()
        restore_outer()
    assert not _is_parsed_source_cache_enabled()


@pytest.mark.allow_direct_assert
def test_parsed_source_cache_scope_thread_safety() -> None:
    """Restore concurrent cache scopes without losing another scope's state."""
    worker_count = 8
    ready = Barrier(worker_count + 1)
    release = Event()

    def enable_and_restore() -> None:
        restore = enable_parsed_source_cache()
        try:
            ready.wait(timeout=5)
            release.wait(timeout=5)
        finally:
            restore()
            restore()

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(enable_and_restore) for _ in range(worker_count)]
        try:
            ready.wait(timeout=5)
            assert _is_parsed_source_cache_enabled()
        finally:
            release.set()
        for future in futures:
            future.result()

    assert not _is_parsed_source_cache_enabled()

    restore = enable_parsed_source_cache()
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(restore) for _ in range(worker_count)]
        for future in futures:
            future.result()

    assert not _is_parsed_source_cache_enabled()


def _mutate_cached_schema(schema: dict[str, Any], input_file_type: InputFileType) -> None:
    match input_file_type:
        case InputFileType.JsonSchema:
            schema["properties"]["firstName"]["type"] = "integer"
        case InputFileType.OpenAPI:
            schema["components"]["schemas"]["Pet"]["properties"]["name"]["type"] = "integer"
        case _:  # pragma: no cover
            msg = f"Unsupported parsed source cache mutation input type: {input_file_type}"
            raise AssertionError(msg)


@pytest.mark.allow_direct_assert
def test_parser_source_cache_skips_unserializable_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep parsed source loading available when cache serialization fails."""
    schema_path = tmp_path / "schema.json"
    schema_path.write_text('{"properties": {"name": {"type": "string"}}}', encoding="utf-8")
    _clear_parser_source_data_cache()
    original = load_data_from_path(schema_path, "utf-8")

    def raise_serialization_error(*_args: object, **_kwargs: object) -> bytes:
        raise ValueError

    monkeypatch.setattr(marshal, "dumps", raise_serialization_error)
    assert_mutable_copy_is_isolated(
        original=original,
        copied=load_data_from_path(schema_path, "utf-8"),
        mutate_copied=lambda value: value["properties"]["name"].update(type="integer"),
        label="uncached unserializable JSON source",
    )

    assert not _parser_source_data_cache


@pytest.mark.allow_direct_assert
def test_parser_source_cache_isolates_non_string_yaml_keys(tmp_path: Path) -> None:
    """Preserve non-string YAML keys in independent cached values."""
    schema_path = tmp_path / "schema.yaml"
    schema_path.write_text("1:\n  nested: true\n", encoding="utf-8")
    _clear_parser_source_data_cache()
    original = load_data_from_path(schema_path, "utf-8")

    assert_mutable_copy_is_isolated(
        original=original,
        copied=load_data_from_path(schema_path, "utf-8"),
        mutate_copied=lambda value: value[1].update(nested=False),
        label="cached non-string-key YAML source",
    )
    assert _parser_source_data_cache


@pytest.mark.allow_direct_assert
def test_parser_source_cache_preserves_yaml_graph_sharing(tmp_path: Path) -> None:
    """Preserve the YAML backend's graph-sharing semantics in an isolated cached source."""
    schema_path = tmp_path / "schema.yaml"
    schema_path.write_text(
        "shared: &shared\n  type: string\nfirst: *shared\nsecond: *shared\n",
        encoding="utf-8",
    )
    _clear_parser_source_data_cache()
    original = load_data_from_path(schema_path, "utf-8")
    load_data_from_path(schema_path, "utf-8")
    cached = load_data_from_path(schema_path, "utf-8")

    assert (cached["first"] is cached["second"]) == (original["first"] is original["second"])
    assert_mutable_copy_is_isolated(
        original=original,
        copied=cached,
        mutate_copied=lambda value: value["first"].update(type="integer"),
        label="cached YAML aliases",
    )


@pytest.mark.parametrize(
    ("input_path", "input_file_type", "extra_args"),
    [
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "external_definitions_root.json",
            InputFileType.JsonSchema,
            None,
            id="jsonschema-external-definitions",
        ),
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "all_of_ref" / "test.json",
            InputFileType.JsonSchema,
            ["--class-name", "Test"],
            id="jsonschema-relative-all-of",
        ),
        pytest.param(
            OPEN_API_DATA_PATH / "paths_external_ref" / "openapi.yaml",
            InputFileType.OpenAPI,
            ["--openapi-scopes", "paths"],
            id="openapi-paths-external-ref",
        ),
        pytest.param(
            OPEN_API_DATA_PATH / "external_ref_mapping" / "api.yaml",
            InputFileType.OpenAPI,
            None,
            id="openapi-external-ref-mapping",
        ),
    ],
)
@pytest.mark.allow_direct_assert
def test_generate_output_matches_with_and_without_parsed_source_cache(
    input_path: Path,
    input_file_type: InputFileType,
    extra_args: Sequence[str] | None,
    tmp_path: Path,
) -> None:
    """Keep representative ref-heavy generation output stable across cache states."""
    cached_output = tmp_path / "cached.py"
    uncached_output = tmp_path / "uncached.py"

    cached_entry_count = _run_generate_with_parsed_source_cache(
        input_path,
        cached_output,
        input_file_type,
        use_cache=True,
        extra_args=extra_args,
    )
    uncached_entry_count = _run_generate_with_parsed_source_cache(
        input_path,
        uncached_output,
        input_file_type,
        use_cache=False,
        extra_args=extra_args,
    )

    assert cached_entry_count > 0
    assert uncached_entry_count == 0
    assert_output(uncached_output.read_text(encoding="utf-8"), cached_output)


@pytest.mark.parametrize(
    ("input_path", "input_file_type"),
    [
        pytest.param(
            JSON_SCHEMA_DATA_PATH / "person.json",
            InputFileType.JsonSchema,
            id="json",
        ),
        pytest.param(
            OPEN_API_DATA_PATH / "api.yaml",
            InputFileType.OpenAPI,
            id="yaml",
        ),
    ],
)
def test_generate_output_ignores_external_cached_source_mutation(
    input_path: Path,
    input_file_type: InputFileType,
    tmp_path: Path,
) -> None:
    """Keep generated output stable after a caller mutates a cached parsed value."""
    cached_output = tmp_path / "cached.py"
    uncached_output = tmp_path / "uncached.py"
    _clear_parser_source_data_cache()
    original = load_data_from_path(input_path, "utf-8")
    cached = load_data_from_path(input_path, "utf-8")
    assert_mutable_copy_is_isolated(
        original=original,
        copied=cached,
        mutate_copied=lambda value: _mutate_cached_schema(value, input_file_type),
        label=f"cached {input_file_type.value} source",
    )

    run_main_with_args(
        _build_generate_args(input_path, cached_output, input_file_type, None),
        use_parsed_source_cache=True,
        use_builtin_default_formatter=False,
    )
    _clear_parser_source_data_cache()
    run_main_with_args(
        _build_generate_args(input_path, uncached_output, input_file_type, None),
        use_parsed_source_cache=False,
        use_builtin_default_formatter=False,
    )

    assert_output(uncached_output.read_text(encoding="utf-8"), cached_output)
