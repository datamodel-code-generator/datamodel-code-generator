"""Check that every hand-written Python block of the generation-target guides is a complete module.

A reader copies such a block into a file and runs it, so it must compile as module code, without a top-level
`await`, and name nothing it does not import or define; Ruff reports syntax errors whatever rules are selected, so
E999, which it no longer accepts, is not selected. Blocks between AUTO-GENERATED markers are rendered from
tested fixtures and checked by their own tests.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from tests.conftest import assert_output

ROOT = Path(__file__).parents[1]
DATA = Path(__file__).parent / "data" / "docs_python_blocks"
EXPECTED = Path(__file__).parent / "data" / "expected" / "docs_python_blocks"
GUIDES = tuple(
    path
    for name in (
        "fastapi-server.md",
        "python-client.md",
        "python-client-helpers.md",
        "python-client-reference.md",
        "fastapi-code-generator-migration.md",
    )
    if (path := ROOT / "docs" / name).exists()
)
FENCE = re.compile(r"(?P<indent> *)```(?P<language>\w*)")
FINDING = re.compile(r"(?P<name>[^/\\\s]+)\.py:(?P<line>\d+):\d+: (?P<message>.+)")
RULES = "F821,F822,F823"


def python_blocks(page: Path) -> list[tuple[str, str]]:
    """Return each fenced Python block outside generated sections, named `<page>-<line>`, without its indent."""
    found: list[tuple[str, str]] = []
    generated = False
    block: list[str] | None = None
    indent = ""
    for number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), 1):
        if block is not None:
            if line.strip() == "```":
                found[-1] = (found[-1][0], "".join(block))
                block = None
            else:
                block.append(f"{line.removeprefix(indent)}\n")
        elif "<!-- BEGIN AUTO-GENERATED" in line or "<!-- END AUTO-GENERATED" in line:
            generated = "BEGIN" in line
        elif (fence := FENCE.fullmatch(line.rstrip())) and fence["language"] == "python" and not generated:
            block, indent = [], fence["indent"]
            found.append((f"{page.stem}-{number}", ""))
    return found


def findings(pages: tuple[Path, ...], directory: Path) -> str:
    """Compile each block as a module, check it with Ruff, and report one finding a line."""
    lines: list[str] = []
    for page in pages:
        for name, code in python_blocks(page):
            (directory / f"{name}.py").write_text(code, encoding="utf-8")
            try:
                compile(code, name, "exec", dont_inherit=True)
            except SyntaxError as error:
                lines.append(f"{name}:{error.lineno}: compile: {error.msg}")
    command = [sys.executable, "-m", "ruff", "check", "--isolated", "--no-cache", "--select", RULES]
    result = subprocess.run(
        [*command, "--output-format", "concise", str(directory)], capture_output=True, text=True, check=False
    )
    lines.extend(
        f"{match['name']}:{match['line']}: {match['message']}"
        for line in result.stdout.splitlines()
        if (match := FINDING.search(line))
    )
    lines.extend(f"ruff: {line}" for line in result.stderr.splitlines())
    return "".join(f"{line}\n" for line in sorted(lines)) or "no findings\n"


def test_guide_python_blocks(tmp_path: Path) -> None:
    """Every hand-written Python block of the guides compiles as a module and names only what it defines."""
    assert_output(findings(GUIDES, tmp_path), EXPECTED / "guides.txt")


def test_python_block_findings(tmp_path: Path) -> None:
    """Blocks in tabs are dedented, generated and other blocks are skipped, and each kind of finding is reported."""
    assert_output(findings((DATA / "page.md",), tmp_path), EXPECTED / "page.txt")
