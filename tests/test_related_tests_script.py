"""Select related tests of temporary git repositories through the script's CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import related_tests
from tests.conftest import assert_output

ROOT = Path(__file__).resolve().parents[1]
GIT = ["git", "-c", "user.name=CI", "-c", "user.email=ci@example.com", "-c", "commit.gpgsign=false"]
SRC = "src/datamodel_code_generator"
TEST = "def test_one() -> None:\n    pass\n\n\ndef test_two() -> None:\n    pass\n"
RULES = """
[[rule]]
id = "docs-to-guide"
prs = [1]
incident = "a guide fragment broke the guide's check"
paths = ["docs/data/*"]
tests = ["tests/test_guide.py", "tests/test_gone.py"]

[[rule]]
id = "added-inputs"
incident = "an added input repeated another"
paths = ["tests/data/*"]
exclude = ["tests/data/expected/*"]
status = "A"
tests = ["tests/test_guide.py::test_two"]

[[rule]]
id = "pattern-lines"
incident = "a change to patterns failed a test"
paths = ["src/*"]
pattern = "(pattern|regex)"
tests = ["tests/test_guide.py::test_one"]
envs = ["dispatch"]

[[rule]]
id = "guide-envs"
prs = [2]
incident = "a guide fragment needed the nightly lane"
paths = ["docs/data/*"]
envs = ["nightly-tier", "py312-nocov-parallel"]

[[rule]]
id = "removed-tests-envs"
prs = [3]
incident = "a deleted test stayed listed in a pinned lane"
paths = ["tests/*.py"]
status = "D"
envs = ["client-httpx2-min-e2e"]

[[rule]]
id = "source-envs"
prs = [4]
incident = "a change to patterns failed on a lane"
paths = ["src/*"]
pattern = "(pattern|regex)"
envs = ["py312-nocov-parallel", "client-httpx2-min-e2e"]
"""
TOX = """[tox]
env_list = fix

[testenv]
deps = x

[testenv:fix]

[testenv:py{311,312}{,-nocov}-parallel]

