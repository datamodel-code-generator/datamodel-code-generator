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
    "docs/data/a.json": "{}\n",
    "tests/data/old.json": "{}\n",
    "related_tests.toml": RULES,
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


WORKING_TREES = {
    "tracked-and-untracked": {f"{SRC}/parser/jsonschema.py": "value = 3\n", "tests/data/new.json": "{}\n"},
    "untracked-with-a-pattern": {f"{SRC}/parser/new.py": "value = 1  # the regex\n"},
    "untracked-without-a-pattern": {f"{SRC}/parser/new.py": "value = 1\n"},
    "untracked-non-ascii-path": {"tests/data/\u00e9.json": "{}\n"},
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
