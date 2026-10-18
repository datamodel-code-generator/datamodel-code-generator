"""Tests for --recommend-options: schema analysis, rule-based option recommendations, and trial naming."""

from __future__ import annotations

import difflib
import io
import json
import re
import shutil
import warnings
from contextlib import redirect_stdout
from importlib.resources import files
from pathlib import Path
from string import Formatter
from typing import TYPE_CHECKING, Any

import jsonschema
import pytest
from packaging.version import Version

from datamodel_code_generator import NamingHintWarning, chdir, generate
from datamodel_code_generator.__main__ import Config, Exit
from datamodel_code_generator.arguments import arg_parser
from datamodel_code_generator.config import GenerateConfig
from datamodel_code_generator.experimental import EXPERIMENTAL_FEATURES
from tests.conftest import MockHttpxResponse, assert_httpx_get_kwargs, assert_output
from tests.main.conftest import (
    DATA_PATH,
    EXPECTED_MAIN_PATH,
    run_main_and_assert,
    run_main_with_args,
    run_main_with_system_exit,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from tests.conftest import HttpxGetMockFactory

EXPECTED_PATH = EXPECTED_MAIN_PATH / "recommend_options"
INPUT_PATH = DATA_PATH / "recommend_options"
CHANGELOG = Path(__file__).parents[2] / "CHANGELOG.md"
RECOMMEND_DOCS = Path(__file__).parents[2] / "docs" / "recommend-options.md"
GRAPHQL_INPUT = DATA_PATH / "graphql" / "simple-star-wars.graphql"
RULES_FILE = "src/datamodel_code_generator/resources/recommender_rules.json"
REGISTRY_GUIDE = (
    f'Every option needs an entry in "options" of {RULES_FILE}: {{"rules": ["<rule id>"]}} when a rule sets it, '
    'or {"not_recommended": "<category>", "reason": "<why the schema does not decide it>"} with category io, '
    "target, preset, style, runtime-behaviour, formatting, compat, debug, platform, command, or pending (pending "
    "also needs follow_up and release). See Adding a New CLI Option in docs/development-contributing.md."
)


def _rules_data() -> dict[str, Any]:
    return json.loads(files("datamodel_code_generator.resources").joinpath("recommender_rules.json").read_text())


def _inputs(*paths: Path) -> dict[str, Path]:
    return {path.name: path for path in paths}


def _recommend(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    args: Sequence[str],
    expected: str,
    *,
    inputs: Mapping[str, Path] | None = None,
    mode: Sequence[str] = ("quick",),
    expected_stderr: str | None = None,
) -> None:
    """Run the CLI from a project directory holding copies of the inputs, so paths in the output are relative.

    ``mode`` follows --recommend-options; an empty one runs the command without a mode. Without ``expected_stderr``,
    nothing may be printed to stderr.
    """
    for name, source in (inputs or {}).items():
        shutil.copy(source, tmp_path / name)
    with chdir(tmp_path):
        run_main_with_args(
            [*args, "--recommend-options", *mode],
            capsys=capsys,
            expected_stdout_path=EXPECTED_PATH / expected,
            expected_stderr=expected_stderr,
            assert_no_stderr=expected_stderr is None,
            use_builtin_default_formatter=False,
        )


@pytest.mark.cli_doc(
    options=["--recommend-options"],
    option_description="""Recommend options for the input schema on top of the latest dated preset (experimental).

`--recommend-options` reads the input schema, measures how it is written (shared property names, inline
enums and objects, repeated shapes, titles, tagged unions, dotted keys, constraint keywords and formats), and
applies the recommender's rule data. For a schema of up to 3000 estimated models, it then generates the
candidate configurations in memory (the preset, the recommendation, and the recommendation without or with
each option), compares their class names and the fields generated without their constraints, and recommends
the best candidate, with class names before and after each option. It prints the latest dated preset to
start from, the options to add with the reason for each, optional validation and policy options that stay out
of the command, options it advises against, the compared candidates, and the resulting command and
pyproject.toml block. Nothing is written. Options set on the command line or in pyproject.toml are kept and
reported when the rules suggest another value. Add `--output-format json` for a structured payload, or
`--generate-prompt` to include the recommendation in an LLM prompt instead.""",
    input_schema="recommend_options/api.yaml",
    cli_args=["--input", "api.yaml", "--input-file-type", "openapi", "--recommend-options"],
    expected_stdout="main/recommend_options/api.txt",
    related_options=[
        "--preset",
        "--output-format",
        "--generate-prompt",
        "--naming-strategy",
        "--enum-field-as-literal",
    ],
)
def test_recommend_options(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Recommend options for the input schema on top of the latest dated preset (experimental).

    `--recommend-options` reads the input schema, measures how it is written (shared property names, inline
    enums and objects, repeated shapes, titles, tagged unions, dotted keys, constraint keywords and formats), and
    applies the recommender's rule data. For a schema of up to 3000 estimated models, it then generates the
    candidate configurations in memory (the preset, the recommendation, and the recommendation without or with
    each option), compares their class names and the fields generated without their constraints, and recommends
    the best candidate, with class names before and after each option. It prints the latest dated preset to
    start from, the options to add with the reason for each, optional validation and policy options that stay out
    of the command, options it advises against, the compared candidates, and the resulting command and
    pyproject.toml block. Nothing is written. Options set on the command line or in pyproject.toml are kept and
    reported when the rules suggest another value. Add `--output-format json` for a structured payload, or
    `--generate-prompt` to include the recommendation in an LLM prompt instead.
    """
    _recommend(
        tmp_path,
        capsys,
        ["--input", "api.yaml", "--input-file-type", "openapi"],
        "api.txt",
        inputs=_inputs(INPUT_PATH / "api.yaml"),
        mode=(),
    )


@pytest.mark.parametrize(
    ("source", "args", "expected"),
    [
        pytest.param("api.yaml", ["--output-format", "json"], "api_json.txt", id="json"),
        pytest.param("union_edges.yaml", [], "union_edges.txt", id="union-edges"),
        pytest.param("service-config.json", [], "service_config.txt", id="jsonschema"),
        pytest.param(
            "service-config.json",
            ["--naming-strategy", "full-path"],
            "service_config_naming_set.txt",
            id="jsonschema-contested-option-set",
        ),
        pytest.param("order.json", [], "order.txt", id="root-name-taken"),
        pytest.param("events.yaml", [], "asyncapi.txt", id="asyncapi"),
        pytest.param("events_v3.yaml", [], "asyncapi_v3.txt", id="asyncapi-v3"),
        pytest.param("swagger.yaml", ["--input-file-type", "openapi"], "swagger.txt", id="swagger"),
        pytest.param(
            "swagger.yaml",
            ["--input-file-type", "openapi", "--output-format", "json"],
            "swagger_json.txt",
            id="swagger-json",
        ),
        pytest.param(
            "swagger.yaml", ["--input-file-type", "jsonschema"], "swagger_jsonschema.txt", id="swagger-definitions"
        ),
        pytest.param(
            "operations.yaml", ["--openapi-scopes", "schemas", "paths"], "operations_paths.txt", id="paths-scope"
        ),
        pytest.param(
            "operations.yaml",
            [
                "--openapi-scopes",
                "paths",
                "parameters",
                "webhooks",
                "requestbodies",
                "--openapi-include-paths",
                "/pets*",
            ],
            "operations.txt",
            id="openapi-scopes",
        ),
        pytest.param(
            "api.yaml",
            ["--use-title-as-name", "--enum-field-as-literal", "one"],
            "api_explicit_options.txt",
            id="explicit-options",
        ),
        pytest.param("api.yaml", ["--preset", "practical-py314-20261017"], "api_practical.txt", id="preset-value"),
        pytest.param("api.yaml", ["--preset", "standard-py314-20261015"], "api_older_preset.txt", id="older-preset"),
        pytest.param("api.yaml", ["--output-model-type", "typing.TypedDict"], "api_typed_dict.txt", id="typed-dict"),
        pytest.param("validation.yaml", [], "validation.txt", id="validation-keywords"),
        pytest.param(
            "validation.yaml", ["--output-format", "json"], "validation_json.txt", id="validation-keywords-json"
        ),
        pytest.param(
            "validation.yaml",
            ["--strict-types", "int", "--generate-schema-validators"],
            "validation_options_set.txt",
            id="validation-options-set",
        ),
        pytest.param("deployment.json", [], "deployment.txt", id="pattern-properties"),
        pytest.param("settings.json", [], "settings.txt", id="draft-07-pattern-map"),
    ],
)
def test_recommend_options_for_schemas(
    source: str, args: list[str], expected: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Recommend options for each supported schema format, keeping the options the command sets.

    Optional validation and policy options are listed apart from the command, unless the command sets them.
    Keywords beside a ``$ref`` count where the document's version applies them (not in draft-07), and a
    patternProperties map with one pattern and no other keys needs no validators.
    """
    extra = [] if "--preset" in args else ["--target-python-version", "3.14"]
    _recommend(tmp_path, capsys, ["--input", source, *extra, *args], expected, inputs=_inputs(INPUT_PATH / source))


def test_recommend_options_quotes_arguments(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Quote file names in the printed command, and list the arguments of the JSON payload as they are."""
    _recommend(
        tmp_path,
        capsys,
        ["--input", "schema's.yaml", "--output-format", "json"],
        "quoted_input_json.txt",
        inputs={"schema's.yaml": INPUT_PATH / "titled_variants.yaml"},
    )


def test_recommend_options_rejects_unknown_modes(capsys: pytest.CaptureFixture[str]) -> None:
    """Reject a mode other than quick and full; the option is meant to be used without a value."""
    run_main_with_system_exit(
        ["--input", "api.yaml", "--recommend-options", "fast"],
        expected_code=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=(
            "argument --recommend-options: unknown mode 'fast'; use --recommend-options without a value"
        ),
    )


@pytest.mark.parametrize(
    ("inputs", "args", "expected"),
    [
        pytest.param(
            _inputs(DATA_PATH / "graphql" / "simple-star-wars.graphql"),
            ["--input", "simple-star-wars.graphql", "--input-file-type", "graphql"],
            "graphql.txt",
            id="graphql",
        ),
        pytest.param({}, ["--input", "."], "directory.txt", id="directory"),
        pytest.param(
            {},
            ["--input-model", "models.py:Pet", "--target-python-version", "3.12"],
            "input_model.txt",
            id="input-model",
        ),
    ],
)
def test_recommend_options_without_schema_rules(
    inputs: dict[str, Path], args: list[str], expected: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Recommend only the latest preset for inputs the schema rules do not analyse."""
    _recommend(tmp_path, capsys, args, expected, inputs=inputs)


def test_recommend_options_from_stdin(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Analyse a schema read from stdin; without a file name, the root class name is left to the user."""
    run_main_and_assert(
        stdin_path=INPUT_PATH / "stdin.json",
        monkeypatch=monkeypatch,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--recommend-options", "quick"],
        capsys=capsys,
        expected_stdout_path=EXPECTED_PATH / "stdin.txt",
    )


def test_recommend_options_needs_an_input(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Ask for an input instead of waiting on an interactive terminal, as generation does."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    run_main_with_args(
        ["--recommend-options"],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Not Found Input: require `stdin` or arguments `--input`, `--url`, or `--input-model`",
        use_builtin_default_formatter=False,
    )


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        pytest.param([], "url.txt", id="no-lock"),
        pytest.param(["--update-lock"], "url_update_lock.txt", id="update-lock"),
    ],
)
def test_recommend_options_from_url(
    args: list[str],
    expected: str,
    mock_httpx_get: HttpxGetMockFactory,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Read a remote schema once, under the remote lock policy; credentials are not repeated in the result."""
    url = "https://api.example.com/specs/api.yaml?token=secret"
    mock_httpx_get(MockHttpxResponse(url, INPUT_PATH / "api.yaml"))
    _recommend(
        tmp_path,
        capsys,
        ["--url", url, "--http-headers", "Authorization: Bearer secret", "--target-python-version", "3.14", *args],
        expected,
    )


def test_recommend_options_requires_the_remote_lock(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Refuse a remote schema with --locked when the lock is missing, as generation does."""
    with chdir(tmp_path):
        run_main_with_args(
            ["--url", "https://api.example.com/specs/api.yaml", "--locked", "--recommend-options"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains="Remote lock file not found: ",
            use_builtin_default_formatter=False,
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
        pytest.param(
            "project_profile_unicode", ["--profile", "ëu"], "project_profile_unicode.txt", id="unicode-profile"
        ),
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
        tmp_path,
        capsys,
        args,
        expected,
        inputs=_inputs(INPUT_PATH / "api.yaml", INPUT_PATH / project / "pyproject.toml"),
    )


@pytest.mark.parametrize(
    ("args", "expected_stderr"),
    [
        pytest.param(["--check", "--output", "model.py"], "--check", id="check"),
        pytest.param(["--watch"], "--watch", id="watch"),
        pytest.param(
            ["--diff-against", "old.yaml", "--output", "model.py"], "--diff-against in quick mode", id="diff-against"
        ),
        pytest.param(["--all-jobs"], "--all-jobs", id="all-jobs"),
        pytest.param(["--job", "a", "--job", "b"], "more than one --job", id="jobs"),
        pytest.param(["--generate-pyproject-config"], "--generate-pyproject-config", id="pyproject-config"),
        pytest.param(["--generate-cli-command"], "--generate-cli-command", id="cli-command"),
        pytest.param(["--install-skill", "codex"], "--install-skill", id="install-skill"),
        pytest.param(["--list-deprecations"], "--list-deprecations", id="list-deprecations"),
        pytest.param(["--list-experimental"], "--list-experimental", id="list-experimental"),
    ],
)
def test_recommend_options_rejects_other_commands(
    args: list[str], expected_stderr: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reject options that check, watch, list, install, or generate something else; a recommendation prints only.

    Quick mode generates nothing, so it has no use for a previous version of the input either.
    """
    run_main_with_args(
        ["--input", "api.yaml", *args, "--recommend-options", "quick"],
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
            "Unable to read the input as an OpenAPI document: Expected dict, got list\n",
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
        expected_stderr_contains="Unable to read the input as an OpenAPI document: ",
        use_builtin_default_formatter=False,
    )


def _printed(args: Sequence[str], capsys: pytest.CaptureFixture[str]) -> str:
    """Run the CLI and return what it printed to stdout; nothing may be printed to stderr."""
    with redirect_stdout(io.StringIO()) as stdout:
        run_main_with_args(args, capsys=capsys, assert_no_stderr=True, use_builtin_default_formatter=False)
    return stdout.getvalue()


@pytest.mark.parametrize(
    ("args", "inputs"),
    [
        pytest.param(["--input", "api.yaml", "--input-file-type", "openapi"], ("api.yaml",), id="text"),
        pytest.param(
            ["--input", "api.yaml", "--input-file-type", "openapi", "--output-format", "json"], ("api.yaml",), id="json"
        ),
        pytest.param(["--job", "api"], ("api.yaml", "project_job/pyproject.toml"), id="job"),
        pytest.param(["--input", "backtick_titles.json"], ("backtick_titles.json",), id="backtick-titles"),
    ],
)
def test_recommend_options_in_prompt(
    args: list[str], inputs: tuple[str, ...], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With --generate-prompt, embed the recommendation in the prompt; the rest of the prompt stays the same.

    The text prompt shows the report before the options, fenced by more backticks than the schema's titles hold, and
    asks to start from its command; the JSON prompt adds the --recommend-options payload as ``recommendation``, valid
    against the prompt, structured-output and recommend-options schemas. The stored output is the difference from the
    prompt without --recommend-options, without line numbers, so that options added to the prompt do not move it.
    """
    for name in inputs:
        shutil.copy(INPUT_PATH / name, tmp_path / Path(name).name)
    prompt = [*args, "--generate-prompt", "Which options keep the class names stable?"]
    with chdir(tmp_path):
        plain = _printed(prompt, capsys)
        combined = _printed([*prompt, "--recommend-options", "quick"], capsys)
    if "json" in args:
        payload = json.loads(combined)
        for kind in ("generate-prompt", "structured-output"):
            jsonschema.validate(payload, json.loads(_printed(["--output-format-json-schema", kind], capsys)))
        schema = json.loads(_printed(["--output-format-json-schema", "recommend-options"], capsys))
        jsonschema.validate(payload["recommendation"], schema)
    diff = difflib.unified_diff(
        plain.splitlines(), combined.splitlines(), "--generate-prompt", "--recommend-options", lineterm=""
    )
    assert_output("".join("@@\n" if line.startswith("@@") else f"{line}\n" for line in diff))


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


def _listing(title: str, items: Iterable[str]) -> str:
    return f"{title}: {', '.join(sorted(items)) or 'none'}"


def test_every_option_is_registered_with_the_recommender() -> None:
    """Every option must be registered with the option recommender, by rule ids or a not-recommended category."""
    registered = set(_rules_data()["options"])
    fields = _option_fields()
    assert_output(
        "\n".join((
            _listing("Options missing from the recommender registry", fields - registered),
            _listing("Registry entries without an option", registered - fields),
            "",
            REGISTRY_GUIDE,
            "",
        )),
        EXPECTED_PATH / "registry_coverage.txt",
    )


def _newest_release() -> str:
    """Return the newest release CHANGELOG.md lists."""
    return re.findall(r"^## \[([^\]]+)\]", CHANGELOG.read_text(encoding="utf-8"), re.MULTILINE)[0]


def _rule_problems(data: dict[str, Any], placeholders: set[str], newest_release: str) -> list[str]:
    """Return the inconsistencies between the rules, the option registry, and the CLI options."""
    options = data["options"]
    rules = data["rules"]
    rule_fields = {rule["id"]: sorted({*rule.get("set", {}), *rule.get("value_from", {})}) for rule in rules}
    decisions = {
        field: sorted({rule["decision"] for rule in rules if field in rule_fields[rule["id"]]})
        for fields in rule_fields.values()
        for field in fields
    }
    option_strings = {action.dest: action.option_strings for action in arg_parser._actions}
    pending = {field: entry for field, entry in sorted(options.items()) if entry.get("not_recommended") == "pending"}
    return [
        *(
            f"rule {rule['id']} sets {field}; list the rule in options.{field}.rules"
            for rule in rules
            for field in rule_fields[rule["id"]]
            if rule["id"] not in options.get(field, {}).get("rules", ())
        ),
        *(
            f"rule {rule['id']} sets {field}, which has no --{field.replace('_', '-')} option"
            for rule in rules
            for field in rule_fields[rule["id"]]
            if f"--{field.replace('_', '-')}" not in option_strings.get(field, ())
        ),
        *(
            f"{field} is decided by {', '.join(names)}; keep it to one decision"
            for field, names in sorted(decisions.items())
            if len(names) > 1
        ),
        *(
            f"rule {rule['id']} uses the unknown placeholder {{{name}}}"
            for rule in rules
            for text in (rule["reason"], rule.get("contested_reason", ""), *rule.get("caveats", ()))
            for _, name, _, _ in Formatter().parse(text)
            if name and name not in placeholders
        ),
        *(
            f"options.{field} lists rule {rule_id}, which does not set it"
            for field, entry in sorted(options.items())
            for rule_id in entry.get("rules", ())
            if field not in rule_fields.get(rule_id, ())
        ),
        *(
            f"options.{field} is pending without follow_up and release"
            for field, entry in pending.items()
            if not (entry.get("follow_up") and entry.get("release"))
        ),
        *(
            f"options.{field} is still pending although {entry['release']} is released; give it a rule or a category"
            for field, entry in pending.items()
            if entry.get("release") and Version(newest_release) >= Version(entry["release"])
        ),
    ]


def _mirror_problems(limit: int) -> list[str]:
    """Return the texts that state the full-trial limit without the rule data's value."""
    help_text = next(action.help for action in arg_parser._actions if "--recommend-options" in action.option_strings)
    texts = {
        "the --recommend-options help in arguments.py": str(help_text),
        "the cli-option.recommend-options note in experimental.py": EXPERIMENTAL_FEATURES[
            "cli-option.recommend-options"
        ].note,
        "docs/recommend-options.md": RECOMMEND_DOCS.read_text(encoding="utf-8"),
    }
    return [
        f"{place} does not state full_trial_max_models ({limit}); update it"
        for place, text in texts.items()
        if str(limit) not in text
    ]


def test_recommender_rules_match_the_option_registry(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Rules only set registered options, registry entries name the rules that set them, and texts use real signals."""
    shutil.copy(INPUT_PATH / "api.yaml", tmp_path / "api.yaml")
    with chdir(tmp_path):
        run_main_with_args(
            ["--input", "api.yaml", "--output-format", "json", "--recommend-options", "quick"],
            use_builtin_default_formatter=False,
        )
    payload = json.loads(capsys.readouterr().out)
    data = _rules_data()
    problems = [
        *_rule_problems(data, {*payload["signals"], *payload["facts"]}, _newest_release()),
        *_mirror_problems(data["full_trial_max_models"]),
    ]
    assert_output(
        "\n".join([
            "Rule data problems:",
            *(f"  - {problem}" for problem in problems or ["none"]),
            "",
            REGISTRY_GUIDE,
            "",
        ]),
        EXPECTED_PATH / "registry_rules.txt",
    )


@pytest.mark.parametrize(
    ("inputs", "args", "mode", "expected"),
    [
        pytest.param(
            [INPUT_PATH / "titled_variants.yaml"],
            ["--input", "titled_variants.yaml", "--target-python-version", "3.14"],
            (),
            "titled_variants.txt",
            id="small-schema-default",
        ),
        pytest.param(
            [INPUT_PATH / "store.yaml", INPUT_PATH / "store_previous.yaml"],
            [
                "--input",
                "store.yaml",
                "--diff-against",
                "store_previous.yaml",
                "--output",
                "models.py",
                "--openapi-scopes",
                "schemas",
                "paths",
                "--target-python-version",
                "3.14",
            ],
            (),
            "full/store_diff_against.txt",
            id="diff-against",
        ),
        pytest.param(
            [INPUT_PATH / "api.yaml"],
            ["--input", "api.yaml", "--output-format", "json", "--target-python-version", "3.14"],
            ("full",),
            "full/api_json.txt",
            id="json",
        ),
        pytest.param(
            [INPUT_PATH / "store.yaml"],
            [
                "--input",
                "store.yaml",
                "--input-file-type",
                "openapi",
                "--generate-server",
                "fastapi",
                "--output",
                "app/models.py",
                "--server-output",
                "app/server",
                "--server-package",
                "app.server",
                "--server-model-package",
                "app.models",
            ],
            ("full",),
            "full/server_target.txt",
            id="server-target",
        ),
        pytest.param(
            [INPUT_PATH / "api.yaml"],
            ["--input", "api.yaml", "--output", "models", "--target-python-version", "3.14"],
            ("full",),
            "full/api_package.txt",
            id="package-output",
        ),
        pytest.param(
            [INPUT_PATH / "api.yaml"],
            ["--input", "api.yaml", "--preset", "standard-py314-20261017"],
            ("full",),
            "full/api_preset_argument.txt",
            id="preset-argument",
        ),
        pytest.param(
            [INPUT_PATH / "service-config.json"],
            ["--input", "service-config.json", "--target-python-version", "3.14"],
            ("full",),
            "full/service_config.txt",
            id="contested-option",
        ),
        pytest.param(
            [INPUT_PATH / "many_options.json"],
            ["--input", "many_options.json", "--target-python-version", "3.14"],
            ("full",),
            "full/many_options.txt",
            id="candidate-limit",
        ),
        pytest.param(
            [INPUT_PATH / "numeric_title.json"],
            ["--input", "numeric_title.json", "--target-python-version", "3.14"],
            ("full",),
            "full/numeric_title.txt",
            id="rejected-candidates",
        ),
        pytest.param(
            [GRAPHQL_INPUT],
            ["--input", "simple-star-wars.graphql", "--input-file-type", "graphql"],
            ("full",),
            "full/graphql.txt",
            id="graphql",
        ),
        pytest.param(
            [],
            ["--input-model", "tests.data.python.input_model.pydantic_models:User", "--target-python-version", "3.12"],
            ("full",),
            "full/input_model.txt",
            id="input-model",
        ),
        pytest.param(
            [INPUT_PATH / "data.json"],
            ["--input", "data.json", "--input-file-type", "json", "--no-use-annotated"],
            ("full",),
            "full/json_data.txt",
            id="json-data",
        ),
        pytest.param(
            [INPUT_PATH / "validation.yaml"],
            ["--input", "validation.yaml", "--target-python-version", "3.14"],
            ("full",),
            "full/validation.txt",
            id="unconstrained-fields",
        ),
        pytest.param(
            [INPUT_PATH / "schedules.yaml"],
            ["--input", "schedules.yaml", "--output", "models", "--target-python-version", "3.14"],
            ("full",),
            "full/schedules_package.txt",
            id="unconstrained-fields-per-module",
        ),
        pytest.param(
            [INPUT_PATH / "schedules.yaml"],
            ["--input", "schedules.yaml", "--target-python-version", "3.14"],
            ("full",),
            "full/schedules.txt",
            id="unconstrained-fields-of-the-kept-parse",
        ),
    ],
)
def test_recommend_options_compares_candidates(
    inputs: list[Path],
    args: list[str],
    mode: tuple[str, ...],
    expected: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Generate the candidate configurations in memory and recommend the one whose class names score best.

    Without a mode, a schema of up to 3000 estimated models is compared. Each option shows the candidates without and
    with it and the class names it changes; the report ends with the changes from the current configuration. Each
    candidate also counts the fields dcg warns it generates without some of their constraints: per module, so
    same-named classes of different modules count apart, and only in the parse whose models the generation keeps,
    such as the repaired single module of dotted keys written to stdout.
    """
    _recommend(tmp_path, capsys, args, expected, inputs=_inputs(*inputs), mode=mode)


def test_recommend_options_compares_candidates_for_directories(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Compare the candidates of a directory input, which generation types itself; print nothing on stderr.

    The type the trial read is a note.
    """
    _recommend(
        tmp_path,
        capsys,
        ["--input", "."],
        "full/directory.txt",
        inputs=_inputs(INPUT_PATH / "catalog.json"),
        mode=("full",),
    )


def test_recommend_options_rejects_candidates_that_fail(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Reject each candidate that cannot decode the directory's schema with a one-line reason, not a traceback.

    The schema is written as exact bytes, so the reported byte position is the same on every platform.
    """
    (tmp_path / "schema.json").write_bytes(b'{"title": "Caf\xc3\xa9", "type": "object"}')
    _recommend(
        tmp_path,
        capsys,
        ["--input", ".", "--encoding", "ascii"],
        "full/directory_undecodable.txt",
        mode=("full",),
    )


def test_recommend_options_counts_long_numbered_names(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Count a numbered class name over 40 characters as numbered, not only as long.

    Without the short root class name, the long root title makes the numbered names long; that candidate no longer
    beats the recommendation.
    """
    shutil.copy(INPUT_PATH / "build-settings.json", tmp_path / "build-settings.json")
    with chdir(tmp_path):
        run_main_and_assert(
            input_path=Path("build-settings.json"),
            input_file_type="jsonschema",
            extra_args=["--formatters", "builtin", "--target-python-version", "3.14", "--recommend-options", "full"],
            capsys=capsys,
            assert_no_stderr=True,
        )


@pytest.mark.parametrize(
    ("inputs", "args", "expected"),
    [
        pytest.param(
            [INPUT_PATH / "large.yaml"],
            ["--input", "large.yaml", "--target-python-version", "3.14"],
            "full/large_default.txt",
            id="large-schema",
        ),
        pytest.param(
            [INPUT_PATH / "large.yaml"],
            [
                "--input",
                "large.yaml",
                "--diff-against",
                "large.yaml",
                "--output",
                "models.py",
                "--target-python-version",
                "3.14",
            ],
            "full/large_default_diff_against.txt",
            id="large-schema-and-previous",
        ),
        pytest.param(
            [GRAPHQL_INPUT],
            ["--input", "simple-star-wars.graphql", "--input-file-type", "graphql"],
            "full/graphql_default.txt",
            id="size-unknown",
        ),
    ],
)
def test_recommend_options_default_skips_large_comparisons(
    inputs: list[Path], args: list[str], expected: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without a mode, apply only the rules when the inputs are estimated above 3000 models or cannot be estimated."""
    _recommend(tmp_path, capsys, args, expected, inputs=_inputs(*inputs), mode=())


@pytest.mark.parametrize(
    ("stdin", "expected"),
    [
        pytest.param(INPUT_PATH / "catalog.json", "full/stdin.txt", id="schema"),
        pytest.param(INPUT_PATH / "numeric_title.json", "full/all_rejected.txt", id="all-rejected"),
    ],
)
def test_recommend_options_compares_candidates_from_stdin(
    stdin: Path, expected: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Generate the candidates from the schema read once from stdin.

    Without a file name for the root class, a root titled with a number fails every candidate, and the rules'
    recommendation stands.
    """
    run_main_and_assert(
        stdin_path=stdin,
        monkeypatch=monkeypatch,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--target-python-version", "3.14", "--recommend-options", "full"],
        capsys=capsys,
        expected_stdout_path=EXPECTED_PATH / expected,
        assert_no_stderr=True,
    )


def test_recommend_options_compares_candidates_from_url(
    mock_httpx_get: HttpxGetMockFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Fetch a remote schema once for the analysis and every candidate."""
    url = "https://api.example.com/specs/catalog.json"
    mock_get = mock_httpx_get(MockHttpxResponse(url, INPUT_PATH / "catalog.json"))
    _recommend(tmp_path, capsys, ["--url", url, "--target-python-version", "3.14"], "full/url.txt", mode=("full",))
    assert_httpx_get_kwargs(mock_get, call_count=1, expected_url=url)


def test_recommend_options_compares_candidates_while_updating_the_lock(
    mock_httpx_get: HttpxGetMockFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Accept remote references the lock does not record yet under --update-lock, and never write the lock."""
    url = "https://api.example.com/specs/catalog.json"
    mock_get = mock_httpx_get(
        MockHttpxResponse(url, INPUT_PATH / "remote_catalog.json"),
        MockHttpxResponse("https://api.example.com/specs/common.json", INPUT_PATH / "remote_common.json"),
    )
    lock = tmp_path / "datamodel-codegen.lock"
    lock.write_text('{"version": 1, "resources": []}\n', encoding="utf-8")
    _recommend(
        tmp_path,
        capsys,
        ["--url", url, "--update-lock", "--target-python-version", "3.14"],
        "full/url_update_lock.txt",
        mode=("full",),
    )
    assert_httpx_get_kwargs(mock_get, call_count=2, expected_urls=[url, "https://api.example.com/specs/common.json"])
    assert_output(lock.read_text(encoding="utf-8"), EXPECTED_PATH / "full" / "url_update_lock_file.txt")


def test_recommend_options_compares_candidates_for_projects(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Compare the candidates with the project's configuration and list the class names adopting them changes."""
    _recommend(
        tmp_path,
        capsys,
        [],
        "full/project_changes.txt",
        inputs=_inputs(INPUT_PATH / "api.yaml", INPUT_PATH / "project_changes" / "pyproject.toml"),
        mode=("full",),
    )


@pytest.mark.parametrize(
    ("args", "expected_stderr"),
    [
        pytest.param(
            ["--input", "catalog.json", "--diff-against", "old.yaml"],
            "Error: --diff-against requires --output as a virtual output path to select file or directory layout\n",
            id="diff-against-without-output",
        ),
        pytest.param(
            ["--input", "catalog.json", "--locked", "--lockfile", "missing.lock"],
            "Remote lock file not found: ",
            id="locked",
        ),
        pytest.param(["--input-model", "missing_models:Pet"], "Cannot find module 'missing_models'", id="input-model"),
        pytest.param(
            ["--input", "catalog.json", "--diff-against", "missing.json", "--output", "models.py"],
            "Unable to read --diff-against missing.json: No such file or directory\n",
            id="diff-against-missing",
        ),
        pytest.param(
            ["--input", "catalog.json", "--input-file-type", "openapi", "--generate-server", "fastapi"],
            "Error: --generate-server requires --server-output, --server-package and --server-model-package\n",
            id="server-target-options",
        ),
    ],
)
def test_recommend_options_full_reports_generation_errors(
    args: list[str], expected_stderr: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Report what stops every generation of the input, as generation reports it, and exit with an error."""
    shutil.copy(INPUT_PATH / "catalog.json", tmp_path / "catalog.json")
    with chdir(tmp_path):
        run_main_with_args(
            [*args, "--recommend-options", "full"],
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stderr_contains=expected_stderr,
            use_builtin_default_formatter=False,
        )


NAMING_HINT_INPUTS = (
    "naming_hint.yaml",
    "naming_hint_collapsed.yaml",
    "naming_hint_few.yaml",
    "naming_hint_minority.yaml",
    "order.json",
)


def _naming_hints(recorded: Iterable[warnings.WarningMessage]) -> str:
    """Return the naming hints among recorded warnings, one a line, with their category as the command line shows."""
    return "".join(
        f"{item.category.__name__}: {item.message}\n" for item in recorded if item.category is NamingHintWarning
    )


@pytest.mark.parametrize(
    ("input_name", "options", "pyproject", "expected_exit", "expected"),
    [
        pytest.param("naming_hint.yaml", [], "", Exit.OK, "naming_hint.txt", id="numbered"),
        pytest.param("naming_hint_few.yaml", [], "", Exit.OK, None, id="below-count"),
        pytest.param("naming_hint_minority.yaml", [], "", Exit.OK, None, id="below-share"),
        pytest.param("naming_hint_collapsed.yaml", ["--collapse-root-models"], "", Exit.OK, None, id="collapsed-root"),
        pytest.param("naming_hint.yaml", ["--naming-strategy", "numbered"], "", Exit.OK, None, id="naming-strategy"),
        pytest.param("naming_hint.yaml", [], 'naming-strategy = "numbered"', Exit.OK, None, id="pyproject-naming"),
        pytest.param(
            "naming_hint.yaml",
            ["--model-name-map", '{"Order": "Purchase"}'],
            "",
            Exit.OK,
            None,
            id="model-name-map",
        ),
        pytest.param(
            "naming_hint.yaml", [], 'model-name-map = {Order = "Purchase"}', Exit.OK, None, id="pyproject-map"
        ),
        pytest.param("naming_hint.yaml", ["--check"], "", Exit.DIFF, None, id="check"),
        pytest.param("naming_hint.yaml", ["--diff-against", "naming_hint_few.yaml"], "", Exit.DIFF, None, id="diff"),
        pytest.param("naming_hint.yaml", ["--output-format", "json"], "", Exit.OK, None, id="json"),
        pytest.param("naming_hint.yaml", ["--disable-warnings"], "", Exit.OK, None, id="disable-warnings"),
        pytest.param("naming_hint.yaml", ["--recommend-options"], "", Exit.OK, None, id="recommend-options"),
    ],
)
def test_naming_hint(
    input_name: str,
    options: list[str],
    pyproject: str,
    expected_exit: Exit,
    expected: str | None,
    tmp_path: Path,
) -> None:
    """Point to --recommend-options after a generation whose class names are mostly numbered collisions.

    The hint needs at least 10 numbered collisions that make one in five classes or more; a collapsed root model keeps
    its name, so the classes named after it are numbered. An explicit naming strategy or model name map, on the
    command line or in pyproject.toml, a check, an input diff, JSON output, disabled warnings, and the candidate runs
    of --recommend-options leave it out; exit codes stay the same.
    """
    for name in NAMING_HINT_INPUTS:
        shutil.copy(INPUT_PATH / name, tmp_path / name)
    if pyproject:
        (tmp_path / "pyproject.toml").write_text(f"[tool.datamodel-codegen]\n{pyproject}\n", encoding="utf-8")
    with chdir(tmp_path), warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", NamingHintWarning)
        run_main_with_args(
            ["--input", input_name, "--input-file-type", "openapi", "--output", "models.py", *options],
            expected_exit=expected_exit,
        )
    assert_output(_naming_hints(recorded), None if expected is None else EXPECTED_PATH / expected)


@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        pytest.param(["--job", "big shop"], "naming_hint.txt", id="one-job"),
        pytest.param(["--job", "plain", "--job", "big shop"], None, id="jobs"),
        pytest.param(["--all-jobs"], None, id="all-jobs"),
    ],
)
def test_naming_hint_batch_jobs(selection: list[str], expected: str | None, tmp_path: Path) -> None:
    """Show the hint for a single job, which --recommend-options takes, and not for several jobs, which it refuses."""
    for name in NAMING_HINT_INPUTS:
        shutil.copy(INPUT_PATH / name, tmp_path / name)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.datamodel-codegen]\n"
        'input-file-type = "openapi"\n'
        'jobs.plain = {input = "order.json", input-file-type = "jsonschema", output = "plain.py"}\n'
        'jobs."big shop" = {input = "naming_hint.yaml", output = "big_shop.py"}\n'
        'jobs.shop = {input = "naming_hint.yaml", output = "shop.py"}\n',
        encoding="utf-8",
    )
    with chdir(tmp_path), warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", NamingHintWarning)
        run_main_with_args(selection)
    assert_output(_naming_hints(recorded), None if expected is None else EXPECTED_PATH / expected)


@pytest.mark.parametrize(
    ("arguments", "output"),
    [
        pytest.param(["--input", "naming_hint.yaml", "--output", "models.py"], "models.py", id="command"),
        pytest.param(["--job", "shop"], "shop.py", id="one-job"),
    ],
)
def test_naming_hint_as_error(arguments: list[str], output: str, tmp_path: Path) -> None:
    """Drop the hint when warnings are errors: the run still writes its output and exits with 0."""
    for name in NAMING_HINT_INPUTS:
        shutil.copy(INPUT_PATH / name, tmp_path / name)
    (tmp_path / "pyproject.toml").write_text(
        "[tool.datamodel-codegen]\n"
        'input-file-type = "openapi"\n'
        "disable-timestamp = true\n"
        'jobs.shop = {input = "naming_hint.yaml", output = "shop.py"}\n',
        encoding="utf-8",
    )
    with chdir(tmp_path), warnings.catch_warnings():
        warnings.simplefilter("error", NamingHintWarning)
        run_main_with_args(arguments)
    assert_output((tmp_path / output).read_text(encoding="utf-8"))


def test_naming_hint_not_shown_by_generate(tmp_path: Path) -> None:
    """Leave the hint to the command line: generate() never shows it, however numbered its class names are."""
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", NamingHintWarning)
        generate(
            INPUT_PATH / "naming_hint.yaml",
            config=GenerateConfig(input_file_type="openapi", output=tmp_path / "models.py", formatters=["builtin"]),
        )
    assert_output(_naming_hints(recorded))
