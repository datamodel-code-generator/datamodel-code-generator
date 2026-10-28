"""Update GitHub Action version references in documentation.

Updates all occurrences of `datamodel-code-generator/datamodel-code-generator@X.Y.Z`
to use the latest GitHub release version.

Usage:
    python scripts/update_docs_version.py
    python scripts/update_docs_version.py --check  # Verify docs are up to date
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
DOCS_DIR = ROOT_DIR / "docs"
README_FILE = ROOT_DIR / "README.md"
PATTERN = re.compile(r"(datamodel-code-generator/datamodel-code-generator@)(\d+\.\d+\.\d+)")


def get_latest_release_version() -> str:
    """Get the latest release version from GitHub."""
    result = subprocess.run(
        ["gh", "release", "list", "--limit", "1", "--exclude-drafts", "--json", "tagName", "-q", ".[0].tagName"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def update_file(file_path: Path, version: str, *, check: bool = False) -> bool:
    """Update version references in a single file.

    Returns True if file was updated (or would be updated in check mode).
    """
    content = file_path.read_text(encoding="utf-8")
    new_content = PATTERN.sub(rf"\g<1>{version}", content)

    if content == new_content:
        return False

    if check:
        return True

    file_path.write_text(new_content, encoding="utf-8")
    return True


def main() -> int:
    """Update all docs files with current version."""
    parser = argparse.ArgumentParser(description="Update GitHub Action version in docs")
    parser.add_argument("--check", action="store_true", help="Check if docs are up to date")
    args = parser.parse_args()

    try:
        version = get_latest_release_version()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Error getting latest release: {e}", file=sys.stderr)
        return 1

    # Update docs directory and README.md
    target_files = list(DOCS_DIR.rglob("*.md"))
    if README_FILE.exists():
        target_files.append(README_FILE)

    updated_files: list[Path] = [md_file for md_file in target_files if update_file(md_file, version, check=args.check)]

    if args.check:
        if updated_files:
            print(f"Docs need updating to version {version}:", file=sys.stderr)
            for f in updated_files:
                print(f"  {f.relative_to(DOCS_DIR.parent)}", file=sys.stderr)
            return 1
        return 0

    if updated_files:
        print(f"Updated {len(updated_files)} file(s) to version {version}:")
        for f in updated_files:
            print(f"  {f.relative_to(DOCS_DIR.parent)}")
    else:
        print(f"All docs already at version {version}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
