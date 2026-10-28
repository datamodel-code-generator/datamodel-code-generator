"""Tests for the central deprecation registry."""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest

from datamodel_code_generator.deprecations import (
    DEPRECATIONS,
    Deprecation,
    deprecation_message,
    render_deprecations,
    render_deprecations_markdown,
    render_release_note_deprecations,
    warn_deprecated,
)
from tests.conftest import assert_output, assert_warnings_do_not_contain

EXPECTED_DEPRECATIONS_PATH = Path(__file__).parent / "data" / "expected" / "deprecations"


def test_deprecation_registry_has_stable_required_metadata() -> None:
    """Every registry entry has enough data for docs, CLI output, and release notes."""
    ids = set(DEPRECATIONS)

    for deprecation_id, deprecation in DEPRECATIONS.items():
        assert deprecation.id == deprecation_id
        assert deprecation.target
        assert deprecation.message
        assert deprecation.warning_since
        assert deprecation.warning_category in {"DeprecationWarning", "FutureWarning", "UserWarning"}

    assert len(ids) == len(DEPRECATIONS)


def test_deprecation_json_output_is_machine_readable() -> None:
    """JSON output is suitable for downstream release tooling."""
    payload = json.loads(render_deprecations("json"))

    assert isinstance(payload, list)
    assert {entry["id"] for entry in payload} == set(DEPRECATIONS)
    assert all("removal_version" in entry for entry in payload)
    assert any(entry["removal_version"] is None for entry in payload)


def test_deprecation_markdown_output_includes_details() -> None:
    """Markdown output contains both summary and detail sections."""
    output = render_deprecations("markdown")

    assert "| ID | Status | Kind | Target | Since | Removal | Replacement |" in output
    assert "TBD" in output
    assert "## Details" in output
    assert "cli.parent-scoped-naming" in output


def test_deprecation_markdown_output_can_omit_header() -> None:
    """Generated snippets can omit the page-level heading."""
    output = render_deprecations_markdown(include_header=False)

    assert not output.startswith("# Deprecations")
    assert output.startswith("| ID | Status | Kind | Target | Since | Removal | Replacement |")


def test_release_note_output_filters_by_version() -> None:
    """Release-note snippets include entries that start warning in the requested version."""
    output = render_release_note_deprecations("0.56.0")

    assert "## Deprecations" in output
    assert "Remote $ref fetching" in output
    assert "behavior.remote-ref-default" not in output


def test_release_note_output_includes_scheduled_removals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Release-note snippets include entries that are scheduled for removal."""
    monkeypatch.setitem(
        DEPRECATIONS,
        "test.scheduled-removal",
        Deprecation(
            id="test.scheduled-removal",
            kind="behavior",
            target="Scheduled removal",
            message="Scheduled removal is no longer available.",
            warning_since="8.0.0",
            removal_version="9.0.0",
        ),
    )

    output = render_release_note_deprecations("9.0.0")

    assert "## Removed Deprecated Features" in output
    assert "Scheduled removal" in output


def test_scheduled_deprecation_release_note_and_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scheduled entries render separately without emitting runtime warnings."""
    scheduled = Deprecation(
        id="test.scheduled-change",
        kind="cli-option",
        target="--scheduled-change",
        message="--scheduled-change will be deprecated.",
        warning_since="8.0.0",
        removal_version=None,
        replacement="--replacement",
        status="scheduled",
    )
    monkeypatch.setitem(DEPRECATIONS, scheduled.id, scheduled)

    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always")
        warn_deprecated(scheduled.id)

    assert_warnings_do_not_contain(recorded_warnings, scheduled.message)
    assert_output(
        render_release_note_deprecations("8.0.0"),
        EXPECTED_DEPRECATIONS_PATH / "scheduled_release_notes.txt",
    )


def test_warn_deprecated_uses_registered_message() -> None:
    """Warning helpers emit the registered message and category."""
    with pytest.warns(DeprecationWarning, match=deprecation_message("cli.parent-scoped-naming")):
        warn_deprecated("cli.parent-scoped-naming")


def _emit_deprecation_warning() -> None:
    warn_deprecated("cli.parent-scoped-naming")


def test_warn_deprecated_default_stacklevel_points_to_caller() -> None:
    """The default stacklevel keeps warnings attributed to the call site."""
    with warnings.catch_warnings(record=True) as recorded_warnings:
        warnings.simplefilter("always")
        _emit_deprecation_warning()

    assert len(recorded_warnings) == 1
    assert Path(recorded_warnings[0].filename).name == "test_deprecations.py"


def test_dependency_notice_release_comparisons() -> None:
    """Numeric boundaries include prereleases without changing local/postrelease ordering."""
    from datamodel_code_generator.deprecations import _dependency_version_is_legacy

    cases = json.loads(
        (Path(__file__).parent / "data" / "config" / "migration_versions.json").read_text(encoding="utf-8")
    )
    assert_output(
        "\n".join(f"{version}: {_dependency_version_is_legacy(version, tuple(minimum))}" for version, minimum in cases),
        EXPECTED_DEPRECATIONS_PATH / "dependency_versions.txt",
    )
