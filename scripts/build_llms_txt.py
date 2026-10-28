"""llms.txt generator for datamodel-code-generator documentation.

Generates llms.txt and llms-full.txt from zensical.toml nav structure.
See https://llmstxt.org/ for the llms.txt specification.

Usage:
    python scripts/build_llms_txt.py
    python scripts/build_llms_txt.py --check  # CI: check if files are up to date
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, TypedDict

try:
    import tomllib
except ImportError:
    import tomli as tomllib

ROOT_DIR = Path(__file__).parent.parent
DOCS_DIR = ROOT_DIR / "docs"
ZENSICAL_TOML = ROOT_DIR / "zensical.toml"
OPTIONAL_PATTERNS = ("faq.md", "development-contributing.md", "cli-reference/manual/*.md")
SKIP_PREFIXES = ("```", "!!!", "[![", "![", "<", "---")
MAX_DESC_LINES, MAX_DESC_LEN = 2, 150
EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001F9FF\U0001FA00-\U0001FAFF\U00002702-\U000027B0"
    r"\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002600-\U000026FF"
    r"\U00002300-\U000023FF\U0000FE00-\U0000FE0F\U0000200D]+",
)
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)]\([^)]+\)")
MARKDOWN_ATTR_RE = re.compile(r"\s*\{#[^}]+}\s*$")


class SiteConfig(TypedDict):
    """Configuration extracted from zensical.toml."""

    site_name: str
    site_description: str
    site_url: str
    nav: list[dict[str, Any]]


@dataclass
class PageInfo:
    """Information about a documentation page."""

    title: str
    path: str
    url: str
    description: str
    content: str
    depth: int = 0


@dataclass
class NavSection:
    """A navigation section with optional children."""

    title: str
    path: str | None = None
    children: list[NavSection] = field(default_factory=list)
    depth: int = 0


def parse_zensical_toml(path: Path) -> SiteConfig:
    """Parse zensical.toml and extract project configuration."""
    if not path.exists():
        sys.exit(f"Error: {path} not found")
    try:
        with path.open("rb") as f:
            project = tomllib.load(f).get("project", {})
    except tomllib.TOMLDecodeError as e:
        sys.exit(f"Error parsing {path}: {e}")
    return SiteConfig(
        site_name=project.get("site_name", ""),
        site_description=project.get("site_description", ""),
        site_url=project.get("site_url", "").rstrip("/"),
        nav=project.get("nav", []),
    )


def flatten_nav(nav: list[dict[str, Any]], depth: int = 0) -> list[NavSection]:
    """Recursively flatten navigation structure."""
    sections: list[NavSection] = []
    for item in nav:
        for title, value in item.items():
            if isinstance(value, str):
                sections.append(NavSection(title=title, path=value, depth=depth))
            elif isinstance(value, list):
                sections.append(NavSection(title=title, children=flatten_nav(value, depth + 1), depth=depth))
    return sections


def clean_summary_text(text: str) -> str:
    """Remove Markdown-only syntax from llms.txt titles and summaries."""
    text = EMOJI_RE.sub("", text)
    text = MARKDOWN_LINK_RE.sub(r"\1", text)
    text = MARKDOWN_ATTR_RE.sub("", text)
    return " ".join(text.split())


def strip_trailing_whitespace(text: str) -> str:
    """Remove trailing spaces and tabs without changing line structure."""
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def extract_page_info(md_path: Path, url: str, depth: int = 0) -> PageInfo | None:
    """Extract title and description from a Markdown file."""
    if not md_path.exists():
        return print(f"Warning: {md_path} not found, skipping", file=sys.stderr)
    try:
        content = md_path.read_text(encoding="utf-8")
    except OSError as e:
        return print(f"Warning: Cannot read {md_path}: {e}", file=sys.stderr)

    title, desc_lines, in_code, found_h1 = md_path.stem.replace("-", " ").replace("_", " ").title(), [], False, False
    for line in content.split("\n"):
        if (s := line.strip()).startswith("```"):
            in_code = not in_code
        elif in_code:
            pass
        elif s.startswith("# ") and not found_h1:
            title, found_h1 = clean_summary_text(s[2:]), True
        elif found_h1:
            if s.startswith("## "):
                break
            if s.startswith(SKIP_PREFIXES) or (not s and desc_lines):
                if not s and desc_lines:
                    break
                continue
            if s:
                desc_lines.append(s)
                if len(desc_lines) >= MAX_DESC_LINES:
                    break

    desc = clean_summary_text(" ".join(desc_lines)) if desc_lines else ""
    if len(desc) > MAX_DESC_LEN:
        desc = desc[: MAX_DESC_LEN - 3] + "..."
    return PageInfo(
        title=title,
        path=md_path.relative_to(DOCS_DIR).as_posix(),
        url=url,
        description=desc,
        content=content,
        depth=depth,
    )


def collect_pages(sections: list[NavSection], site_url: str, depth: int = 0) -> list[PageInfo]:
    """Collect all pages from navigation sections."""
    pages: list[PageInfo] = []
    for section in sections:
        if p := section.path:
            base = p.rsplit(".", 1)[0]
            url = f"{site_url}/" if base == "index" else f"{site_url}/{base.removesuffix('/index')}/"
            if page := extract_page_info(DOCS_DIR / p, url, depth):
                pages.append(page)
        if section.children:
            pages.extend(collect_pages(section.children, site_url, depth + 1))
    return pages


def generate_llms_txt(config: SiteConfig, sections: list[NavSection], pages: list[PageInfo]) -> str:
    """Generate llms.txt content."""
    page_map = {p.path: p for p in pages}

    def fmt(page: PageInfo) -> str:
        return f"- [{page.title}]({page.url})" + (f": {page.description}" if page.description else "")

    def is_optional(path: str | None) -> bool:
        return path is not None and any(fnmatch(path, pat) for pat in OPTIONAL_PATTERNS)

    def render(section: NavSection) -> list[str]:
        if section.path:
            return [fmt(p)] if (p := page_map.get(section.path)) else []
        if not section.children:
            return []
        hdr = "##" if section.depth == 0 else "###"
        lines = [f"{hdr} {section.title}", ""]
        for child in section.children:
            lines.extend(render(child))
        return [*lines, ""]

    main, optional = [], []
    for s in sections:
        is_opt = (s.path and is_optional(s.path)) or (s.children and all(is_optional(c.path) for c in s.children))
        (optional if is_opt else main).append(s)

    lines = [f"# {config['site_name']}", "", f"> {config['site_description']}", ""]
    for s in main:
        lines.extend(render(s))
    if optional:
        lines.extend(["## Optional", ""])
        lines.extend(
            fmt(p)
            for s in optional
            for item in ([s] if s.path else s.children)
            if item.path and (p := page_map.get(item.path))
        )
        lines.append("")

    while lines and not lines[-1]:
        lines.pop()
    return "\n".join([*lines, ""])


def generate_llms_full_txt(pages: list[PageInfo]) -> str:
    """Generate llms-full.txt with all documentation content."""
    parts: list[str] = []
    for page in pages:
        content = page.content.strip()
        if content.startswith("# ") and (nl := content.find("\n")) != -1:
            content = content[nl + 1 :].strip()
        content = strip_trailing_whitespace(content)
        parts.extend([f"# {page.title}", "", f"Source: {page.url}", "", content, "", "---", ""])
    return "\n".join(parts)


def check_files(files: dict[Path, str]) -> int:
    """Check if generated files match expected content."""
    errors = [
        f"{path} does not exist" if not path.exists() else f"{path} is out of date"
        for path, expected in files.items()
        if not path.exists() or path.read_text(encoding="utf-8") != expected
    ]
    if errors:
        for e in errors:
            print(f"Error: {e}", file=sys.stderr)
        print("\nRun 'python scripts/build_llms_txt.py' to regenerate.", file=sys.stderr)
        return 1
    print("llms.txt files are up to date")
    return 0


def main() -> int:
    """Run the llms.txt generator."""
    parser = argparse.ArgumentParser(description="Generate llms.txt files from documentation")
    parser.add_argument("--check", action="store_true", help="Check if files are up to date without modifying")
    parser.add_argument("--output-dir", type=Path, default=DOCS_DIR, help="Output directory (default: docs/)")
    args = parser.parse_args()

    config = parse_zensical_toml(ZENSICAL_TOML)
    sections = flatten_nav(config["nav"])
    pages = collect_pages(sections, config["site_url"])
    files = {
        args.output_dir / "llms.txt": generate_llms_txt(config, sections, pages),
        args.output_dir / "llms-full.txt": generate_llms_full_txt(pages),
    }

    if args.check:
        return check_files(files)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for path, content in files.items():
        path.write_text(content, encoding="utf-8")
        print(f"Generated {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
