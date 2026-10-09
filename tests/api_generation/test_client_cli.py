"""Generate the client target from the command line and pyproject.toml: settings, precedence, and refusals."""

from __future__ import annotations

import ast
import json
import shutil
import warnings
from pathlib import Path
from typing import Any

import pytest

from datamodel_code_generator import generate, load_pyproject_config
from datamodel_code_generator.__main__ import Exit
from tests.conftest import assert_generated_modules_output, assert_output, create_assert_file_content
from tests.data.python.client_generation import (
    client_cli_arguments,
    client_cli_modules,
    client_generate_options,
    prepare_client_case,
)
from tests.main.conftest import run_main_and_assert, run_main_with_args, run_main_with_system_exit

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform" / "client"
CLI = SOURCE / "cli"
EXPECTED = DATA / "expected" / "main" / "generation_platform" / "client"
DEPENDENCIES = (EXPECTED / "cli" / "dependencies.txt").read_text(encoding="utf-8")
OPTIONS = [
    *("--target-python-version", "3.11", "--openapi-scopes", "schemas", "api"),
    *("--output-model-type", "pydantic_v2.BaseModel", "--formatters", "builtin", "--disable-timestamp"),
]
PACKAGES = ["--client-package", "client", "--client-model-package", "models"]
CLIENT = ["--generate-client", "httpx2", "--client-output", "client", *PACKAGES]
DOC_OPTIONS = ["--input-file-type", "openapi", "--output", "models.py", *OPTIONS, *CLIENT]
DOC_INPUT = "generation_platform/client/cli/options.yaml"
DOC_OUTPUT = "main/generation_platform/client/cli/options"
SYNC = "client/resources/pets/_sync.py"

assert_file_content = create_assert_file_content(EXPECTED)


def _inputs(root: Path, *pyproject: str) -> list[tuple[Path, Path]]:
    return [
        (CLI / "options.yaml", root / "options.yaml"),
        (CLI / "protocols.json", root / "protocols.json"),
        *((CLI / name, root / "pyproject.toml") for name in pyproject),
    ]


def _copy(root: Path, *pyproject: str) -> None:
    for source, destination in _inputs(root, *pyproject):
        shutil.copy2(source, destination)


def _methods(module: Path, root: Path) -> str:
    """Name each method of the module's first resource class with its keyword and variadic keyword arguments."""
    resource = next(
        node
        for node in ast.parse(module.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ClassDef) and node.name.endswith("Resource")
    )
    methods: dict[str, list[str]] = {}
    for node in resource.body:
        if not isinstance(node, ast.FunctionDef) or node.name.startswith(("_", "with_")):
            continue
        methods[node.name] = [argument.arg for argument in node.args.kwonlyargs]
        if (variadic := node.args.kwarg) is not None:
            methods[node.name].append(f"**{variadic.arg}")
    return f"# {module.relative_to(root).as_posix()}\n" + "".join(
        f"{name}({', '.join(arguments)})\n" for name, arguments in methods.items()
    )


