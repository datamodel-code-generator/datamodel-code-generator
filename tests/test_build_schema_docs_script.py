"""Tests for schema documentation generation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import build_schema_docs
from tests.conftest import assert_output

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_schema_docs.py"
EXPECTED_SCHEMA_DOCS_PATH = Path(__file__).resolve().parent / "data" / "expected" / "schema_docs"


def _assert_schema_docs_check_is_up_to_date() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            f"build_schema_docs.py --check failed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            pytrace=False,
        )


def test_build_schema_docs_check_is_up_to_date() -> None:
    """Generated schema docs are committed."""
    _assert_schema_docs_check_is_up_to_date()


def test_build_schema_docs_check_reports_captured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Schema docs check failures include captured process output."""

    def fail_check(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="generated diff", stderr="update hint")

    monkeypatch.setattr(subprocess, "run", fail_check)

    with pytest.raises(pytest.fail.Exception, match="stdout:\ngenerated diff\nstderr:\nupdate hint"):
        _assert_schema_docs_check_is_up_to_date()


def test_supported_formats_features_content() -> None:
    """Supported format feature docs are generated from schema metadata."""
    assert_output(
        build_schema_docs.generate_supported_features_content(),
        EXPECTED_SCHEMA_DOCS_PATH / "supported_features.txt",
    )


def test_supported_data_types_features_content() -> None:
    """Supported data type feature docs are generated from schema metadata."""
    assert_output(
        build_schema_docs.generate_supported_data_types_content(),
        EXPECTED_SCHEMA_DOCS_PATH / "supported_data_types_features.txt",
    )


def test_supported_formats_data_format_content() -> None:
    """Supported format data type docs are generated from schema metadata."""
    assert_output(
        build_schema_docs.generate_data_format_content(include_schema_types=False),
        EXPECTED_SCHEMA_DOCS_PATH / "supported_formats_data_formats.txt",
    )


def test_supported_data_types_format_content() -> None:
    """Supported data type format docs are generated from schema metadata."""
    assert_output(
        build_schema_docs.generate_supported_data_types_format_content(),
        EXPECTED_SCHEMA_DOCS_PATH / "supported_data_types_formats.txt",
    )


def test_input_format_guide_content() -> None:
    """Input format guide docs are generated from parser metadata."""
    assert_output(
        build_schema_docs.generate_input_format_guide_content(),
        EXPECTED_SCHEMA_DOCS_PATH / "input_format_guide.txt",
    )


def test_update_docs_target_replaces_marked_section(tmp_path: Path) -> None:
    """Generated docs targets replace only their marked section."""
    docs_path = tmp_path / "doc.md"
    docs_path.write_text("Before\n<!-- BEGIN -->\nstale\n<!-- END -->\nAfter\n", encoding="utf-8")
    target = build_schema_docs.GeneratedDocsTarget(
        path=docs_path,
        begin_marker="<!-- BEGIN -->",
        end_marker="<!-- END -->",
        content="\nGenerated\n",
    )

    build_schema_docs.update_docs_target(target, check=False)

    assert_output(docs_path.read_text(encoding="utf-8"), EXPECTED_SCHEMA_DOCS_PATH / "updated_target.txt")
