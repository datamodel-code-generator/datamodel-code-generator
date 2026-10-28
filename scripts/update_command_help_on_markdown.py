"""Update generated sections in markdown documentation files."""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

START_MARK: str = "<!-- start command help -->"
END_MARK: str = "<!-- end command help -->"
RECIPE_START_MARK: str = "<!-- BEGIN AUTO-GENERATED CLI RECIPE QUICK STARTS -->"
RECIPE_END_MARK: str = "<!-- END AUTO-GENERATED CLI RECIPE QUICK STARTS -->"
BASH_CODE_BLOCK: str = "```bash"
CODE_BLOCK_END: str = "```"

CURRENT_DIR = Path(__file__).parent
PROJECT_DIR = CURRENT_DIR.parent
DOC_DIR = PROJECT_DIR / "docs"
SRC_DIR = PROJECT_DIR / "src"
README_FILE = PROJECT_DIR / "README.md"
DOCS_INDEX_FILE = DOC_DIR / "index.md"
README_CLI_REFERENCE_ROOT = "https://datamodel-code-generator.koxudaxi.dev/cli-reference"
README_CLI_REFERENCE_INDEX = "https://datamodel-code-generator.koxudaxi.dev/cli-reference/"
DOCS_CLI_REFERENCE_ROOT = "cli-reference"
DOCS_CLI_REFERENCE_INDEX = "cli-reference/index.md"

for import_path in (PROJECT_DIR, SRC_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from datamodel_code_generator.arguments import arg_parser  # noqa: E402
from scripts.build_cli_docs import generate_homepage_recipe_quick_starts  # noqa: E402

# CLI help is now auto-generated in docs/cli-reference/ by build_cli_docs.py
# These files no longer contain command help markers
TARGET_MARKDOWN_FILES: list[Path] = []

REPLACE_MAP = {"(default: UTF-8)": "(default: utf-8)", "'": r"''"}


@dataclass(frozen=True, slots=True)
class RecipeMarkdownTarget:
    """Markdown file and link style for generated CLI recipe quick-starts."""

    path: Path
    cli_reference_root: str
    cli_reference_extension: str
    cli_reference_index: str


RECIPE_MARKDOWN_TARGETS: tuple[RecipeMarkdownTarget, ...] = (
    RecipeMarkdownTarget(
        path=README_FILE,
        cli_reference_root=README_CLI_REFERENCE_ROOT,
        cli_reference_extension="/",
        cli_reference_index=README_CLI_REFERENCE_INDEX,
    ),
    RecipeMarkdownTarget(
        path=DOCS_INDEX_FILE,
        cli_reference_root=DOCS_CLI_REFERENCE_ROOT,
        cli_reference_extension=".md",
        cli_reference_index=DOCS_CLI_REFERENCE_INDEX,
    ),
)


def get_help() -> str:
    """Get formatted help text from argument parser."""
    os.environ["COLUMNS"] = "94"
    os.environ["LINES"] = "24"
    with io.StringIO() as f:
        arg_parser.print_help(file=f)
        output = f.getvalue()
    for k, v in REPLACE_MAP.items():
        output = output.replace(k, v)
    # Remove any terminal codes
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)


def replace_marked_section(markdown_text: str, start_mark: str, end_mark: str, replacement: str) -> str:
    """Replace generated markdown content between ordered markers."""
    start_pos = markdown_text.find(start_mark)
    if start_pos == -1:
        msg = f"Could not find {start_mark} in markdown_text"
        raise ValueError(msg)
    end_pos = markdown_text.find(end_mark, start_pos + len(start_mark))
    if end_pos == -1:
        msg = f"Could not find {end_mark} after {start_mark} in markdown_text"
        raise ValueError(msg)
    return markdown_text[: start_pos + len(start_mark)] + "\n" + replacement.rstrip() + "\n" + markdown_text[end_pos:]


def inject_help(markdown_text: str, help_text: str) -> str:
    """Inject help text into markdown between start and end markers."""
    return replace_marked_section(
        markdown_text,
        START_MARK,
        END_MARK,
        f"{BASH_CODE_BLOCK}\n{help_text}{CODE_BLOCK_END}",
    )


def inject_recipe_quick_starts(
    markdown_text: str,
    *,
    cli_reference_root: str,
    cli_reference_extension: str,
    cli_reference_index: str,
) -> str:
    """Inject generated CLI recipe quick-starts into markdown."""
    return replace_marked_section(
        markdown_text,
        RECIPE_START_MARK,
        RECIPE_END_MARK,
        generate_homepage_recipe_quick_starts(
            cli_reference_root=cli_reference_root,
            cli_reference_extension=cli_reference_extension,
            cli_reference_index=cli_reference_index,
        ),
    )


def main() -> int:
    """Update or validate generated sections in target markdown files."""
    script_parser = argparse.ArgumentParser(description="Update generated sections in markdown files")
    script_parser.add_argument(
        "--check",
        action="store_true",
        help="Check if the file content is up to date without modifying",
    )
    args = script_parser.parse_args()
    check: bool = args.check

    out_of_date: list[Path] = []
    help_text = get_help() if TARGET_MARKDOWN_FILES else ""
    for file_path in TARGET_MARKDOWN_FILES:
        with file_path.open("r") as f:
            markdown_text = f.read()
        new_markdown_text = inject_help(markdown_text, help_text)
        if new_markdown_text != markdown_text:
            if check:
                out_of_date.append(file_path)
                continue
            with file_path.open("w") as f:
                f.write(new_markdown_text)

    for target in RECIPE_MARKDOWN_TARGETS:
        markdown_text = target.path.read_text(encoding="utf-8")
        new_markdown_text = inject_recipe_quick_starts(
            markdown_text,
            cli_reference_root=target.cli_reference_root,
            cli_reference_extension=target.cli_reference_extension,
            cli_reference_index=target.cli_reference_index,
        )
        if new_markdown_text != markdown_text:
            if check:
                out_of_date.append(target.path)
                continue
            target.path.write_text(new_markdown_text, encoding="utf-8")

    if out_of_date:
        for path in out_of_date:
            print(f"Markdown generated sections are out of date: {path.relative_to(PROJECT_DIR)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
