"""Tests for llms.txt documentation generation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts import build_llms_txt
from tests.conftest import assert_output

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_llms_txt.py"
EXPECTED_LLMS_TXT_PATH = Path(__file__).resolve().parent / "data" / "expected" / "llms_txt"


def test_build_llms_txt_check_is_up_to_date() -> None:
    """Generated llms.txt files are committed."""
    subprocess.run([sys.executable, str(SCRIPT), "--check"], check=True)


def test_clean_summary_text_removes_markdown_links_and_heading_attrs() -> None:
    """llms.txt summaries do not leak raw Markdown link or heading syntax."""
    output = "\n".join((
        build_llms_txt.clean_summary_text(
            "See [Supported Data Types](./supported-data-types.md#openapi-3-and-json-schema) for details."
        ),
        build_llms_txt.clean_summary_text("Generate from Python Models {#python-model}"),
        "",
    ))

    assert_output(output, EXPECTED_LLMS_TXT_PATH / "description_cleanup.txt")


def test_generate_llms_full_txt_strips_trailing_whitespace() -> None:
    """llms-full.txt output does not retain source line-end whitespace."""
    output = build_llms_txt.generate_llms_full_txt([
        build_llms_txt.PageInfo(
            title="Example",
            path="example.md",
            url="https://example.test/example/",
            description="",
            content="# Example\n\nLine with spaces    \n\t   \nNext line\t",
        )
    ])

    assert_output(output, EXPECTED_LLMS_TXT_PATH / "full_txt_trailing_whitespace.txt")
