"""Real generation coverage for formatter and runtime dependency migration notices."""

from __future__ import annotations

import importlib
import os
import shlex
import shutil
import subprocess
import sys
import warnings
from pathlib import Path

import black
import isort
import pydantic
import pytest
from packaging.version import Version

import datamodel_code_generator
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


def _optional_dependency_notices(recorded: list[warnings.WarningMessage]) -> str:
    return "\n".join(
        str(item.message) for item in recorded if str(item.message).startswith(("Jinja2", "Genson", "argcomplete"))
    )


@pytest.mark.parametrize("kind", ["schema", "json", "csv", "custom-template"])
@pytest.mark.parametrize("entry", ["api", "cli"])
def test_optional_dependency_notices_real_generation(kind: str, entry: str, tmp_path: Path) -> None:
    """Jinja2 and Genson notices show only when custom templates or data inputs need them."""
    schema, input_file_type = {
        "schema": (DATA_PATH / "jsonschema" / "migration_notice.json", "jsonschema"),
        "json": (DATA_PATH / "json" / "pet.json", "json"),
        "csv": (DATA_PATH / "csv" / "simple.csv", "csv"),
        "custom-template": (DATA_PATH / "jsonschema" / "migration_notice.json", "jsonschema"),
    }[kind]
    template_dir = DATA_PATH / "templates" if kind == "custom-template" else None
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", FutureWarning)
        if entry == "api":
            generate(
                schema,
                config=GenerateConfig(
                    input_file_type=input_file_type,
                    formatters=["builtin"],
                    custom_template_dir=template_dir,
                    output=tmp_path / "output.py",
                    disable_timestamp=True,
                ),
            )
        else:
            run_main_with_args(
                [
                    *("--input", str(schema), "--input-file-type", input_file_type),
                    *("--output", str(tmp_path / "output.py"), "--disable-timestamp", "--formatters", "builtin"),
                    *(("--custom-template-dir", str(template_dir)) if template_dir else ()),
                ],
                use_builtin_default_formatter=False,
            )
    assert_output(_optional_dependency_notices(recorded), name="notices")


def test_optional_dependency_notices_deduplicated_in_one_cli_invocation(tmp_path: Path) -> None:
    """One CLI invocation reports each optional dependency once even when several jobs need it."""
    shutil.copyfile(DATA_PATH / "json" / "pet.json", tmp_path / "pet.json")
    job = (
        f'input = "pet.json"\ninput-file-type = "json"\nformatters = ["builtin"]\ndisable-timestamp = true\n'
        f'custom-template-dir = "{(DATA_PATH / "templates").as_posix()}"\n'
    )
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.datamodel-codegen.jobs.first]\n{job}output = "first.py"\n'
        f'[tool.datamodel-codegen.jobs.second]\n{job}output = "second.py"\n',
        encoding="utf-8",
    )
    with chdir(tmp_path), warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", FutureWarning)
        run_main_with_args(["--all-jobs"], use_builtin_default_formatter=False)
    assert_output(_optional_dependency_notices(recorded), name="notices")


