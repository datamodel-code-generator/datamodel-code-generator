"""Compare parameter styles, media types, and the runtime's imports against handwritten expectations."""

from __future__ import annotations

from pathlib import Path

from tests.api_generation.support.model_codec_reports import (
    alias_hint_report,
    media_report,
    parameter_decoding_report,
    parameter_encoding_report,
    runtime_import_report,
)
from tests.conftest import assert_output

DATA = Path(__file__).parents[1] / "data"
CODECS = DATA / "generation_platform/codecs"
EXPECTED = DATA / "expected/main/generation_platform/codecs"
RUNTIME = Path(__file__).parents[2] / "src/datamodel_code_generator/_runtime"


def test_parameter_style_encoding() -> None:
    """Encode official OpenAPI style examples and boundary values, then decode them again."""
    assert_output(parameter_encoding_report(CODECS / "parameter-encoding.json"), EXPECTED / "parameter-encoding.txt")


def test_parameter_raw_decoding() -> None:
    """Decode ordered raw occurrences for every location without inventing omission or defaults."""
    assert_output(parameter_decoding_report(CODECS / "parameter-decoding.json"), EXPECTED / "parameter-decoding.txt")


def test_media_types_and_error_records() -> None:
    """Normalize media identities, classify their builtin forms, and refuse text outside its charset."""
    assert_output(media_report(CODECS / "media-types.json"), EXPECTED / "media-types.txt")


def test_wire_alias_hints() -> None:
    """Resolve the recursive JSON aliases from another module through private import aliases."""
    assert_output(alias_hint_report(), EXPECTED / "alias-hints.txt")


def test_runtime_imports_stay_embeddable() -> None:
    """Keep the runtime importable from a generated package without the generator distribution."""
    assert_output(runtime_import_report(RUNTIME), EXPECTED / "runtime-imports.txt")
