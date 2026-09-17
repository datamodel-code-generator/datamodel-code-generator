"""End-to-end checks for formatting documentation without rewriting generated code."""

from __future__ import annotations

import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest
from packaging.version import Version

from scripts import build_cli_docs, build_docs_examples, build_preset_docs
from tests.conftest import assert_output

DATA = Path(__file__).parent / "data"
EXPECTED = DATA / "expected" / "docs_formatting"
MODEL_PATH = "docs_formatting/model.py"


@pytest.mark.skipif(Version(version("ruff")) < Version("0.16.6"), reason="Requires Ruff Markdown formatting")
@pytest.mark.parametrize("kind", ["fixture", "golden", "stdout_file", "stdout", "extra", "readme", "quick_start"])
def test_documentation_formatting_preserves_generated_examples(kind: str) -> None:
    """Real Ruff formats surrounding prose examples while keeping generated fixtures verbatim."""
    model = (DATA / "expected" / "main" / MODEL_PATH).read_text(encoding="utf-8")
    example = build_cli_docs.CLIDocExample(node_id="formatting", option_description="")
    match kind:
        case "fixture":
            rendered = build_docs_examples.fenced("python", model)
        case "golden":
            example.golden_output = MODEL_PATH
            rendered = build_cli_docs._generate_single_example_output(example)
        case "stdout_file":
            example.expected_stdout = MODEL_PATH
            rendered = build_cli_docs._generate_single_example_output(example)
        case "stdout":
            example.expected_stdout = model
            rendered = build_cli_docs._generate_single_example_output(example)
        case "extra":
            example.extra_outputs = [{"title": "Model", "path": MODEL_PATH, "language": "python"}]
            rendered = build_cli_docs._generate_extra_outputs(example)
        case "readme":
            rendered = build_preset_docs._render_readme_quick_start("example", model.rstrip(), "practical")
        case "quick_start":
            rendered = build_preset_docs._render_docs_quick_start("example", model.rstrip(), "practical")
    markdown = (DATA / "docs_formatting" / "explanation.md").read_text(encoding="utf-8")
    markdown = markdown.replace("<!-- generated-example -->", rendered)
    command = [sys.executable, "-m", "ruff", "format", "--isolated", "--preview", "--stdin-filename", "example.md", "-"]
    result = subprocess.run(command, input=markdown, encoding="utf-8", capture_output=True, check=True)
    assert_output(result.stdout, EXPECTED / f"{kind}.txt")
    repeated = subprocess.run(command, input=result.stdout, encoding="utf-8", capture_output=True, check=True)
    assert_output(repeated.stdout, EXPECTED / f"{kind}.txt")
