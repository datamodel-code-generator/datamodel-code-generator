"""Diagram builder.

Renders Mermaid diagram sources (``docs/assets/diagrams/*.mmd``) into committed
light/dark SVG files used by both ``README.md`` and the docs site. The README
uses the exported SVGs instead of live Mermaid so GitHub only has to render
ordinary images.

Rendering uses the Mermaid CLI (``mmdc``), which is run through ``npx`` so no
global install is required. Node.js is only needed when the diagram changes; the
generated SVGs are committed, so consumers never need Node to view them.

Each rendered SVG embeds the SHA-256 of its source ``.mmd`` as a comment. The
``--check`` mode compares that embedded hash against the current source, so it
validates staleness without re-rendering.

Usage:
    python scripts/build_diagrams.py          # Render/update SVGs
    python scripts/build_diagrams.py --check   # Fail if committed SVGs are stale
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

DIAGRAMS_DIR = Path(__file__).parent.parent / "docs" / "assets" / "diagrams"
REPO_ROOT = Path(__file__).parent.parent

THEMES = {
    "light": {"mmdc_theme": "default", "background": "#ffffff"},
    "dark": {"mmdc_theme": "dark", "background": "#0d1117"},
}

# Puppeteer config so mmdc's headless Chromium runs in CI/containers.
PUPPETEER_CONFIG = {"args": ["--no-sandbox"]}

# Render with native SVG <text> instead of <foreignObject>/HTML labels. HTML
# labels are dropped by browsers when an SVG is loaded via <img> and stripped by
# GitHub's SVG sanitizer, so the README needs the exported SVG to avoid them.
MERMAID_CONFIG = {
    "htmlLabels": False,
    "flowchart": {
        "htmlLabels": False,
        "nodeSpacing": 24,
        "rankSpacing": 34,
        "padding": 8,
    },
}

HASH_MARKER = "datamodel-codegen:source-sha256:"
HASH_RE = re.compile(rf"<!-- {re.escape(HASH_MARKER)}([0-9a-f]{{64}}) -->")
SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
SECTION_HEADINGS = {"Schemas", "Raw data", "Python objects", "Models", "Client · Server"}
SECTION_HEADING_ATTRS = {
    "fill": "#0288d1",
    "font-weight": "800",
}
ARROW_MARKER_ATTRS = {
    "markerWidth": "14",
    "markerHeight": "14",
    "refX": "7",
    "refY": "5",
}

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


class DiagramRenderError(RuntimeError):
    """Raised when Mermaid CLI rendering fails."""


def source_hash(source: Path) -> str:
    """Return the SHA-256 of a ``.mmd`` source file."""
    return hashlib.sha256(source.read_bytes()).hexdigest()


def embedded_hash(svg: Path) -> str | None:
    """Return the source hash embedded in a committed SVG, if present."""
    if not svg.exists():
        return None
    if match := HASH_RE.search(svg.read_text(encoding="utf-8")):
        return match.group(1)
    return None


def rendered_svg_path(source: Path, theme: str) -> Path:
    """Return the committed SVG path for a source/theme pair."""
    return DIAGRAMS_DIR / f"{source.stem}-{theme}.svg"


def npx_executable() -> str:
    """Return an absolute npx executable path, or raise a user-facing error."""
    if npx := shutil.which("npx"):
        return npx
    msg = "Node.js/npx not found. Install Node.js to render diagrams: https://nodejs.org/"
    raise DiagramRenderError(msg)


def render_error_output(error: subprocess.CalledProcessError) -> str:
    """Return captured process output, if mermaid-cli emitted any."""
    if output := "\n".join(
        stream.strip() for stream in (error.stdout, error.stderr) if isinstance(stream, str) and stream.strip()
    ):
        return output
    return "No output captured from mermaid-cli."


def merge_row_tspans(row: ET.Element, text: str) -> list[ET.Element]:
    """Merge one Mermaid text row into a single tspan so spaces render."""
    inner_tspans = row.findall(f"./{{{SVG_NS}}}tspan")
    if not inner_tspans:
        return []
    first, *extra_tspans = inner_tspans
    first.text = text
    for tspan in extra_tspans:
        row.remove(tspan)
    return [first]


def adjust_svg(markup: str) -> str:
    """Adjust the exported SVG for GitHub-safe rendering.

    Mermaid's native SVG text renderer does not reliably support partial
    Markdown emphasis inside one flowchart node. Apply the emphasis to the
    exported SVG so README rendering stays plain <text>/<tspan> without
    <foreignObject>.

    Mermaid also keeps the arrowhead marker small even when link stroke width is
    increased. Scale the colored arrow markers so the arrowhead remains visible
    against the thicker conversion line.
    """
    root = ET.fromstring(markup)
    for row in root.findall(f".//{{{SVG_NS}}}tspan[@class='text-outer-tspan row']"):
        original_tspans = row.findall(f"./{{{SVG_NS}}}tspan")
        inner_tspans = merge_row_tspans(row, "".join(tspan.text or "" for tspan in original_tspans).strip())
        match row_text := "".join(tspan.text or "" for tspan in inner_tspans).strip():
            case _ if row_text in SECTION_HEADINGS:
                for tspan in inner_tspans:
                    for attr, value in SECTION_HEADING_ATTRS.items():
                        tspan.set(attr, value)
    for marker in root.findall(f".//{{{SVG_NS}}}marker"):
        if "pointEnd__" in marker.get("id", ""):
            for attr, value in ARROW_MARKER_ATTRS.items():
                marker.set(attr, value)
    return ET.tostring(root, encoding="unicode")


def render_theme(source: Path, theme: str, config: dict[str, str], tmp_dir: Path, digest: str) -> tuple[Path, str]:
    """Render one source/theme pair and return the destination with stamped SVG."""
    tmp = tmp_dir / f"{source.stem}-{theme}.svg"
    command = [
        npx_executable(),
        "--yes",
        "-p",
        "@mermaid-js/mermaid-cli",
        "mmdc",
        "-i",
        str(source),
        "-o",
        str(tmp),
        "-t",
        config["mmdc_theme"],
        "-b",
        config["background"],
        "-p",
        str(tmp_dir / "mmdc-puppeteer.json"),
        "-c",
        str(tmp_dir / "mmdc-config.json"),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as error:
        msg = f"Node.js/npx not found ({error}). Install Node.js to render diagrams: https://nodejs.org/"
        raise DiagramRenderError(msg) from error
    except subprocess.CalledProcessError as error:
        msg = (
            f"mermaid-cli failed to render {source.relative_to(REPO_ROOT)} for the {theme} theme "
            f"(exit code {error.returncode}).\n{render_error_output(error)}"
        )
        raise DiagramRenderError(msg) from error
    stamp = f"\n<!-- {HASH_MARKER}{digest} -->\n"
    return rendered_svg_path(source, theme), adjust_svg(tmp.read_text(encoding="utf-8")) + stamp


def render(source: Path) -> dict[Path, str]:
    """Render one ``.mmd`` source per theme and return the stamped SVG markup."""
    digest = source_hash(source)
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        pptr_cfg = tmp_dir / "mmdc-puppeteer.json"
        pptr_cfg.write_text(json.dumps(PUPPETEER_CONFIG), encoding="utf-8")
        mmd_cfg = tmp_dir / "mmdc-config.json"
        mmd_cfg.write_text(json.dumps(MERMAID_CONFIG), encoding="utf-8")
        return dict(render_theme(source, theme, config, tmp_dir, digest) for theme, config in THEMES.items())


def check_diagrams(sources: list[Path]) -> int:
    """Return non-zero when committed SVGs are missing or stale."""
    stale = [
        svg
        for source in sources
        for theme in THEMES
        if embedded_hash(svg := rendered_svg_path(source, theme)) != source_hash(source)
    ]
    if not stale:
        return 0
    listing = "\n".join(f"  - {p.relative_to(REPO_ROOT)}" for p in stale)
    print(
        f"Diagram SVGs are out of date:\n{listing}\nRun: tox run -e diagrams",
        file=sys.stderr,
    )
    return 1


def write_diagrams(sources: list[Path]) -> int:
    """Render and write all diagrams."""
    for source in sources:
        for dest, markup in render(source).items():
            dest.write_text(markup, encoding="utf-8")
            print(f"Rendered {dest.relative_to(REPO_ROOT)}")
    return 0


def main() -> int:
    """Render diagrams, or validate that committed SVGs are up to date."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify committed SVGs were rendered from the current .mmd sources.",
    )
    args = parser.parse_args()

    sources = sorted(DIAGRAMS_DIR.glob("*.mmd"))
    if not sources:
        print(f"No .mmd sources found in {DIAGRAMS_DIR}", file=sys.stderr)
        return 1

    try:
        match args.check:
            case True:
                return check_diagrams(sources)
            case False:
                return write_diagrams(sources)
            case _:
                return 1
    except DiagramRenderError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