[testenv:client-httpx2-{min,max}-e2e]
"""
BASE = {
    f"{SRC}/__init__.py": "",
    f"{SRC}/parser/jsonschema.py": "value = 1\n",
    f"{SRC}/parser/_names.py": "value = 1\n",
    f"{SRC}/parser/other.py": "value = 1\n",
    "scripts/build_it.py": "value = 1\n",
    "scripts/plain.py": "value = 1\n",
    "tests/parser/test_jsonschema.py": TEST,
    "tests/parser/test_names.py": TEST,
    "tests/test_build_it_script.py": TEST,
    "tests/test_guide.py": TEST,
    "tests/test_ci_workflow.py": TEST,
    "tests/main/test_cli_fast_paths.py": TEST,
    "tests/data/expected/main/cli_fast_paths/fast_paths_load_these_modules[version].txt": (
        '{\n  "package": [\n    "datamodel_code_generator",\n    "datamodel_code_generator.parser.listed",\n'
        '    "datamodel_code_generator.sub"\n  ]\n}\n'
    ),
    "tests/data/expected/main/cli_fast_paths/version_fast_path.txt": '"datamodel_code_generator.parser.jsonschema"\n',
    f"{SRC}/sub/__init__.py": "",
    f"{SRC}/parser/listed.py": "value = 1\n",
    "docs/data/a.json": "{}\n",
    "tests/data/old.json": "{}\n",
    "related_tests.toml": RULES,
    "tox.ini": TOX,
}
CASES: dict[str, dict[str, dict[str, str | None]]] = {
    "mirror-of-a-module": {"head": {f"{SRC}/parser/jsonschema.py": "value = 2\n"}},
    "mirror-of-a-private-module": {"head": {f"{SRC}/parser/_names.py": "value = 2\n"}},
    "module-without-a-mirror": {"head": {f"{SRC}/parser/other.py": "value = 2\n"}},
    "mirror-of-a-script": {"head": {"scripts/build_it.py": "value = 2\n"}},
    "script-without-a-mirror": {"head": {"scripts/plain.py": "value = 2\n"}},
    "deleted-module": {"head": {f"{SRC}/parser/jsonschema.py": None}},
    "row-by-path": {"head": {"docs/data/a.json": '{"a": 1}\n'}},
    "row-by-added-path": {"head": {"tests/data/new.json": "{}\n"}},
    "row-status-modified": {"head": {"tests/data/old.json": '{"a": 1}\n'}},
    "row-excluded-path": {"head": {"tests/data/expected/new.txt": "x\n"}},
    "row-pattern-added-line": {"head": {f"{SRC}/parser/other.py": "value = 2  # the pattern\n"}},
    "row-pattern-removed-line": {
        "base": {f"{SRC}/parser/other.py": "value = 1  # regex\n"},
        "head": {f"{SRC}/parser/other.py": "value = 1\n"},
    },
    "row-pattern-absent": {"head": {f"{SRC}/parser/other.py": "value = 2\n"}},
    "row-by-a-non-ascii-path": {"head": {"tests/data/\u00e9.json": "{}\n"}},
    "fast-path-listed-module": {"head": {f"{SRC}/parser/listed.py": "value = 2\n"}},
    "fast-path-listed-package": {"head": {f"{SRC}/sub/__init__.py": "value = 2\n"}},
    "fast-path-listed-top-package": {"head": {f"{SRC}/__init__.py": "value = 2\n"}},
    "fast-path-unlisted-module": {"head": {f"{SRC}/parser/_names.py": "value = 2\n"}},
    "fast-path-deleted-module": {"head": {f"{SRC}/parser/listed.py": None}},
    "fast-path-list-edited": {
        "head": {"tests/data/expected/main/cli_fast_paths/fast_paths_load_these_modules[version].txt": "{}\n"}
    },
    "added-test-function": {"head": {"tests/test_guide.py": TEST + "\n\ndef test_three() -> None:\n    pass\n"}},
    "changed-test-function": {"head": {"tests/test_guide.py": TEST.replace("pass\n", "assert True\n", 1)}},
    "changed-test-class": {
        "base": {"tests/test_suite.py": "class TestIt:\n    def test_one(self) -> None:\n        pass\n"},
        "head": {"tests/test_suite.py": "class TestIt:\n    def test_one(self) -> None:\n        assert True\n"},
    },
    "changed-parametrized-test": {
        "base": {"tests/test_cases.py": '@pytest.mark.parametrize("x", [1])\ndef test_x(x: int) -> None:\n    pass\n'},
        "head": {
            "tests/test_cases.py": '@pytest.mark.parametrize("x", [1, 2])\ndef test_x(x: int) -> None:\n    pass\n'
        },
    },
    "removed-test-function": {"head": {"tests/test_guide.py": TEST.partition("\n\n\n")[0] + "\n"}},
    "changed-module-level-code": {"head": {"tests/test_guide.py": "import os\n\n\n" + TEST}},
    "changed-test-named-fixture": {
        "base": {
            "tests/test_guide.py": "import pytest\n\n\n@pytest.fixture\ndef test_data() -> int:\n    return 1\n\n\n"
            + TEST
        },
        "head": {
            "tests/test_guide.py": "import pytest\n\n\n@pytest.fixture\ndef test_data() -> int:\n    return 2\n\n\n"
            + TEST
        },
    },
    "changed-blank-line-only": {"head": {"tests/test_guide.py": TEST.replace("\n\n\n", "\n\n\n\n")}},
    "changed-helper-module": {
        "base": {"tests/helpers.py": "value = 1\n"},
        "head": {"tests/helpers.py": "value = 2\n"},
    },
    "changed-test-module-with-a-syntax-error": {"head": {"tests/test_guide.py": TEST + "def broken(:\n"}},
    "added-test-helper": {"head": {"tests/helpers.py": "value = 1\n"}},
    "deleted-test-module": {"head": {"tests/test_guide.py": None}},
    "test-input-under-data": {"head": {"tests/data/python/model.py": "value = 1\n"}},
    "nothing-changed": {"head": {}},
}
ENV_CASES: dict[str, dict[str, dict[str, str | None]]] = {
    "row-by-path": {"head": {"docs/data/a.json": '{"a": 1}\n'}},
    "row-by-pattern": {"head": {f"{SRC}/parser/other.py": "value = 2  # the regex\n"}},
    "environment-asked-by-two-rows": {
        "head": {"docs/data/a.json": '{"a": 1}\n', f"{SRC}/parser/other.py": "value = 2  # the regex\n"}
    },
    "row-by-deleted-status": {"head": {"tests/test_guide.py": None}},
    "row-pattern-absent": {"head": {f"{SRC}/parser/other.py": "value = 2\n"}},
    "row-with-tests-only": {"head": {"tests/data/new.json": "{}\n"}},
    "nothing-changed": {"head": {}},
}


def _git(repository: Path, *args: str) -> None:
    subprocess.run([*GIT, *args], cwd=repository, check=True, capture_output=True)


def _write(repository: Path, files: dict[str, str | None]) -> None:
    for name, content in files.items():
        path = repository / name
        if content is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _repository(tmp_path: Path, base: dict[str, str | None], head: dict[str, str | None]) -> Path:
    """Build a repository with a `base` commit and a `head` commit that applies `head` on top."""
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q", "-b", "main")
    _write(repository, {**BASE, **base})
    _git(repository, "add", "-A")
    _git(repository, "commit", "-q", "-m", "base")
    _git(repository, "tag", "base")
    _write(repository, head)
    _git(repository, "add", "-A")
    _git(repository, "commit", "-q", "--allow-empty", "-m", "head")
    return repository


def _select(repository: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *args: str) -> str:
    """Run the script in the repository with the rows of its related_tests.toml, or the shipped rows in this one."""
    monkeypatch.chdir(repository)
    rules = [] if repository == ROOT else ["--rules", "related_tests.toml"]
    code = related_tests.main([*rules, *args])
    captured = capsys.readouterr()
    return f"exit status {code}\n{captured.out}{captured.err}"


@pytest.mark.parametrize("case", CASES)
def test_related_tests_select_the_diff(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mirrors and rows select the tests of a committed diff; targets that do not exist are left out."""
    spec = CASES[case]
    repository = _repository(tmp_path, spec.get("base", {}), spec["head"])
    assert_output(_select(repository, monkeypatch, capsys, "--base", "base", "--head", "HEAD"))


