"""Build the release-version list for the browser playground manifest."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any

PLAYGROUND_RUNTIME_PATH = "docs/assets/playground/runtime.py"
COMPILED_TEMPLATE_REGISTRY_PATH = "src/datamodel_code_generator/model/_compiled_templates/registry.py"
LEGACY_RUNTIME_PACKAGES = ("jinja2>=2.10.1,<4",)


def run_gh(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run gh and return the completed process."""
    return subprocess.run(
        ["gh", *args],
        capture_output=True,
        check=check,
        text=True,
    )


def _release_tags(repo: str, limit: int) -> list[str]:
    result = run_gh([
        "release",
        "list",
        "--repo",
        repo,
        "--limit",
        str(limit),
        "--exclude-drafts",
        "--json",
        "tagName,isPrerelease",
        "--jq",
        "[.[] | select(.isPrerelease == false) | .tagName]",
    ])
    tags = json.loads(result.stdout or "[]")
    return [tag for tag in tags if isinstance(tag, str) and tag]


def _has_file(repo: str, tag: str, path: str) -> bool:
    result = run_gh(
        [
            "api",
            "--method",
            "GET",
            f"repos/{repo}/contents/{path}",
            "-f",
            f"ref={tag}",
        ],
        check=False,
    )
    return result.returncode == 0


def _has_playground_runtime(repo: str, tag: str) -> bool:
    return _has_file(repo, tag, PLAYGROUND_RUNTIME_PATH)


def _uses_compiled_templates(repo: str, tag: str) -> bool:
    return _has_file(repo, tag, COMPILED_TEMPLATE_REGISTRY_PATH)


def _release_version(tag: str, *, uses_compiled_templates: bool) -> dict[str, Any]:
    version = {
        "id": tag,
        "label": tag,
        "kind": "release",
        "install": {
            "type": "requirement",
            "requirement": f"datamodel-code-generator=={tag}",
            "deps": False,
        },
        "app": "runtime.py",
    }
    if not uses_compiled_templates:
        version["runtime_packages"] = list(LEGACY_RUNTIME_PACKAGES)
    return version


def _build_release_versions(repo: str, limit: int) -> list[dict[str, Any]]:
    versions = []
    for tag in _release_tags(repo, limit):
        if not _has_playground_runtime(repo, tag):
            continue
        versions.append(_release_version(tag, uses_compiled_templates=_uses_compiled_templates(repo, tag)))
    return versions


def main() -> int:
    """Print release versions that can run in the browser playground."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", default=os.environ.get("GITHUB_REPOSITORY", "datamodel-code-generator/datamodel-code-generator")
    )
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    try:
        print(json.dumps(_build_release_versions(args.repo, args.limit), ensure_ascii=False))
    except (json.JSONDecodeError, subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"Could not build playground release list: {exc}", file=sys.stderr)
        print("[]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
