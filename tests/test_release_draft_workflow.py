"""Regression tests for the release draft workflow."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from tests.conftest import assert_output


@pytest.mark.allow_direct_assert
def test_update_draft_job_uses_exact_least_privilege_permissions() -> None:
    """PR labeling and comments need only pull-request access beside release access."""
    workflow_path = Path(__file__).parents[1] / ".github" / "workflows" / "release-draft.yaml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    assert workflow["jobs"]["update-draft"]["permissions"] == {
        "contents": "write",
        "pull-requests": "write",
    }


@pytest.mark.allow_direct_assert
def test_analysis_fast_path_only_skips_claude_and_validation() -> None:
    """Trusted analysis and Claude analysis must converge on the same artifact."""
    workflow_path = Path(__file__).parents[1] / ".github" / "workflows" / "release-draft.yaml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["analyze"]["steps"]
    steps_by_name = {step["name"]: step for step in steps}
    prepare_diff_script = steps_by_name["Prepare exact PR diff"]["run"]

    assert "gh api --paginate" in prepare_diff_script
    assert 'previous_path: (if .status == "renamed" then .previous_filename else null end)' in prepare_diff_script
    assert steps_by_name["Prepare release analysis route"]["id"] == "analysis-route"
    assert (
        '--expected-changed-files "${{ needs.resolve-pr.outputs.changed_files }}"'
        in steps_by_name["Prepare release analysis route"]["run"]
    )
    assert steps_by_name["Run Claude Code Analysis"]["if"] == ("steps.analysis-route.outputs.requires_claude == 'true'")
    assert steps_by_name["Parse Claude output"]["if"] == "steps.analysis-route.outputs.requires_claude == 'true'"
    assert "if" not in steps_by_name["Upload analysis artifact"]
    assert steps_by_name["Upload analysis artifact"]["with"]["path"] == "${{ steps.pr-diff.outputs.analysis_path }}"


def test_maintenance_exclusion_is_wired_before_note_generation() -> None:
    """The native label filter receives the independent routing result before notes are generated."""
    root = Path(__file__).parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/release-draft.yaml").read_text(encoding="utf-8"))
    release_config = yaml.safe_load((root / ".github/release.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["update-draft"]["steps"]
    step_names = [step["name"] for step in steps]
    label_index = step_names.index("Exclude maintenance changes from release notes")
    label_step = steps[label_index]

    assert_output(
        json.dumps(
            {
                "analysis_output": workflow["jobs"]["analyze"]["outputs"],
                "concurrency": workflow["concurrency"],
                "label_before_note_generation": label_index
                < step_names.index("Calculate version and update draft release"),
                "label_condition": label_step["if"],
                "label_request": label_step["run"],
                "release_config": release_config,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        root / "tests/data/expected/release_draft_workflow/maintenance_exclusion.txt",
    )


def test_claude_output_schema_rejects_invalid_reasoning() -> None:
    """The actual action schema rejects unsafe explanations before local validation."""
    root = Path(__file__).parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/release-draft.yaml").read_text(encoding="utf-8"))
    claude_step = next(step for step in workflow["jobs"]["analyze"]["steps"] if step.get("id") == "claude")
    args = shlex.split(claude_step["with"]["claude_args"])
    schema = json.loads(args[args.index("--json-schema") + 1])
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    cases = json.loads((root / "tests/data/release_draft_workflow/reasoning.json").read_text(encoding="utf-8"))
    assert_output(
        json.dumps({name: validator.is_valid(output) for name, output in cases.items()}, indent=2) + "\n",
        root / "tests/data/expected/release_draft_workflow/reasoning.txt",
    )


def test_manual_release_analysis_uses_validated_pr_outputs() -> None:
    """Manual and event-driven runs share pinned code and validated PR identities."""
    root = Path(__file__).parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/release-draft.yaml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    resolve = jobs["resolve-pr"]
    analyze = jobs["analyze"]
    update = jobs["update-draft"]
    analysis_steps = {step["name"]: step for step in analyze["steps"]}
    assert_output(
        json.dumps(
            {
                "dispatch": workflow[True]["workflow_dispatch"],
                "resolve": resolve,
                "analyze_needs": analyze["needs"],
                "analyze_if": analyze["if"],
                "analyze_checkout": analysis_steps["Checkout repository"]["with"],
                "diff_env": analysis_steps["Prepare exact PR diff"]["env"],
                "route": analysis_steps["Prepare release analysis route"]["run"],
                "validator_pr": analysis_steps["Parse Claude output"]["env"]["PR_NUMBER"],
                "update_needs": update["needs"],
                "update_env": update["env"],
                "last_update_step": update["steps"][-1],
                "prompt_pr_references": analysis_steps["Run Claude Code Analysis"]["with"]["prompt"].count(
                    "${{ needs.resolve-pr.outputs.pr_number }}"
                ),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        root / "tests/data/expected/release_draft_workflow/manual_dispatch.txt",
    )


@pytest.mark.parametrize(
    "requested_pr",
    json.loads((Path(__file__).parent / "data/release_draft_workflow/invalid_pr_numbers.json").read_text()),
)
def test_manual_release_analysis_rejects_unsafe_pr_numbers(requested_pr: str, tmp_path: Path) -> None:
    """Invalid caller input exits the real shell step before any API or credential use."""
    root = Path(__file__).parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/release-draft.yaml").read_text(encoding="utf-8"))
    step = workflow["jobs"]["resolve-pr"]["steps"][-1]
    result = subprocess.run(
        ["bash", "-c", step["run"]],
        cwd=tmp_path,
        env={**os.environ, "REQUESTED_PR": requested_pr},
        capture_output=True,
        check=False,
        text=True,
    )
    assert_output(
        json.dumps(
            {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "created_files": sorted(path.name for path in tmp_path.iterdir()),
            },
            sort_keys=True,
        )
        + "\n",
        root / "tests/data/expected/release_draft_workflow/invalid_pr_number.txt",
    )
