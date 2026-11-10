"""Tests for code formatting functionality."""

from __future__ import annotations

import ast
import pickle
import shutil
import subprocess
import sys
import tokenize
import warnings
from pathlib import Path
from typing import cast
from unittest import mock

import black
import isort
import pytest

import datamodel_code_generator
import datamodel_code_generator._builtin_formatter as builtin_formatter
import datamodel_code_generator._format_types as format_types
import datamodel_code_generator.format as format_module
from tests.conftest import assert_output

DEFAULT_KNOWN_FIRST_PARTY = format_module.DEFAULT_KNOWN_FIRST_PARTY
CodeFormatter = format_module.CodeFormatter
Formatter = format_module.Formatter
PythonVersion = format_module.PythonVersion
PythonVersionMin = format_module.PythonVersionMin
_format_import_node_without_reordering = format_module._format_import_node_without_reordering
_get_builtin_line_length = format_module._get_builtin_line_length
_get_builtin_known_first_party = format_module._get_builtin_known_first_party
_get_builtin_string_normalization = format_module._get_builtin_string_normalization
_normalize_string_quotes = format_module._normalize_string_quotes
apply_builtin_formatter = format_module.apply_builtin_formatter
resolve_use_type_checking_imports = format_module.resolve_use_type_checking_imports

EXAMPLE_LICENSE_FILE = str(Path(__file__).parent / "data/python/custom_formatters/license_example.txt")

UN_EXIST_FORMATTER = "tests.data.python.custom_formatters.un_exist"
WRONG_FORMATTER = "tests.data.python.custom_formatters.wrong"
NOT_SUBCLASS_FORMATTER = "tests.data.python.custom_formatters.not_subclass"
ADD_COMMENT_FORMATTER = "tests.data.python.custom_formatters.add_comment"
ADD_LICENSE_FORMATTER = "tests.data.python.custom_formatters.add_license"
FAKE_RUFF_PATH = "/opt/fake-ruff/bin/ruff"
PARITY_BUCKETS = 8
BLACK_VERSION_DEPENDENT_NORMALIZED_EXPECTED_FILES = {
    "main/jsonschema/custom_file_header_path_prepend_multi_file/__init__.py",
    "main/jsonschema/custom_file_header_path_prepend_multi_file/order.py",
    "main/jsonschema/custom_file_header_path_prepend_multi_file/product.py",
    "main/jsonschema/custom_file_header_path_prepend_multi_file/user.py",
    "main/openapi/custom_file_header_with_docstring_and_import.py",
}
BUILTIN_FORMATTER_LOCAL_CONSTANTS = {
    "DEFAULT_LINE_LENGTH",
    "DEFAULT_KNOWN_FIRST_PARTY",
    "MAX_TOP_LEVEL_BLANK_LINES",
    "MAX_SHORT_DEFAULT_OVERFLOW",
    "LONG_TARGET_PREFIX_LENGTH",
    "TYPE_ALIAS_INLINE_ARGUMENT_COUNT",
    "STRING_PREFIX_PATTERN",
}
BUILTIN_FORMATTER_EXPECTED_PATH = Path(__file__).parent / "data" / "expected" / "builtin_formatter"
BUILTIN_STRING_NORMALIZATION_INPUT_PATH = (
    Path(__file__).parent / "data" / "python" / "builtin_formatter_string_normalization.txt"
)
BUILTIN_WRAP_STRING_LITERAL_INPUT_PATH = (
    Path(__file__).parent / "data" / "python" / "builtin_formatter_wrap_string_literal.txt"
)
BUILTIN_PEP695_INVALID_INPUT_PATH = Path(__file__).parent / "data" / "python" / "builtin_formatter_pep695_invalid.txt"
BUILTIN_PEP695_INVALID_EXPECTED_PATH = BUILTIN_FORMATTER_EXPECTED_PATH / (
    "pep695_invalid.txt" if sys.version_info[:2] >= (3, 12) else "pep695_invalid_legacy.txt"
)
BUILTIN_QUALIFIED_HEADS_INPUT_PATH = Path(__file__).parent / "data" / "python" / "builtin_formatter_qualified_heads.txt"


def test_builtin_formatter_moved_names_are_reexported() -> None:
    """Test format.py keeps an explicit re-export shim for moved builtin formatter names."""
    assert format_module._BUILTIN_FORMATTER_REEXPORTS
    assert "__all__" not in vars(format_module)
    for name in format_module._BUILTIN_FORMATTER_REEXPORTS:
        assert getattr(format_module, name) is getattr(builtin_formatter, name)
    for name in BUILTIN_FORMATTER_LOCAL_CONSTANTS:
        assert getattr(format_module, name) is getattr(format_types, name)


@pytest.mark.parametrize("module_name", ["datamodel_code_generator.format", "datamodel_code_generator"])
def test_light_import_does_not_load_builtin_formatter(module_name: str) -> None:
    """Test light package imports do not import the built-in formatter module."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import importlib, sys; "
                f"importlib.import_module({module_name!r}); "
                "print('datamodel_code_generator._builtin_formatter' in sys.modules)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout == "False\n"


def test_format_star_import_keeps_constants_without_loading_builtin_formatter() -> None:
    """Test star import keeps public constants without loading the built-in formatter module."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "namespace = {}; "
                "exec('from datamodel_code_generator.format import *', namespace); "
                "print(namespace['DEFAULT_LINE_LENGTH']); "
                "print('datamodel_code_generator._builtin_formatter' in sys.modules)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout == "88\nFalse\n"


def test_package_import_does_not_load_format_module() -> None:
    """Test package root import keeps formatter exports lazy."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            ("import sys; import datamodel_code_generator; print('datamodel_code_generator.format' in sys.modules)"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout == "False\n"


@pytest.mark.parametrize(
    "statement",
    [
        "import datamodel_code_generator._format_types",
        "import datamodel_code_generator.arguments",
        "import datamodel_code_generator.config",
        "from datamodel_code_generator import DEFAULT_FORMATTERS, PythonVersionMin",
    ],
)
def test_light_format_type_imports_do_not_load_formatter_runtime(statement: str) -> None:
    """Test light format type consumers do not import formatter runtime modules."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                f"{statement}; "
                "print('datamodel_code_generator.format' in sys.modules); "
                "print('datamodel_code_generator._builtin_formatter' in sys.modules)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout == "False\nFalse\n"


def test_format_type_reexports_keep_public_identity() -> None:
    """Test moved format types keep the public class identity and pickle path."""
    assert format_module.DateClassType is format_types.DateClassType
    assert format_module.DatetimeClassType is format_types.DatetimeClassType
    assert format_module.Formatter is format_types.Formatter
    assert format_module.PythonVersion is format_types.PythonVersion
    assert format_module.PythonVersionMin is format_types.PythonVersionMin
    assert format_module.DEFAULT_FORMATTERS is format_types.DEFAULT_FORMATTERS
    assert datamodel_code_generator.PythonVersion is format_types.PythonVersion
    assert datamodel_code_generator.DEFAULT_FORMATTERS is format_types.DEFAULT_FORMATTERS
    assert format_types.PythonVersion.__module__ == "datamodel_code_generator.format"
    assert pickle.loads(pickle.dumps(format_types.PythonVersion.PY_310)) is format_types.PythonVersion.PY_310


def test_apply_builtin_formatter_keeps_concrete_public_python_version_type() -> None:
    """Test the public formatter wrapper keeps the concrete PythonVersion annotation."""
    assert format_module.apply_builtin_formatter.__annotations__["python_version"] == "PythonVersion | None"
    assert format_module.apply_builtin_formatter("x=1\n", python_version=PythonVersionMin) == (
        builtin_formatter.apply_builtin_formatter("x=1\n", python_version=PythonVersionMin)
    )


def test_python_version() -> None:
    """Ensure that the python version used for the tests is properly listed."""
    _ = PythonVersion("{}.{}".format(*sys.version_info[:2]))


_PYTHON_VERSION_CAPABILITY_MINIMUMS: dict[str, tuple[int, int]] = {
    "has_union_operator": (3, 10),
    "has_kw_only_dataclass": (3, 10),
    "has_typed_dict_non_required": (3, 11),
    "has_string_arguments_in_builtin_generics": (3, 11),
    "has_strenum": (3, 11),
    "has_type_statement": (3, 12),
    "has_typed_dict_read_only": (3, 13),
    "has_native_deferred_annotations": (3, 14),
    "has_typed_dict_closed": (3, 15),
}


@pytest.mark.parametrize("python_version", list(PythonVersion), ids=lambda version: version.value)
def test_python_version_capabilities(python_version: PythonVersion) -> None:
    """Every public capability of a target Python version holds from the release that added it."""
    assert {name: getattr(python_version, name) for name in _PYTHON_VERSION_CAPABILITY_MINIMUMS} == {
        name: python_version.version_key >= minimum for name, minimum in _PYTHON_VERSION_CAPABILITY_MINIMUMS.items()
    }


def test_python_version_has_native_deferred_annotations() -> None:
    """Test that has_native_deferred_annotations returns correct values for each Python version."""
    assert not PythonVersion.PY_310.has_native_deferred_annotations
    assert not PythonVersion.PY_311.has_native_deferred_annotations
    assert not PythonVersion.PY_312.has_native_deferred_annotations
    assert not PythonVersion.PY_313.has_native_deferred_annotations
    assert PythonVersion.PY_314.has_native_deferred_annotations


@pytest.mark.parametrize(
    ("skip_string_normalization", "expected_output"),
    [
        (True, "a = 'b'"),
        (False, 'a = "b"'),
    ],
)
def test_format_code_with_skip_string_normalization(
    skip_string_normalization: bool,
    expected_output: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test code formatting with skip string normalization option."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        skip_string_normalization=skip_string_normalization,
        formatters=[Formatter.BLACK, Formatter.ISORT],
    )

    formatted_code = formatter.format_code("a = 'b'")

    assert formatted_code == expected_output + "\n"


def test_format_code_builtin_formatter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test dependency-free built-in formatting."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.BUILTIN],
    )

    formatted_code = formatter.format_code(
        "from pydantic import Field, BaseModel\n"
        "import sys\n"
        "from __future__ import annotations\n"
        "from .models import Pet\n"
        "\n"
        "class Model(BaseModel):\n"
        "    pet: Pet\n"
        "    name: str = Field(...)\n"
    )

    assert (
        formatted_code == "from __future__ import annotations\n"
        "\n"
        "import sys\n"
        "\n"
        "from pydantic import BaseModel, Field\n"
        "\n"
        "from .models import Pet\n"
        "\n"
        "\n"
        "class Model(BaseModel):\n"
        "    pet: Pet\n"
        "    name: str = Field(...)\n"
    )


def test_builtin_generated_formatter_edge_cases() -> None:
    """Preserve fallback behavior for uncommon generator-owned source shapes."""
    cases = (
        ("unterminated-import", "from package import (\n    Item\n", {}),
        ("newer-target", "value = 1\n", {"python_version": PythonVersion.PY_314}),
        ("empty", "\n\n", {}),
        ("leading-header", "# generated header\nvalue = 1\n", {}),
        ("body-only", "class Model:\n    pass\n", {}),
        ("invalid-import", "from package import )\n", {}),
        ("compound-import-line", "import package; value = 1\n", {}),
        ("import-only", "import package\n", {}),
        ("import-class", "import package\n\n\nclass Model:\n    pass\n", {}),
        ("import-statement", "import package\n\nvalue = 1\n", {}),
        ("qualified-heads", BUILTIN_QUALIFIED_HEADS_INPUT_PATH.read_text(encoding="utf-8"), {}),
    )
    formatted_cases = [
        f"[{name}]\n{builtin_formatter._apply_builtin_generated_formatter(source, **options)!r}"
        for name, source, options in cases
    ]
    formatted_output = "\n".join(formatted_cases)

    assert_output(
        f"{formatted_output}\n",
        BUILTIN_FORMATTER_EXPECTED_PATH / "generated_edge_cases.txt",
    )


def test_format_code_ignores_builtin_when_external_formatter_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test built-in formatting does not add work before external formatters."""

    def fail_get_builtin_line_length(*_args: object, **_kwargs: object) -> int:
        pytest.fail("built-in formatter configuration should not be read")  # pragma: no cover

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "datamodel_code_generator.format._get_builtin_line_length",
        fail_get_builtin_line_length,
    )

    with pytest.warns(UserWarning, match="built-in formatter is ignored"):
        formatter = CodeFormatter(
            PythonVersionMin,
            formatters=[Formatter.BUILTIN, Formatter.BLACK],
        )

    assert not formatter.use_builtin_formatter
    assert formatter.format_code("x=1\n") == "x = 1\n"


def test_format_code_builtin_formatter_uses_format_module_reexported_callables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test built-in formatting call sites resolve through format.py globals."""
    known_first_party = frozenset({"example"})
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[tool.ruff]\nline-length = 60\n", encoding="utf-8")

    with (
        mock.patch("datamodel_code_generator.format._is_valid_builtin_line_length", return_value=False) as validator,
        pytest.raises(ValueError, match="builtin_format_line_length must be a positive integer"),
    ):
        CodeFormatter(PythonVersionMin, formatters=[Formatter.BUILTIN], builtin_format_line_length=99)
    validator.assert_called_once_with(99)

    with (
        mock.patch("datamodel_code_generator.format._find_pyproject_toml", return_value=None) as pyproject_finder,
        mock.patch("datamodel_code_generator.format._get_builtin_line_length", return_value=99) as line_length_reader,
        mock.patch(
            "datamodel_code_generator.format._get_builtin_known_first_party",
            return_value=known_first_party,
        ) as known_first_party_reader,
        mock.patch(
            "datamodel_code_generator.format._get_builtin_string_normalization",
            return_value=True,
        ) as string_normalization_reader,
        mock.patch("datamodel_code_generator.format.apply_builtin_formatter", return_value="patched\n") as formatter,
    ):
        code_formatter = CodeFormatter(
            PythonVersionMin,
            formatters=[Formatter.BUILTIN],
        )
        formatted_code = code_formatter.format_code("x=1\n")

    assert code_formatter.builtin_line_length == 99
    assert code_formatter.builtin_known_first_party == known_first_party
    assert code_formatter.builtin_string_normalization
    assert formatted_code == "patched\n"
    line_length_reader.assert_called_once()
    known_first_party_reader.assert_called_once()
    string_normalization_reader.assert_called_once()
    shared_tool_config = line_length_reader.call_args.kwargs["tool_config"]
    assert shared_tool_config is known_first_party_reader.call_args.kwargs["tool_config"]
    assert shared_tool_config is string_normalization_reader.call_args.kwargs["tool_config"]
    pyproject_finder.assert_called_once()
    assert shared_tool_config == {}
    formatter.assert_called_once()


