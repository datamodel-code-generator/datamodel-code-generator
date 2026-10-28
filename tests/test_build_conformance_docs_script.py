"""Tests for conformance dashboard documentation generation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts import build_conformance_docs

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_conformance_docs.py"


def test_build_conformance_docs_check_is_up_to_date() -> None:
    """Generated conformance docs are committed."""
    subprocess.run([sys.executable, str(SCRIPT), "--check"], check=True)


def test_generated_content_includes_all_current_conformance_suites() -> None:
    """Every local conformance/e2e tox runner is represented in generated content."""
    content = build_conformance_docs.render_conformance_markdown()
    discovered_envs = build_conformance_docs.discover_conformance_tox_envs()
    registered_envs = {spec.tox_env for spec in build_conformance_docs.CONFORMANCE_SUITES}

    assert registered_envs == set(discovered_envs)
    for spec in build_conformance_docs.CONFORMANCE_SUITES:
        assert spec.suite_name in content
        assert f"`{spec.tox_env}`" in content


def test_each_listed_runner_script_and_tox_env_exists() -> None:
    """The generated rows point at real local tox envs and runner scripts."""
    tox_runner_scripts = build_conformance_docs.load_tox_runner_scripts()

    for spec in build_conformance_docs.CONFORMANCE_SUITES:
        assert spec.tox_env in tox_runner_scripts
        assert (ROOT / tox_runner_scripts[spec.tox_env]).is_file()


def test_expected_corpus_counts_are_parsed_from_workflow() -> None:
    """Workflow --expected-* arguments remain visible to the docs generator."""
    jobs = build_conformance_docs.load_workflow_jobs()

    assert jobs["w3c-xmlschema-e2e"].expected_counts == {
        "documents": 5284,
        "unique-documents": 5283,
    }
    assert jobs["asyncapi-spec-json-schemas-e2e"].expected_counts == {"schemas": 12}
    assert jobs["apache-avro-schema-pass-e2e"].expected_counts == {"schemas": 28}
    assert jobs["protobuf-official-e2e"].expected_counts == {"schemas": 65}


def test_jsonschema_suite_counts_are_parsed_from_test_metadata() -> None:
    """JSON-Schema-Test-Suite counts are derived without importing test modules."""
    counts = build_conformance_docs.load_json_schema_suite_counts()

    assert counts.default_drafts == ("draft7", "draft2020-12")
    assert counts.group_counts_by_draft["draft7"] == 257
    assert counts.group_counts_by_draft["draft2020-12"] == 383
    assert counts.test_counts_by_draft["draft7"] == 927
    assert counts.test_counts_by_draft["draft2020-12"] == 1299
