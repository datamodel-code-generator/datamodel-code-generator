"""Tests for --recommend-options: schema analysis and rule-based option recommendations."""

from __future__ import annotations

import json
import shutil
from importlib.resources import files
from string import Formatter
from typing import TYPE_CHECKING, Any

import pytest
from packaging.version import Version

from datamodel_code_generator import chdir, get_version
from datamodel_code_generator.__main__ import Config, Exit
from datamodel_code_generator.arguments import arg_parser
from datamodel_code_generator.config import GenerateConfig
from tests.conftest import MockHttpxResponse
from tests.main.conftest import DATA_PATH, EXPECTED_MAIN_PATH, run_main_and_assert, run_main_with_args

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from tests.conftest import HttpxGetMockFactory

EXPECTED_PATH = EXPECTED_MAIN_PATH / "recommend_options"
INPUT_PATH = DATA_PATH / "recommend_options"
RULES_FILE = "src/datamodel_code_generator/resources/recommender_rules.json"
CATEGORIES = (
    "io, target, preset, style, runtime-behaviour, formatting, compat, debug, platform, command, or pending "
    "(pending also needs follow_up and release)"
)


def _rules_data() -> dict[str, Any]:
    return json.loads(files("datamodel_code_generator.resources").joinpath("recommender_rules.json").read_text())


def _recommend(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    args: Sequence[str],
    expected: str,
    *,
    inputs: Sequence[Path] = (),
    expected_exit: Exit = Exit.OK,
) -> None:
    """Run the CLI from a project directory holding copies of the inputs, so paths in the output are relative."""
    for source in inputs:
        shutil.copy(source, tmp_path / source.name)
    with chdir(tmp_path):
        run_main_with_args(
            [*args, "--recommend-options"],
            expected_exit=expected_exit,
            capsys=capsys,
            expected_stdout_path=EXPECTED_PATH / expected,
            use_builtin_default_formatter=False,
        )


