"""Real generation coverage for formatter and runtime dependency migration notices."""

from __future__ import annotations

import shlex
import shutil
import warnings
from pathlib import Path

import black
import isort
import pydantic
import pytest
from packaging.version import Version

from datamodel_code_generator import chdir, generate
from datamodel_code_generator.__main__ import generate_cli_command
from datamodel_code_generator.config import GenerateConfig
from datamodel_code_generator.deprecations import cli_migration_warning_scope
from datamodel_code_generator.util import load_toml
from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, EXPECTED_MAIN_PATH, run_main_with_args

EXPECTED = EXPECTED_MAIN_PATH / "migration_warnings"


@pytest.mark.parametrize(
    "selection",
    ["implicit", "black", "isort", "both", "builtin", "ruff", "old-preset", "new-preset", "empty", "custom"],
)
@pytest.mark.parametrize("entry", ["cli", "pyproject", "api", "profile", "job"])
def test_migration_notices_real_generation(selection: str, entry: str, tmp_path: Path) -> None:
    """Resolve actual settings and preserve generated dataclass output in each installed dependency environment."""
    shutil.copyfile(DATA_PATH / "jsonschema" / "migration_notice.json", tmp_path / "schema.json")
    source = DATA_PATH / "config" / "migration_warnings" / f"{selection}.toml"
    table = {
        "cli": "tool.datamodel-codegen",
        "pyproject": "tool.datamodel-codegen",
        "profile": "tool.datamodel-codegen.profiles.selected",
        "job": "tool.datamodel-codegen.jobs.selected",
        "api": "tool.datamodel-codegen",
    }[entry]
    if entry != "cli" or selection == "empty":
        (tmp_path / "pyproject.toml").write_text(f"[{table}]\n{source.read_text(encoding='utf-8')}", encoding="utf-8")
    with chdir(tmp_path), warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", FutureWarning)
        if entry == "api":
            values = {key.replace("-", "_"): value for key, value in load_toml(source).items()}
            values.pop("input")
            if "custom_formatters" in values:
                values["custom_formatters"] = [values["custom_formatters"]]
            generate(Path("schema.json"), config=GenerateConfig.model_validate(values))
        else:
            args = (
                shlex.split(
                    generate_cli_command({
                        key.replace("-", "_"): value for key, value in load_toml(source).items() if value != []
                    })
                )[1:]
                if entry == "cli"
                else {"pyproject": [], "profile": ["--profile", "selected"], "job": ["--job", "selected"]}[entry]
            )
            run_main_with_args(args, use_builtin_default_formatter=False)
    implicit = selection in {"implicit", "old-preset", "custom"}
    uses_black = implicit or selection in {"black", "both"}
    uses_isort = implicit or selection in {"isort", "both"}
    notices = (
        ("Default formatters", "default", implicit),
        ("Black/isort", "optional", not implicit and (uses_black or uses_isort)),
        ("Support for Black", "black", uses_black and Version(black.__version__) < Version("24.3.0")),
        ("Support for isort", "isort", uses_isort and Version(isort.__version__) < Version("6")),
        ("Support for DCG", "pydantic", Version(pydantic.VERSION) < Version("2.8.2")),
    )
    for prefix, name, expected in notices:
        assert_output(
            "\n".join(str(item.message) for item in recorded if str(item.message).startswith(prefix)),
            EXPECTED / f"{name if expected else 'empty'}.txt",
        )
    assert_output(
        (tmp_path / "output.py").read_text(encoding="utf-8"),
        EXPECTED / {"custom": "custom.py", "black": "black.py", "empty": "raw.py"}.get(selection, "model.py"),
    )


@pytest.mark.parametrize("selection", ["implicit", "both", "new-preset"])
def test_migration_notices_batch_deduplicated(selection: str, tmp_path: Path) -> None:
    """One CLI invocation reports each migration once even with warnings configured as always."""
    shutil.copyfile(DATA_PATH / "jsonschema" / "migration_notice.json", tmp_path / "schema.json")
    source = (DATA_PATH / "config" / "migration_warnings" / f"{selection}.toml").read_text(encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        f"[tool.datamodel-codegen.jobs.first]\n{source}\n"
        f"[tool.datamodel-codegen.jobs.second]\n{source.replace('output.py', 'second.py')}",
        encoding="utf-8",
    )
    with chdir(tmp_path), warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", FutureWarning)
        run_main_with_args(["--all-jobs"], use_builtin_default_formatter=False)
    messages = [str(item.message) for item in recorded if item.category is FutureWarning]
    versions = "-".join(
        f"{'old-' if Version(installed) < Version(minimum) else ''}{name}"
        for name, installed, minimum in (
            ("pydantic", pydantic.VERSION, "2.8.2"),
            ("black", black.__version__, "24.3.0"),
            ("isort", isort.__version__, "6"),
        )
    )
    assert_output("\n".join(messages), EXPECTED / f"batch-{selection}-{versions}.txt")
    assert_output((tmp_path / "output.py").read_text(encoding="utf-8"), EXPECTED / "model.py")
    assert_output((tmp_path / "second.py").read_text(encoding="utf-8"), EXPECTED / "model.py")


@pytest.mark.parametrize("action", ["ignore", "error"])
def test_migration_api_warning_filters(action: str, tmp_path: Path) -> None:
    """The Python API preserves user warning suppression and warning-as-error behavior."""
    config = GenerateConfig(
        input_file_type="jsonschema",
        output_model_type="dataclasses.dataclass",
        formatters=["black", "isort"],
        output=tmp_path / "output.py",
        disable_timestamp=True,
    )
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter(action, FutureWarning)
        if action == "error":
            with pytest.raises(FutureWarning):
                generate(DATA_PATH / "jsonschema" / "migration_notice.json", config=config)
        else:
            generate(DATA_PATH / "jsonschema" / "migration_notice.json", config=config)
    assert_output("\n".join(str(item.message) for item in recorded), EXPECTED / "empty.txt")
    assert_output(str((tmp_path / "output.py").exists()), EXPECTED / f"file-{action}.txt")


def test_migration_error_filter_is_not_consumed_by_cli_scope(tmp_path: Path) -> None:
    """A warning promoted to an error remains an error on subsequent generation attempts."""
    with cli_migration_warning_scope(), warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        for _ in range(2):
            with pytest.raises(FutureWarning):
                generate(
                    DATA_PATH / "jsonschema" / "migration_notice.json",
                    config=GenerateConfig(
                        input_file_type="jsonschema",
                        formatters=["black", "isort"],
                        output=tmp_path / "output.py",
                        disable_timestamp=True,
                    ),
                )
    assert_output(str((tmp_path / "output.py").exists()), EXPECTED / "file-error.txt")