@pytest.mark.parametrize(
    "requirement",
    [
        pytest.param("datamodel-code-generator[genson]>=0.95", id="genson-extra"),
        pytest.param("Datamodel__Code.Generator[Ruff, ALL]", id="all-extra-normalized"),
        pytest.param("datamodel-code-generator[genson-dev]", id="other-extra"),
        pytest.param('datamodel-code-generator[genson]; sys_platform == "never"', id="marker"),
        pytest.param('datamodel-code-generator[genson]; extra == "codegen"', id="own-extra"),
        pytest.param("datamodel-code-generator", id="no-extra"),
    ],
)
def test_genson_notice_for_installed_requirements(
    requirement: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An installed package that requires the genson extra unconditionally has done what the Genson notice asks.

    An interrupted installation without METADATA and one whose METADATA is not UTF-8 are skipped.
    """
    (tmp_path / "dependent-1.0.dist-info").mkdir()
    (tmp_path / "dependent-1.0.dist-info" / "METADATA").write_text(
        f"Name: dependent\nVersion: 1.0\nRequires-Dist: {requirement}\n", encoding="utf-8"
    )
    (tmp_path / "unfinished-1.0.dist-info").mkdir()
    (tmp_path / "latin-1.0.dist-info").mkdir()
    (tmp_path / "latin-1.0.dist-info" / "METADATA").write_bytes(b"Name: latin\nVersion: 1\nSummary: caf\xe9\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", FutureWarning)
        generate({"a": 1}, config=GenerateConfig(input_file_type="dict", formatters=["builtin"]))
    assert_output(_optional_dependency_notices(recorded), name="notices")


def test_migration_notices_name_the_code_that_called_generate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A notice that ``generate()`` emits names the module that called it, here while that module is imported.

    Neither the frames of the package nor the import machinery's own frames between the module and the test are named,
    so a ``module=`` filter for the calling module matches the notices.
    """
    (tmp_path / "notice_caller.py").write_text(
        "from datamodel_code_generator import InputFileType, generate\n\n"
        "generate({'a': 1}, input_file_type=InputFileType.Dict)\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(tmp_path)

    def notices(*module_filters: str) -> list[warnings.WarningMessage]:
        sys.modules.pop("notice_caller", None)
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always", FutureWarning)
            for module in module_filters:
                warnings.filterwarnings("ignore", category=FutureWarning, module=module)
            try:
                importlib.import_module("notice_caller")
            finally:
                sys.modules.pop("notice_caller", None)
        return [item for item in recorded if str(item.message).startswith(("Genson", "Default formatters"))]

    left = notices("notice_caller")
    assert_output(
        "".join(
            f"{Path(item.filename).relative_to(tmp_path)}:{item.lineno}: {str(item.message).split('. ')[0]}\n"
            for item in notices()
        )
        + f"left by a module filter for the caller: {len(left)}\n"
    )


def test_cli_notices_name_the_package(tmp_path: Path) -> None:
    """A notice of a CLI invocation names the package, not the code that called ``main()``.

    The console script and the ``__main__.py`` of ``python -m`` both run as ``__main__``, and Python's default filters
    show a ``DeprecationWarning`` raised from ``__main__``, so the CLI shows the notices it showed before they named the
    caller, through either entry point.
    """
    (schema := tmp_path / "schema.json").write_text('{"type": "object", "properties": {"a": {"type": "string"}}}')
    args = ["--input", str(schema), "--input-file-type", "jsonschema", "--output-model-type", "pydantic_v2.BaseModel"]
    with chdir(tmp_path), warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        run_main_with_args([*args, "--output", str(tmp_path / "models.py")])
    package = Path(datamodel_code_generator.__file__).parent
    notice = next(item for item in recorded if "--use-annotated is recommended" in str(item.message))
    env = {
        **{key: value for key, value in os.environ.items() if key != "PYTHONWARNINGS"},
        "PYTHONPATH": str(package.parent),
    }
    entry_points = {
        "console script": ["-c", "import sys; from datamodel_code_generator.__main__ import main; sys.exit(main())"],
        "python -m": ["-m", "datamodel_code_generator"],
    }
    stderr = {
        name: subprocess.run(
            [sys.executable, *entry, *args, "--formatters", "builtin"],
            capture_output=True,
            check=True,
            cwd=tmp_path,
            env=env,
            text=True,
            timeout=120,
        ).stderr
        for name, entry in entry_points.items()
    }
    assert_output(
        f"notice of the CLI run names: {Path(notice.filename).resolve().relative_to(package.resolve()).as_posix()}\n"
        + "".join(
            f"DeprecationWarning lines, {name}: {text.count('DeprecationWarning')}\n" for name, text in stderr.items()
        )
    )