@pytest.mark.cli_doc(
    options=["--recommend-options"],
    option_description="""Recommend options for the input schema on top of the latest dated preset (experimental).

`--recommend-options` reads the input schema, measures how it is written (shared property names, inline
enums and objects, repeated shapes, titles, tagged unions, dotted keys), and applies the recommender's rule
data. It prints the latest dated preset to start from, the options to add with the reason for each and what
they changed on measured schemas, options it advises against, and the resulting command and pyproject.toml
block. Nothing is generated or written. Options set on the command line or in pyproject.toml are kept and
reported when the rules suggest another value. Add `--output-format json` for a structured payload.""",
    input_schema="recommend_options/api.yaml",
    cli_args=["--input", "api.yaml", "--input-file-type", "openapi", "--recommend-options"],
    expected_stdout="main/recommend_options/api.txt",
    related_options=["--preset", "--output-format", "--naming-strategy", "--enum-field-as-literal"],
)
def test_recommend_options(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Recommend options for the input schema on top of the latest dated preset (experimental).

    `--recommend-options` reads the input schema, measures how it is written (shared property names, inline
    enums and objects, repeated shapes, titles, tagged unions, dotted keys), and applies the recommender's rule
    data. It prints the latest dated preset to start from, the options to add with the reason for each and what
    they changed on measured schemas, options it advises against, and the resulting command and pyproject.toml
    block. Nothing is generated or written. Options set on the command line or in pyproject.toml are kept and
    reported when the rules suggest another value. Add `--output-format json` for a structured payload.
    """
    _recommend(
        tmp_path,
        capsys,
        ["--input", "api.yaml", "--input-file-type", "openapi"],
        "api.txt",
        inputs=[INPUT_PATH / "api.yaml"],
    )


@pytest.mark.parametrize(
    ("source", "args", "expected"),
    [
        pytest.param(INPUT_PATH / "api.yaml", ["--output-format", "json"], "api_json.txt", id="json"),
        pytest.param(INPUT_PATH / "titled_variants.yaml", [], "titled_variants.txt", id="practical-preset"),
        pytest.param(INPUT_PATH / "union_edges.yaml", [], "union_edges.txt", id="union-edges"),
        pytest.param(INPUT_PATH / "service-config.json", [], "service_config.txt", id="jsonschema"),
        pytest.param(
            INPUT_PATH / "service-config.json",
            ["--naming-strategy", "full-path"],
            "service_config_naming_set.txt",
            id="jsonschema-contested-option-set",
        ),
        pytest.param(INPUT_PATH / "events.yaml", [], "asyncapi.txt", id="asyncapi"),
        pytest.param(
            INPUT_PATH / "api.yaml",
            ["--use-title-as-name", "--enum-field-as-literal", "one"],
            "api_explicit_options.txt",
            id="explicit-options",
        ),
        pytest.param(
            INPUT_PATH / "api.yaml", ["--preset", "practical-py314-20261017"], "api_practical.txt", id="preset-value"
        ),
        pytest.param(
            INPUT_PATH / "api.yaml", ["--preset", "standard-py314-20261015"], "api_older_preset.txt", id="older-preset"
        ),
        pytest.param(
            INPUT_PATH / "api.yaml",
            ["--output-model-type", "typing.TypedDict"],
            "api_typed_dict.txt",
            id="typed-dict",
        ),
    ],
)
def test_recommend_options_for_schemas(
    source: Path, args: list[str], expected: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Recommend options for each supported schema format, keeping the options the command sets."""
    extra = [] if "--preset" in args else ["--target-python-version", "3.14"]
    _recommend(tmp_path, capsys, ["--input", source.name, *extra, *args], expected, inputs=[source])


@pytest.mark.parametrize(
    ("inputs", "args", "expected"),
    [
        pytest.param(
            [DATA_PATH / "graphql" / "simple-star-wars.graphql"],
            ["--input", "simple-star-wars.graphql", "--input-file-type", "graphql"],
            "graphql.txt",
            id="graphql",
        ),
        pytest.param([], ["--input", "."], "directory.txt", id="directory"),
        pytest.param(
            [],
            ["--input-model", "models.py:Pet", "--target-python-version", "3.12"],
            "input_model.txt",
            id="input-model",
        ),
    ],
)
def test_recommend_options_without_schema_rules(
    inputs: list[Path], args: list[str], expected: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Recommend only the latest preset for inputs the schema rules do not analyse."""
    _recommend(tmp_path, capsys, args, expected, inputs=inputs)


def test_recommend_options_from_stdin(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Analyse a schema read from stdin; without a file name, the root class name is left to the user."""
    run_main_and_assert(
        stdin_path=INPUT_PATH / "stdin.json",
        monkeypatch=monkeypatch,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--recommend-options"],
        capsys=capsys,
        expected_stdout_path=EXPECTED_PATH / "stdin.txt",
    )


def test_recommend_options_from_url(
    mock_httpx_get: HttpxGetMockFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Read a remote schema once; credentials in headers and the URL are not repeated in the recommendation."""
    url = "https://api.example.com/specs/api.yaml?token=secret"
    mock_httpx_get(MockHttpxResponse(url, INPUT_PATH / "api.yaml"))
    _recommend(
        tmp_path,
        capsys,
        ["--url", url, "--http-headers", "Authorization: Bearer secret", "--target-python-version", "3.14"],
        "url.txt",
    )


@pytest.mark.parametrize(
    ("project", "args", "expected"),
    [
        pytest.param("project_changes", [], "project_changes.txt", id="changes"),
        pytest.param("project_current", [], "project_current.txt", id="current"),
        pytest.param(
            "project_changes",
            ["--preset", "standard-py314-20261015"],
            "project_preset_argument.txt",
            id="preset-argument",
        ),
        pytest.param("project_profile", ["--profile", "api"], "project_profile.txt", id="profile"),
        pytest.param("project_job", ["--job", "api"], "project_job.txt", id="job"),
        pytest.param(
            "project_changes",
            ["--preset", "standard-py314-20261015", "--output-format", "json"],
            "project_preset_argument_json.txt",
            id="preset-argument-json",
        ),
    ],
)
def test_recommend_options_for_projects(
    project: str, args: list[str], expected: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Recommend changes to the pyproject.toml table the command reads, keeping what it already sets."""
    _recommend(
        tmp_path, capsys, args, expected, inputs=[INPUT_PATH / "api.yaml", INPUT_PATH / project / "pyproject.toml"]
    )


@pytest.mark.parametrize(
    ("args", "expected_stderr"),
    [
        pytest.param(["--check", "--output", "model.py"], "--check", id="check"),
        pytest.param(["--watch"], "--watch", id="watch"),
        pytest.param(["--diff-against", "old.yaml", "--output", "model.py"], "--diff-against", id="diff-against"),
        pytest.param(["--all-jobs"], "--all-jobs", id="all-jobs"),
        pytest.param(["--job", "a", "--job", "b"], "more than one --job", id="jobs"),
        pytest.param(["--generate-pyproject-config"], "--generate-pyproject-config", id="pyproject-config"),
        pytest.param(["--generate-cli-command"], "--generate-cli-command", id="cli-command"),
    ],
)
def test_recommend_options_rejects_other_commands(
    args: list[str], expected_stderr: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reject options that check, watch, or generate something else, since a recommendation writes nothing."""
    run_main_with_args(
        ["--input", "api.yaml", *args, "--recommend-options"],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr=f"Error: --recommend-options cannot be used with {expected_stderr}\n",
        use_builtin_default_formatter=False,
    )


@pytest.mark.parametrize(
    ("args", "expected_stderr"),
    [
        pytest.param(
            ["--input", "missing.yaml"],
            "Unable to read the input missing.yaml: No such file or directory\n",
            id="missing",
        ),
        pytest.param(
            ["--input", str(INPUT_PATH / "list.yaml"), "--input-file-type", "openapi"],
            "Unable to read the input as a openapi document: Expected dict, got list\n",
            id="not-a-mapping",
        ),
    ],
)
def test_recommend_options_reports_unreadable_inputs(
    args: list[str], expected_stderr: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Report an input the recommender cannot read and exit with an error."""
    with chdir(tmp_path):
        run_main_with_args(
            [*args, "--recommend-options"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr=expected_stderr,
            use_builtin_default_formatter=False,
        )


def test_recommend_options_reports_invalid_yaml(capsys: pytest.CaptureFixture[str]) -> None:
    """Report a schema that is not valid YAML or JSON."""
    run_main_with_args(
        ["--input", str(INPUT_PATH / "invalid.yaml"), "--input-file-type", "openapi", "--recommend-options"],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Unable to read the input as a openapi document: ",
        use_builtin_default_formatter=False,
    )


def test_recommend_options_json_schema(capsys: pytest.CaptureFixture[str]) -> None:
    """Print the JSON Schema of the --recommend-options --output-format json payload."""
    run_main_with_args(
        ["--output-format-json-schema", "recommend-options"],
        capsys=capsys,
        expected_stdout_path=EXPECTED_PATH / "json_schema.txt",
        use_builtin_default_formatter=False,
    )


def _option_fields() -> set[str]:
    """Return every configuration field and CLI option destination the recommender registry must cover."""
    cli_fields = {name for name, field in Config.model_fields.items() if not field.exclude}
    destinations = {action.dest for action in arg_parser._actions if action.option_strings}
    return cli_fields | set(GenerateConfig.model_fields) | destinations


@pytest.mark.allow_direct_assert("fails with the registry entries a new or removed option needs")
def test_every_option_is_registered_with_the_recommender() -> None:
    """Every option must be registered with the option recommender, by rule ids or a not-recommended category."""
    registered = set(_rules_data()["options"])
    fields = _option_fields()
    if missing := sorted(fields - registered):
        pytest.fail(
            f"Options missing from the recommender registry: {', '.join(missing)}.\n"
            f'Add each to "options" in {RULES_FILE}: {{"rules": ["<rule id>"]}} when a rule recommends it, or '
            f'{{"not_recommended": "<category>", "reason": "<why the schema does not decide it>"}} with category '
            f"{CATEGORIES}. See docs/recommend-options.md."
        )
    if stale := sorted(registered - fields):
        pytest.fail(
            f'Registry entries without an option: {", ".join(stale)}. Remove them from "options" in {RULES_FILE}.'
        )


def _rule_problems(data: dict[str, Any], placeholders: set[str]) -> list[str]:
    """Return the inconsistencies between the rules and the option registry."""
    options = data["options"]
    problems: list[str] = []
    decisions: dict[str, str] = {}
    rule_fields = {rule["id"]: {*rule.get("set", {}), *rule.get("value_from", {})} for rule in data["rules"]}
    for rule in data["rules"]:
        for field in sorted(rule_fields[rule["id"]]):
            if rule["id"] not in options.get(field, {}).get("rules", ()):
                problems.append(f"rule {rule['id']} sets {field}; list the rule in options.{field}.rules")
            if decisions.setdefault(field, rule["decision"]) != rule["decision"]:
                problems.append(f"{field} is decided by both {decisions[field]} and {rule['decision']}")
        texts = [rule["reason"], rule.get("contested_reason", ""), *rule.get("caveats", ())]
        if unknown := sorted(
            {name for text in texts for _, name, _, _ in Formatter().parse(text) if name} - placeholders
        ):
            problems.append(f"rule {rule['id']} uses unknown placeholders {unknown}")
    for field, entry in options.items():
        problems.extend(
            f"options.{field} lists rule {rule_id}, which does not set it"
            for rule_id in entry.get("rules", ())
            if field not in rule_fields.get(rule_id, set())
        )
        if entry.get("not_recommended") == "pending" and not (entry.get("follow_up") and entry.get("release")):
            problems.append(f"options.{field} is pending without follow_up and release")
        elif entry.get("not_recommended") == "pending" and Version(get_version()) > Version(entry["release"]):
            problems.append(f"options.{field} is still pending after {entry['release']}; add its rule or a category")
    return problems


@pytest.mark.allow_direct_assert("fails with the inconsistencies between the rule data and the option registry")
def test_recommender_rules_match_the_option_registry(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Rules only set registered options, registry entries name the rules that set them, and texts use real signals."""
    shutil.copy(INPUT_PATH / "api.yaml", tmp_path / "api.yaml")
    with chdir(tmp_path):
        run_main_with_args(
            ["--input", "api.yaml", "--output-format", "json", "--recommend-options"],
            use_builtin_default_formatter=False,
        )
    payload = json.loads(capsys.readouterr().out)
    if problems := _rule_problems(_rules_data(), {*payload["signals"], *payload["facts"]}):
        pytest.fail(f"Fix {RULES_FILE}:\n" + "\n".join(f"  - {problem}" for problem in problems))