@pytest.mark.parametrize("case", ENV_CASES)
def test_related_tests_print_the_environments_of_the_diff(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--envs prints each environment the fired rows ask for, once, with the rows that ask for it, and no tests."""
    repository = _repository(tmp_path, {}, ENV_CASES[case]["head"])
    assert_output(_select(repository, monkeypatch, capsys, "--base", "base", "--head", "HEAD", "--envs"))


WORKING_TREES = {
    "tracked-and-untracked": {f"{SRC}/parser/jsonschema.py": "value = 3\n", "tests/data/new.json": "{}\n"},
    "untracked-with-a-pattern": {f"{SRC}/parser/new.py": "value = 1  # the regex\n"},
    "untracked-without-a-pattern": {f"{SRC}/parser/new.py": "value = 1\n"},
    "untracked-listed-module": {f"{SRC}/parser/listed.py": "value = 3\n"},
    "untracked-non-ascii-path": {"tests/data/\u00e9.json": "{}\n"},
    "untracked-test-module": {"tests/parser/test_new.py": TEST},
    "uncommitted-test-function": {"tests/test_guide.py": TEST.replace("pass\n", "assert True\n", 1)},
}


@pytest.mark.parametrize("case", WORKING_TREES)
def test_related_tests_read_the_working_tree_without_a_head(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without --head an uncommitted change and an untracked file, with its lines, count like a check before commit."""
    repository = _repository(tmp_path, {}, {})
    _write(repository, WORKING_TREES[case])
    assert_output(_select(repository, monkeypatch, capsys, "--base", "base"))


@pytest.mark.parametrize("args", [("--base", "nobody"), ("--base", "base", "--head", "nobody")], ids=["base", "head"])
def test_related_tests_fail_when_git_fails(
    args: tuple[str, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A revision git cannot read ends the run with an error, never with an empty selection."""
    repository = _repository(tmp_path, {}, {f"{SRC}/parser/jsonschema.py": "value = 2\n"})
    assert_output(_select(repository, monkeypatch, capsys, *args))


def test_related_tests_need_a_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A run without --base and without --check is refused."""
    repository = _repository(tmp_path, {}, {})
    monkeypatch.chdir(repository)
    with pytest.raises(SystemExit) as raised:
        related_tests.main([])
    assert_output(f"exit status {raised.value.code}\n{capsys.readouterr().err.splitlines()[-1].split(': ', 1)[1]}\n")


def test_related_tests_rows_of_the_repository_name_existing_targets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every row of scripts/related_tests.toml has an incident, a unique id and targets that exist."""
    assert_output(_select(ROOT, monkeypatch, capsys, "--check"))


def test_related_tests_check_reports_problem_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A row naming a missing file or function, a row without an incident and a repeated id fail the check."""
    repository = _repository(tmp_path, {}, {})
    rows = """
[[rule]]
id = "gone-file"
incident = "x"
paths = ["a"]
tests = ["tests/test_gone.py"]

[[rule]]
id = "gone-function"
incident = "x"
paths = ["a"]
tests = ["tests/test_guide.py::test_three"]

[[rule]]
id = "gone-function"
paths = ["a"]
tests = ["tests/test_guide.py::test_one"]
"""
    (repository / "related_tests.toml").write_text(rows, encoding="utf-8")
    assert_output(_select(repository, monkeypatch, capsys, "--check"))


def test_related_tests_rows_select_with_the_repository_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The shipped rows fire on a workflow change, with the tests that exist in the repository."""
    repository = _repository(tmp_path, {}, {".github/workflows/test.yaml": "name: test\n"})
    (repository / "related_tests.toml").write_text((ROOT / "scripts/related_tests.toml").read_text("utf-8"), "utf-8")
    assert_output(_select(repository, monkeypatch, capsys, "--base", "base", "--head", "HEAD"))


SHIPPED_ROW_FILES = {
    "docs-source-of-client-generation": (
        (),
        {
            "tests/data/expected/api_generation/client_generation/client_protocols[a].txt": "value = {}\n",
            "tests/test_build_docs_examples_script.py": TEST,
        },
    ),
    "platform-observations-of-the-test-modules": (
        ("--envs",),
        {
            "tests/data/expected/api_generation/client_runtime/client_runtime[a].txt": "value = {}\n",
            "tests/data/expected/model_codecs/wire_values/a.txt": "value = {}\n",
            "tests/data/expected/parser/openapi_scope/a.txt": "value = {}\n",
            "tests/data/expected/main/generation_api_contract/a.txt": "value = {}\n",
        },
    ),
}


@pytest.mark.parametrize("case", SHIPPED_ROW_FILES)
def test_related_tests_rows_follow_the_stored_outputs_where_they_live(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The shipped rows read the folders the stored outputs of the docs and of the platform tests are in now."""
    args, files = SHIPPED_ROW_FILES[case]
    base = {name: text.replace("{}", "1") for name, text in files.items()}
    head = {name: text.replace("{}", "2") for name, text in files.items()}
    repository = _repository(tmp_path, base, head)
    (repository / "related_tests.toml").write_text((ROOT / "scripts/related_tests.toml").read_text("utf-8"), "utf-8")
    assert_output(_select(repository, monkeypatch, capsys, "--base", "base", "--head", "HEAD", *args))


def test_related_tests_check_reports_unknown_environments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An environment tox.ini does not define and is not a lane, a row with envs and no prs, and an empty row fail."""
    repository = _repository(tmp_path, {}, {})
    rows = """
[[rule]]
id = "known"
prs = [1]
incident = "x"
paths = ["a"]
envs = ["dispatch", "nightly-tier", "fix", "py311-nocov-parallel", "py312-parallel", "client-httpx2-max-e2e"]

[[rule]]
id = "unknown"
prs = [1]
incident = "x"
paths = ["a"]
envs = ["py313-parallel", "nightly", "client-httpx2-e2e", "py312-nocov"]

[[rule]]
id = "no-prs"
incident = "x"
paths = ["a"]
envs = ["dispatch"]

[[rule]]
id = "empty"
incident = "x"
paths = ["a"]
"""
    (repository / "related_tests.toml").write_text(rows, encoding="utf-8")
    assert_output(_select(repository, monkeypatch, capsys, "--check"))


def test_related_tests_rows_print_the_environments_of_the_repository_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The shipped rows ask for the client and typing environments, the nightly tier and the dispatch run."""
    base = {
        "tests/data/expected/main/generation_platform/MsgspecStruct.txt": "calls 273\n",
        "tests/data/expected/main/jsonschema/model.py": "value = 1\n",
        ".github/workflows/test.yaml": "python-version: 3.14\n",
        f"{SRC}/_runtime/client/transport.py": "value = 1\n",
    }
    head = {
        "tests/data/expected/main/generation_platform/MsgspecStruct.txt": "calls 265\n",
        "tests/data/expected/main/jsonschema/model.py": "value = 2\n",
        ".github/workflows/test.yaml": "python-version: 3.15\n",
        f"{SRC}/_runtime/client/transport.py": "value = 2\n",
    }
    repository = _repository(tmp_path, base, head)
    (repository / "related_tests.toml").write_text((ROOT / "scripts/related_tests.toml").read_text("utf-8"), "utf-8")
    assert_output(_select(repository, monkeypatch, capsys, "--base", "base", "--head", "HEAD", "--envs"))