def test_format_code_builtin_formatter_uses_explicit_line_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test built-in formatter uses explicit line length configuration."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.ruff]\nline-length = 88\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.BUILTIN],
        builtin_format_line_length=140,
    )

    formatted_code = formatter.format_code(
        "from module import Zed, ExtremelyLongGeneratedTypeName, AnotherLongGeneratedTypeName, "
        "GeneratedTypeNameThatFitsWithinConfiguredLineLength\n"
    )

    assert (
        formatted_code == "from module import AnotherLongGeneratedTypeName, ExtremelyLongGeneratedTypeName, "
        "GeneratedTypeNameThatFitsWithinConfiguredLineLength, Zed\n"
    )


@pytest.mark.parametrize("line_length", [0, -1, True])
def test_format_code_builtin_formatter_rejects_invalid_explicit_line_length(
    line_length: int | bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test built-in formatter rejects invalid explicit line length values."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="builtin_format_line_length must be a positive integer"):
        CodeFormatter(
            PythonVersionMin,
            formatters=[Formatter.BUILTIN],
            builtin_format_line_length=line_length,
        )


def test_builtin_config_helpers_fall_back_without_pyproject(tmp_path: Path) -> None:
    """Test moved built-in config helpers keep no-pyproject fallback behavior."""
    assert _get_builtin_line_length(tmp_path) == format_module.DEFAULT_LINE_LENGTH
    assert _get_builtin_known_first_party(tmp_path) == DEFAULT_KNOWN_FIRST_PARTY
    assert not _get_builtin_string_normalization(tmp_path, skip_string_normalization=True)


def test_builtin_config_helpers_read_pyproject_directly(tmp_path: Path) -> None:
    """Test moved built-in config helpers still read pyproject for direct callers."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[tool.ruff]\nline-length = 120\n[tool.black]\nskip-string-normalization = false\n",
        encoding="utf-8",
    )

    assert _get_builtin_line_length(tmp_path) == 120
    assert _get_builtin_string_normalization(tmp_path, skip_string_normalization=True)


def test_builtin_line_length_helper_rejects_invalid_explicit_value(tmp_path: Path) -> None:
    """Test the moved line length helper still validates direct callers."""
    with pytest.raises(ValueError, match="builtin_format_line_length must be a positive integer"):
        _get_builtin_line_length(tmp_path, 0)


def test_format_code_builtin_formatter_uses_datamodel_codegen_line_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test built-in formatter uses datamodel-codegen line length configuration."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.datamodel-codegen]\nbuiltin-format-line-length = 140\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.BUILTIN],
    )

    formatted_code = formatter.format_code(
        "from module import Zed, ExtremelyLongGeneratedTypeName, AnotherLongGeneratedTypeName, "
        "GeneratedTypeNameThatFitsWithinConfiguredLineLength\n"
    )

    assert (
        formatted_code == "from module import AnotherLongGeneratedTypeName, ExtremelyLongGeneratedTypeName, "
        "GeneratedTypeNameThatFitsWithinConfiguredLineLength, Zed\n"
    )


def test_format_code_builtin_formatter_falls_back_to_ruff_line_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test built-in formatter falls back to Ruff line length configuration."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.ruff]\nline-length = 140\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.BUILTIN],
    )

    formatted_code = formatter.format_code(
        "from module import Zed, ExtremelyLongGeneratedTypeName, AnotherLongGeneratedTypeName, "
        "GeneratedTypeNameThatFitsWithinConfiguredLineLength\n"
    )

    assert (
        formatted_code == "from module import AnotherLongGeneratedTypeName, ExtremelyLongGeneratedTypeName, "
        "GeneratedTypeNameThatFitsWithinConfiguredLineLength, Zed\n"
    )


@pytest.mark.parametrize(
    ("black_config", "skip_string_normalization", "expected_alias"),
    [
        ("skip-string-normalization = false\n", True, '"mapViewMode"'),
        ("skip-string-normalization = true\n", True, "'mapViewMode'"),
        ("", True, "'mapViewMode'"),
        ("skip-string-normalization = true\n", False, '"mapViewMode"'),
    ],
)
def test_format_code_builtin_formatter_reads_black_skip_string_normalization(
    black_config: str,
    skip_string_normalization: bool,
    expected_alias: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test built-in formatter reuses Black string normalization config."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(f"[tool.black]\n{black_config}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.BUILTIN],
        skip_string_normalization=skip_string_normalization,
    )

    formatted_code = formatter.format_code(
        "from typing import Literal\n"
        "from pydantic import BaseModel, Field\n"
        "\n"
        "\n"
        "class Model(BaseModel):\n"
        "    mode: Literal['MODE_2D'] = Field(..., alias='mapViewMode')\n"
    )

    assert f"alias={expected_alias}" in formatted_code


def test_format_code_builtin_formatter_ignores_non_integer_line_lengths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test built-in formatter ignores invalid line length configuration."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[tool.datamodel-codegen]\n"
        "builtin_format_line_length = -1\n"
        "[tool.ruff]\n"
        "line-length = false\n"
        "[tool.black]\n"
        'line-length = "140"\n'
        "[tool.isort]\n"
        'line_length = "140"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.BUILTIN],
    )

    formatted_code = formatter.format_code(
        "from module import Zed, ExtremelyLongGeneratedTypeName, AnotherLongGeneratedTypeName, "
        "GeneratedTypeNameThatPushesTheImportPastTheDefaultLineLength\n"
    )

    assert (
        formatted_code == "from module import (\n"
        "    AnotherLongGeneratedTypeName,\n"
        "    ExtremelyLongGeneratedTypeName,\n"
        "    GeneratedTypeNameThatPushesTheImportPastTheDefaultLineLength,\n"
        "    Zed,\n"
        ")\n"
    )


def test_format_code_builtin_formatter_wraps_long_imports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test built-in formatter wraps import lines that exceed the default line length."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.BUILTIN],
    )

    formatted_code = formatter.format_code(
        "from module import Zed, ExtremelyLongGeneratedTypeName, AnotherLongGeneratedTypeName, "
        "GeneratedTypeNameThatPushesTheImportPastTheDefaultLineLength\n"
    )

    assert (
        formatted_code == "from module import (\n"
        "    AnotherLongGeneratedTypeName,\n"
        "    ExtremelyLongGeneratedTypeName,\n"
        "    GeneratedTypeNameThatPushesTheImportPastTheDefaultLineLength,\n"
        "    Zed,\n"
        ")\n"
    )


def test_format_code_builtin_formatter_preserves_commented_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test built-in formatter keeps import comments attached to their import line."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.BUILTIN],
    )

    formatted_code = formatter.format_code(
        "from pydantic import Field, BaseModel  # noqa: F401\n"
        "import sys\n"
        "from __future__ import annotations\n"
        "\n"
        "class Model(BaseModel):\n"
        "    name: str\n"
    )

    assert (
        formatted_code == "from __future__ import annotations\n"
        "\n"
        "import sys\n"
        "\n"
        "from pydantic import Field, BaseModel  # noqa: F401\n"
        "\n"
        "\n"
        "class Model(BaseModel):\n"
        "    name: str\n"
    )


def test_format_code_builtin_formatter_sorts_type_checking_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test built-in formatter sorts imports inside a generated TYPE_CHECKING block."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.BUILTIN],
    )

    formatted_code = formatter.format_code(
        "from typing import TYPE_CHECKING\n"
        "from pydantic import BaseModel\n"
        "\n"
        "if TYPE_CHECKING:\n"
        "    from .models import Zebra, Antelope\n"
        "    import os\n"
        "\n"
        "class Model(BaseModel):\n"
        "    pet: Antelope\n"
    )

    assert formatted_code == (
        "from typing import TYPE_CHECKING\n"
        "\n"
        "from pydantic import BaseModel\n"
        "\n"
        "if TYPE_CHECKING:\n"
        "    import os\n"
        "\n"
        "    from .models import Antelope, Zebra\n"
        "\n"
        "\n"
        "class Model(BaseModel):\n"
        "    pet: Antelope\n"
    )


def test_apply_builtin_formatter_sorts_aliased_imports_like_isort() -> None:
    """Test built-in formatter keeps isort-compatible groups for aliased imports."""
    code = (
        "from pydantic import Field\n"
        "from pydantic import BaseModel as Model\n"
        "from pydantic import ConfigDict\n"
        "\n"
        "class Pet(Model):\n"
        "    name: str = Field(...)\n"
    )

    assert apply_builtin_formatter(code) == (
        "from pydantic import BaseModel as Model\n"
        "from pydantic import ConfigDict\n"
        "from pydantic import Field\n"
        "\n"
        "\n"
        "class Pet(Model):\n"
        "    name: str = Field(...)\n"
    )


@pytest.mark.parametrize(
    "imports",
    [
        "import foo\nimport Foo\n",
        "import Foo\nimport foo\n",
        "from x import foo\nfrom x import fOo\n",
        "from x import fOo, foo\n",
        "import os\nfrom x import fOo\nimport OS\nfrom x import foo\n",
    ],
)
def test_apply_builtin_formatter_keeps_case_only_import_ties_in_order_like_isort(imports: str) -> None:
    """Keep imports and names that differ only in case in the order they first appear, as isort does."""
    code = f"{imports}\n\nprint(1)\n"

    assert apply_builtin_formatter(code) == black.format_str(
        isort.code(code, config=isort.Config(profile="black")), mode=black.Mode()
    )


@pytest.mark.parametrize(
    "code",
    [
        "from .b import Y\nfrom .a import X\nfrom . import b\nfrom . import a\n\nprint(X, Y, a, b)\n",
        (
            "def f():\n"
            "    from .resources.detailed_zip_code_information._sync import DetailedZipCodeInformationResource\n"
            "    return DetailedZipCodeInformationResource\n"
        ),
        (
            "class A:\n"
            "    def f(self):\n"
            "        from .b import Y\n"
            "\n"
            "        from .a import X\n"
            "        import sys\n"
            "\n"
            "        return X, Y, sys\n"
        ),
        (
            "from typing import TYPE_CHECKING\n"
            "\n"
            "if TYPE_CHECKING:\n"
            "    from .protocols._async_helpers import AsyncRootWebsocket\n"
            "    from .protocols._async_helpers import AsyncProtocolHelpers\n"
            "else:\n"
            "    AsyncRootWebsocket = AsyncProtocolHelpers = None\n"
        ),
        "import typing\n\nif typing.TYPE_CHECKING:\n    import b\n    import a\n    VALUE = a.b\n",
        "class A:\n    import b\n\n\n    import a\n    x = 1\n",
        "from termios import *\n\nprint(TCSANOW)\n",
        "def f():\n    from termios import *\n    from os import path\n    return path\n",
        "from tkinter import *\nfrom tkinter import messagebox\nfrom tkinter import _get_temp_root, _destroy_root\n",
        "try:\n    from _etree import *\n    from _etree import _set_factories\nexcept ImportError:\n    x = 1\n",
        "from b import x\nfrom a import *\nfrom a import y as z\nfrom a import w\n",
    ],
)
def test_apply_builtin_formatter_sorts_nested_import_runs_like_isort(code: str) -> None:
    """Sort, merge and wrap each run of imports in a nested body as isort and Black lay it out."""
    assert apply_builtin_formatter(code) == black.format_str(
        isort.code(code, config=isort.Config(profile="black")), mode=black.FileMode(string_normalization=False)
    )


def test_apply_builtin_formatter_keeps_commented_nested_imports_at_their_indent() -> None:
    """Keep a commented import of a nested run as written, at the indent of the run.

    A comment line ends a run, and imports that share a line stay as they are.
    """
    code = (
        "def g():\n"
        "    import b\n"
        "    # a comment ends the run\n"
        "    import a\n"
        "    import a; import b\n"
        "\n"
        "\n"
        "def f():\n"
        "    try:\n"
        "        import b  # noqa: F401\n"
        "        from .b import (Y,  # first\n"
        "            Z)\n"
        "        import a\n"
        "    except ImportError:\n"
        "        pass\n"
    )

    assert apply_builtin_formatter(code) == (
        "def g():\n"
        "    import b\n"
        "    # a comment ends the run\n"
        "    import a\n"
        "    import a; import b\n"
        "\n"
        "\n"
        "def f():\n"
        "    try:\n"
        "        import b  # noqa: F401\n"
        "        import a\n"
        "\n"
        "        from .b import (Y,  # first\n"
        "            Z)\n"
        "    except ImportError:\n"
        "        pass\n"
    )


def test_format_import_node_rejects_unsupported_nodes() -> None:
    """Test direct import node helpers reject unexpected AST nodes."""
    unsupported_node = cast("ast.Import | ast.ImportFrom", ast.parse("pass\n").body[0])

    with pytest.raises(TypeError, match="Unsupported import node: Pass"):
        _format_import_node_without_reordering(unsupported_node, ["pass"])


def test_apply_builtin_formatter_adds_blank_after_module_docstring() -> None:
    """Test built-in formatter keeps a blank line between module docstrings and imports."""
    code = '"""Generated models."""\nimport sys\n\nclass Pet:\n    pass\n'

    assert apply_builtin_formatter(code) == '"""Generated models."""\n\nimport sys\n\n\nclass Pet:\n    pass\n'


def test_apply_builtin_formatter_collapses_top_level_decorator_blank_lines() -> None:
    """Test built-in formatter keeps black-compatible spacing before top-level decorators."""
    code = (
        "from dataclasses import dataclass\n"
        "\n"
        "Alias = str\n"
        '"""Alias docs."""\n'
        "\n"
        "\n"
        "\n"
        "@dataclass\n"
        "class Pet:\n"
        "    name: str\n"
    )

    assert apply_builtin_formatter(code) == (
        "from dataclasses import dataclass\n"
        "\n"
        "Alias = str\n"
        '"""Alias docs."""\n'
        "\n"
        "\n"
        "@dataclass\n"
        "class Pet:\n"
        "    name: str\n"
    )


def test_apply_builtin_formatter_removes_blank_between_stacked_decorators() -> None:
    """Test built-in formatter keeps black-compatible stacked decorator spacing."""
    code = (
        "from dataclasses import dataclass\n"
        "from typing_extensions import deprecated\n"
        "\n"
        "\n"
        "@deprecated('LegacyUser is deprecated.')\n"
        "\n"
        "@dataclass\n"
        "class LegacyUser:\n"
        "    name: str\n"
    )

    assert apply_builtin_formatter(code) == (
        "from dataclasses import dataclass\n"
        "\n"
        "from typing_extensions import deprecated\n"
        "\n"
        "\n"
        "@deprecated('LegacyUser is deprecated.')\n"
        "@dataclass\n"
        "class LegacyUser:\n"
        "    name: str\n"
    )


def test_apply_builtin_formatter_removes_blank_between_decorator_and_class() -> None:
    """Test built-in formatter keeps decorators attached to decorated classes."""
    code = "from my_module import my_decorator\n\n\n@my_decorator\n\nclass User:\n    name: str\n"

    assert apply_builtin_formatter(code) == (
        "from my_module import my_decorator\n\n\n@my_decorator\nclass User:\n    name: str\n"
    )


def test_apply_builtin_formatter_adds_blank_between_assignment_and_class() -> None:
    """Test built-in formatter keeps black-compatible spacing before classes."""
    code = '__all__ = [\n    "Model",\n]\n\nclass Model:\n    id: str\n'

    assert apply_builtin_formatter(code) == '__all__ = [\n    "Model",\n]\n\n\nclass Model:\n    id: str\n'


def test_apply_builtin_formatter_wraps_module_subscript_assignment() -> None:
    """Test built-in formatter wraps long module-level subscript assignments."""
    code = (
        "class Model:\n"
        "    id: str\n"
        "\n"
        "Model.__annotations__['__pydantic_extra__'] = Dict[str, float | str | bool | dict[str, Any] | None]\n"
        "Model.model_rebuild(force=True)\n"
    )

    assert apply_builtin_formatter(code) == (
        "class Model:\n"
        "    id: str\n"
        "\n"
        "\n"
        "Model.__annotations__['__pydantic_extra__'] = Dict[\n"
        "    str, float | str | bool | dict[str, Any] | None\n"
        "]\n"
        "Model.model_rebuild(force=True)\n"
    )


def test_apply_builtin_formatter_wraps_nested_module_subscript_assignment() -> None:
    """Test built-in formatter wraps nested subscript assignments."""
    code = "Model.__annotations__['__pydantic_extra__'] = Dict[str, list[VeryLongName | OtherVeryLongName | None]]\n"

    assert apply_builtin_formatter(code, line_length=40) == (
        "Model.__annotations__[\n"
        "    '__pydantic_extra__'\n"
        "] = Dict[\n"
        "    str,\n"
        "    list[\n"
        "        VeryLongName\n"
        "        | OtherVeryLongName\n"
        "        | None\n"
        "    ],\n"
        "]\n"
    )


def test_apply_builtin_formatter_wraps_module_subscript_union_element() -> None:
    """Test built-in formatter wraps union elements in module-level subscripts."""
    code = "Model.__annotations__['__pydantic_extra__'] = Dict[str, VeryLongName | OtherVeryLongName | None]\n"

    assert apply_builtin_formatter(code, line_length=40) == (
        "Model.__annotations__[\n"
        "    '__pydantic_extra__'\n"
        "] = Dict[\n"
        "    str,\n"
        "    VeryLongName\n"
        "    | OtherVeryLongName\n"
        "    | None,\n"
        "]\n"
    )


def test_apply_builtin_formatter_keeps_one_line_class_annotation_spacing() -> None:
    """Test post-class annotation spacing ignores one-line classes."""
    code = "class Model: pass\nModel.__annotations__['__pydantic_extra__'] = Dict[str, int]\n"

    assert apply_builtin_formatter(code) == code


def test_apply_builtin_formatter_normalizes_simple_string_quotes() -> None:
    """Test built-in formatter can match black string normalization for generated strings."""
    code = (
        "from typing import Literal\n"
        "from pydantic import BaseModel, Field\n"
        "\n"
        "\n"
        "class Model(BaseModel):\n"
        "    mode: Literal['MODE_2D'] = Field(..., alias='mapViewMode')\n"
    )

    assert apply_builtin_formatter(code, string_normalization=True) == (
        "from typing import Literal\n"
        "\n"
        "from pydantic import BaseModel, Field\n"
        "\n"
        "\n"
        "class Model(BaseModel):\n"
        '    mode: Literal["MODE_2D"] = Field(..., alias="mapViewMode")\n'
    )


def test_builtin_formatter_string_normalization_fixture() -> None:
    """Keep quote normalization and its no-op path byte-compatible across source shapes."""
    formatted_cases: list[str] = []
    corpus = BUILTIN_STRING_NORMALIZATION_INPUT_PATH.read_text(encoding="utf-8")
    for case in corpus.split("\n---CASE---\n"):
        name, source = case.split("\n", 1)
        formatted = apply_builtin_formatter(
            source,
            string_normalization=True,
        )
        formatted_cases.append(f"[{name}]\n{formatted}")

    assert_output("\n".join(formatted_cases))


def test_builtin_formatter_wrap_string_literal_fixture() -> None:
    r"""Wrap and split strings as black's preview string processing does, for each case at its own line length.

    The cases are strings after operators, with trailers, in joined groups black does not merge, in parentheses,
    dictionary entries and values, list, set and tuple elements, f-strings and ``\\N{...}`` escapes.
    """
    formatted_cases: list[str] = []
    corpus = BUILTIN_WRAP_STRING_LITERAL_INPUT_PATH.read_text(encoding="utf-8")
    for case in corpus.split("\n---CASE---\n"):
        header, source = case.split("\n", 1)
        name, line_length = header.split()
        formatted = apply_builtin_formatter(source, line_length=int(line_length), wrap_string_literal=True)
        formatted_cases.append(f"[{name}]\n{formatted}")

    assert_output("\n".join(formatted_cases))


def test_builtin_formatter_string_normalization_preserves_pep695_invalid_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep tokenizer errors from invalid PEP 695 fallback sources unchanged."""
    monkeypatch.setattr(builtin_formatter.sys, "version_info", (3, 11, 0, "final", 0))
    formatted_cases: list[str] = []
    corpus = BUILTIN_PEP695_INVALID_INPUT_PATH.read_text(encoding="utf-8")
    for case in corpus.split("\n---CASE---\n"):
        name, source = case.split("\n", 1)
        try:
            apply_builtin_formatter(
                source,
                python_version=PythonVersion.PY_312,
                string_normalization=True,
            )
        except tokenize.TokenError as error:
            formatted_cases.append(f"[{name}]\n{type(error).__name__}\n")
        else:
            formatted_cases.append(f"[{name}]\n{source}")

    assert_output(
        "\n".join(formatted_cases),
        BUILTIN_PEP695_INVALID_EXPECTED_PATH,
    )


def test_builtin_formatter_keeps_target_identifiers_and_joiners_alike_on_every_python() -> None:
    """Format code the running Python cannot parse alike on every Python.

    Letters the running Python lacks are lent away while formatting and given back, never as a letter the NFKC form
    of the code uses. Joiners, which Python 3.13 lets identifiers hold, are lent in names and stay as written when
    strings are rewrapped, as black writes them.
    """
    description = "joined\u200dword " * 12
    joiners = (
        "from pydantic import BaseModel, Field\n\n\nclass Model(BaseModel):\n"
        f"    text: str = Field(..., description='{description}')\n"
    )
    compatibility = (
        "# \u4e00\u4e01\u4e02\u4e03\u4e04\u4e05\u4e06\u4e07\u4e08\u4e09\u4e0a\u4e0b\u4e0c\n"
        "from pydantic import BaseModel\n\nfrom .m import Y\u0c5c, X\uf967\n\n\n"
        "class Model(BaseModel):\n    value: X\uf967 | Y\u0c5c | None = None\n"
    )
    joined_names = (
        "from pydantic import BaseModel\nfrom __future__ import annotations\n\nfrom .mo\u200ddels import Pet\n"
        "import json\nclass Mo\u200ddel(BaseModel):\n    pet: Pet\n"
    )
    assert_output(
        "\n".join((
            apply_builtin_formatter(joiners, wrap_string_literal=True, python_version=PythonVersion.PY_315),
            apply_builtin_formatter(compatibility, python_version=PythonVersion.PY_315),
            apply_builtin_formatter(joined_names, python_version=PythonVersion.PY_313),
        )),
        BUILTIN_FORMATTER_EXPECTED_PATH / "target_identifiers.txt",
    )


def test_builtin_formatter_groups_each_module_of_an_import_by_target() -> None:
    """Group each module one import statement names in its own section, as isort does, by the target's library.

    tomllib joins the standard library in Python 3.11.
    """
    code = "import tomllib, pydantic, os\nimport yaml\n\n\nclass Model(pydantic.BaseModel):\n    pass\n"
    assert_output(
        "\n".join(
            apply_builtin_formatter(code, python_version=version)
            for version in (PythonVersion.PY_310, PythonVersion.PY_311)
        ),
        BUILTIN_FORMATTER_EXPECTED_PATH / "import_modules_by_target.txt",
    )


def test_builtin_formatter_string_normalization_fallback_handles_private_inputs() -> None:
    """Keep the private normalization helper safe for non-exact strings and lexical whitespace."""
    assert (
        builtin_formatter._normalize_string_quotes_if_needed(
            'value = "ok"',
            source_is_valid=False,
        )
        == 'value = "ok"'
    )
    assert (
        builtin_formatter._normalize_string_quotes_if_needed(
            "value\t=1",
            source_is_valid=True,
        )
        == "value =1"
    )
    assert (
        builtin_formatter._normalize_string_quotes_if_needed(
            "value = 1\f+2",
            source_is_valid=True,
        )
        == "value = 1 +2"
    )

    class StringSubclass(str):  # noqa: FURB189, SLOT000 - intentionally exercises a hostile string subclass
        def __contains__(self, _item: object) -> bool:
            raise AssertionError

    assert (
        builtin_formatter._normalize_string_quotes_if_needed(
            StringSubclass('value = "ok"'),
            source_is_valid=True,
        )
        == 'value = "ok"'
    )


def test_apply_builtin_formatter_normalizes_blank_after_class_docstring() -> None:
    """Test built-in formatter keeps black-compatible class docstring spacing."""
    code = (
        "from enum import Enum\n"
        "\n"
        "\n"
        "class Shift(Enum):\n"
        '    """\n'
        "    Employee shift status\n"
        '    """\n'
        "    ON_SHIFT = 'ON_SHIFT'\n"
    )

    assert apply_builtin_formatter(code) == (
        "from enum import Enum\n"
        "\n"
        "\n"
        "class Shift(Enum):\n"
        '    """\n'
        "    Employee shift status\n"
        '    """\n'
        "\n"
        "    ON_SHIFT = 'ON_SHIFT'\n"
    )


def test_apply_builtin_formatter_wraps_type_alias_type_union() -> None:
    """Test built-in formatter wraps generated TypeAliasType unions like black."""
    code = (
        "from typing import TypeAliasType, Union\n"
        "\n"
        "\n"
        'Resource = TypeAliasType("Resource", Union[\n'
        "    'Car',\n"
        "    'Employee',\n"
        "])\n"
    )

    assert apply_builtin_formatter(code) == (
        "from typing import TypeAliasType, Union\n"
        "\n"
        "Resource = TypeAliasType(\n"
        '    "Resource",\n'
        "    Union[\n"
        "        'Car',\n"
        "        'Employee',\n"
        "    ],\n"
        ")\n"
    )


def test_apply_builtin_formatter_wraps_inline_type_alias_type_union() -> None:
    """Test built-in formatter matches black for inline Union TypeAliasType calls."""
    code = (
        "from typing import TypeAliasType, Union\n"
        "\n"
        "\n"
        'JsonType = TypeAliasType("JsonType", Union[ElementaryType, list["JsonType"], dict[str, "JsonType"]])\n'
    )

    assert apply_builtin_formatter(code) == (
        "from typing import TypeAliasType, Union\n"
        "\n"
        "JsonType = TypeAliasType(\n"
        '    "JsonType", Union[ElementaryType, list["JsonType"], dict[str, "JsonType"]]\n'
        ")\n"
    )


def test_apply_builtin_formatter_keeps_fitting_annotated_type_alias_type_argument() -> None:
    """Keep an Annotated argument inline when only the outer TypeAliasType call is long."""
    code = (
        'LocalTitleRefMapAdditionalProperty = TypeAliasType("LocalTitleRefMapAdditionalProperty", '
        "Annotated[int, Field(ge=1, le=9, title='LocalValue')])\n"
    )

    assert apply_builtin_formatter(code) == (
        "LocalTitleRefMapAdditionalProperty = TypeAliasType(\n"
        '    "LocalTitleRefMapAdditionalProperty",\n'
        "    Annotated[int, Field(ge=1, le=9, title='LocalValue')],\n"
        ")\n"
    )


def test_apply_builtin_formatter_wraps_long_annotated_type_alias_type_argument() -> None:
    """Wrap an Annotated argument when it cannot fit on its own continuation line."""
    code = (
        'Alias = TypeAliasType("Alias", '
        "Annotated[VeryLongGeneratedTypeNameThatExceedsTheDefaultLineLength, Field(description='x')])\n"
    )

    assert apply_builtin_formatter(code) == (
        "Alias = TypeAliasType(\n"
        '    "Alias",\n'
        "    Annotated[\n"
        "        VeryLongGeneratedTypeNameThatExceedsTheDefaultLineLength, Field(description='x')\n"
        "    ],\n"
        ")\n"
    )


def test_apply_builtin_formatter_wraps_annotated_type_alias_type_union_argument() -> None:
    """Keep an Annotated PEP 604 union expanded inside TypeAliasType."""
    code = (
        'Payment = TypeAliasType("Payment", '
        "Annotated[CardPayment | CashPayment | None, Field(None, discriminator='kind')])\n"
    )

    assert apply_builtin_formatter(code) == (
        "Payment = TypeAliasType(\n"
        '    "Payment",\n'
        "    Annotated[CardPayment | CashPayment | None, Field(None, discriminator='kind')],\n"
        ")\n"
    )


def test_apply_builtin_formatter_wraps_type_alias_union_assignment() -> None:
    """Test built-in formatter matches black for TypeAlias Union assignments."""
    code = (
        "from typing import TypeAlias, Union\n"
        "\n"
        "\n"
        "# safe\n"
        "# raise RuntimeError('executed')\n"
        "SearchResult: TypeAlias = Union[\n"
        "        'A',\n"
        "        'B',\n"
        "    ]\n"
    )

    assert apply_builtin_formatter(code) == (
        "from typing import TypeAlias, Union\n"
        "\n"
        "# safe\n"
        "# raise RuntimeError('executed')\n"
        "SearchResult: TypeAlias = Union[\n"
        "    'A',\n"
        "    'B',\n"
        "]\n"
    )


def test_apply_builtin_formatter_wraps_type_alias_annotated_assignment() -> None:
    """Wrap long Annotated TypeAlias assignments like Black."""
    code = "LocalTitleRefMapAdditionalProperty: TypeAlias = Annotated[int, Meta(ge=1, le=9, title='LocalValue')]\n"

    assert apply_builtin_formatter(code) == (
        "LocalTitleRefMapAdditionalProperty: TypeAlias = Annotated[\n    int, Meta(ge=1, le=9, title='LocalValue')\n]\n"
    )


def test_apply_builtin_formatter_keeps_inline_type_alias_union_assignment() -> None:
    """Test built-in formatter keeps short TypeAlias Union assignments inline."""
    code = "from typing import TypeAlias, Union\n\n\nSearchResult: TypeAlias = Union['A', 'B']\n"

    assert (
        apply_builtin_formatter(code)
        == "from typing import TypeAlias, Union\n\nSearchResult: TypeAlias = Union['A', 'B']\n"
    )


def test_apply_builtin_formatter_keeps_non_union_type_alias_assignment() -> None:
    """Test built-in formatter only rewrites TypeAlias Union assignments."""
    code = "from typing import TypeAlias\n\n\nSearchResult: TypeAlias = tuple[\n    'A',\n    'B',\n]\n"

    assert (
        apply_builtin_formatter(code)
        == "from typing import TypeAlias\n\nSearchResult: TypeAlias = tuple[\n    'A',\n    'B',\n]\n"
    )


def test_apply_builtin_formatter_wraps_long_tuple_nested_in_dict() -> None:
    """Wrap long tuple values nested in generated class metadata like Black."""
    code = (
        "class Payload:\n"
        "    __json_schema_pattern_properties__: ClassVar[tuple[Any, ...]] = (\n"
        "        {\n"
        "            'declared_properties': ('allOfValueMap', 'arrayIrrelevantConstraintMap', "
        "'booleanIrrelevantConstraintMap'),\n"
        "            'rejected_patterns': (),\n"
        "        },\n"
        "    )\n"
    )

    assert apply_builtin_formatter(code) == (
        "class Payload:\n"
        "    __json_schema_pattern_properties__: ClassVar[tuple[Any, ...]] = (\n"
        "        {\n"
        "            'declared_properties': (\n"
        "                'allOfValueMap',\n"
        "                'arrayIrrelevantConstraintMap',\n"
        "                'booleanIrrelevantConstraintMap',\n"
        "            ),\n"
        "            'rejected_patterns': (),\n"
        "        },\n"
        "    )\n"
    )


@pytest.mark.parametrize(
    "nested_value",
    ["(1, 2)", "{'key': 1}", repr("x" * 90)],
)
def test_apply_builtin_formatter_keeps_existing_tuple_element(nested_value: str) -> None:
    """Keep an existing tuple element intact while wrapping an overlong sibling."""
    code = (
        "class Payload:\n"
        "    value: ClassVar[tuple[Any, ...]] = (\n"
        f"        {nested_value},\n"
        "        {\n"
        "            'items': ('abcdefghijklmno', 'abcdefghijklmno', 'abcdefghijklmno', 'abcdefghijklmno'),\n"
        "        },\n"
        "    )\n"
    )

    assert apply_builtin_formatter(code) == (
        "class Payload:\n"
        "    value: ClassVar[tuple[Any, ...]] = (\n"
        f"        {nested_value},\n"
        "        {\n"
        "            'items': (\n"
        "                'abcdefghijklmno',\n"
        "                'abcdefghijklmno',\n"
        "                'abcdefghijklmno',\n"
        "                'abcdefghijklmno',\n"
        "            ),\n"
        "        },\n"
        "    )\n"
    )


def test_apply_builtin_formatter_wraps_empty_tuple_assignment_annotation() -> None:
    """Test built-in formatter matches black for a long empty-tuple assignment."""
    code = (
        "class Payload:\n"
        "    very_long_generated_registry_name_that_exceeds_the_builtin_formatter_line_length_limit: tuple = ()\n"
    )

    assert len(code.splitlines()[1]) > 88
    assert apply_builtin_formatter(code) == (
        "class Payload:\n"
        "    very_long_generated_registry_name_that_exceeds_the_builtin_formatter_line_length_limit: (\n"
        "        tuple\n"
        "    ) = ()\n"
    )


def test_apply_builtin_formatter_keeps_empty_nested_literals() -> None:
    """Keep empty nested literals intact while formatting a long tuple sibling."""
    code = (
        "class Payload:\n"
        "    value: ClassVar[tuple[Any, ...]] = (\n"
        "        {\n"
        "            'items': ('abcdefghijklmno', 'abcdefghijklmno', 'abcdefghijklmno', 'abcdefghijklmno'),\n"
        "            'empty_tuple_with_a_deliberately_long_generated_key_name_that_exceeds_line_length': (),\n"
        "            'empty_dict_with_a_deliberately_long_generated_key_name_that_exceeds_line_length': {},\n"
        "        },\n"
        "    )\n"
    )

    assert apply_builtin_formatter(code) == (
        "class Payload:\n"
        "    value: ClassVar[tuple[Any, ...]] = (\n"
        "        {\n"
        "            'items': (\n"
        "                'abcdefghijklmno',\n"
        "                'abcdefghijklmno',\n"
        "                'abcdefghijklmno',\n"
        "                'abcdefghijklmno',\n"
        "            ),\n"
        "            'empty_tuple_with_a_deliberately_long_generated_key_name_that_exceeds_line_length': (),\n"
        "            'empty_dict_with_a_deliberately_long_generated_key_name_that_exceeds_line_length': {},\n"
        "        },\n"
        "    )\n"
    )


def test_apply_builtin_formatter_preserves_single_entry_dict_trailing_comma() -> None:
    """Preserve magic trailing commas when rebuilding nested dictionaries."""
    code = (
        "class Payload:\n"
        "    value: ClassVar[tuple[Any, ...]] = (\n"
        "        {\n"
        "            'nested': {\n"
        "                'short': 1,\n"
        "            },\n"
        "        },\n"
        "        {\n"
        "            'items': ('abcdefghijklmno', 'abcdefghijklmno', 'abcdefghijklmno', 'abcdefghijklmno'),\n"
        "        },\n"
        "    )\n"
    )

    assert apply_builtin_formatter(code) == (
        "class Payload:\n"
        "    value: ClassVar[tuple[Any, ...]] = (\n"
        "        {\n"
        "            'nested': {\n"
        "                'short': 1,\n"
        "            },\n"
        "        },\n"
        "        {\n"
        "            'items': (\n"
        "                'abcdefghijklmno',\n"
        "                'abcdefghijklmno',\n"
        "                'abcdefghijklmno',\n"
        "                'abcdefghijklmno',\n"
        "            ),\n"
        "        },\n"
        "    )\n"
    )


def test_apply_builtin_formatter_preserves_long_annotated_tuple_assignment() -> None:
    """Prefer wrapping a long Annotated field over generic tuple-value formatting."""
    code = (
        "class Payload:\n"
        "    value: Annotated[VeryLongGeneratedTypeNameThatMakesTheAnnotationOverflow, "
        "Field(description='metadata')] = ('a', 'b')\n"
    )

    assert apply_builtin_formatter(code) == (
        "class Payload:\n"
        "    value: Annotated[\n"
        "        VeryLongGeneratedTypeNameThatMakesTheAnnotationOverflow,\n"
        "        Field(description='metadata'),\n"
        "    ] = ('a', 'b')\n"
    )


def test_apply_builtin_formatter_splits_multiline_annotated_tuple_assignment() -> None:
    """Test built-in formatter matches black for a multiline Annotated tuple assignment."""
    code = (
        "class Payload:\n"
        "    value: Annotated[VeryLongGeneratedTypeNameThatMakesTheAnnotationOverflow, "
        "Field(description='metadata')] = (\n"
        "        {\n"
        "            'items': ('abcdefghijklmno', 'abcdefghijklmno', 'abcdefghijklmno', 'abcdefghijklmno'),\n"
        "        },\n"
        "    )\n"
    )

    assert apply_builtin_formatter(code) == (
        "class Payload:\n"
        "    value: Annotated[\n"
        "        VeryLongGeneratedTypeNameThatMakesTheAnnotationOverflow,\n"
        "        Field(description='metadata'),\n"
        "    ] = (\n"
        "        {\n"
        "            'items': (\n"
        "                'abcdefghijklmno',\n"
        "                'abcdefghijklmno',\n"
        "                'abcdefghijklmno',\n"
        "                'abcdefghijklmno',\n"
        "            ),\n"
        "        },\n"
        "    )\n"
    )


def test_apply_builtin_formatter_splits_overlong_classvar_tuple_header() -> None:
    """Test built-in formatter matches black for a multiline ClassVar assignment with an overlong header."""
    code = (
        "class Payload:\n"
        "    value: ClassVar[tuple[VeryLongGeneratedTypeNameThatMakesTheAnnotationOverflow, ...]] = (\n"
        "        {\n"
        "            'items': ('abcdefghijklmno', 'abcdefghijklmno', 'abcdefghijklmno', 'abcdefghijklmno'),\n"
        "        },\n"
        "    )\n"
    )

    assert len(code.splitlines()[1]) > 88
    assert apply_builtin_formatter(code) == (
        "class Payload:\n"
        "    value: ClassVar[\n"
        "        tuple[VeryLongGeneratedTypeNameThatMakesTheAnnotationOverflow, ...]\n"
        "    ] = (\n"
        "        {\n"
        "            'items': (\n"
        "                'abcdefghijklmno',\n"
        "                'abcdefghijklmno',\n"
        "                'abcdefghijklmno',\n"
        "                'abcdefghijklmno',\n"
        "            ),\n"
        "        },\n"
        "    )\n"
    )


def test_apply_builtin_formatter_preserves_comment_in_multiline_tuple() -> None:
    """Do not reconstruct multiline tuple metadata that contains a comment."""
    code = (
        "class Payload:\n"
        "    value: ClassVar[tuple[Any, ...]] = (\n"
        "        {\n"
        "            # preserve this metadata explanation\n"
        f"            'items': {tuple('abcdefghijklmno' for _ in range(6))!r},\n"
        "        },\n"
        "    )\n"
    )

    assert apply_builtin_formatter(code) == code


def test_apply_builtin_formatter_splits_non_name_tuple_assignment_target() -> None:
    """Test built-in formatter matches black for a non-name annotated assignment target."""
    code = (
        "class Payload:\n"
        "    registry['key']: ClassVar[tuple[Any, ...]] = (\n"
        "        {\n"
        "            'items': ('abcdefghijklmno', 'abcdefghijklmno', 'abcdefghijklmno', 'abcdefghijklmno'),\n"
        "        },\n"
        "    )\n"
    )

    assert apply_builtin_formatter(code) == (
        "class Payload:\n"
        "    registry['key']: ClassVar[tuple[Any, ...]] = (\n"
        "        {\n"
        "            'items': (\n"
        "                'abcdefghijklmno',\n"
        "                'abcdefghijklmno',\n"
        "                'abcdefghijklmno',\n"
        "                'abcdefghijklmno',\n"
        "            ),\n"
        "        },\n"
        "    )\n"
    )


def test_apply_builtin_formatter_preserves_comment_in_annotated_alias() -> None:
    """Do not reconstruct a multiline Annotated alias that contains a comment."""
    code = "Alias: TypeAlias = Annotated[\n    int,\n    # preserve constraint rationale\n    Field(ge=1),\n]\n"

    assert apply_builtin_formatter(code) == code


def test_apply_builtin_formatter_normalizes_blank_lines_without_imports() -> None:
    """Test built-in formatter normalizes top-level blanks when no imports exist."""
    code = "Alias = str\n\n\n\nOtherAlias = Alias\n"

    assert apply_builtin_formatter(code) == "Alias = str\n\n\nOtherAlias = Alias\n"


def test_builtin_formatter_comment_token_guard_skips_lines_without_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test comment-token detection avoids tokenization when a line cannot contain comments."""

    def fail_generate_tokens(*_args: object, **_kwargs: object) -> None:
        pytest.fail("comment-token guard should skip tokenization")  # pragma: no cover

    monkeypatch.setattr(
        builtin_formatter,
        "tokenize",
        mock.Mock(wraps=builtin_formatter.tokenize, generate_tokens=fail_generate_tokens),
    )

    assert not builtin_formatter._has_comment_token("value = 1")


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("value = '# not a comment'", False),
        ("value = 1  # comment", True),
    ],
)
def test_builtin_formatter_comment_token_guard_tokenizes_hash_lines(line: str, expected: bool) -> None:
    """Test comment-token detection still tokenizes lines that contain a hash."""
    assert builtin_formatter._has_comment_token(line) is expected


def test_builtin_formatter_blank_line_guard_skips_tokenize_without_multiline_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test blank-line normalization avoids tokenization when multi-line strings are impossible."""

    def fail_generate_tokens(*_args: object, **_kwargs: object) -> None:
        pytest.fail("blank-line guard should skip tokenization")  # pragma: no cover

    monkeypatch.setattr(
        builtin_formatter,
        "tokenize",
        mock.Mock(wraps=builtin_formatter.tokenize, generate_tokens=fail_generate_tokens),
    )

    code = "class Model:\n    pass\n\n\n\nclass OtherModel:\n    pass"

    assert_output(
        f"{builtin_formatter._normalize_top_level_blank_lines(code)}\n",
        BUILTIN_FORMATTER_EXPECTED_PATH / "blank_line_normalization.txt",
    )


def test_builtin_formatter_blank_line_guard_keeps_docstring_body_lines() -> None:
    """Test blank-line normalization tokenizes code that may contain multi-line strings."""
    code = '"""doc\nclass NotCode:\n    pass\n"""\n\n\n\nclass Model:\n    pass'

    assert builtin_formatter._normalize_top_level_blank_lines(code) == (
        '"""doc\nclass NotCode:\n    pass\n"""\n\n\nclass Model:\n    pass'
    )


@pytest.mark.parametrize("line_separator", ["\n", "\r\n"])
def test_builtin_formatter_blank_line_guard_keeps_backslash_continuation_body_lines(line_separator: str) -> None:
    """Test backslash-continued string lines are not treated as top-level code."""
    code = line_separator.join([
        "value = 'first\\",
        "class NotCode:'",
        "",
        "",
        "",
        "class Model:",
        "    pass",
    ])
    expected_lines = [
        "value = 'first\\",
        "class NotCode:'",
        "",
        "",
        "class Model:",
        "    pass",
    ]

    assert builtin_formatter._normalize_top_level_blank_lines(code) == "\n".join(expected_lines)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="type statements require Python 3.12")
