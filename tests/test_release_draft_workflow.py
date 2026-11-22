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

from tests.capabilities import needs
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
                "triggers": workflow[True],
                "resolve": resolve,
                "analyze_needs": analyze["needs"],
                "analyze_if": analyze["if"],
                "analyze_checkout": analysis_steps["Checkout repository"]["with"],
                "analyze_verify_checkout": analysis_steps["Verify trusted checkout"],
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
    )


@pytest.mark.parametrize(
    "requested_pr",
    json.loads((Path(__file__).parent / "data/release_draft_workflow/invalid_pr_numbers.json").read_text()),
)
@needs("posix")
def test_manual_release_analysis_rejects_unsafe_pr_numbers(requested_pr: str, tmp_path: Path) -> None:
    """Invalid caller input exits the real shell step before any API or credential use.

    The test runs the step with ``LC_ALL=C``, where ``[0-9]`` matches ASCII digits only as in the C.UTF-8 locale of
    the Ubuntu runner, and without the runner's variables, so a step that accepted the input would stop before
    reaching the API.
    """
    root = Path(__file__).parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/release-draft.yaml").read_text(encoding="utf-8"))
    step = workflow["jobs"]["resolve-pr"]["steps"][-1]
    result = subprocess.run(
        ["bash", "-c", step["run"]],
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"], "LC_ALL": "C", "REQUESTED_PR": requested_pr},
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
    )


@needs("posix")
def test_trusted_checkout_rejects_other_commits(tmp_path: Path) -> None:
    """Every checkout job stops before repository scripts run unless HEAD is the workflow commit."""
    root = Path(__file__).parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/release-draft.yaml").read_text(encoding="utf-8"))
    environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    repository = tmp_path / "repository"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "--quiet", repository], check=True, env=environment)
    tree = subprocess.run(
        ["git", "hash-object", "-w", "-t", "tree", "--stdin"],
        input="",
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    commits: dict[str, str] = {}
    for name, parent in (("other", None), ("workflow", "other")):
        commits[name] = subprocess.run(
            ["git", "commit-tree", tree, *(["-p", commits[parent]] if parent else [])],
            input=name + "\n",
            cwd=repository,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    subprocess.run(["git", "update-ref", "HEAD", commits["workflow"]], cwd=repository, check=True)
    cases = (
        ("workflow", repository, commits["workflow"]),
        ("other", repository, commits["other"]),
        ("missing", repository, ""),
        ("non_git", workspace, commits["workflow"]),
    )
    results = {}
    jobs = [
        name
        for name, job in workflow["jobs"].items()
        if any(step["name"] == "Verify trusted checkout" for step in job["steps"])
    ]
    for job in jobs:
        step = next(step for step in workflow["jobs"][job]["steps"] if step["name"] == "Verify trusted checkout")
        for case, cwd, trusted_sha in cases:
            result = subprocess.run(
                ["bash", "-c", step["run"]],
                cwd=cwd,
                env={**os.environ, "TRUSTED_SHA": trusted_sha, "GIT_CEILING_DIRECTORIES": str(tmp_path)},
                capture_output=True,
                check=False,
                text=True,
            )
            results[f"{job}/{case}"] = {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "last_stderr_line": next(reversed(result.stderr.splitlines()), ""),
            }
    assert_output(
        json.dumps(results, indent=2, sort_keys=True) + "\n",
    )


def test_update_draft_runs_without_the_analysis() -> None:
    """The rebuild needs only the notes: each step is gated on the outcome of the job whose output it reads."""
    root = Path(__file__).parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/release-draft.yaml").read_text(encoding="utf-8"))
    update = workflow["jobs"]["update-draft"]
    assert_output(
        json.dumps(
            {
                "if": update["if"],
                "needs": update["needs"],
                "steps": {step["name"]: step.get("if") for step in update["steps"]},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


FAKE_GH = """#!/bin/bash
echo "$*" >> "$FAKE_LOG"
case "$*" in
  "release list"*--exclude-drafts*) echo v0.99.0 ;;
  "release list"*) ;;
  "api"*/commits/*) echo 2026-11-21T20:00:00Z ;;
  "api"*/compare/failing...*) exit 1 ;;
  "api"*/compare/tag...*) echo identical ;;
  "api"*/compare/before...*) echo ahead ;;
  "api"*/compare/after...*) echo behind ;;
  "pr list"*) cat "$FAKE_PRS" ;;
  "pr view"*) echo "$FAKE_TITLE" ;;
  "api"*) echo "GENERATED NOTES" ;;
  "release create"*) cat > "$FAKE_BODY" ;;
esac
"""
UNDECLARED_CASES = {
    "records only": ("false", "false", ""),
    "breaking records": ("true", "false", ""),
    "labelled pull requests": ("false", "false", "5\tafter\tChange one\n9\tafter\tChange two\n"),
    "merged in or after the tag": (
        "false",
        "false",
        "4\ttag\tIn the tagged commit\n5\tbefore\tBefore the tag\n9\tafter\tAfter the tag, same second\n",
    ),
    "compare call fails": ("false", "false", "4\tfailing\tUnknown to the API\n"),
    "label not searchable yet": ("false", "true", ""),
}


@pytest.mark.parametrize("case", sorted(UNDECLARED_CASES))
@needs("posix")
def test_update_draft_lists_the_undeclared_changes(case: str, tmp_path: Path) -> None:
    """The draft starts with the merged pull requests labelled undeclared-change and a minor bump follows them."""
    root = Path(__file__).parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/release-draft.yaml").read_text(encoding="utf-8"))
    step = next(
        step
        for step in workflow["jobs"]["update-draft"]["steps"]
        if step["name"] == "Calculate version and update draft release"
    )
    has_breaking, has_bc, labelled = UNDECLARED_CASES[case]
    bin_dir = tmp_path / "bin"
    notes = tmp_path / "notes"
    bin_dir.mkdir()
    notes.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH, encoding="utf-8")
    (bin_dir / "gh").chmod(0o755)
    (notes / "has-breaking.txt").write_text(has_breaking + "\n", encoding="utf-8")
    (notes / "notes.md").write_text("## Record Notes\n", encoding="utf-8")
    (tmp_path / "prs.txt").write_text(labelled, encoding="utf-8")
    result = subprocess.run(
        ["bash", "-c", step["run"].replace("${{ github.repository }}", "owner/repository")],
        cwd=tmp_path,
        env={
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "NOTES_DIR": str(notes),
            "HAS_BC": has_bc,
            "PR_NUMBER": "12",
            "GITHUB_REPOSITORY": "owner/repository",
            "FAKE_LOG": str(tmp_path / "gh.log"),
            "FAKE_PRS": str(tmp_path / "prs.txt"),
            "FAKE_TITLE": "Change three",
            "FAKE_BODY": str(tmp_path / "body.md"),
        },
        capture_output=True,
        check=False,
        text=True,
    )
    calls = (tmp_path / "gh.log").read_text(encoding="utf-8").splitlines()
    assert_output(
        json.dumps(
            {
                "returncode": result.returncode,
                "stderr": result.stderr,
                "search": next(call for call in calls if call.startswith("pr list")),
                "created": next((call for call in calls if call.startswith("release create")), None),
                "body": (tmp_path / "body.md").read_text(encoding="utf-8") if (tmp_path / "body.md").exists() else None,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
