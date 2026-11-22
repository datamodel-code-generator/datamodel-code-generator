"""Print the pull request that added each unreleased change record, for the release notes.

Usage:
    python scripts/record_prs.py [--repository DIR] [--revision REV] > record-prs.json

A record is the fragment `docs/data/migration/unreleased/<id>.toml`. The pull request that added it is the one whose
squash commit on the first-parent history of the revision last added the file (a fragment removed by a release stamp
and written again later belongs to the later pull request; a renamed fragment to the pull request that renamed it).
The number comes from the `(#<n>)` that ends the commit's subject; a fragment added by a commit without one, and a
fragment the revision no longer holds, is left out. The output is a JSON object from record id to number, which
`scripts/build_migration_docs.py --release-notes unreleased --record-prs FILE` reads. It needs the history: the
release draft workflow checks out with full depth.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

FRAGMENTS = "docs/data/migration/unreleased/"
PR_SUFFIX = re.compile(r"\(#([1-9]\d*)\)$")
COMMIT = "\0"


def record_prs(repository: Path, revision: str = "HEAD") -> dict[str, int]:
    """Return the number of the pull request that added each fragment the revision holds, by record id."""
    held = subprocess.run(
        ["git", "-C", str(repository), "ls-tree", "--name-only", "-z", revision, FRAGMENTS],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.split("\0")
    log = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "log",
            "--first-parent",
            "--no-renames",
            "--diff-filter=A",
            "--name-only",
            "--format=%x00%s",
            revision,
            "--",
            FRAGMENTS,
        ],
        capture_output=True,
        check=True,
        text=True,
    ).stdout
    prs: dict[str, int] = {}
    seen: set[str] = set()
    for commit in log.split(COMMIT)[1:]:
        subject, *paths = commit.strip("\n").split("\n")
        number = PR_SUFFIX.search(subject.rstrip())
        for path in paths:
            if path in held and path.endswith(".toml") and path not in seen:
                seen.add(path)
                if number:
                    prs[Path(path).stem] = int(number[1])
    return dict(sorted(prs.items()))


def main(argv: list[str] | None = None) -> int:
    """Print the record ids and pull request numbers as JSON."""
    parser = argparse.ArgumentParser(prog="record_prs.py", description=__doc__.splitlines()[0])
    parser.add_argument("--repository", type=Path, default=Path(), help="the repository (default: the current one)")
    parser.add_argument("--revision", default="HEAD", help="the revision whose fragments are listed (default: HEAD)")
    ns = parser.parse_args(argv)
    print(json.dumps(record_prs(ns.repository, ns.revision), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