def test_apply_builtin_formatter_normalizes_type_alias_blank_lines_without_imports() -> None:
    """Test built-in formatter normalizes top-level blanks between type statements."""
    code = "type Foo = str\n\n\n\ntype Bar = Foo\n"

    assert apply_builtin_formatter(code) == "type Foo = str\n\n\ntype Bar = Foo\n"


def test_builtin_formatter_respects_target_python_version_for_ast_parse() -> None:
    """Test built-in formatter parses code using the configured target Python version."""
    code = "type Foo = str\n\n\n\ntype Bar = Foo\n"

    py310_formatter = CodeFormatter(PythonVersion.PY_310, formatters=[Formatter.BUILTIN])
    py312_formatter = CodeFormatter(PythonVersion.PY_312, formatters=[Formatter.BUILTIN])

    assert py310_formatter.format_code(code) == code
    assert py312_formatter.format_code(code) == "type Foo = str\n\n\ntype Bar = Foo\n"


def test_builtin_formatter_formats_type_alias_modules_on_older_runtimes() -> None:
    """Test built-in formatter handles target Python 3.12 type aliases on older runtimes."""
    code = (
        "from typing import Annotated\n"
        "from pydantic import Field\n"
        "\n"
        "\n"
        "type Alias = Annotated[str | bool, Field(..., "
        "description='An annotated union type', title='MyAnnotatedType')]\n"
        "\n"
        "\n"
        "\n"
        "class Model:\n"
        "    x_value: Annotated[Alias | None, Field(validate_default=True)] = {'type': 'b', 'value': 1}\n"
    )

    assert apply_builtin_formatter(code, line_length=88, python_version=PythonVersion.PY_312) == (
        "from typing import Annotated\n"
        "\n"
        "from pydantic import Field\n"
        "\n"
        "type Alias = Annotated[\n"
        "    str | bool,\n"
        "    Field(..., description='An annotated union type', title='MyAnnotatedType'),\n"
        "]\n"
        "\n"
        "\n"
        "class Model:\n"
        "    x_value: Annotated[Alias | None, Field(validate_default=True)] = {\n"
        "        'type': 'b',\n"
        "        'value': 1,\n"
        "    }\n"
    )


