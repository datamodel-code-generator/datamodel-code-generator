"""Tests for llms.txt documentation generation."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from scripts import build_llms_txt
from tests.conftest import assert_output

if TYPE_CHECKING:
    import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_llms_txt.py"
MIGRATION_SCRIPT = ROOT / "scripts" / "build_migration_docs.py"
EXPECTED_LLMS_TXT_PATH = Path(__file__).resolve().parent / "data" / "expected" / "llms_txt"


def _not_pages(directory: str, names: list[str]) -> list[str]:
    kept = {name for name in names if name.endswith(".md") or name == "llms.txt" or Path(directory, name).is_dir()}
    return [name for name in names if name not in kept]


def test_build_llms_txt_check_is_up_to_date(tmp_path: Path) -> None:
    """The committed llms.txt is up to date with the pages the docs deploy has; llms-full.txt is built at deploy.

    The deploy builds the upgrade guide's all-changes page, which llms.txt lists, before this script, so the check runs
    on a copy of the pages with that page built.
    """
    checkout = tmp_path / "checkout"
    docs = checkout / "docs"
    shutil.copytree(ROOT / "docs", docs, ignore=_not_pages)
    shutil.copy2(ROOT / "zensical.toml", checkout)
    (checkout / "scripts").mkdir()
    shutil.copy2(SCRIPT, checkout / "scripts")
    migration = ["--docs-dir", str(docs / "migration"), "--json", str(docs / "data" / "migration.json")]
    subprocess.run([sys.executable, str(MIGRATION_SCRIPT), *migration], check=True)
    subprocess.run([sys.executable, str(checkout / "scripts" / SCRIPT.name), "--check"], check=True)


def test_build_llms_txt_builds_the_missing_guide_page_first(tmp_path: Path) -> None:
    """A local run builds the upgrade guide's all-changes page the deploy builds, so llms.txt keeps listing it."""
    docs = tmp_path / "docs"
    (docs / "migration").mkdir(parents=True)
    build_llms_txt.build_missing_guide(docs, MIGRATION_SCRIPT)
    built = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file())
    build_llms_txt.build_missing_guide(docs, tmp_path / "absent.py")
    assert_output(f"built {built}\n")


def test_build_llms_txt_writes_both_files_and_checks_only_the_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The build writes llms.txt and llms-full.txt; --check compares llms.txt alone, so no llms-full.txt is needed."""
    monkeypatch.setattr(sys, "argv", ["build_llms_txt.py", "--output-dir", str(tmp_path)])
    codes = [build_llms_txt.main()]
    written = sorted(path.name for path in tmp_path.iterdir())
    (tmp_path / "llms-full.txt").unlink()
    monkeypatch.setattr(sys, "argv", ["build_llms_txt.py", "--check", "--output-dir", str(tmp_path)])
    codes.append(build_llms_txt.main())
    printed = capsys.readouterr().out.replace(str(tmp_path), "<output dir>").replace("\\", "/")
    assert_output(f"exit codes {codes}\nwritten {written}\n{printed}")


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
