"""Check README and docs overview blocks that are intentionally duplicated."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
README_PATH = ROOT / "README.md"
DOCS_INDEX_PATH = ROOT / "docs" / "index.md"

SECTION_HEADING = "## ✨ What it does"
HERO_ALT_RE = re.compile(r'alt="([^"]+)"|!\[([^\]]+)]\([^)]*hero-light\.svg[^)]*\)')
URL_RE = re.compile(r"https?://[^\s\"')>]+")
PLAYGROUND_NOTE_PREFIX = "Playground privacy:"


@dataclass(frozen=True, slots=True)
class OverviewSnapshot:
    """Normalized overview fields that should stay synchronized."""

    tagline: str
    badges: tuple[str, ...]
    hero_alt: str
    privacy_note: str
    feature_bullets: tuple[str, ...]


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _find_line(lines: list[str], target: str) -> int:
    for index, line in enumerate(lines):
        if line.strip() == target:
            return index
    msg = f"Could not find {target!r}"
    raise ValueError(msg)


def _extract_tagline(lines: list[str]) -> str:
    if not lines or not lines[0].startswith("# "):
        msg = "Overview file must start with an H1"
        raise ValueError(msg)
    for line in lines[1:]:
        if tagline := line.strip():
            return tagline
    msg = "Overview tagline is missing"
    raise ValueError(msg)


def _extract_badges(lines: list[str]) -> tuple[str, ...]:
    end = _find_line(lines, SECTION_HEADING)
    badges = tuple(line.strip() for line in lines[:end] if _is_badge_line(line))
    if badges:
        return badges
    msg = "Overview badges are missing"
    raise ValueError(msg)


def _is_badge_line(line: str) -> bool:
    for url in URL_RE.findall(line):
        parsed = urlsplit(url)
        match parsed.hostname:
            case "img.shields.io":
                return True
            case "codecov.io" if parsed.path.startswith("/gh/"):
                return True
    return False


def _extract_hero_alt(lines: list[str]) -> str:
    for line in lines:
        if (match := HERO_ALT_RE.search(line)) and (alt := match.group(1) or match.group(2)):
            return alt
    msg = "Overview hero alt text is missing"
    raise ValueError(msg)


def _normalize_note_line(line: str, *, source: str) -> str:
    match source:
        case "readme":
            line = line.removeprefix(">").strip()
        case "docs":
            line = line.removeprefix("    ")
        case _:
            msg = f"Unknown overview source {source!r}"
            raise ValueError(msg)
    return line.removeprefix(PLAYGROUND_NOTE_PREFIX).strip()


def _extract_readme_privacy_note(lines: list[str]) -> str:
    start = _find_line(lines, "> [!NOTE]")
    note_lines: list[str] = []
    for line in lines[start + 1 :]:
        if not line.startswith(">"):
            break
        if cleaned := _normalize_note_line(line, source="readme"):
            note_lines.append(cleaned)
    return _join_note(note_lines, "README privacy note is missing")


def _extract_docs_privacy_note(lines: list[str]) -> str:
    start = _find_line(lines, '!!! note "Playground privacy"')
    note_lines: list[str] = []
    for line in lines[start + 1 :]:
        if not line:
            if note_lines:
                break
            continue
        if not line.startswith("    "):
            break
        if cleaned := _normalize_note_line(line, source="docs"):
            note_lines.append(cleaned)
    return _join_note(note_lines, "Docs privacy note is missing")


def _join_note(lines: list[str], error: str) -> str:
    if lines:
        return " ".join(" ".join(lines).split())
    raise ValueError(error)


def _extract_feature_bullets(lines: list[str]) -> tuple[str, ...]:
    start = _find_line(lines, SECTION_HEADING)
    bullets: list[str] = []
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped == "---" and bullets:
            break
        if stripped.startswith("- "):
            bullets.append(stripped)
    if bullets:
        return tuple(bullets)
    msg = "Overview feature bullets are missing"
    raise ValueError(msg)


def extract_snapshot(path: Path, *, source: str) -> OverviewSnapshot:
    """Extract synchronized overview fields from README.md or docs/index.md."""
    lines = _read_lines(path)
    if source == "readme":
        privacy_note = _extract_readme_privacy_note(lines)
    elif source == "docs":
        privacy_note = _extract_docs_privacy_note(lines)
    else:
        msg = f"Unknown overview source {source!r}"
        raise ValueError(msg)
    return OverviewSnapshot(
        tagline=_extract_tagline(lines),
        badges=_extract_badges(lines),
        hero_alt=_extract_hero_alt(lines),
        privacy_note=privacy_note,
        feature_bullets=_extract_feature_bullets(lines),
    )


def compare_snapshots(readme: OverviewSnapshot, docs: OverviewSnapshot) -> tuple[str, ...]:
    """Return field names that differ between README and docs overview blocks."""
    return tuple(
        field_name
        for field_name in OverviewSnapshot.__dataclass_fields__
        if getattr(readme, field_name) != getattr(docs, field_name)
    )


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Check README/docs overview synchronization")
    parser.add_argument("--readme", type=Path, default=README_PATH, help="README path")
    parser.add_argument("--docs-index", type=Path, default=DOCS_INDEX_PATH, help="docs/index.md path")
    return parser.parse_args()


def _extract_snapshot_for_cli(path: Path, *, source: str) -> OverviewSnapshot:
    try:
        return extract_snapshot(path, source=source)
    except ValueError as error:
        msg = f"{path}: {error}"
        raise ValueError(msg) from error


def main() -> int:
    """Run the overview synchronization check."""
    args = parse_args()
    try:
        readme = _extract_snapshot_for_cli(args.readme, source="readme")
        docs = _extract_snapshot_for_cli(args.docs_index, source="docs")
    except ValueError as error:
        print(f"Could not extract overview snapshot: {error}", file=sys.stderr)
        return 1
    if not (mismatches := compare_snapshots(readme, docs)):
        print("README.md and docs/index.md overview blocks are synchronized")
        return 0
    print("README.md and docs/index.md overview blocks differ:", file=sys.stderr)
    for mismatch in mismatches:
        print(f"  - {mismatch}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