def test_builtin_formatter_pep695_placeholder_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test helper branches for parsing Python 3.12 type aliases on older runtimes."""
    monkeypatch.setattr(builtin_formatter.sys, "version_info", (3, 11, 0, "final", 0))

    assert builtin_formatter._needs_pep695_type_alias_placeholders(PythonVersion.PY_312)
    assert not builtin_formatter._needs_pep695_type_alias_placeholders(PythonVersion.PY_311)
    assert not builtin_formatter._needs_pep695_type_alias_placeholders(None)

    alias_lines = [
        "x = 1",
        "type Alias = Annotated[",
        "    str | bool,",
        "    Field(...),",
        "]",
        "y = 2",
    ]
    assert builtin_formatter._pep695_type_alias_statement_end(alias_lines, 1) == 4
    assert builtin_formatter._pep695_type_alias_statement_end([], 0) == 0
    assert builtin_formatter._pep695_type_alias_statement_end(["type Alias = Annotated["], 0) == 0
    assert builtin_formatter._replace_pep695_type_aliases_with_placeholders("\n".join(alias_lines)) == (
        f"x = 1\n{builtin_formatter.PEP695_TYPE_ALIAS_PLACEHOLDER} = None\n\n\n\ny = 2"
    )

    parse = builtin_formatter._parse_builtin_code_with_source_validity
    assert parse("type Alias =\n", PythonVersion.PY_312)[0] is not None
    assert parse("if", PythonVersion.PY_312) == (None, False)
    assert parse("    type Alias =\n", PythonVersion.PY_312) == (None, False)
    assert builtin_formatter._has_comment_token("value = 'unterminated #")
    assert not builtin_formatter._has_comment_token("value = 'https://example.com/#fragment'")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("", []),
        ("first\nsecond\n", ["first", "second"]),
        ("first\r\nsecond\r\n", ["first", "second"]),
        ("first\rsecond", ["first", "second"]),
        ("next\u0085line\u2028paragraph\u2029end", ["next\u0085line\u2028paragraph\u2029end"]),
    ],
)
def test_builtin_formatter_splits_only_physical_python_lines(source: str, expected: list[str]) -> None:
    """Unicode separators remain source content while physical newlines are normalized."""
    assert builtin_formatter._split_python_lines(source) == expected


def test_apply_builtin_formatter_parenthesizes_short_annotated_default() -> None:
    """Test built-in formatter matches black for short overflowing Annotated defaults."""
    code = (
        "from typing import Annotated, Literal\n"
        "from pydantic import Field\n"
        "\n"
        "\n"
        "class Model:\n"
        "    typename__: Annotated[Literal['Notification'] | None, Field(alias='__typename')] = 'Notification'\n"
    )

    assert apply_builtin_formatter(code) == (
        "from typing import Annotated, Literal\n"
        "\n"
        "from pydantic import Field\n"
        "\n"
        "\n"
        "class Model:\n"
        "    typename__: Annotated[Literal['Notification'] | None, Field(alias='__typename')] = (\n"
        "        'Notification'\n"
        "    )\n"
    )


def test_apply_builtin_formatter_parenthesizes_long_union_annotation() -> None:
    """Test built-in formatter matches black for long generated union annotations."""
    code = (
        "class Model:\n"
        "    optional_oneof_with_null_and_constraint: OptionalOneofWithNullAndConstraint | None | UnsetType = UNSET\n"
        "    optional_nullable_with_constraint: Annotated[str, Meta(max_length=50)] | UnsetType = UNSET\n"
    )

    assert apply_builtin_formatter(code) == (
        "class Model:\n"
        "    optional_oneof_with_null_and_constraint: (\n"
        "        OptionalOneofWithNullAndConstraint | None | UnsetType\n"
        "    ) = UNSET\n"
        "    optional_nullable_with_constraint: (\n"
        "        Annotated[str, Meta(max_length=50)] | UnsetType\n"
        "    ) = UNSET\n"
    )


def test_apply_builtin_formatter_parenthesizes_union_annotation_with_long_default() -> None:
    """Test built-in formatter matches black for long union annotations with long defaults."""
    code = (
        "class Model:\n"
        "    qualified_state: example_spec_proto3_alias_state.ExampleSpecProto3AliasState | None = "
        "example_spec_proto3_alias_state.ExampleSpecProto3AliasState.ALIAS_STATE_UNSPECIFIED\n"
    )

    assert apply_builtin_formatter(code, line_length=88) == (
        "class Model:\n"
        "    qualified_state: (\n"
        "        example_spec_proto3_alias_state.ExampleSpecProto3AliasState | None\n"
        "    ) = (\n"
        "        example_spec_proto3_alias_state.ExampleSpecProto3AliasState.ALIAS_STATE_UNSPECIFIED\n"
        "    )\n"
    )


def test_apply_builtin_formatter_parenthesizes_constrained_call_union_annotation_with_default() -> None:
    """Test built-in formatter matches black for constrained call union annotations."""
    code = (
        "class Model:\n"
        "    price: condecimal(ge=Decimal('0'), le=Decimal('99999.99'), multiple_of=Decimal('0.01')) | None = None\n"
    )

    assert apply_builtin_formatter(code, line_length=88) == (
        "class Model:\n"
        "    price: (\n"
        "        condecimal(ge=Decimal('0'), le=Decimal('99999.99'), multiple_of=Decimal('0.01'))\n"
        "        | None\n"
        "    ) = None\n"
    )


def test_apply_builtin_formatter_parenthesizes_union_annotation_with_string_default() -> None:
    """Test built-in formatter matches black for long union annotations with string defaults."""
    code = "class Model:\n    typename__: Literal['Notification'] | None = 'Notification'\n"

    assert apply_builtin_formatter(code, line_length=40) == (
        "class Model:\n    typename__: (\n        Literal['Notification'] | None\n    ) = 'Notification'\n"
    )


def test_apply_builtin_formatter_wraps_string_default_with_single_quote() -> None:
    """Test wrapped generated string defaults keep values with single quotes intact."""
    code = (
        "class Model:\n"
        '    message: str = Field(..., description="it\'s a generated description with enough words to wrap")\n'
        '    note: str = Field(description="it\'s a generated note without a default value")\n'
    )

    assert apply_builtin_formatter(code, line_length=48, wrap_string_literal=True) == (
        "class Model:\n"
        "    message: str = Field(\n"
        "        ...,\n"
        "        description=(\n"
        '            "it\'s a generated description with"\n'
        '            " enough words to wrap"\n'
        "        ),\n"
        "    )\n"
        "    note: str = Field(\n"
        "        description=(\n"
        '            "it\'s a generated note without a"\n'
        '            " default value"\n'
        "        )\n"
        "    )\n"
    )


def test_apply_builtin_formatter_wrap_string_literal_joins_implicitly_concatenated_strings() -> None:
    """Wrap implicitly joined strings as one, as black's string processing does, and leave a parameter's default whole.

    The first string of a joined value was wrapped alone, which left the next string after the parentheses.
    """
    code = (
        "class Model(Base, note='a generated class keyword with words' ' Done.'):\n"
        "    x: str = Field(None, description='a generated description text' f' {done}')\n"
        "\n"
        "    def f(self, value='a generated parameter default with words'):\n"
        "        pass\n"
    )

    assert apply_builtin_formatter(code, line_length=40, wrap_string_literal=True) == (
        "class Model(\n"
        "    Base,\n"
        "    note=(\n"
        "        'a generated class keyword with'\n"
        "        ' words Done.'\n"
        "    ),\n"
        "):\n"
        "    x: str = Field(\n"
        "        None,\n"
        "        description=(\n"
        "            'a generated description'\n"
        "            f' text {done}'\n"
        "        ),\n"
        "    )\n"
        "\n"
        "    def f(\n"
        "        self,\n"
        "        value='a generated parameter default with words',\n"
        "    ):\n"
        "        pass\n"
    )


def test_apply_builtin_formatter_wrap_string_literal_splits_bytes_and_f_strings_like_black() -> None:
    """Split a long bytes literal or f-string argument as black's string processing does, keeping each value.

    Wrapping used to read a bytes literal back as text, which changed its value, and raised ValueError on an f-string.
    """
    code = (
        "class Model:\n"
        "    x: str = Field(None, description=f'{prefix} a long description text that goes on and on')\n"
        "    y: bytes = Field(None, default=b'a long bytes literal text that goes on and on and on')\n"
    )

    assert apply_builtin_formatter(code, line_length=40, wrap_string_literal=True) == (
        "class Model:\n"
        "    x: str = Field(\n"
        "        None,\n"
        "        description=(\n"
        "            f'{prefix} a long'\n"
        "            ' description text that'\n"
        "            ' goes on and on'\n"
        "        ),\n"
        "    )\n"
        "    y: bytes = Field(\n"
        "        None,\n"
        "        default=(\n"
        "            b'a long bytes literal text'\n"
        "            b' that goes on and on'\n"
        "            b' and on'\n"
        "        ),\n"
        "    )\n"
    )


def test_apply_builtin_formatter_formats_hash_inside_field_string() -> None:
    """Test URL fragments inside generated strings are not treated as comments."""
    code = (
        "class ErrorResponse:\n"
        "    type: AnyUrl | None = Field('about:blank', description='An absolute URI that identifies the problem "
        "type.  When dereferenced,\\nit SHOULD provide human-readable documentation for the problem type\\n(e.g., "
        "using HTML).\\n', examples=['https://tools.ietf.org/html/rfc7231#section-6.6.4'])\n"
    )

    assert apply_builtin_formatter(code, line_length=88) == (
        "class ErrorResponse:\n"
        "    type: AnyUrl | None = Field(\n"
        "        'about:blank',\n"
        "        description='An absolute URI that identifies the problem type.  When dereferenced,\\nit SHOULD "
        "provide human-readable documentation for the problem type\\n(e.g., using HTML).\\n',\n"
        "        examples=['https://tools.ietf.org/html/rfc7231#section-6.6.4'],\n"
        "    )\n"
    )


def test_apply_builtin_formatter_wraps_union_subscript_annotation() -> None:
    """Test built-in formatter matches black for generated Union annotations."""
    code = (
        "class Api(Struct):\n"
        "    apiKey: Union[Annotated[str, Meta(description='To be used as a dataset parameter value')], "
        "UnsetType] = UNSET\n"
        "    optional_nullable_with_constraint: Union[Annotated[str, Meta(max_length=50)], UnsetType] = UNSET\n"
    )

    assert apply_builtin_formatter(code) == (
        "class Api(Struct):\n"
        "    apiKey: Union[\n"
        "        Annotated[str, Meta(description='To be used as a dataset parameter value')],\n"
        "        UnsetType,\n"
        "    ] = UNSET\n"
        "    optional_nullable_with_constraint: Union[\n"
        "        Annotated[str, Meta(max_length=50)], UnsetType\n"
        "    ] = UNSET\n"
    )


def test_format_code_builtin_formatter_wraps_generated_model_statements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test built-in formatter wraps generated model statements that Black would split."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.BUILTIN],
    )

    formatted_code = formatter.format_code(
        "from typing import Annotated\n"
        "from pydantic import BaseModel, ConfigDict, Field\n"
        "\n"
        "class Model(BaseModel):\n"
        "    model_config = ConfigDict(extra='forbid', populate_by_name=True, json_schema_extra={'x-one': 'two'})\n"
        "    name: str = Field(None, examples=['dog', 'cat'], description='description', "
        "title='Long Title That makes it exceed line length maybe')\n"
        "    pet: Annotated[VeryLongGeneratedTypeNameThatExceedsTheDefaultLineLength, "
        "Field(description='x', title='y')]\n"
    )

    assert (
        formatted_code == "from typing import Annotated\n"
        "\n"
        "from pydantic import BaseModel, ConfigDict, Field\n"
        "\n"
        "\n"
        "class Model(BaseModel):\n"
        "    model_config = ConfigDict(\n"
        "        extra='forbid', populate_by_name=True, json_schema_extra={'x-one': 'two'}\n"
        "    )\n"
        "    name: str = Field(\n"
        "        None,\n"
        "        examples=['dog', 'cat'],\n"
        "        description='description',\n"
        "        title='Long Title That makes it exceed line length maybe',\n"
        "    )\n"
        "    pet: Annotated[\n"
        "        VeryLongGeneratedTypeNameThatExceedsTheDefaultLineLength,\n"
        "        Field(description='x', title='y'),\n"
        "    ]\n"
    )


def test_format_code_builtin_formatter_handles_additional_generated_model_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test less common generated model formatting branches."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.BUILTIN],
        builtin_format_line_length=72,
    )
    long_pattern = "a" * 80

    formatted_code = formatter.format_code(
        "import sys  # noqa: F401\n"
        "from typing import TYPE_CHECKING\n"
        "\n"
        "if TYPE_CHECKING:\n"
        "    VALUE = 1\n"
        "\n"
        "class Model:\n"
        "    model_config_with_an_extremely_long_name_to_force_formatting = ConfigDict()\n"
        "    model_config = ConfigDict(**CONFIG_WITH_A_LONG_NAME_TO_FORCE_FORMATTING)\n"
        "    alias: str = pydantic.Field(None, description='uses attribute field call')\n"
        "    plain_value = 'this line is long enough to be inspected but is not a generated formatter target'\n"
        "    existing: str = Field(\n"
        "        None,\n"
        "        description='already wrapped',\n"
        "    )\n"
        "    metadata: Annotated[VeryLongGeneratedTypeNameThatExceedsTheConfiguredLineLength]\n"
        "    defaulted_metadata: Annotated[VeryLongGeneratedTypeNameThatExceedsTheConfiguredLineLength, "
        "Field(description='kept as-is because it already has a default')] = None\n"
        "    typename__: Annotated[Literal['Notification'] | None, Field(alias='__typename')] = 'Notification'\n"
        f"    hostName: constr(pattern=r'{long_pattern}', strict=True) | None = None\n"
        "    optional_nested_value: list[list[VeryLongGeneratedTypeNameThatExceedsTheConfiguredLineLength | None] "
        "| None] "
        "| None = None\n"
        "    nested: Annotated[VeryLongGeneratedTypeNameThatExceedsTheConfiguredLineLength, "
        "Field(description='This nested field is long enough to wrap inside Annotated')]\n"
    )

    assert formatted_code == (
        "import sys  # noqa: F401\n"
        "from typing import TYPE_CHECKING\n"
        "\n"
        "if TYPE_CHECKING:\n"
        "    VALUE = 1\n"
        "\n"
        "\n"
        "class Model:\n"
        "    model_config_with_an_extremely_long_name_to_force_formatting = (\n"
        "        ConfigDict()\n"
        "    )\n"
        "    model_config = ConfigDict(\n"
        "        **CONFIG_WITH_A_LONG_NAME_TO_FORCE_FORMATTING\n"
        "    )\n"
        "    alias: str = pydantic.Field(\n"
        "        None, description='uses attribute field call'\n"
        "    )\n"
        "    plain_value = 'this line is long enough to be inspected but is not a generated formatter target'\n"
        "    existing: str = Field(\n"
        "        None,\n"
        "        description='already wrapped',\n"
        "    )\n"
        "    metadata: Annotated[\n"
        "        VeryLongGeneratedTypeNameThatExceedsTheConfiguredLineLength\n"
        "    ]\n"
        "    defaulted_metadata: Annotated[\n"
        "        VeryLongGeneratedTypeNameThatExceedsTheConfiguredLineLength,\n"
        "        Field(\n"
        "            description='kept as-is because it already has a default'\n"
        "        ),\n"
        "    ] = None\n"
        "    typename__: Annotated[\n"
        "        Literal['Notification'] | None, Field(alias='__typename')\n"
        "    ] = 'Notification'\n"
        "    hostName: (\n"
        "        constr(\n"
        "            pattern=r'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',\n"
        "            strict=True,\n"
        "        )\n"
        "        | None\n"
        "    ) = None\n"
        "    optional_nested_value: (\n"
        "        list[\n"
        "            list[\n"
        "                VeryLongGeneratedTypeNameThatExceedsTheConfiguredLineLength\n"
        "                | None\n"
        "            ]\n"
        "            | None\n"
        "        ]\n"
        "        | None\n"
        "    ) = None\n"
        "    nested: Annotated[\n"
        "        VeryLongGeneratedTypeNameThatExceedsTheConfiguredLineLength,\n"
        "        Field(\n"
        "            description='This nested field is long enough to wrap inside Annotated'\n"
        "        ),\n"
        "    ]\n"
    )


def test_apply_builtin_formatter_wraps_msgspec_field_default_factory() -> None:
    """Test built-in formatter matches black for generated msgspec field defaults."""
    code = (
        "class Bar(Struct):\n"
        "    original_foo: Foo_1 | UnsetType = field(default_factory=lambda: "
        "convert({'text': 'abc', 'number': 123}, type=Foo_1))\n"
        "    baz: list[Foo_1] | UnsetType = field(default_factory=lambda: "
        "convert([{'text': 'abc', 'number': 123}, {'text': 'efg', 'number': 456}], type=list[Foo_1]))\n"
    )

    assert apply_builtin_formatter(code) == (
        "class Bar(Struct):\n"
        "    original_foo: Foo_1 | UnsetType = field(\n"
        "        default_factory=lambda: convert({'text': 'abc', 'number': 123}, type=Foo_1)\n"
        "    )\n"
        "    baz: list[Foo_1] | UnsetType = field(\n"
        "        default_factory=lambda: convert(\n"
        "            [{'text': 'abc', 'number': 123}, {'text': 'efg', 'number': 456}],\n"
        "            type=list[Foo_1],\n"
        "        )\n"
        "    )\n"
    )


def test_apply_builtin_formatter_handles_remaining_generated_edges() -> None:
    """Test generated formatting branches that are less common but supported."""
    code = (
        "class Model:\n"
        "    very_long_attribute_name: VeryLongPlainAnnotation = DEFAULT_VALUE\n"
        "    field_with_long_value: VeryLongPlainAnnotation = "
        "Field(..., description='long generated field', title='Long Field')\n"
        "    empty_value_with_long_name: VeryLongPlainAnnotation\n"
        "\n"
        "    data: dict[str, type] = {**BASE_FIELDS, 'id': str, 'name': str}\n"
        "\n"
        "class LeftRoot(RootModel[constr(pattern='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa') | None]):\n"
        "    pass\n"
        "\n"
        "class RightRoot(RootModel[None | constr(pattern='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')]):\n"
        "    pass\n"
        "\n"
        "class UnionModel:\n"
        "    right_annotated: VeryLongPlainTypeName | "
        "Annotated[VeryLongAnnotatedTypeName, Field(description='x')] = DEFAULT\n"
        "    simple_union: VeryLongPlainTypeName | OtherVeryLongPlainTypeName | None = DEFAULT\n"
        "\n"
        "Alias = TypeAliasType('Alias', Union[str, int], **OPTIONS)\n"
    )

    assert apply_builtin_formatter(code, line_length=40, string_normalization=True) == (
        "class Model:\n"
        "    very_long_attribute_name: (\n"
        "        VeryLongPlainAnnotation\n"
        "    ) = DEFAULT_VALUE\n"
        "    field_with_long_value: (\n"
        "        VeryLongPlainAnnotation\n"
        "    ) = Field(\n"
        "        ...,\n"
        '        description="long generated field",\n'
        '        title="Long Field",\n'
        "    )\n"
        "    empty_value_with_long_name: (\n"
        "        VeryLongPlainAnnotation\n"
        "    )\n"
        "\n"
        "    data: dict[str, type] = {\n"
        "        **BASE_FIELDS,\n"
        '        "id": str,\n'
        '        "name": str,\n'
        "    }\n"
        "\n"
        "\n"
        "class LeftRoot(\n"
        "    RootModel[\n"
        "        constr(\n"
        '            pattern="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n'
        "        )\n"
        "        | None\n"
        "    ]\n"
        "):\n"
        "    pass\n"
        "\n"
        "\n"
        "class RightRoot(\n"
        "    RootModel[\n"
        "        None\n"
        "        | constr(\n"
        '            pattern="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n'
        "        )\n"
        "    ]\n"
        "):\n"
        "    pass\n"
        "\n"
        "\n"
        "class UnionModel:\n"
        "    right_annotated: (\n"
        "        VeryLongPlainTypeName\n"
        "        | Annotated[\n"
        "            VeryLongAnnotatedTypeName,\n"
        '            Field(description="x"),\n'
        "        ]\n"
        "    ) = DEFAULT\n"
        "    simple_union: (\n"
        "        VeryLongPlainTypeName\n"
        "        | OtherVeryLongPlainTypeName\n"
        "        | None\n"
        "    ) = DEFAULT\n"
        "\n"
        "\n"
        "Alias = TypeAliasType(\n"
        '    "Alias", Union[str, int], **OPTIONS\n'
        ")\n"
    )


def test_apply_builtin_formatter_handles_no_import_string_normalization_and_spacing() -> None:
    """Test quote normalization and blank-line normalization without imports."""
    code = (
        "class Model:\n"
        '    """Doc."""\n'
        "\n"
        "\n"
        "\n"
        "    value: str\n"
        "\n"
        "    def very_long_generated_method_name_without_arguments():\n"
        "        pass\n"
        "\n"
        "    def very_long_generated_method_name_with_arguments(value: str):\n"
        "        pass\n"
        "\n"
        "def very_long_generated_function_name_without_arguments():\n"
        "    pass\n"
        "\n"
        "def very_long_generated_function_name_with_arguments(value: str):\n"
        "    pass\n"
        "\n"
        "class VeryLongGeneratedClassNameWithoutAnyBaseClass:\n"
        "    value = 'x'\n"
        "\n"
        "class VeryLongGeneratedClassNameWithPlainBase(VeryLongPlainBase):\n"
        '    value = "x"\n'
    )

    assert apply_builtin_formatter(code, line_length=40, string_normalization=True) == (
        "class Model:\n"
        '    """Doc."""\n'
        "\n"
        "    value: str\n"
        "\n"
        "    def very_long_generated_method_name_without_arguments():\n"
        "        pass\n"
        "\n"
        "    def very_long_generated_method_name_with_arguments(\n"
        "        value: str,\n"
        "    ):\n"
        "        pass\n"
        "\n"
        "\n"
        "def very_long_generated_function_name_without_arguments():\n"
        "    pass\n"
        "\n"
        "\n"
        "def very_long_generated_function_name_with_arguments(\n"
        "    value: str,\n"
        "):\n"
        "    pass\n"
        "\n"
        "\n"
        "class VeryLongGeneratedClassNameWithoutAnyBaseClass:\n"
        '    value = "x"\n'
        "\n"
        "\n"
        "class VeryLongGeneratedClassNameWithPlainBase(\n"
        "    VeryLongPlainBase\n"
        "):\n"
        '    value = "x"\n'
    )


def test_builtin_formatter_private_edge_helpers(tmp_path: Path) -> None:
    """Test small helper branches used by generated-code formatting."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.isort]\nknown_first_party = "not-a-list"\n',
        encoding="utf-8",
    )
    assert _get_builtin_known_first_party(tmp_path) == DEFAULT_KNOWN_FIRST_PARTY
    assert _normalize_string_quotes("a = \"ok\"\nb = 'has \" quote'\nc = 'abc\\\\'\nd = 'ok'\n") == (
        'a = "ok"\nb = \'has " quote\'\nc = "abc\\\\"\nd = "ok"\n'
    )


