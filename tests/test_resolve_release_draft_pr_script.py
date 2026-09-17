"""End-to-end tests for trusted release draft PR resolution."""

from __future__ import annotations

import json
import operator
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import assert_output

ROOT = Path(__file__).parents[1]
DATA_PATH = Path(__file__).parent / "data" / "resolve_release_draft_pr"


@pytest.fixture(scope="module")
def git_history(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, str]]:
    """Create a real commit graph with included, later, and unrelated changes."""
    repository = tmp_path_factory.mktemp("release-history")
    environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
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
    for name, parent in (("ancestor", None), ("pinned", "ancestor"), ("later", "pinned"), ("unrelated", None)):
        commits[name] = subprocess.run(
            ["git", "commit-tree", tree, *(["-p", commits[parent]] if parent else [])],
            input=name + "\n",
            cwd=repository,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    # Moving the branch past the trusted snapshot must not broaden the accepted history.
    subprocess.run(["git", "update-ref", "refs/heads/main", commits["later"]], cwd=repository, check=True)
    return repository, commits


@pytest.mark.parametrize(
    "case",
    [
        {
            "name": path.stem,
            "input": path.name,
            "merge": "ancestor",
            "trusted": "pinned",
            "expected": path.with_suffix(".txt").name,
        }
        for path in sorted(DATA_PATH.glob("*.json"))
    ]
    + json.loads((DATA_PATH / "ancestry/cases.json").read_text(encoding="utf-8")),
    ids=operator.itemgetter("name"),
)
def test_resolve_release_draft_pr_cli(
    case: dict[str, str], tmp_path: Path, git_history: tuple[Path, dict[str, str]]
) -> None:
    """The real CLI emits only validated scalars and ignores untrusted PR text."""
    repository, commits = git_history
    merge_sha = commits.get(case["merge"], case["merge"])
    raw_payload = (DATA_PATH / case.get("input", "valid.json")).read_text(encoding="utf-8").replace("a" * 40, merge_sha)
    if "base_ref" in case:
        payload = json.loads(raw_payload)
        payload["base"]["ref"] = case["base_ref"]
        raw_payload = json.dumps(payload)
    pr_path = tmp_path / "pr.json"
    pr_path.write_text(raw_payload, encoding="utf-8")
    output_path = tmp_path / "output"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/resolve_release_draft_pr.py"),
            "--pr-path",
            str(pr_path),
            "--pr-number",
            "4071",
            "--repository",
            "owner/repo",
            "--trusted-sha",
            commits.get(case["trusted"], case["trusted"]),
            "--github-output-path",
            str(output_path),
        ],
        capture_output=True,
        check=False,
        cwd=repository,
        text=True,
    )
    assert_output(
        json.dumps(
            {
                "returncode": result.returncode,
                "output": (
                    output_path.read_text(encoding="utf-8").replace(merge_sha, "a" * 40)
                    if output_path.exists()
                    else None
                ),
                "stderr": result.stderr,
            },
            sort_keys=True,
        )
        + "\n",
        DATA_PATH / case["expected"],
    )


def test_import_does_not_resolve_a_pr() -> None:
    """Importing the CLI module cannot parse caller arguments or write workflow outputs."""
    result = subprocess.run(
        [sys.executable, "-c", "import scripts.resolve_release_draft_pr"],
        capture_output=True,
        check=False,
        cwd=Path(__file__).parents[1],
        text=True,
    )
    assert_output(
        json.dumps({"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}, sort_keys=True)
        + "\n",
        DATA_PATH / "import.txt",
    )