def test_client_cli_generate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write the models and the client package, find nothing to change, then report an edited file as a difference."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("options.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=[*OPTIONS, *CLIENT],
        copy_files=_inputs(tmp_path),
        capsys=capsys,
        expected_stderr=DEPENDENCIES,
    )
    assert_file_content(tmp_path / SYNC, "cli/options/_sync.py")
    run_main_and_assert(
        input_path=Path("options.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=[*OPTIONS, *CLIENT, "--check"],
        capsys=capsys,
        assert_no_stderr=True,
    )
    (tmp_path / SYNC).write_text("# edited\n", encoding="utf-8")
    run_main_and_assert(
        input_path=Path("options.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=[*OPTIONS, *CLIENT, "--check"],
        expected_exit=Exit.DIFF,
        capsys=capsys,
        expected_stdout_path=EXPECTED / "cli" / "check-edited.txt",
        assert_no_stderr=True,
    )


@pytest.mark.parametrize("job", [[], ["--job", "client"]], ids=["options", "job"])
def test_client_cli_nested_models(
    job: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep the models inside the client package, from options or a job: one tree to write and to check."""
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path, *(["pyproject-nested-jobs.toml"] if job else []))
    arguments = job or [
        *("--input", "options.yaml", "--input-file-type", "openapi", "--output", "client/models.py"),
        *OPTIONS,
        *CLIENT,
        *("--client-model-package", "client.models"),
    ]
    run_main_with_args(arguments, capsys=capsys, expected_stderr=DEPENDENCIES)
    assert_file_content(tmp_path / "client" / "models.py", "cli/options/models.py")
    run_main_with_args([*arguments, "--check"], capsys=capsys, assert_no_stderr=True)
    (tmp_path / "client" / "extensions.py").write_text("# User extension\n", encoding="utf-8")
    (tmp_path / "client" / "models.py").unlink()
    run_main_with_args(
        [*arguments, "--check"],
        expected_exit=Exit.DIFF,
        capsys=capsys,
        expected_stdout_path=EXPECTED / "cli" / "check-nested.txt",
        assert_no_stderr=True,
    )


@pytest.mark.parametrize("form", ["pyproject", "options", "python", "loaded"])
@pytest.mark.parametrize("case", ["pets-unpack", "retries", "compression", "auth", "media", "fields"])
def test_client_cli_equivalence(
    case: str, form: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write the package the Python settings of a case render, from its pyproject.toml keys, options, or generate().

    Tables of pyproject.toml are inline JSON on the command line and mappings in generate(), which also loads the
    keys from another directory.
    """
    monkeypatch.chdir(tmp_path)
    prepare_client_case(case, tmp_path)
    pyproject = CLI / f"pyproject-{case}.toml"
    match form:
        case "pyproject":
            shutil.copy2(pyproject, tmp_path / "pyproject.toml")
            run_main_with_args([], capsys=capsys)
        case "options":
            run_main_with_args(client_cli_arguments(pyproject), capsys=capsys)
        case "python":
            source, options = client_generate_options(pyproject, tmp_path)
            generate(source, **options)
        case _:
            shutil.copy2(pyproject, tmp_path / "pyproject.toml")
            source, _ = client_generate_options(pyproject, tmp_path)
            (elsewhere := tmp_path / "elsewhere").mkdir()
            monkeypatch.chdir(elsewhere)
            generate(tmp_path / source, config=load_pyproject_config(tmp_path))
    expected, modules = client_cli_modules(case, tmp_path)
    assert_generated_modules_output(modules, EXPECTED / "packages" / expected / "pydantic_v2_BaseModel")


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("base", []),
        ("profile", ["--profile", "unpack"]),
        ("cli-over-profile", ["--profile", "unpack", "--client-signature-style", "explicit"]),
        ("operation-over-cli", ["--client-body-arguments", "both"]),
        ("cli-table", ["--client-operations", '{"/paths/~1pets/post": {"name": "add"}}']),
        ("cli-empty-table", ["--client-operations", "{}"]),
        ("profile-output", ["--profile", "elsewhere"]),
        ("cli-output", ["--profile", "elsewhere", "--client-output", "service"]),
    ],
)
def test_client_cli_precedence(
    name: str,
    arguments: list[str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Take each client setting from the command line, then the profile, then the base table.

    An operation's own entry beats the global setting, and a command-line table replaces the pyproject.toml one.
    """
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path, "pyproject-precedence.toml")
    run_main_with_args(arguments, capsys=capsys, expected_stderr=DEPENDENCIES)
    report = "".join(_methods(path, tmp_path) for path in sorted(tmp_path.glob("*/resources/pets/_sync.py")))
    assert_output(
        f"$ datamodel-codegen {' '.join(arguments)}\n{report}", EXPECTED / "cli" / "precedence" / f"{name}.txt"
    )


def test_client_cli_pyproject_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolve the documents of pyproject.toml tables of operation references and helpers against its directory.

    Inline JSON operation references and helpers on the command line resolve against the working directory.
    """
    project, work = tmp_path / "project", tmp_path / "project" / "work"
    work.mkdir(parents=True)
    _copy(project, "pyproject-paths.toml")
    monkeypatch.chdir(work)
    run_main_with_args([], capsys=capsys, expected_stderr=DEPENDENCIES)
    helpers = (
        (CLI / "protocols.json")
        .read_text(encoding="utf-8")
        .replace(
            '"operation": "/paths/~1pets/get"',
            '"operation": {"pointer": "/paths/~1pets/get", "document": "../options.yaml"}',
        )
    )
    run_main_with_args(
        [
            *("--client-output", "service", "--client-protocols", helpers),
            *("--client-operations", '{"../options.yaml#/paths/~1pets/get": {"name": "find_pets"}}'),
        ],
        capsys=capsys,
        expected_stderr=DEPENDENCIES,
    )
    packages = sorted(path.parents[1] for path in tmp_path.rglob("protocols/_helpers.py"))
    assert_output(
        "".join(_methods(path / "resources" / "pets" / "_sync.py", tmp_path) for path in packages),
        EXPECTED / "cli" / "pyproject-paths.txt",
    )


def test_client_cli_pyproject_protocols_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read a helper file pyproject.toml names relative to its directory, like the same file on the command line."""
    project, work = tmp_path / "project", tmp_path / "project" / "work"
    work.mkdir(parents=True)
    _copy(project, "pyproject-protocols.toml")
    monkeypatch.chdir(work)
    run_main_with_args([], capsys=capsys, expected_stderr=DEPENDENCIES)
    assert_file_content(project / "client" / "protocols" / "_helpers.py", "cli/options/protocols/_helpers.py")


def _json_files(root: Path, *pyproject: str) -> None:
    """Copy the JSON files that name options.yaml as their sibling, and the document, into a directory of a root.

    One more helper file names its missing document by an absolute path.
    """
    shutil.copytree(CLI / "json-files", directory := root / "api")
    shutil.copy2(CLI / "options.yaml", directory / "options.yaml")
    helpers = json.loads((directory / "unresolved.json").read_text(encoding="utf-8"))
    helpers["pets.all"]["operation"]["document"] = (directory.resolve() / "missing.yaml").as_posix()
    (directory / "absolute.json").write_text(json.dumps({"pets.all": helpers["pets.all"]}), encoding="utf-8")
    for name in pyproject:
        shutil.copy2(CLI / name, root / "pyproject.toml")


@pytest.mark.parametrize(
    ("pyproject", "arguments"),
    [
        (
            [],
            [
                *("--input", "{project}/api/options.yaml", "--input-file-type", "openapi", *OPTIONS),
                *("--output", "{project}/models.py", "--generate-client", "httpx2", *PACKAGES),
                *("--client-output", "{project}/client", "--client-protocols", "{project}/api/protocols.json"),
                *("--client-operations", "{project}/api/operations.json"),
            ],
        ),
        (
            [],
            [
                *("--input", "../api/options.yaml", "--input-file-type", "openapi", *OPTIONS),
                *("--output", "../models.py", "--generate-client", "httpx2", *PACKAGES),
                *("--client-output", "../client", "--client-protocols", "../api/protocols.json"),
                *("--client-operations", "../api/operations.json"),
            ],
        ),
        (["pyproject-json-files.toml"], []),
        (["pyproject-json-files-jobs.toml"], ["--job", "client"]),
    ],
    ids=["absolute", "relative", "pyproject", "job"],
)
def test_client_cli_json_file_documents(
    pyproject: list[str],
    arguments: list[str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve the documents that a JSON file of helpers or operations names against the file's directory.

    The file is an option with an absolute or a relative path, or a key of pyproject.toml or of one of its jobs.
    """
    project, work = tmp_path / "project", tmp_path / "project" / "work"
    work.mkdir(parents=True)
    _json_files(project, *pyproject)
    monkeypatch.chdir(work)
    run_main_with_args(
        [argument.format(project=project) for argument in arguments], capsys=capsys, expected_stderr=DEPENDENCIES
    )
    assert_file_content(project / "client" / "protocols" / "_helpers.py", "cli/json-files/_helpers.py")
    assert_output(_methods(project / SYNC, tmp_path), EXPECTED / "cli" / "json-files" / "documents.txt")


@pytest.mark.parametrize(
    ("name", "option"),
    [
        ("unresolved", "--client-protocols"),
        ("absolute", "--client-protocols"),
        ("list", "--client-protocols"),
        ("unresolved-operations", "--client-operations"),
    ],
)
def test_client_cli_json_file_errors(
    name: str, option: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Name the file that a document of a helper or operations file resolved to, an absolute one once.

    A helper file that holds no object is refused like the same inline JSON.
    """
    monkeypatch.chdir(tmp_path)
    _json_files(tmp_path)
    run_main_and_assert(
        input_path=Path("api/options.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=[*OPTIONS, *CLIENT, option, f"api/{name}.json"],
        expected_exit=Exit.ERROR,
        output_should_not_exist=True,
    )
    assert_output(
        capsys.readouterr().err.replace(tmp_path.resolve().as_posix(), "<root>"),
        EXPECTED / "cli" / "json-files" / f"{name}.txt",
    )


def test_client_cli_generate_pyproject_config(capsys: pytest.CaptureFixture[str]) -> None:
    """Print the client options of a command line as [tool.datamodel-codegen] keys, like model options."""
    run_main_with_args(
        [
            *("--input", "options.yaml", "--output", "models.py", *CLIENT),
            *("--client-signature-style", "unpack", "--client-body-arguments", "both"),
            *("--client-resource-names", '{"pets": "animals"}', "--client-protocols", "protocols.json"),
            *("--client-operations", '{"/paths/~1pets/get": {"name": "list_all"}}'),
            *("--client-default-base-url", "https://api.example.com/v1"),
            *("--client-server-base-url", "https://api.example.com/"),
            "--generate-pyproject-config",
        ],
        capsys=capsys,
        expected_stdout_path=EXPECTED / "cli" / "pyproject-config.txt",
    )


def test_client_cli_generate_cli_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Print the client keys of pyproject.toml as command-line options, like model keys."""
    monkeypatch.chdir(tmp_path)
    shutil.copy2(CLI / "pyproject-configured.toml", tmp_path / "pyproject.toml")
    run_main_with_args(
        ["--generate-cli-command"], capsys=capsys, expected_stdout_path=EXPECTED / "cli" / "cli-command.txt"
    )


def test_client_cli_ignore_pyproject(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Generate only the models when --ignore-pyproject leaves out the client pyproject.toml selects."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("options.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=[*OPTIONS, "--ignore-pyproject"],
        copy_files=_inputs(tmp_path, "pyproject-client.toml"),
        file_should_not_exist=tmp_path / "client",
    )


def test_client_cli_unselected_pyproject(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ignore client settings that only pyproject.toml holds while no client is selected."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        (CLI / "pyproject-configured.toml").read_text(encoding="utf-8").replace('generate-client = "httpx2"\n', ""),
        encoding="utf-8",
    )
    run_main_and_assert(
        input_path=Path("options.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=OPTIONS,
        copy_files=_inputs(tmp_path),
        file_should_not_exist=tmp_path / "client",
    )


@pytest.mark.parametrize(
    ("pyproject", "arguments", "written", "unwritten"),
    [
        ("pyproject-server.toml", CLIENT, "client", "server"),
        (
            "pyproject-client.toml",
            ["--generate-server", "fastapi", "--server-output", "server", "--server-package", "server"],
            "server",
            "client",
        ),
    ],
    ids=["client-over-server", "server-over-client"],
)
def test_client_cli_selector_replaces_pyproject(
    pyproject: str,
    arguments: list[str],
    written: str,
    unwritten: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generate the target the command line selects instead of the one pyproject.toml selects."""
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path, pyproject)
    run_main_with_args(
        [*arguments, "--server-model-package", "models"] if written == "server" else arguments, capsys=capsys
    )
    assert_output(
        f"{written} {(tmp_path / written).is_dir()}\n{unwritten} {(tmp_path / unwritten).exists()}\n",
        EXPECTED / "cli" / f"selected-{written}.txt",
    )


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ([*OPTIONS, "--client-signature-style", "unpack"], "--client-signature-style requires --generate-client"),
        (
            [*OPTIONS, *PACKAGES, "--client-protocols", "{}"],
            "--client-package, --client-model-package and --client-protocols require --generate-client",
        ),
        (
            [*OPTIONS, "--generate-server", "fastapi", "--client-output", "client"],
            "--client-output requires --generate-client",
        ),
        (
            [*OPTIONS, "--generate-client", "httpx2"],
            "--generate-client requires --client-output, --client-package and --client-model-package",
        ),
        (
            [*OPTIONS, "--generate-client", "httpx2", "--client-package", "client"],
            "--generate-client requires --client-output and --client-model-package",
        ),
        (
            [*OPTIONS, *CLIENT, "--server-layout", "single"],
            "--server-layout requires --generate-server",
        ),
    ],
    ids=[
        "client-option",
        "client-options",
        "server-selected",
        "no-client-settings",
        "missing-settings",
        "server-option",
    ],
)
def test_client_cli_usage(
    arguments: list[str], message: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refuse client options without the client, and a client without the settings it requires."""
    run_main_and_assert(
        input_path=CLI / "options.yaml",
        output_path=tmp_path / "models.py",
        input_file_type="openapi",
        extra_args=arguments,
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr=f"Error: {message}\n",
        output_should_not_exist=True,
    )


def test_client_cli_both_selectors(capsys: pytest.CaptureFixture[str]) -> None:
    """Refuse a command line that selects the server and the client together."""
    run_main_with_system_exit(
        ["--input", "options.yaml", "--output", "models.py", "--generate-server", "fastapi", *CLIENT],
        expected_code=2,
        capsys=capsys,
        expected_stderr_contains="argument --generate-client: not allowed with argument --generate-server\n",
    )


@pytest.mark.parametrize(
    ("pyproject", "stderr"),
    [
        (
            "pyproject-both.toml",
            "--generate-server and --generate-client cannot be used together\n",
        ),
        ("pyproject-bogus.toml", "Invalid configuration: 1 validation error for Config\nclient_signature_style\n"),
        ("pyproject-invalid.toml", "Invalid --client-protocols: Input should be a valid dictionary\n"),
    ],
    ids=["both", "unselected", "protocols"],
)
def test_client_cli_invalid_pyproject(
    pyproject: str, stderr: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse invalid client keys of pyproject.toml like invalid model keys, and both targets selected together."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("options.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=OPTIONS,
        copy_files=_inputs(tmp_path, pyproject),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=stderr,
        output_should_not_exist=True,
    )


def _payload(result: dict[str, Any], root: Path) -> list[str]:
    """Describe a generation payload and whether each file it reports matches the published file.

    The copied runtime modules are reported together.
    """
    matches = {item["path"]: item["content"] == (root / item["path"]).read_text("utf-8") for item in result["files"]}
    runtime = [matched for path, matched in matches.items() if "_runtime" in Path(path).parts]
    return [
        f"kind {result['kind']}; output {result['output']}",
        *(
            f"file {path}; matches published {matched}"
            for path, matched in matches.items()
            if "_runtime" not in Path(path).parts
        ),
        f"runtime files match published {bool(runtime) and all(runtime)}",
    ]


def test_client_cli_generation_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report a client run, alone and as a job next to a server job, with the payload of a server run."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("options.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=[*OPTIONS, *CLIENT, "--output-format", "json"],
        copy_files=_inputs(tmp_path, "pyproject-shared-models-jobs.toml"),
    )
    single = capsys.readouterr()
    lines = [
        "$ datamodel-codegen --generate-client httpx2 --output-format json",
        *_payload(json.loads(single.out), tmp_path),
    ]
    run_main_with_args(["--all-jobs", "--output-format", "json"])
    jobs = capsys.readouterr()
    lines.append("$ datamodel-codegen --all-jobs --output-format json")
    for job in json.loads(jobs.out)["jobs"]:
        lines.extend((f"job {job['name']}", *_payload(job["result"], tmp_path)))
    assert_output(single.err, EXPECTED / "cli" / "dependencies.txt")
    assert_output(jobs.err, EXPECTED / "cli" / "shared-models-dependencies.txt")
    assert_output("\n".join(lines).replace(Path.cwd().as_posix(), "<root>") + "\n", EXPECTED / "cli" / "json.txt")


@pytest.mark.parametrize(
    ("arguments", "stderr"),
    [
        (["--watch", "--check"], "Error: --watch and --check cannot be used together\n"),
        (
            ["--update-lock", "--lockfile", "client/api.lock"],
            "Remote lock for 'command' ({lock}) overlaps client output for 'command': {client}\n",
        ),
    ],
    ids=["watch", "lock-in-client-output"],
)
def test_client_cli_conflicts(
    arguments: list[str],
    stderr: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse the model mode conflicts, and a remote lock inside the client output, as for the server."""
    monkeypatch.chdir(tmp_path)
    root = tmp_path.resolve()
    run_main_and_assert(
        input_path=Path("options.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=[*OPTIONS, *CLIENT, *arguments],
        copy_files=_inputs(tmp_path),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr=stderr.format(lock=root / "client" / "api.lock", client=root / "client"),
        output_should_not_exist=True,
    )


def test_client_cli_job(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Write the models and the package of a client job as a single run does, check them, then check a drift."""
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path, "pyproject-jobs.toml")
    run_main_with_args(["--job", "client"], capsys=capsys, expected_stderr=DEPENDENCIES)
    assert_file_content(tmp_path / "models.py", "cli/options/models.py")
    assert_file_content(tmp_path / SYNC, "cli/options/_sync.py")
    run_main_with_args(["--job", "client", "--check"], capsys=capsys, assert_no_stderr=True)
    (tmp_path / SYNC).write_text("# edited\n", encoding="utf-8")
    run_main_with_args(
        ["--all-jobs", "--check"],
        expected_exit=Exit.DIFF,
        capsys=capsys,
        expected_stdout_path=EXPECTED / "cli" / "check-edited.txt",
        assert_no_stderr=True,
    )


def test_client_cli_shadowed_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Report a client module beside a package of its name, on the run that writes it and on an unchanged one.

    --check, which writes nothing, reports none.
    """
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path)
    (package := tmp_path / SYNC.removesuffix(".py")).mkdir(parents=True)
    (package / "__init__.py").touch()
    lines = []
    for run, options, expected_exit in (
        ("first", [], Exit.OK),
        ("unchanged", [], Exit.OK),
        ("check", ["--check"], Exit.DIFF),
    ):
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always", UserWarning)
            run_main_with_args(["--input", "options.yaml", *DOC_OPTIONS, *options], expected_exit=expected_exit)
        lines.append(f"# {run} run")
        lines.extend(
            f"{item.category.__name__}: {str(item.message).replace(tmp_path.as_posix(), '<root>')}" for item in recorded
        )
    assert_output("\n".join(lines) + "\n", EXPECTED / "cli" / "shadowed-module.txt")


def test_client_cli_shared_models_jobs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write the models a server job and a client job share once, next to the package of each job, then check them."""
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path, "pyproject-shared-models-jobs.toml")
    run_main_with_args(
        ["--all-jobs"],
        capsys=capsys,
        expected_stderr=(EXPECTED / "cli" / "shared-models-dependencies.txt").read_text(encoding="utf-8"),
    )
    assert_file_content(tmp_path / "models.py", "cli/options/models.py")
    assert_file_content(tmp_path / SYNC, "cli/options/_sync.py")
    assert_file_content(tmp_path / "server" / "services.py", "cli/shared-models-services.py")
    run_main_with_args(["--all-jobs", "--check"], capsys=capsys, assert_no_stderr=True)


@pytest.mark.parametrize(
    ("pyproject", "arguments", "stderr"),
    [
        (
            "pyproject-jobs.toml",
            ["--all-jobs", "--watch", "--check"],
            "Error: --watch and --check cannot be used together\n",
        ),
        (
            "pyproject-model-jobs.toml",
            ["--all-jobs", "--client-body-arguments", "both"],
            "Error: --client-body-arguments requires --generate-client\n",
        ),
        (
            "pyproject-overlap-jobs.toml",
            ["--all-jobs"],
            "Jobs 'models' (output: {schemas}) and 'client' (client output: {client}) have overlapping output paths\n",
        ),
        (
            "pyproject-differing-models-jobs.toml",
            ["--all-jobs"],
            "Error: could not publish batch output: {shared}: Jobs 'server' and 'client' generate different models\n",
        ),
        (
            "pyproject-jobs.toml",
            ["--all-jobs", "--update-lock", "--lockfile", "client/py.typed"],
            "Remote lock for 'client' ({lock}) overlaps client output for 'client': {client}\n",
        ),
    ],
    ids=["watch", "model-job-client-option", "overlap", "differing-models", "lock-in-client-output"],
)
def test_client_cli_jobs(
    pyproject: str,
    arguments: list[str],
    stderr: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse what a client job cannot honor, as for a server job, writing nothing.

    A server job and a client job can share a models output, which they must generate identically.
    """
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path, pyproject)
    root = tmp_path.resolve()
    run_main_with_args(
        arguments,
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr=stderr.format(
            schemas=root / "client" / "schemas.py",
            client=root / "client",
            shared=(Path.cwd() / "models.py").as_posix(),
            lock=root / "client" / "py.typed",
        ),
    )
    assert_output(
        "".join(f"{path.name}\n" for path in sorted(tmp_path.iterdir())), EXPECTED / "cli" / "jobs-unwritten.txt"
    )


def test_client_cli_api_scope_required(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse a client whose model options leave out the api scope, instead of adding it to the models."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("options.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=[*OPTIONS, "--openapi-scopes", "schemas", *CLIENT],
        copy_files=_inputs(tmp_path),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr="Error: --generate-client requires --openapi-scopes to include api\n",
        file_should_not_exist=[tmp_path / "models.py", tmp_path / "client"],
    )


@pytest.mark.parametrize(
    ("arguments", "stderr"),
    [
        (["--client-operations", '"all"'], "Invalid --client-operations: Input should be a valid dictionary"),
        (
            ["--client-operations", '{"/paths/~1pets/get": "list_all"}'],
            "Invalid --client-operations: /paths/~1pets/get: Input should be a valid dictionary",
        ),
        (
            ["--client-resource-names", '{"pets": 1}'],
            "Invalid --client-resource-names: pets: Input should be a valid string",
        ),
        (
            ["--client-protocols", "missing.json"],
            "Invalid JSON for --client-protocols: Expecting value: line 1 column 1 (char 0)",
        ),
        (["--client-protocols", "[]"], "Invalid --client-protocols: Input should be a valid dictionary"),
        (
            ["--client-protocols", "[" * 65 + "]" * 65],
            "Invalid JSON for --client-protocols: nests collections deeper than 64 levels",
        ),
    ],
    ids=["operations", "operation", "resource-names", "protocols-file", "protocols-object", "protocols-depth"],
)
def test_client_cli_json_errors(
    arguments: list[str],
    stderr: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse a JSON client option whose value does not have the documented shape, like a JSON model option."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("options.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=[*OPTIONS, *CLIENT, *arguments],
        copy_files=_inputs(tmp_path),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr=f"{stderr}\n",
        output_should_not_exist=True,
    )


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        (
            "operations",
            [
                "--client-operations",
                (
                    '{"/paths/~1pets/get": {"label": "pets", "runtime": {"retry": true}}, '
                    '"/paths/~1pets/post": {"parameter_names": {"limit": "size"}}, '
                    '"#/paths/~1pets/get": {"parameter_names": ["limit"]}, '
                    '"other.yaml#/paths/~1pets/get": {"body_field_names": {"application/json": "tag"}}, '
                    '"/paths/~1cats/get": {"runtime": {"idempotency": {"header": "Key"}}}, '
                    '"/paths/~1dogs/get": {"runtime": "fast"}, '
                    '"/paths/~1owls/get": {"runtime": null}, '
                    '"/paths/~1bats/get": {"parameter_names": null}, '
                    '"/paths/~1apes/get": {"body_field_names": null}}'
                ),
            ],
        ),
        (
            "values",
            [
                *("--client-resource-names", '{"pets": "Pets"}', "--client-default-base-url", "ftp://example.com"),
                "--client-operations",
                (
                    '{"/paths/~1pets/get": {"name": "List", "parameter_names": {"query:limit": "page size"}, '
                    '"runtime": {"success_statuses": [200], "accepted_content_encodings": ["br"]}}, '
                    '"/paths/~1pets/post": {"body_field_names": {"application/json": {"tag": "pet_tag"}}, '
                    '"runtime": {"idempotency": {"header_name": "bad name"}, "success_statuses": "302"}}}'
                ),
            ],
        ),
        (
            "protocols",
            [
                *("--client-protocols", '{"pets.all": {"kind": "pagination"}, "Bad": {"kind": "teleport"}}'),
                *("--client-operations", '{"/paths/~1pets/get": {"name": "list_all"}}'),
            ],
        ),
        (
            "references",
            [
                "--client-operations",
                '{"/paths/~1cats/get": {"name": "list_cats"}, "other.yaml#/paths/~1pets/get": {"name": "list_all"}}',
            ],
        ),
    ],
)
def test_client_cli_setting_errors(
    name: str, arguments: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report every client setting the client cannot use, before any file is written."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("options.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=[*OPTIONS, *CLIENT, *arguments],
        copy_files=_inputs(tmp_path),
        expected_exit=Exit.ERROR,
        output_should_not_exist=True,
    )
    assert_output(
        capsys.readouterr().err.replace(tmp_path.resolve().as_posix(), "<root>"),
        EXPECTED / "cli" / "setting-errors" / f"{name}.txt",
    )


@pytest.mark.parametrize("form", ["pyproject", "options"])
def test_client_cli_member_types(
    form: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report each wrong-typed member of an operation's settings, from pyproject.toml tables or the same options."""
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path, *(["pyproject-types.toml"] if form == "pyproject" else []))
    run_main_with_args(
        [] if form == "pyproject" else client_cli_arguments(CLI / "pyproject-types.toml"), expected_exit=Exit.ERROR
    )
    assert_output(capsys.readouterr().err, EXPECTED / "cli" / "setting-errors" / "types.txt")


@pytest.mark.parametrize(
    ("source", "arguments", "generated", "expected"),
    [
        pytest.param(
            "options.yaml",
            [],
            SYNC,
            "_sync.py",
            id="generate-client",
            marks=pytest.mark.cli_doc(
                options=["--generate-client"],
                option_description="""Generate an HTTPX2 client package for the models (experimental).

`--generate-client httpx2` generates the models of an OpenAPI document as usual and, in the same run, an HTTPX2
client package at `--client-output`: a `Client` and an `AsyncClient` with a resource for each tag and a method for
each operation. The models need the `api` scope (`--openapi-scopes schemas api`) and a target Python version of 3.11
or later. Like every client setting, it can also be set in `[tool.datamodel-codegen]` of pyproject.toml, here as
`generate-client = "httpx2"`. It cannot be used with `--generate-server`.""",
                input_schema=DOC_INPUT,
                cli_args=DOC_OPTIONS,
                golden_output=f"{DOC_OUTPUT}/_sync.py",
            ),
        ),
        pytest.param(
            "options.yaml",
            [],
            SYNC,
            "_sync.py",
            id="client-output",
            marks=pytest.mark.cli_doc(
                options=["--client-output"],
                option_description="""Write the client package to this directory (experimental).

`--client-output` is required with `--generate-client`. A path given on the command line is relative to the working
directory, and the `client-output` key of pyproject.toml is relative to the pyproject.toml directory, as for
`--output`.""",
                input_schema=DOC_INPUT,
                cli_args=DOC_OPTIONS,
                golden_output=f"{DOC_OUTPUT}/_sync.py",
            ),
        ),
        pytest.param(
            "options.yaml",
            [],
            SYNC,
            "_sync.py",
            id="client-package",
            marks=pytest.mark.cli_doc(
                options=["--client-package"],
                option_description="""Name the import path of the client package (experimental).

`--client-package` is required with `--generate-client`. The generated README and the dependency command a
generation prints name the package by it; the package imports its own modules relatively.""",
                input_schema=DOC_INPUT,
                cli_args=DOC_OPTIONS,
                golden_output=f"{DOC_OUTPUT}/_sync.py",
            ),
        ),
        pytest.param(
            "options.yaml",
            [],
            SYNC,
            "_sync.py",
            id="client-model-package",
            marks=pytest.mark.cli_doc(
                options=["--client-model-package"],
                option_description="""Name the import path of the models the client package imports (experimental).

`--client-model-package` is required with `--generate-client`, and names the module or package `--output`
generates.""",
                input_schema=DOC_INPUT,
                cli_args=DOC_OPTIONS,
                golden_output=f"{DOC_OUTPUT}/_sync.py",
            ),
        ),
        pytest.param(
            "options.yaml",
            ["--client-signature-style", "unpack"],
            SYNC,
            "signature-style/_sync.py",
            id="client-signature-style",
            marks=pytest.mark.cli_doc(
                options=["--client-signature-style"],
                option_description="""Declare the arguments of the operation methods (experimental).

`explicit` (the default) declares each argument as a keyword parameter; `unpack` declares one
`**kwargs: Unpack[TypedDict]` per operation, whose keys are the same arguments.""",
                input_schema=DOC_INPUT,
                cli_args=[*DOC_OPTIONS, "--client-signature-style", "unpack"],
                golden_output=f"{DOC_OUTPUT}/signature-style/_sync.py",
            ),
        ),
        pytest.param(
            "options.yaml",
            ["--client-body-arguments", "both"],
            SYNC,
            "body-arguments/_sync.py",
            id="client-body-arguments",
            marks=pytest.mark.cli_doc(
                options=["--client-body-arguments"],
                option_description="""Choose how operation methods take request bodies (experimental).

`body` (the default) takes the body as one `body` argument; `both` also takes the properties of an object body as
keyword arguments, so a call can pass either. The `body_arguments` of an operation in `--client-operations`
overrides it for that operation.""",
                input_schema=DOC_INPUT,
                cli_args=[*DOC_OPTIONS, "--client-body-arguments", "both"],
                golden_output=f"{DOC_OUTPUT}/body-arguments/_sync.py",
            ),
        ),
        pytest.param(
            "options.yaml",
            ["--client-resource-names", '{"pets": "animals"}'],
            "client/resources/animals/_sync.py",
            "resource-names/_sync.py",
            id="client-resource-names",
            marks=pytest.mark.cli_doc(
                options=["--client-resource-names"],
                option_description="""Name the resources of tags (experimental).

The JSON object, inline or in a file, maps a tag to the dotted namespace of the resource its operations join, such
as `{"pets": "store.pets"}` for `client.store.pets`. Other operations join the resource of their first tag. In
pyproject.toml, `client-resource-names` is a table, and a command-line value replaces the whole table.""",
                input_schema=DOC_INPUT,
                cli_args=[*DOC_OPTIONS, "--client-resource-names", '{"pets": "animals"}'],
                golden_output=f"{DOC_OUTPUT}/resource-names/_sync.py",
            ),
        ),
        pytest.param(
            "options.yaml",
            [
                "--client-operations",
                '{"/paths/~1pets/get": {"name": "list_all", "parameter_names": {"query:limit": "page_size"}}}',
            ],
            SYNC,
            "operations/_sync.py",
            id="client-operations",
            marks=pytest.mark.cli_doc(
                options=["--client-operations"],
                option_description="""Set the client settings of single operations (experimental).

The JSON object, inline or in a file, maps operation references to their settings. An operation reference is the
JSON pointer of the path item method, such as `/paths/~1pets/get`, optionally after a document and `#`, such as
`pets.yaml#/paths/~1pets/get`. A relative document resolves against the JSON file that holds the reference, or
without a file against the working directory, and against the pyproject.toml directory for a table. The settings are
`resource`, `name`, `parameter_names` (keyed by location and name,
such as `{"query:limit": "page_size"}`), `request_media_type`, `response_media_type`, `description`,
`body_arguments`, which overrides `--client-body-arguments`, `body_field_names` (keyed by media type, then property,
such as `{"application/json": {"petName": "pet_name"}}`), and `runtime`: `request_id_header`, `success_statuses`,
`retry_safety`, `idempotency` (`{"header_name": "Idempotency-Key"}`), `retry_after_ms_header`,
`should_retry_header`, `auth_challenge_less_401`, and `accepted_content_encodings`. In pyproject.toml,
`client-operations` is a table, and a command-line value replaces the whole table.""",
                input_schema=DOC_INPUT,
                cli_args=[
                    *DOC_OPTIONS,
                    "--client-operations",
                    '{"/paths/~1pets/get": {"name": "list_all", "parameter_names": {"query:limit": "page_size"}}}',
                ],
                golden_output=f"{DOC_OUTPUT}/operations/_sync.py",
            ),
        ),
        pytest.param(
            "options-unserved.yaml",
            ["--client-default-base-url", "https://api.example.com/v1"],
            "client/_operations.py",
            "default-base-url/_operations.py",
            id="client-default-base-url",
            marks=pytest.mark.cli_doc(
                options=["--client-default-base-url"],
                option_description="""Set the server URL of operations that declare no servers (experimental).

An absolute `http` or `https` URL without userinfo, query, or fragment. Operations whose document and path declare
servers keep them.""",
                input_schema="generation_platform/client/cli/options-unserved.yaml",
                cli_args=[*DOC_OPTIONS, "--client-default-base-url", "https://api.example.com/v1"],
                golden_output=f"{DOC_OUTPUT}/default-base-url/_operations.py",
            ),
        ),
        pytest.param(
            "options-relative.yaml",
            ["--client-server-base-url", "https://api.example.com/"],
            "client/_operations.py",
            "server-base-url/_operations.py",
            id="client-server-base-url",
            marks=pytest.mark.cli_doc(
                options=["--client-server-base-url"],
                option_description="""Resolve relative server URLs against this base URL (experimental).

An absolute `http` or `https` URL without userinfo, query, or fragment. Without it, relative server URLs resolve
against the document's URL when it is read from one.""",
                input_schema="generation_platform/client/cli/options-relative.yaml",
                cli_args=[*DOC_OPTIONS, "--client-server-base-url", "https://api.example.com/"],
                golden_output=f"{DOC_OUTPUT}/server-base-url/_operations.py",
            ),
        ),
        pytest.param(
            "options.yaml",
            ["--client-protocols", "protocols.json"],
            "client/protocols/_helpers.py",
            "protocols/_helpers.py",
            id="client-protocols",
            marks=pytest.mark.cli_doc(
                options=["--client-protocols"],
                option_description="""Declare the protocol helpers of the client package (experimental).

The JSON object, inline or in a file, maps each helper's name to the definition of a pagination, polling, stream,
WebSocket, cache, upload, or webhook helper, as the Python client guide describes. Relative documents that its
references name resolve against the directory of the JSON file that holds them, on the command line and for the
`client-protocols` key alike. For inline JSON they resolve against the working directory, and for a table of the
`client-protocols` key against the pyproject.toml directory.""",
                input_schema=DOC_INPUT,
                cli_args=[*DOC_OPTIONS, "--client-protocols", "protocols.json"],
                golden_output=f"{DOC_OUTPUT}/protocols/_helpers.py",
            ),
        ),
    ],
)
def test_client_cli_options(
    source: str, arguments: list[str], generated: str, expected: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write the client file each client option changes."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path(source),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=[*OPTIONS, *CLIENT, *arguments],
        copy_files=[(CLI / source, tmp_path / source), (CLI / "protocols.json", tmp_path / "protocols.json")],
    )
    assert_file_content(tmp_path / generated, f"cli/options/{expected}")