@pytest.mark.parametrize(
    ("code", "expected_code"),
    [
        ("", ""),
        ("class Model:\n    pass", "class Model:\n    pass\n"),
        ("if", "if\n"),
        ("import typing as t", "import typing as t\n"),
    ],
)
def test_apply_builtin_formatter_handles_simple_edge_cases(code: str, expected_code: str) -> None:
    """Test built-in formatter behavior when no import block can be rewritten."""
    assert apply_builtin_formatter(code) == expected_code


@pytest.mark.parametrize("bucket", range(PARITY_BUCKETS))
def test_apply_builtin_formatter_matches_black_isort_for_normalized_expected_files(bucket: int, tmp_path: Path) -> None:
    """Keep built-in formatting aligned with black + isort for generated model outputs.

    Each bucket checks every PARITY_BUCKETS-th expected file, so the buckets together check every file once. Files
    black cannot parse are skipped; black 24.1 reports a name holding a letter its own Python lacks as a KeyError.
    """
    expected_path = Path(__file__).parent / "data" / "expected"
    isort_config = isort.Config(settings_path=str(tmp_path))
    black_mode = black.FileMode(line_length=88, string_normalization=False)
    checked_files = 0
    mismatches: list[str] = []

    for path in sorted(expected_path.rglob("*.py"))[bucket::PARITY_BUCKETS]:
        relative_path = path.relative_to(expected_path).as_posix()
        if relative_path in BLACK_VERSION_DEPENDENT_NORMALIZED_EXPECTED_FILES:
            continue

        code = path.read_text(encoding="utf-8")
        try:
            black_isort_code = black.format_str(
                isort.code(code, config=isort_config),
                mode=black_mode,
            )
        except (black.InvalidInput, KeyError):
            continue

        if code != black_isort_code:
            continue

        checked_files += 1
        if apply_builtin_formatter(code) != black_isort_code:
            mismatches.append(relative_path)  # pragma: no cover

    assert checked_files > 1000 // PARITY_BUCKETS
    assert not mismatches


