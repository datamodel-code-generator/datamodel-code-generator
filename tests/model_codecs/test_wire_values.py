"""Compare wire snapshots and JSON media against handwritten expectations."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from tests.conftest import assert_output
from tests.data.python.model_codec_reports import (
    alias_hint_report,
    json_media_report,
    media_report,
    missing_format_report,
    parameter_decoding_report,
    parameter_encoding_report,
    pattern_report,
    runtime_import_report,
    schema_report,
    wire_value_report,
)

if TYPE_CHECKING:
    import pytest

DATA = Path(__file__).parents[1] / "data"
CODECS = DATA / "generation_platform/codecs"
EXPECTED = DATA / "expected/main/generation_platform/codecs"
RUNTIME = Path(__file__).parents[2] / "src/datamodel_code_generator/_runtime"


def test_json_media_round_trips() -> None:
    """Keep exact numbers, member order, and presence while rejecting ambiguous JSON bodies."""
    assert_output(json_media_report(CODECS / "json-media.json"), EXPECTED / "json-media.txt")


def test_wire_value_copies() -> None:
    """Copy JSON-domain values independently and reject objects outside the wire domain."""
    assert_output(wire_value_report(), EXPECTED / "wire-values.txt")


def test_ecma_patterns_through_re2() -> None:
    """Match ECMA-262 Unicode-mode semantics with RE2 and enforce every documented limit."""
    assert_output(pattern_report(CODECS / "patterns.json"), EXPECTED / "patterns.txt")


def test_offline_schema_validation() -> None:
    """Validate against bundled 2020-12 resources with exact numbers, RE2 patterns, and precise pointers."""
    assert_output(schema_report(CODECS / "schema-validation.json"), EXPECTED / "schema-validation.txt")


def test_parameter_style_encoding() -> None:
    """Encode official OpenAPI style examples and boundary values, then decode them again."""
    assert_output(parameter_encoding_report(CODECS / "parameter-encoding.json"), EXPECTED / "parameter-encoding.txt")


def test_parameter_raw_decoding() -> None:
    """Decode ordered raw occurrences for every location without inventing omission or defaults."""
    assert_output(parameter_decoding_report(CODECS / "parameter-decoding.json"), EXPECTED / "parameter-decoding.txt")


def test_media_types_and_error_records() -> None:
    """Normalize media identities and keep issue records finite, value-free, and identifier-coded."""
    assert_output(media_report(CODECS / "media-types.json"), EXPECTED / "media-types.txt")


def test_missing_format_dependency_stops_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail validator construction when an installed format checker set lacks a builtin format."""
    assert_output(
        missing_format_report(monkeypatch, CODECS / "schema-validation.json"), EXPECTED / "missing-format.txt"
    )


def test_wire_alias_hints() -> None:
    """Resolve the recursive JSON and wire aliases from another module through private import aliases."""
    assert_output(alias_hint_report(), EXPECTED / "alias-hints.txt")


def test_runtime_imports_stay_embeddable() -> None:
    """Keep the runtime importable from a generated package without the generator distribution."""
    assert_output(runtime_import_report(RUNTIME), EXPECTED / "runtime-imports.txt")
