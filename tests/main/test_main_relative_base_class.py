"""Relative base classes through the public CLI and generation API."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import Error, generate
from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.config import GenerateConfig
from tests.conftest import assert_output
from tests.main.conftest import run_main_with_args

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def base_class_package(tmp_path: Path) -> tuple[Path, Path]:
    """Create a nested package with local and ancestor base classes."""
    package = tmp_path / "pkg"
    for directory in (package, package / "models", package / "models" / "v1"):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "__init__.py").write_text("", encoding="utf-8")
    base_source = (
        "from msgspec import Struct\n\n"
        "class BaseClass(Struct):\n"
        "    def __post_init__(self):\n"
        "        self.value += 1\n"
    )
    (package / "base.py").write_text(base_source, encoding="utf-8")
    (package / "models" / "v1" / "base.py").write_text(base_source, encoding="utf-8")
    schema = tmp_path / "schema.json"
    schema.write_text(
        json.dumps({
            "title": "MyClass",
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        }),
        encoding="utf-8",
    )
    return schema, package / "models" / "v1" / "model.py"


def _base_class_setting(mode: str, path: str) -> tuple[str, str | dict[str, str | list[str]]]:
    if mode == "global":
        return "base_class", path
    return "base_class_map", {"MyClass": [path] if mode == "list" else path}


@pytest.mark.parametrize("engine", ["cli", "api", "mutated-api"])
@pytest.mark.parametrize("mode", ["global", "scalar", "list"])
@pytest.mark.parametrize("path", [".base.BaseClass", "...base.BaseClass", "pkg.base.BaseClass"])
def test_relative_base_class_import_and_hook(
    base_class_package: tuple[Path, Path], engine: str, mode: str, path: str
) -> None:
    """Preserve relative imports and execute inherited post-init behavior."""
    schema, output = base_class_package
    field, value = _base_class_setting(mode, path)
    if engine == "cli":
        run_main_with_args([
            "--input",
            str(schema),
            "--input-file-type",
            "jsonschema",
            "--output",
            str(output),
            "--output-model-type",
            "msgspec.Struct",
            "--disable-timestamp",
            "--formatters",
            "builtin",
            "--" + field.replace("_", "-"),
            value if isinstance(value, str) else json.dumps(value),
        ])
    else:
        options = {
            "input_file_type": "jsonschema",
            "output": output,
            "output_model_type": "msgspec.Struct",
            "disable_timestamp": True,
            "formatters": ["builtin"],
        }
        if engine == "api":
            options[field] = value
        config = GenerateConfig.model_validate(options)
        if engine == "mutated-api":
            setattr(config, field, value)
        generate(schema, config=config)
    assert_output(output.read_text(encoding="utf-8"), name="model")
    result = subprocess.check_output(
        [
            sys.executable,
            "-c",
            "from pkg.models.v1.model import MyClass; print(MyClass.__bases__[0].__name__, MyClass(value=41).value)",
        ],
        cwd=schema.parent,
        text=True,
    )
    assert_output(result, name="runtime")


@pytest.mark.parametrize("engine", ["cli", "api", "mutated-api"])
@pytest.mark.parametrize("mode", ["global", "scalar", "list"])
@pytest.mark.parametrize(
    "path",
    [
        ".base.1x",
        "...base.class",
        "...base..BaseClass",
        "...",
        "...base.BaseClass;print(1)",
        ".BaseClass",
        "..BaseClass",
    ],
)
def test_relative_base_class_rejects_invalid_names(
    base_class_package: tuple[Path, Path], capsys: pytest.CaptureFixture[str], engine: str, mode: str, path: str
) -> None:
    """Reject invalid relative names before they can reach generated source."""
    schema, output = base_class_package
    field, value = _base_class_setting(mode, path)
    if engine == "cli":
        run_main_with_args(
            [
                "--input",
                str(schema),
                "--input-file-type",
                "jsonschema",
                "--output",
                str(output),
                "--" + field.replace("_", "-"),
                value if isinstance(value, str) else json.dumps(value),
            ],
            expected_exit=Exit.ERROR,
        )
        assert_output(capsys.readouterr().err)
    else:
        if engine == "mutated-api":
            config = GenerateConfig.model_validate({"input_file_type": "jsonschema"})
            setattr(config, field, value)
            with pytest.raises(Error) as exc:
                generate(schema, config=config)
        else:
            with pytest.raises(Error) as exc:
                generate(schema, config=GenerateConfig.model_validate({"input_file_type": "jsonschema", field: value}))
        assert_output(str(exc.value))


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--additional-imports", ".base.BaseClass"),
        ("--type-overrides", '{"MyClass.value": ".base.BaseClass"}'),
        ("--class-decorators", "@.base.decorate"),
    ],
)
def test_relative_paths_remain_invalid_for_other_options(
    base_class_package: tuple[Path, Path], capsys: pytest.CaptureFixture[str], option: str, value: str
) -> None:
    """Keep relative-path support limited to base-class settings."""
    schema, output = base_class_package
    run_main_with_args(
        ["--input", str(schema), "--input-file-type", "jsonschema", "--output", str(output), option, value],
        expected_exit=Exit.ERROR,
    )
    assert_output(capsys.readouterr().err)


@pytest.mark.parametrize("value", [".base.BaseClass", 42])
def test_mutated_additional_imports_remain_invalid(base_class_package: tuple[Path, Path], value: object) -> None:
    """Reject invalid imports assigned after configuration validation."""
    schema, _ = base_class_package
    config = GenerateConfig.model_validate({"input_file_type": "jsonschema"})
    config.additional_imports = [value]
    with pytest.raises(Error) as exc:
        generate(schema, config=config)
    assert_output(str(exc.value))
