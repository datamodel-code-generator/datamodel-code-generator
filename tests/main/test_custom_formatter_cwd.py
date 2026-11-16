"""Custom formatters resolve relative ``--custom-formatters-kwargs`` paths from the caller cwd."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from datamodel_code_generator.__main__ import Exit, main

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_custom_formatter_relative_kwarg_path_under_subdir_check_and_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Resolve relative file paths in custom formatter kwargs under subdir, --check, and --output-format json."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "header.txt").write_text("# CUSTOM HEADER\n", encoding="utf-8")
    (tmp_path / "schema.json").write_text(
        json.dumps({"type": "object", "properties": {"id": {"type": "integer"}}}),
        encoding="utf-8",
    )
    formatter_mod = tmp_path / "rel_header_formatter.py"
    formatter_mod.write_text(
        """from pathlib import Path
from datamodel_code_generator.format import CustomCodeFormatter

class CodeFormatter(CustomCodeFormatter):
    def apply(self, code: str) -> str:
        header = Path(self.formatter_kwargs['header_path']).read_text(encoding='utf-8')
        return header + code
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("rel_header_formatter", None)

    out_file = tmp_path / "gen" / "model.py"
    rc = main([
        "--input",
        "schema.json",
        "--input-file-type",
        "jsonschema",
        "--output",
        "gen/model.py",
        "--custom-formatters",
        "rel_header_formatter",
        "--custom-formatters-kwargs",
        json.dumps({"header_path": "header.txt"}),
    ])
    assert rc == Exit.OK
    assert "# CUSTOM HEADER\n" in out_file.read_text(encoding="utf-8")

    rc_check = main([
        "--input",
        "schema.json",
        "--input-file-type",
        "jsonschema",
        "--output",
        "gen/model.py",
        "--custom-formatters",
        "rel_header_formatter",
        "--custom-formatters-kwargs",
        json.dumps({"header_path": "header.txt"}),
        "--check",
    ])
    assert rc_check == Exit.OK

    json_out = tmp_path / "out.json"
    capsys.readouterr()
    rc_json = main([
        "--input",
        "schema.json",
        "--input-file-type",
        "jsonschema",
        "--output",
        str(json_out),
        "--output-format",
        "json",
        "--custom-formatters",
        "rel_header_formatter",
        "--custom-formatters-kwargs",
        json.dumps({"header_path": "header.txt"}),
    ])
    assert rc_json == Exit.OK
    payload = json.loads(capsys.readouterr().out)
    assert "# CUSTOM HEADER\n" in payload["files"][0]["content"]
