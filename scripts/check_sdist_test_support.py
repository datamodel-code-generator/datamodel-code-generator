"""Check that source distributions include helper files required by tests."""

from __future__ import annotations

import argparse
import tarfile
from pathlib import Path, PurePosixPath

EXCLUDED_SCRIPT_PARTS = frozenset({"__pycache__"})


def _find_sdist(dist_dir: Path) -> Path:
    match sorted(dist_dir.glob("*.tar.gz")):
        case [sdist]:
            return sdist
        case []:
            msg = f"No sdist archive found in {dist_dir}"
            raise SystemExit(msg)
        case sdists:
            msg = "Expected one sdist archive, found:\n" + "\n".join(f"  - {path}" for path in sdists)
            raise SystemExit(msg)
    raise AssertionError


def _archive_relative_path(member_name: str) -> PurePosixPath | None:
    match PurePosixPath(member_name).parts:
        case (_, *relative_parts) if relative_parts:
            return PurePosixPath(*relative_parts)
        case _:
            return None
    return None


def _archive_files(sdist: Path) -> frozenset[PurePosixPath]:
    with tarfile.open(sdist, "r:gz") as archive:
        return frozenset(
            path for member in archive.getmembers() if member.isfile() and (path := _archive_relative_path(member.name))
        )


def _contains_top_level(paths: frozenset[PurePosixPath], root: str) -> bool:
    for path in paths:
        match path.parts:
            case (top_level, *_) if top_level == root:
                return True
            case _:
                continue
    return False


def _local_script_files(root: Path) -> frozenset[PurePosixPath]:
    return frozenset(
        PurePosixPath(path.relative_to(root).as_posix())
        for path in sorted((root / "scripts").rglob("*"))
        if path.is_file() and not EXCLUDED_SCRIPT_PARTS.intersection(path.parts)
    )


def check_sdist_test_support(sdist: Path, root: Path) -> None:
    """Fail when the sdist ships tests without the script helpers they import."""
    archive_files = _archive_files(sdist)
    if not _contains_top_level(archive_files, "tests"):
        return

    if not (missing_files := sorted(_local_script_files(root) - archive_files)):
        return

    msg = "sdist includes tests but is missing script helpers:\n" + "\n".join(f"  - {path}" for path in missing_files)
    raise SystemExit(msg)


def main() -> None:
    """Run the sdist test-support check."""
    parser = argparse.ArgumentParser()
    parser.add_argument("dist_dir", type=Path)
    args = parser.parse_args()

    check_sdist_test_support(_find_sdist(args.dist_dir), Path.cwd())


if __name__ == "__main__":
    main()
