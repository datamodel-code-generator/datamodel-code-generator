"""Resolve trusted scalar release-analysis inputs from a GitHub pull-request response."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any


def _resolve_pr(payload: Any, pr_number: int, repository: str) -> dict[str, str]:
    """Validate merged PR metadata without relying on a mutable base-branch name."""
    match payload:
        case {
            "number": int(number),
            "merged": True,
            "merge_commit_sha": str(merge_sha),
            "changed_files": int(changed_files),
            "base": {"ref": str(base_ref), "repo": {"full_name": str(base_repository)}},
            "labels": list(labels),
        } if (
            type(number) is int
            and number == pr_number > 0
            and base_ref
            and base_repository == repository
            and re.fullmatch(r"[0-9a-f]{40}", merge_sha)
            and type(changed_files) is int
            and changed_files >= 0
            and all(isinstance(label, dict) and isinstance(label.get("name"), str) for label in labels)
        ):
            return {
                "pr_number": str(number),
                "merge_sha": merge_sha,
                "changed_files": str(changed_files),
                "should_analyze": str(not any(label["name"] == "breaking-change-analyzed" for label in labels)).lower(),
            }
    message = "Expected the requested PR to be merged into this repository with valid metadata."
    raise SystemExit(message)


def _validate_ancestry(merge_sha: str, trusted_sha: str) -> None:
    """Require the merged change to be included in the pinned trusted workflow commit."""
    if not re.fullmatch(r"[0-9a-f]{40}", trusted_sha):
        message = "Expected a full pinned workflow commit SHA."
        raise SystemExit(message)
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", merge_sha, trusted_sha],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    match result.returncode:
        case 0:
            return
        case _:
            message = "PR merge commit must be contained in the pinned workflow commit."
            raise SystemExit(message)


def main() -> None:
    """Validate API metadata before writing any GitHub Actions outputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr-path", required=True, type=Path)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--trusted-sha", required=True)
    parser.add_argument("--github-output-path", required=True, type=Path)
    args = parser.parse_args()
    try:
        payload = json.loads(raw_payload) if (raw_payload := args.pr_path.read_text(encoding="utf-8")) else None
    except (OSError, UnicodeError, ValueError) as exc:
        message = "Unable to read pull-request metadata."
        raise SystemExit(message) from exc
    outputs = _resolve_pr(payload, args.pr_number, args.repository)
    _validate_ancestry(outputs["merge_sha"], args.trusted_sha)
    with args.github_output_path.open("a", encoding="utf-8") as output:
        output.writelines(f"{key}={value}\n" for key, value in outputs.items())


if __name__ == "__main__":
    main()