def test_format_code_un_exist_custom_formatter() -> None:
    """Test error when custom formatter module doesn't exist."""
    with pytest.raises(datamodel_code_generator.Error, match="Unable to import custom formatter"):
        _ = CodeFormatter(
            PythonVersionMin,
            custom_formatters=[UN_EXIST_FORMATTER],
            formatters=[Formatter.BLACK, Formatter.ISORT],
        )


def test_format_code_invalid_formatter_name() -> None:
    """Test error when custom formatter has no CodeFormatter class."""
    with pytest.raises(NameError):
        _ = CodeFormatter(
            PythonVersionMin,
            custom_formatters=[WRONG_FORMATTER],
            formatters=[Formatter.BLACK, Formatter.ISORT],
        )


def test_format_code_is_not_subclass() -> None:
    """Test error when custom formatter doesn't inherit CustomCodeFormatter."""
    with pytest.raises(TypeError):
        _ = CodeFormatter(
            PythonVersionMin,
            custom_formatters=[NOT_SUBCLASS_FORMATTER],
            formatters=[Formatter.BLACK, Formatter.ISORT],
        )


def test_format_code_with_custom_formatter_without_kwargs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test custom formatter that doesn't require kwargs."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        custom_formatters=[ADD_COMMENT_FORMATTER],
        formatters=[Formatter.BLACK, Formatter.ISORT],
    )

    formatted_code = formatter.format_code("x = 1\ny = 2")

    assert formatted_code == "# a comment\nx = 1\ny = 2" + "\n"


