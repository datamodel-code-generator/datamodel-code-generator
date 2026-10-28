"""Tests for the central experimental feature registry."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from datamodel_code_generator.experimental import (
    EXPERIMENTAL_FEATURES,
    ExperimentalFeature,
    render_experimental_features,
    render_experimental_features_markdown,
    render_release_note_experimental_features,
)

if TYPE_CHECKING:
    import pytest


def test_experimental_registry_has_stable_required_metadata() -> None:
    """Every registry entry has enough data for docs, CLI output, and release notes."""
    ids = set(EXPERIMENTAL_FEATURES)

    for feature_id, feature in EXPERIMENTAL_FEATURES.items():
        assert feature.id == feature_id
        assert feature.target
        assert feature.message
        assert feature.since_version

    assert len(ids) == len(EXPERIMENTAL_FEATURES)


def test_experimental_json_output_is_machine_readable() -> None:
    """JSON output is suitable for downstream release tooling."""
    payload = json.loads(render_experimental_features("json"))

    assert isinstance(payload, list)
    assert {entry["id"] for entry in payload} == set(EXPERIMENTAL_FEATURES)
    assert all("since_version" in entry for entry in payload)


def test_experimental_table_output_includes_registered_features() -> None:
    """Table output contains registered targets and compatibility notes."""
    output = render_experimental_features("table")
    compact_output = " ".join(output.split())

    assert "ID" in output
    assert "behavior.batch-generation-jobs" in output
    assert "[tool.datamodel-codegen.jobs], --job, --all-jobs" in output
    assert "behavior.remote-reference-lock" in output
    assert "datamodel-codegen.lock, --lockfile, --update-lock, and --locked" in output
    assert "cli-option.install-skill" in output
    assert "--install-skill, --skill-scope, and --overwrite-skill" in output
    assert "input-format.avro" in output
    assert "--input-file-type xmlschema" in output
    assert "Notes:" in output
    assert "`datamodel-code-generator[http]` remains the stable HTTPX backend and is not deprecated" in compact_output
    assert "`datamodel-code-generator[httpx2]` is experimental" in compact_output
    assert "The default HTTP backend policy is auto" in compact_output
    assert "stable httpx is selected when its client module is installed, including when both pairs are installed" in (
        compact_output
    )
    assert "experimental httpx2 is selected only when that module is absent" in compact_output
    assert "`HTTPBackend.HTTPX2` to require the experimental pair" in compact_output
    assert "Explicit selections and paired dependency errors do not fall back" in compact_output
    assert "The lock stores opaque SHA-256 request-identity digests and SHA-256 body digests" in compact_output


def test_experimental_markdown_output_includes_details() -> None:
    """Markdown output contains both summary and detail sections."""
    output = render_experimental_features("markdown")

    assert "| ID | Kind | Target | Since | Tracking |" in output
    assert "## Details" in output
    assert "behavior.batch-generation-jobs" in output
    assert "[tool.datamodel-codegen.jobs], --job, --all-jobs" in output
    assert "input-format.asyncapi" in output
    assert "input-format.avro" in output
    assert "input-format.protobuf" in output
    assert "input-format.xmlschema" in output
    assert "cli-option.generate-schema-validators" in output
    assert "cli-option.install-skill" in output
    assert "cli-option.use-missing-sentinel" in output
    assert "formatter.builtin" in output
    assert "behavior.remote-reference-lock" in output
    assert "lock document schema and request-identity compatibility may evolve" in output
    assert "integrity mismatches remain fail-closed and credentials are never persisted" in output


def test_experimental_markdown_output_can_omit_header() -> None:
    """Generated snippets can omit the page-level heading."""
    output = render_experimental_features_markdown(include_header=False)

    assert not output.startswith("# Experimental Features")
    assert output.startswith("| ID | Kind | Target | Since | Tracking |")


def test_release_note_output_filters_by_version() -> None:
    """Release-note snippets include entries introduced in the requested version."""
    output = render_release_note_experimental_features("0.59.0")

    assert "## Experimental Features" in output
    assert "--input-file-type asyncapi" in output
    assert "--input-file-type avro" in output
    assert "--input-file-type protobuf" in output
    assert "--input-file-type xmlschema" in output
    assert "--formatters builtin" in output

    batch_jobs_output = render_release_note_experimental_features("0.72.3")

    assert "## Experimental Features" in batch_jobs_output
    assert "[tool.datamodel-codegen.jobs], --job, --all-jobs" in batch_jobs_output
    assert "configuration schema, batch output, and transactional/watch execution contracts" in batch_jobs_output


def test_release_note_output_includes_schema_validation_features() -> None:
    """Release-note snippets include experimental schema validation features."""
    output = render_release_note_experimental_features("0.66.1")

    assert "## Experimental Features" in output
    assert "--generate-schema-validators" in output
    assert "--schema-validator-type" in output
    assert "--use-missing-sentinel" in output


def test_release_note_output_includes_remote_reference_lock_feature() -> None:
    """Release-note snippets identify the experimental remote lock behavior."""
    output = render_release_note_experimental_features("0.72.3")

    assert "## Experimental Features" in output
    assert "datamodel-codegen.lock, --lockfile, --update-lock, and --locked" in output
    assert "lock document schema and request-identity compatibility may evolve" in output


def test_release_note_output_includes_agent_skill_installation() -> None:
    """Release-note snippets identify the experimental Agent Skill installer."""
    output = render_release_note_experimental_features("0.76.0")

    assert "## Experimental Features" in output
    assert "--install-skill, --skill-scope, and --overwrite-skill" in output
    assert "supported clients, scopes, and installed skill contents may change" in output


def test_experimental_markdown_includes_tracking_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Markdown details include optional tracking metadata when it is registered."""
    monkeypatch.setitem(
        EXPERIMENTAL_FEATURES,
        "test.tracked-feature",
        ExperimentalFeature(
            id="test.tracked-feature",
            kind="behavior",
            target="Tracked behavior",
            message="Tracked behavior is experimental.",
            since_version="9.0.0",
            tracking_issue="https://github.com/koxudaxi/datamodel-code-generator/issues/9999",
        ),
    )

    output = render_experimental_features_markdown()

    assert "test.tracked-feature" in output
    assert "https://github.com/koxudaxi/datamodel-code-generator/issues/9999" in output


def test_release_note_output_omits_unmatched_versions() -> None:
    """Release-note snippets are empty when no experimental feature was introduced."""
    assert not render_release_note_experimental_features("9.9.9")
