"""Read the files of a directory input that are text; a hidden file that is not text is no schema (#7128)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import InputFileTypeLiteral, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

FINDER_METADATA = b"\x00\x00\x00\x01Bud1\x00\x00\x10\x00\x00\x08\x00"
"""The start of a ``.DS_Store`` file, which Finder writes into every directory it opens."""
HIDDEN_BINARY_PLACES = ["a/.DS_Store", "a/b/.DS_Store", ".cache/thumbnail.bin", "a/.hidden/.DS_Store"]
COMMON_ARGS = ("--formatters", "builtin", "--disable-timestamp")


def _write_schemas(input_dir: Path, encoding: str = "utf-8") -> None:
    """Write two schema files, the second naming the first by a reference."""
    (input_dir / "a").mkdir(parents=True)
    (input_dir / "a" / "pet.json").write_text(
        json.dumps({"title": "Pet", "type": "object", "properties": {"name": {"type": "string"}}}), encoding=encoding
    )
    (input_dir / "owner.json").write_text(
        json.dumps({"title": "Owner", "type": "object", "properties": {"pet": {"$ref": "a/pet.json"}}}),
        encoding=encoding,
    )


@pytest.mark.parametrize("input_file_type", ["jsonschema", None])
@pytest.mark.parametrize("place", HIDDEN_BINARY_PLACES)
def test_directory_input_skips_hidden_binary_files(
    tmp_path: Path, place: str, input_file_type: InputFileTypeLiteral | None
) -> None:
    """Skip a hidden file that is not text, such as Finder's ``.DS_Store``, instead of stopping on it (#7128)."""
    input_dir = tmp_path / "schemas"
    _write_schemas(input_dir)
    hidden = input_dir / place
    hidden.parent.mkdir(parents=True, exist_ok=True)
    hidden.write_bytes(FINDER_METADATA)
    run_main_and_assert(
        input_path=input_dir,
        output_path=tmp_path / "models",
        input_file_type=input_file_type,
        extra_args=COMMON_ARGS,
    )


@pytest.mark.parametrize("input_file_type", ["jsonschema", None])
def test_directory_input_skips_hidden_binary_files_beside_the_output(
    tmp_path: Path, input_file_type: InputFileTypeLiteral | None
) -> None:
    """Skip it where the output directory lies inside the input directory too (the directory input filter)."""
    input_dir = tmp_path / "schemas"
    _write_schemas(input_dir)
    (input_dir / ".DS_Store").write_bytes(FINDER_METADATA)
    run_main_and_assert(
        input_path=input_dir,
        output_path=input_dir / "models",
        input_file_type=input_file_type,
        extra_args=COMMON_ARGS,
    )


def test_directory_input_keeps_hidden_text_files(tmp_path: Path) -> None:
    """Keep a hidden file that reads as text an input: its schema becomes a module as it always did."""
    input_dir = tmp_path / "schemas"
    _write_schemas(input_dir)
    (input_dir / ".draft.json").write_text(json.dumps({"title": "Draft", "type": "object"}), encoding="utf-8")
    run_main_and_assert(
        input_path=input_dir,
        output_path=tmp_path / "models",
        input_file_type="jsonschema",
        extra_args=COMMON_ARGS,
    )


def test_directory_input_keeps_hidden_files_that_are_text_in_the_encoding(tmp_path: Path) -> None:
    """Keep a hidden UTF-16 schema an input with ``--encoding utf-16``, though its bytes hold NUL."""
    input_dir = tmp_path / "schemas"
    _write_schemas(input_dir, "utf-16")
    (input_dir / ".schemas").mkdir()
    (input_dir / ".schemas" / "draft.json").write_text(
        json.dumps({"title": "Draft", "type": "object"}), encoding="utf-16"
    )
    run_main_and_assert(
        input_path=input_dir,
        output_path=tmp_path / "models",
        input_file_type="jsonschema",
        extra_args=(*COMMON_ARGS, "--encoding", "utf-16"),
    )