def test_format_code_with_custom_formatter_with_kwargs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test custom formatter with kwargs."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        custom_formatters=[ADD_LICENSE_FORMATTER],
        custom_formatters_kwargs={"license_file": EXAMPLE_LICENSE_FILE},
        formatters=[Formatter.BLACK, Formatter.ISORT],
    )

    formatted_code = formatter.format_code("x = 1\ny = 2")

    assert (
        formatted_code
        == """# MIT License
#
# Copyright (c) 2023 Blah-blah
#
x = 1
y = 2
"""
    )


def test_format_code_with_two_custom_formatters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test chaining multiple custom formatters."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        custom_formatters=[
            ADD_COMMENT_FORMATTER,
            ADD_LICENSE_FORMATTER,
        ],
        custom_formatters_kwargs={"license_file": EXAMPLE_LICENSE_FILE},
        formatters=[Formatter.BLACK, Formatter.ISORT],
    )

    formatted_code = formatter.format_code("x = 1\ny = 2")

    assert (
        formatted_code
        == """# MIT License
#
# Copyright (c) 2023 Blah-blah
#
# a comment
x = 1
y = 2
"""
    )


def test_format_code_ruff_format_formatter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test ruff format formatter."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.RUFF_FORMAT],
    )
    with (
        mock.patch.object(formatter, "_find_ruff_path", return_value=FAKE_RUFF_PATH),
        mock.patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = b"output"
        formatted_code = formatter.format_code("input")

    assert formatted_code == "output"
    mock_run.assert_called_once_with(
        (FAKE_RUFF_PATH, "format", "-"),
        input=b"input",
        capture_output=True,
        check=False,
        cwd=str(tmp_path),
    )


def test_format_code_ruff_check_formatter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test ruff check formatter with auto-fix."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.RUFF_CHECK],
    )
    with (
        mock.patch.object(formatter, "_find_ruff_path", return_value=FAKE_RUFF_PATH),
        mock.patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = b"output"
        formatted_code = formatter.format_code("input")

    assert formatted_code == "output"
    mock_run.assert_called_once_with(
        (FAKE_RUFF_PATH, "check", "--fix", "--unsafe-fixes", "-"),
        input=b"input",
        capture_output=True,
        check=False,
        cwd=str(tmp_path),
    )


def test_format_code_ruff_check_formatter_without_type_checking_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test ruff check formatter keeps runtime imports when requested."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.RUFF_CHECK],
        use_type_checking_imports=False,
    )
    with (
        mock.patch.object(formatter, "_find_ruff_path", return_value=FAKE_RUFF_PATH),
        mock.patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = b"output"
        formatted_code = formatter.format_code("input")

    assert formatted_code == "output"
    mock_run.assert_called_once_with(
        (FAKE_RUFF_PATH, "check", "--fix", "--unsafe-fixes", "--unfixable", "TC001,TC002,TC003", "-"),
        input=b"input",
        capture_output=True,
        check=False,
        cwd=str(tmp_path),
    )


@pytest.mark.parametrize("explicit_value", [True, False])
def test_resolve_use_type_checking_imports_respects_explicit_value(explicit_value: bool) -> None:
    """Test explicit TYPE_CHECKING import settings are preserved."""
    assert (
        resolve_use_type_checking_imports(
            explicit_value,
            is_multi_module_output=True,
            formatters=[Formatter.RUFF_CHECK, Formatter.RUFF_FORMAT],
            requires_runtime_imports_with_ruff_check=True,
        )
        is explicit_value
    )


def test_resolve_use_type_checking_imports_defaults_to_runtime_imports_for_deferred_pydantic_ruff() -> None:
    """Test deferred Ruff formatting keeps runtime imports for modular Pydantic output by default."""
    assert not resolve_use_type_checking_imports(
        None,
        is_multi_module_output=True,
        formatters=[Formatter.RUFF_CHECK, Formatter.RUFF_FORMAT],
        requires_runtime_imports_with_ruff_check=True,
    )


def test_resolve_use_type_checking_imports_keeps_existing_default_outside_deferred_pydantic_ruff() -> None:
    """Test non-modular or non-Pydantic output keeps TYPE_CHECKING imports enabled by default."""
    assert resolve_use_type_checking_imports(
        None,
        is_multi_module_output=False,
        formatters=[Formatter.RUFF_CHECK, Formatter.RUFF_FORMAT],
        requires_runtime_imports_with_ruff_check=True,
    )
    assert resolve_use_type_checking_imports(
        None,
        is_multi_module_output=True,
        formatters=[Formatter.RUFF_CHECK],
        requires_runtime_imports_with_ruff_check=False,
    )
    assert resolve_use_type_checking_imports(
        None,
        is_multi_module_output=True,
        formatters=[Formatter.RUFF_FORMAT],
        requires_runtime_imports_with_ruff_check=True,
    )


def test_format_code_ruff_check_and_format_uses_resolved_ruff_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test combined Ruff formatting reuses the resolved Ruff executable."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.RUFF_CHECK, Formatter.RUFF_FORMAT],
    )
    with (
        mock.patch.object(formatter, "_find_ruff_path", return_value=FAKE_RUFF_PATH) as mock_find_ruff_path,
        mock.patch("subprocess.run") as mock_run,
    ):
        mock_run.side_effect = [
            mock.Mock(returncode=0, stdout=b"checked"),
            mock.Mock(returncode=0, stdout=b"formatted"),
        ]
        formatted_code = formatter.format_code("input")

    assert formatted_code == "formatted"
    mock_find_ruff_path.assert_called_once_with()
    assert mock_run.call_args_list == [
        mock.call(
            (FAKE_RUFF_PATH, "check", "--fix", "--unsafe-fixes", "-"),
            input=b"input",
            capture_output=True,
            check=False,
            cwd=str(tmp_path),
        ),
        mock.call(
            (FAKE_RUFF_PATH, "format", "-"),
            input=b"checked",
            capture_output=True,
            check=False,
            cwd=str(tmp_path),
        ),
    ]


def test_format_code_ruff_check_failure_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test Ruff check failures do not replace generated code with stdout."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.RUFF_CHECK],
    )
    with (
        mock.patch.object(formatter, "_find_ruff_path", return_value=FAKE_RUFF_PATH),
        mock.patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = b""
        mock_run.return_value.stderr = b"parse error"

        with pytest.raises(RuntimeError, match="parse error"):
            formatter.format_code("input")


def test_format_code_ruff_check_uses_stdout_when_fix_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test Ruff check can return fixed stdout while reporting remaining diagnostics."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.RUFF_CHECK],
    )
    with (
        mock.patch.object(formatter, "_find_ruff_path", return_value=FAKE_RUFF_PATH),
        mock.patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = b"fixed"
        mock_run.return_value.stderr = b"remaining lint"

        formatted_code = formatter.format_code("input")

    assert formatted_code == "fixed"


def test_format_code_ruff_check_internal_error_raises_with_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test Ruff check abnormal failures do not use stdout as formatted code."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.RUFF_CHECK],
    )
    with (
        mock.patch.object(formatter, "_find_ruff_path", return_value=FAKE_RUFF_PATH),
        mock.patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 2
        mock_run.return_value.stdout = b"partial output"
        mock_run.return_value.stderr = b"invalid config"

        with pytest.raises(RuntimeError, match="invalid config"):
            formatter.format_code("input")


def test_format_code_ruff_format_failure_raises_with_stdout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test Ruff format failures report stdout when stderr is empty."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.RUFF_FORMAT],
    )
    with (
        mock.patch.object(formatter, "_find_ruff_path", return_value=FAKE_RUFF_PATH),
        mock.patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 2
        mock_run.return_value.stdout = b"formatter output"
        mock_run.return_value.stderr = b""

        with pytest.raises(RuntimeError, match="formatter output"):
            formatter.format_code("input")


def test_format_code_ruff_failure_raises_without_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test Ruff failures without output still include the exit code."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.RUFF_FORMAT],
    )
    with (
        mock.patch.object(formatter, "_find_ruff_path", return_value=FAKE_RUFF_PATH),
        mock.patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 2
        mock_run.return_value.stdout = b""
        mock_run.return_value.stderr = b""

        with pytest.raises(RuntimeError, match="no output"):
            formatter.format_code("input")


def test_find_ruff_path_prefers_virtualenv_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test Ruff lookup prefers the executable next to the active Python."""
    monkeypatch.setattr(Path, "exists", lambda _path: True)

    ruff_name = "ruff.exe" if sys.platform == "win32" else "ruff"
    assert CodeFormatter._find_ruff_path() == str(Path(sys.executable).parent / ruff_name)


def test_find_ruff_path_uses_path_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test Ruff lookup falls back to PATH."""
    monkeypatch.setattr(Path, "exists", lambda _path: False)
    monkeypatch.setattr(shutil, "which", lambda _name: FAKE_RUFF_PATH)

    assert CodeFormatter._find_ruff_path() == FAKE_RUFF_PATH


def test_find_ruff_path_missing_raises_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test missing Ruff is detected before subprocess execution."""
    monkeypatch.setattr(Path, "exists", lambda _path: False)
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    with pytest.raises(RuntimeError, match=r"datamodel-code-generator\[ruff\]"):
        CodeFormatter._find_ruff_path()


def test_settings_path_with_existing_file(tmp_path: Path) -> None:
    """Test settings_path with existing file uses parent directory."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.black]\nline-length = 60\n", encoding="utf-8")
    existing_file = tmp_path / "existing.py"
    existing_file.write_text("", encoding="utf-8")

    formatter = CodeFormatter(
        PythonVersionMin, settings_path=existing_file, formatters=[Formatter.BLACK, Formatter.ISORT]
    )

    assert formatter.settings_path == str(tmp_path)


def test_settings_path_with_nonexistent_file(tmp_path: Path) -> None:
    """Test settings_path with nonexistent file uses existing parent."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.black]\nline-length = 60\n", encoding="utf-8")
    nonexistent_file = tmp_path / "nonexistent.py"

    formatter = CodeFormatter(
        PythonVersionMin, settings_path=nonexistent_file, formatters=[Formatter.BLACK, Formatter.ISORT]
    )

    assert formatter.settings_path == str(tmp_path)


def test_settings_path_with_deeply_nested_nonexistent_path(tmp_path: Path) -> None:
    """Test settings_path with deeply nested nonexistent path finds existing ancestor."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.black]\nline-length = 60\n", encoding="utf-8")
    nested_path = tmp_path / "a" / "b" / "c" / "nonexistent.py"

    formatter = CodeFormatter(
        PythonVersionMin, settings_path=nested_path, formatters=[Formatter.BLACK, Formatter.ISORT]
    )

    assert formatter.settings_path == str(tmp_path)


