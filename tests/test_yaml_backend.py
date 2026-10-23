"""Tests for YAML backend detection and ryaml/PyYAML switching."""

from __future__ import annotations

import io
import re
import sys
import warnings
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
import yaml

from datamodel_code_generator import Error, InputFileType, infer_input_type, load_yaml, load_yaml_dict_from_path
from datamodel_code_generator.util import (
    _is_yaml_deprecated_bool_warning_enabled,
    get_yaml_backend,
    get_yaml_parse_errors,
    warn_yaml_deprecated_bool_values,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _clear_caches() -> Iterator[None]:
    """Clear lru_cache before and after each test."""
    get_yaml_backend.cache_clear()
    get_yaml_parse_errors.cache_clear()
    yield
    get_yaml_backend.cache_clear()
    get_yaml_parse_errors.cache_clear()


class TestGetYamlBackend:
    """Tests for get_yaml_backend()."""

    def test_without_ryaml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ryaml is not importable, returns 'pyyaml'."""
        monkeypatch.setitem(sys.modules, "ryaml", None)
        assert get_yaml_backend() == "pyyaml"

    def test_with_ryaml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ryaml is importable, returns 'ryaml'."""
        mock_ryaml = MagicMock()
        monkeypatch.setitem(sys.modules, "ryaml", mock_ryaml)
        assert get_yaml_backend() == "ryaml"


class TestGetYamlParseErrors:
    """Tests for get_yaml_parse_errors()."""

    def test_pyyaml_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without ryaml, only yaml.YAMLError is returned."""
        monkeypatch.setitem(sys.modules, "ryaml", None)
        errors = get_yaml_parse_errors()
        assert errors == (yaml.YAMLError,)

    def test_includes_ryaml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With ryaml, InvalidYamlError is included."""
        mock_ryaml = MagicMock()
        mock_ryaml.InvalidYamlError = type("InvalidYamlError", (Exception,), {})
        monkeypatch.setitem(sys.modules, "ryaml", mock_ryaml)
        errors = get_yaml_parse_errors()
        assert yaml.YAMLError in errors
        assert mock_ryaml.InvalidYamlError in errors
        assert len(errors) == 2


class TestLoadYaml:
    """Tests for load_yaml() with backend switching."""

    def test_load_yaml_dict_from_path_reads_yaml_dict(self, tmp_path: Path) -> None:
        """Read YAML dict data from a local path."""
        path = tmp_path / "schema.yaml"
        path.write_text("key: value\n", encoding="utf-8")

        assert load_yaml_dict_from_path(path, "utf-8") == {"key": "value"}

    def test_pyyaml_fallback_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ryaml is unavailable, PyYAML is used for string input."""
        monkeypatch.setitem(sys.modules, "ryaml", None)
        result = load_yaml("key: value")
        assert result == {"key": "value"}

    def test_pyyaml_fallback_textio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ryaml is unavailable, PyYAML is used for TextIO input."""
        monkeypatch.setitem(sys.modules, "ryaml", None)
        result = load_yaml(io.StringIO("key: value"))
        assert result == {"key": "value"}

    def test_load_yaml_allows_literal_unsupported_tag_marker_text(self) -> None:
        """Literal marker text is valid YAML data when it is not a YAML tag."""
        result = load_yaml("description: 'literal !!set text'\n")

        assert result == {"description": "literal !!set text"}

    def test_load_yaml_allows_literal_unsupported_tag_marker_text_in_sequence(self) -> None:
        """Literal marker text is valid YAML sequence data when it is not a YAML tag."""
        result = load_yaml("descriptions:\n  - 'literal !!set text'\n")

        assert result == {"descriptions": ["literal !!set text"]}

    def test_load_yaml_allows_comment_only_unsupported_tag_marker_text(self) -> None:
        """Comment-only marker text is not a YAML tag."""
        result = load_yaml("# !!set\n")

        assert result is None

    def test_load_yaml_rejects_yaml_set_tag(self) -> None:
        """YAML set tags are rejected through the public YAML loader."""
        with pytest.raises(yaml.YAMLError, match=r"Unsupported YAML tag: tag:yaml\.org,2002:set"):
            load_yaml("fruits: !!set\n  ? apple\n")

    def test_load_yaml_rejects_yaml_set_tag_with_custom_handle(self) -> None:
        """YAML set tags are rejected even when declared through a custom YAML tag handle."""
        with pytest.raises(yaml.YAMLError, match=r"Unsupported YAML tag: tag:yaml\.org,2002:set"):
            load_yaml("%TAG !e! tag:yaml.org,2002:\n---\nfruits: !e!set\n  ? apple\n")

    def test_with_ryaml_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ryaml is available, ryaml.loads() is used for string input."""
        mock_ryaml = MagicMock()
        mock_ryaml.loads.return_value = {"key": "value"}
        monkeypatch.setitem(sys.modules, "ryaml", mock_ryaml)
        result = load_yaml("key: value")
        mock_ryaml.loads.assert_called_once_with("key: value")
        assert result == {"key": "value"}

    def test_warn_yaml_deprecated_bool_values_skips_scan_when_warning_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When deprecation warnings are ignored, skip the extra YAML source scan."""
        pattern = MagicMock()
        monkeypatch.setattr("datamodel_code_generator.util._YAML_DEPRECATED_BOOL_LINE_PATTERN", pattern)

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            warn_yaml_deprecated_bool_values("enabled: True\n")

        pattern.finditer.assert_not_called()

    def test_warn_yaml_deprecated_bool_values_warns_when_enabled(self) -> None:
        """When deprecation warnings are enabled, warn for YAML 1.1 bool values."""
        with pytest.warns(DeprecationWarning, match=r"YAML bool 'True' is deprecated"):
            warn_yaml_deprecated_bool_values("enabled: True\n")

    def test_yaml_deprecated_bool_warning_enabled_ignores_unmatched_filters(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Warning filter entries for other messages or modules do not disable the YAML warning."""
        monkeypatch.setattr(
            warnings,
            "filters",
            [
                ("ignore", re.compile(r"Other warning"), DeprecationWarning, None, 0),
                ("ignore", None, DeprecationWarning, re.compile(r"other_module"), 0),
            ],
        )

        assert _is_yaml_deprecated_bool_warning_enabled()

    def test_yaml_deprecated_bool_warning_enabled_ignores_unmatched_string_filters(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """String warning filter entries for other modules do not crash or disable the YAML warning."""
        monkeypatch.setattr(
            warnings,
            "filters",
            [
                ("default", None, DeprecationWarning, "__main__", 0),
            ],
        )

        assert _is_yaml_deprecated_bool_warning_enabled()

    def test_yaml_deprecated_bool_warning_enabled_matches_string_filters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """String warning filter entries can still disable the YAML warning when they match."""
        monkeypatch.setattr(
            warnings,
            "filters",
            [
                (
                    "ignore",
                    "YAML bool ",
                    DeprecationWarning,
                    "datamodel_code_generator",
                    0,
                ),
            ],
        )

        assert not _is_yaml_deprecated_bool_warning_enabled()

    def test_with_ryaml_textio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ryaml is available, TextIO.read() is called before ryaml.loads()."""
        mock_ryaml = MagicMock()
        mock_ryaml.loads.return_value = {"key": "value"}
        stream = io.StringIO("key: value")
        monkeypatch.setitem(sys.modules, "ryaml", mock_ryaml)
        result = load_yaml(stream)
        mock_ryaml.loads.assert_called_once_with("key: value")
        assert result == {"key": "value"}


class TestInferInputType:
    """Tests for infer_input_type() with backend error handling."""

    def test_csv_with_pyyaml_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """YAML parse error from PyYAML returns CSV type."""
        monkeypatch.setitem(sys.modules, "ryaml", None)
        result = infer_input_type("a,b:\n1,2\n")
        assert result == InputFileType.CSV

    def test_csv_with_ryaml_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CSV-looking text that neither ryaml nor PyYAML reads returns CSV type."""
        mock_invalid_yaml_error = type("InvalidYamlError", (Exception,), {})
        mock_ryaml = MagicMock()
        mock_ryaml.InvalidYamlError = mock_invalid_yaml_error
        mock_ryaml.loads.side_effect = mock_invalid_yaml_error("parse error")
        monkeypatch.setitem(sys.modules, "ryaml", mock_ryaml)
        result = infer_input_type("a,b:\n1,2\n")
        assert result == InputFileType.CSV

    def test_non_csv_ryaml_error_raises_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-CSV text that neither ryaml nor PyYAML reads raises the inference error with ryaml's parse error."""
        mock_invalid_yaml_error = type("InvalidYamlError", (Exception,), {})
        mock_ryaml = MagicMock()
        mock_ryaml.InvalidYamlError = mock_invalid_yaml_error
        mock_ryaml.loads.side_effect = mock_invalid_yaml_error("parse error")
        monkeypatch.setitem(sys.modules, "ryaml", mock_ryaml)
        with pytest.raises(Error, match=r"YAML parser error: InvalidYamlError: parse error"):
            infer_input_type("key: [unclosed\n")

    def test_openapi_detection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OpenAPI input is detected correctly regardless of backend."""
        monkeypatch.setitem(sys.modules, "ryaml", None)
        result = infer_input_type("openapi: '3.0.0'\ninfo:\n  title: Test\n  version: '1.0'")
        assert result == InputFileType.OpenAPI