def test_format_directory_ruff_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test format_directory with ruff check."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.RUFF_CHECK],
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    with (
        mock.patch.object(formatter, "_find_ruff_path", return_value=FAKE_RUFF_PATH),
        mock.patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 0
        formatter.format_directory(output_dir)

    mock_run.assert_called_once_with(
        (FAKE_RUFF_PATH, "check", "--fix", "--unsafe-fixes", str(output_dir)),
        capture_output=True,
        check=False,
        cwd=str(tmp_path),
    )


def test_format_directory_ruff_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test format_directory with ruff format."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.RUFF_FORMAT],
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    with (
        mock.patch.object(formatter, "_find_ruff_path", return_value=FAKE_RUFF_PATH),
        mock.patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 0
        formatter.format_directory(output_dir)

    mock_run.assert_called_once_with(
        (FAKE_RUFF_PATH, "format", str(output_dir)),
        capture_output=True,
        check=False,
        cwd=str(tmp_path),
    )


def test_format_directory_both_ruff_formatters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test format_directory with both ruff check and format."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.RUFF_CHECK, Formatter.RUFF_FORMAT],
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    with (
        mock.patch.object(formatter, "_find_ruff_path", return_value=FAKE_RUFF_PATH),
        mock.patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 0
        formatter.format_directory(output_dir)

    assert mock_run.call_count == 2
    mock_run.assert_any_call(
        (FAKE_RUFF_PATH, "check", "--fix", "--unsafe-fixes", str(output_dir)),
        capture_output=True,
        check=False,
        cwd=str(tmp_path),
    )
    mock_run.assert_any_call(
        (FAKE_RUFF_PATH, "format", str(output_dir)),
        capture_output=True,
        check=False,
        cwd=str(tmp_path),
    )


def test_format_directory_ruff_check_without_type_checking_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test format_directory keeps runtime imports when requested."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.RUFF_CHECK],
        use_type_checking_imports=False,
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    with (
        mock.patch.object(formatter, "_find_ruff_path", return_value=FAKE_RUFF_PATH),
        mock.patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 0
        formatter.format_directory(output_dir)

    mock_run.assert_called_once_with(
        (FAKE_RUFF_PATH, "check", "--fix", "--unsafe-fixes", "--unfixable", "TC001,TC002,TC003", str(output_dir)),
        capture_output=True,
        check=False,
        cwd=str(tmp_path),
    )


def test_format_directory_both_ruff_formatters_without_type_checking_imports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test format_directory keeps runtime imports with both Ruff formatters."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.RUFF_CHECK, Formatter.RUFF_FORMAT],
        use_type_checking_imports=False,
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    with (
        mock.patch.object(formatter, "_find_ruff_path", return_value=FAKE_RUFF_PATH),
        mock.patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 0
        formatter.format_directory(output_dir)

    assert mock_run.call_count == 2
    mock_run.assert_any_call(
        (FAKE_RUFF_PATH, "check", "--fix", "--unsafe-fixes", "--unfixable", "TC001,TC002,TC003", str(output_dir)),
        capture_output=True,
        check=False,
        cwd=str(tmp_path),
    )
    mock_run.assert_any_call(
        (FAKE_RUFF_PATH, "format", str(output_dir)),
        capture_output=True,
        check=False,
        cwd=str(tmp_path),
    )


def test_defer_formatting_skips_ruff_in_format_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that defer_formatting=True skips ruff in format_code."""
    monkeypatch.chdir(tmp_path)
    formatter = CodeFormatter(
        PythonVersionMin,
        formatters=[Formatter.BLACK, Formatter.RUFF_CHECK, Formatter.RUFF_FORMAT],
        defer_formatting=True,
    )

    with mock.patch("subprocess.run") as mock_run:
        formatted_code = formatter.format_code("x = 1")

    mock_run.assert_not_called()
    assert "x = 1" in formatted_code


def test_generate_with_ruff_batch_formatting(tmp_path: Path) -> None:
    """Test that generate uses batch ruff formatting for directory output."""
    schema = """
    {
        "type": "object",
        "properties": {
            "name": {"type": "string"}
        }
    }
    """
    output_dir = tmp_path / "output"

    with (
        mock.patch("datamodel_code_generator.format.CodeFormatter._find_ruff_path", return_value=FAKE_RUFF_PATH),
        mock.patch("datamodel_code_generator.format.subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 0
        datamodel_code_generator.generate(
            input_=schema,
            output=output_dir,
            formatters=[Formatter.RUFF_CHECK, Formatter.RUFF_FORMAT],
            module_split_mode=datamodel_code_generator.ModuleSplitMode.Single,
        )

    assert mock_run.call_count == 2
    mock_run.assert_any_call(
        (
            FAKE_RUFF_PATH,
            "check",
            "--fix",
            "--unsafe-fixes",
            "--unfixable",
            "TC001,TC002,TC003",
            str(output_dir),
        ),
        capture_output=True,
        check=False,
        cwd=mock.ANY,
    )
    mock_run.assert_any_call(
        (FAKE_RUFF_PATH, "format", str(output_dir)),
        capture_output=True,
        check=False,
        cwd=mock.ANY,
    )


def test_generate_with_ruff_batch_formatting_and_explicit_type_checking_imports(tmp_path: Path) -> None:
    """Test explicit TYPE_CHECKING imports override the modular Pydantic Ruff default."""
    schema = """
    {
        "type": "object",
        "properties": {
            "name": {"type": "string"}
        }
    }
    """
    output_dir = tmp_path / "output"

    with (
        mock.patch("datamodel_code_generator.format.CodeFormatter._find_ruff_path", return_value=FAKE_RUFF_PATH),
        mock.patch("datamodel_code_generator.format.subprocess.run") as mock_run,
    ):
        mock_run.return_value.returncode = 0
        datamodel_code_generator.generate(
            input_=schema,
            output=output_dir,
            formatters=[Formatter.RUFF_CHECK, Formatter.RUFF_FORMAT],
            module_split_mode=datamodel_code_generator.ModuleSplitMode.Single,
            use_type_checking_imports=True,
        )

    assert mock_run.call_count == 2
    mock_run.assert_any_call(
        (FAKE_RUFF_PATH, "check", "--fix", "--unsafe-fixes", str(output_dir)),
        capture_output=True,
        check=False,
        cwd=mock.ANY,
    )
    mock_run.assert_any_call(
        (FAKE_RUFF_PATH, "format", str(output_dir)),
        capture_output=True,
        check=False,
        cwd=mock.ANY,
    )


def test_code_formatter_warns_when_formatters_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that FutureWarning is emitted when formatters is None (default)."""
    monkeypatch.chdir(tmp_path)
    with pytest.warns(FutureWarning, match="^Default formatters will change to builtin"):
        CodeFormatter(PythonVersionMin)


def test_code_formatter_optional_warning_when_formatters_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit external formatters receive the separate optional-installation notice."""
    monkeypatch.chdir(tmp_path)
    with pytest.warns(FutureWarning, match="^Black/isort will become optional"):
        CodeFormatter(PythonVersionMin, formatters=[Formatter.BLACK, Formatter.ISORT])


def test_code_formatter_no_warning_when_formatters_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that no warning is emitted when formatters is empty list."""
    monkeypatch.chdir(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        CodeFormatter(PythonVersionMin, formatters=[])


# ---------------------------------------------------------------------------
# Tests for missing black/isort formatter messages
# ---------------------------------------------------------------------------


@pytest.mark.abnormal_path("black and isort are required dependencies, so an e2e test cannot run without them")
@pytest.mark.parametrize(
    ("missing", "formatters", "message"),
    [
        (
            ("black",),
            [Formatter.BLACK, Formatter.ISORT],
            (
                r"black is not installed.*Install it \(datamodel-code-generator\[black\]\)"
                r".*--formatters builtin.*formatters=\[Formatter\.BUILTIN\]"
            ),
        ),
        (
            ("isort",),
            [Formatter.BLACK, Formatter.ISORT],
            r"isort is not installed.*Install it \(datamodel-code-generator\[isort\]\).*--formatters builtin",
        ),
        (
            ("black", "isort"),
            [Formatter.BLACK, Formatter.ISORT],
            (
                r"black and isort are not installed.*Install them \(datamodel-code-generator\[black,isort\]\)"
                r".*--formatters builtin"
            ),
        ),
    ],
)
def test_missing_formatter_raises_actionable_error(
    missing: tuple[str, ...],
    formatters: list[Formatter],
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A selected black or isort that cannot be imported names the extras and the builtin formatter."""
    monkeypatch.chdir(tmp_path)
    for name in missing:
        monkeypatch.setitem(sys.modules, name, None)

    with pytest.raises(ImportError, match=message):
        CodeFormatter(PythonVersionMin, formatters=formatters)


@pytest.mark.abnormal_path("black and isort are required dependencies, so an e2e test cannot run without them")
def test_default_formatters_missing_raises_actionable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The implicit default of black and isort reports the same remedies."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, "black", None)
    monkeypatch.setitem(sys.modules, "isort", None)

    with (
        pytest.warns(FutureWarning, match="Default formatters will change to builtin"),
        pytest.raises(ImportError, match=r"black and isort are not installed.*--formatters builtin"),
    ):
        CodeFormatter(PythonVersionMin)


@pytest.mark.abnormal_path("a broken transitive dependency of black or isort cannot be installed in an e2e environment")
@pytest.mark.parametrize("formatter", [Formatter.BLACK, Formatter.ISORT])
def test_formatter_transitive_import_error_is_not_reported_as_missing(
    formatter: Formatter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dependency missing inside an installed formatter is re-raised, not reported as the formatter missing."""
    monkeypatch.chdir(tmp_path)

    def fake_import_module(_name: str) -> object:
        msg = "No module named 'formatter_dependency'"
        raise ModuleNotFoundError(msg, name="formatter_dependency")

    monkeypatch.setattr(format_module, "import_module", fake_import_module)

    with pytest.raises(ModuleNotFoundError, match="formatter_dependency"):
        CodeFormatter(PythonVersionMin, formatters=[formatter])


def test_both_installed_no_missing_formatter_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normal case: both black and isort installed - no missing formatter error."""
    monkeypatch.chdir(tmp_path)

    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        cf = CodeFormatter(PythonVersionMin, formatters=[Formatter.BLACK, Formatter.ISORT])

    assert Formatter.BLACK in cf.formatters
    assert Formatter.ISORT in cf.formatters
    assert not cf.use_builtin_formatter


@pytest.mark.abnormal_path("black and isort are required dependencies, so an e2e test cannot run without them")
def test_explicit_builtin_formatter_needs_no_external_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The builtin formatter works while black and isort cannot be imported."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setitem(sys.modules, "black", None)
    monkeypatch.setitem(sys.modules, "isort", None)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cf = CodeFormatter(PythonVersionMin, formatters=[Formatter.BUILTIN])

    assert cf.use_builtin_formatter


BLACK_LAYOUT_CASES: tuple[tuple[str, int], ...] = (
    ("values = [item.name for item in collection if item.enabled and item.visible]\n", 30),
    ("def function_name(argument: int) -> tuple[int, str,]:\n    pass\n", 88),
    ("value = prefix + function_name(first_argument_value_long_name_here_ok)\n", 30),
    ("import os\n", 3),
    ("def function_name(first_argument: int = 1, *, second_argument: str = 'x') -> None:\n    pass\n", 40),
    ("value = '''multi\nline'''\n", 10),
    ("value = 'string literal that is quite long and overflows the limit for sure'.a().b()\n", 40),
    ("class Model(Base):\n    values: list[int] = [first_number, second_number, third_number]\n", 30),
    ("text = ('first implicit part of a string ' 'second implicit part of the string')\n", 30),
    ("value = [first_item_long, second_item_long] + other_value_name\n", 40),
    ("flag = candidate in allowed_values or other_value is None or not enabled\n", 30),
    ("value = function_name(first_argument,)[index_value].method_name(other_argument)\n", 40),
    ("@decorator_with_a_long_name(first_argument_value, second_argument_value)\ndef f():\n    pass\n", 30),
    ("callback = lambda: compute_something(first_argument, second_argument)\n", 30),
    ("result_value_with_a_long_name = very_long_function_name_without_arguments()\n", 30),
    ("message = 'a long template string with {} placeholder text'.format(value).strip()\n", 40),
    ("value = f'{first_value}' + f'{second_value!r:>10}' + first_value_with_long_name\n", 30),
    ("value = prefix_value_with_a_much_longer_name_than_fits + function_name(argument_one)\n", 40),
    ("pair = (assigned := compute_value(first_argument, second_argument_value))\n", 30),
    ("first = second = third_value_with_a_very_long_name_that_overflows_the_limit\n", 40),
    ("def function_name(argument: int) -> Annotated[VeryLongReturnTypeName, Field(description='x')]:\n    pass\n", 40),
    ("empty_tuple_with_a_very_long_name_that_overflows = ()\n", 30),
    ("value = not_a_call_but_a_very_long_name_that_cannot_be_split_anywhere_at_all\n", 30),
    ("value: Annotated[int, Field(description='some description text')] = default\n", 30),
    ("message = 'a long template string with {} placeholder text'.format\n", 40),
    ("values[index_name] = function_name(first_argument_value, second_argument_value)\n", 30),
    ("class VeryLongClassNameForTesting(FirstBaseClassName, SecondBaseClassName, metaclass=Meta):\n    pass\n", 40),
    ("value = (first for first in first_sequence_with_a_long_name if first_condition)\n", 30),
    ("value = [first_item_long, second_item_long] + other_value_name_that_is_much_longer_than_the_line\n", 40),
    ("value = prefix_value + function_name()\n", 30),
    ("value = first_choice if some_condition_holds else second_choice_value\n", 30),
    ("print(first_argument_value, second_argument_value, third_argument_value_x)\n", 30),
    ('class A:\n    """Ends with "quote" """\n', 88),
    ('class A:\n    """"Quoted" start."""\n', 88),
    ('class A:\n    """Path \\\\"""\n', 88),
    ('class A:\n    """   """\n', 88),
    ('class A:\n    """Title.\n\n    """\n', 88),
    ('class A:\n    """Title.\n    \n"""\n', 88),
    ('class A:\n    r"""Path \\ """\n', 88),
    ('class A:\n    class B:\n        """Title.\n\n        Last line."""\n', 10),
    ('class A:\n    """"""\n', 88),
    ("value = [first_item_long, second_item_long][index_value] + other_value_name\n", 40),
    ("value = first_function(argument_one) + second_function(argument_two_long)\n", 40),
    ("values[first_index, second_index,] = function_name(first_argument_value, second_argument)\n", 40),
    ("class Model:\n    日本語の長いフィールド名日本語の長いフィールド名: str = Field(None)\n", 49),
    ("class Model:\n    value: Optional[VeryLongGeneratedTypeName]() = None\n", 11),
    ("value = (first_item + second_item) * third_item_with_a_long_name\n", 30),
    ("def f() -> Optional[GeneratedNameOfTheFirstModel, GeneratedNameOfTheSecondModel]:\n    pass\n", 40),
    ("def f() -> 'Forward[GeneratedNameOfTheFirstModel, GeneratedNameOfTheSecondModel]':\n    pass\n", 40),
    ("def f(self) -> tuple[int, str,] | (FirstModel | SecondModel):\n    pass\n", 20),
    ("class Model:\n    value: (yield first_value) = second_value_with_a_long_name\n", 32),
    ("def function_name(first_argument, second_argument) -> (yield):\n    pass\n", 30),
)
UNSUPPORTED_LAYOUT_CASES: tuple[tuple[str, int], ...] = (
    ("nested = ((first_value, second_value))\n", 30),
    ("value = first_value.attribute[1:2]\n", 10),
    ("value = (\n    first_value  # comment\n)\n", 10),
    ("value = first_value_with_a_long_name \\\n    + second_value\n", 10),
    ("value = first_value ** 2 + second_value_with_a_long_name_here_and_more_text\n", 30),
    ("value = lambda argument: argument + first_value_with_a_long_name_here_more\n", 30),
    ("def function_name[T](argument: T) -> T:\n    pass\n", 20),
    ('x = """unterminated\n', 88),
    ("class A:\n  value = function_name(first_argument, second_argument)\n", 20),
    ("value = t'{first_value} and {second_value} in a long template string'\n", 30),
    ("@(\n    decorator_name)\ndef f():\n    pass\n", 10),
    ("value = function_name(1 .real, first_argument_value, second_argument_value)\n", 30),
    ("x = 1; y = function_name(first_argument, second_argument)\n", 30),
    ("class A:\n    x = 1; y = function_name(first_argument, second_argument)\n", 30),
    ("class A(B): x: int = first_value_with_a_long_name\n", 20),
    ("x: lambda: first_value_with_a_long_name = b\n", 20),
)


def test_builtin_formatter_lays_out_statements_as_black_does() -> None:
    """Lay out statements, definition headers and docstrings as black does at the given line lengths."""
    assert_output(
        "\n".join(
            f"[{line_length}]\n{apply_builtin_formatter(code, line_length=line_length)}"
            for code, line_length in BLACK_LAYOUT_CASES
        )
    )


@pytest.mark.parametrize(("code", "line_length"), UNSUPPORTED_LAYOUT_CASES)
def test_builtin_formatter_leaves_unsupported_statements_as_they_are(code: str, line_length: int) -> None:
    """Leave a statement whose constructs the line splitter does not lay out as it is."""
    assert apply_builtin_formatter(code, line_length=line_length) == code
