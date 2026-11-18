"""Print the review items that the diffs of temporary git repositories trigger, through the script's CLI."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import review_checklist
from tests.conftest import assert_output

ROOT = Path(__file__).resolve().parents[1]
GIT = ["git", "-c", "user.name=CI", "-c", "user.email=ci@example.com", "-c", "commit.gpgsign=false"]
RULE_FILES = ("AGENTS.md", "docs/architecture.md", "docs/development-contributing.md")
COVERAGE_TEST = '''"""Report the coverage exclusions."""

from tests.conftest import assert_output

EXPECTED = ROOT / "tests/data/expected/ci_coverage"


def test_report(case, capsys):
    assert_output(capsys.readouterr().out, EXPECTED / f"{case}.txt")
'''
FORWARD = """def forward_root_model_aliases(positions, reference, index):
    return positions.get(model_key_of(reference), -1) >= index
"""
TEXT_PROBLEMS = """def text_problems(base, head, labels):
    return [label for label in labels if base.output(label) != head.output(label)]
"""
GUIDE_ENTRY = """[[change]]
id = "graphql-typename-property"
category = "decided-change"
title = "GraphQL object models gain a read-only `__typename` property"
keep = { flags = ["--graphql-no-typename"], note = "The option also leaves out the `typename__` field that the earlier \
release wrote, so it restores more than the property." }
"""
BASE = {
    "tests/test_coverage.py": COVERAGE_TEST,
    "src/datamodel_code_generator/parser/base.py": FORWARD,
    "scripts/declared_changes.py": TEXT_PROBLEMS,
    "docs/notes.md": "one\ntwo\n",
    "docs/old.md": "gone\n",
}
CASES = {
    "expected-path": {
        "head": {
            "tests/test_coverage.py": COVERAGE_TEST
            + """

def test_exclusions(case, capsys):
    assert_output(capsys.readouterr().out, EXPECTED / f"exclusions-{case}.txt")
"""
        }
    },
    "key-fallback": {
        "head": {
            "src/datamodel_code_generator/parser/base.py": FORWARD.replace(
                "positions.get(model_key_of(reference), -1) >= index", "positions.names_at_or_after(reference, index)"
            )
        }
    },
    "masked-comparison": {
        "head": {
            "scripts/declared_changes.py": TEXT_PROBLEMS.replace(".output(label)", ".stored(label)"),
            "tests/test_coverage.py": COVERAGE_TEST.replace(
                "capsys.readouterr().out, EXPECTED",
                "capsys.readouterr().out, transform=str.strip, expected_path=EXPECTED",
            ),
        }
    },
    "nothing-triggered": {"head": {"docs/notes.md": "one\nthree\n"}, "deleted": ["docs/old.md"]},
    "working-tree": {
        "head": {"docs/data/migration/unreleased/graphql-typename-property.toml": GUIDE_ENTRY},
        "deleted": ["docs/old.md", "src/datamodel_code_generator/parser/base.py"],
        "renamed": {"scripts/declared_changes.py": "scripts/_change_records.py"},
        "uncommitted": True,
    },
}


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def _repository(tmp_path: Path, spec: dict) -> Path:
    """Commit the rule files and ``BASE`` on branch ``base``, then the head's changes on ``main`` or in the tree."""
    repository = tmp_path / "repository"
    _write(repository, {name: (ROOT / name).read_text(encoding="utf-8") for name in RULE_FILES} | BASE)
    for command in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"], ["branch", "base"]):
        subprocess.run([*GIT, *command], cwd=repository, check=True)
    for name in spec.get("deleted", []):
        (repository / name).unlink()
    for source, target in spec.get("renamed", {}).items():
        (repository / source).rename(repository / target)
    _write(repository, spec["head"])
    if not spec.get("uncommitted"):
        for command in (["add", "-A"], ["commit", "-q", "--allow-empty", "-m", "head"]):
            subprocess.run([*GIT, *command], cwd=repository, check=True)
    return repository


@pytest.mark.parametrize("case", CASES)
def test_review_checklist_of_a_diff(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The three misses of 2026-11-18 trigger their items; untouched paths and deletions trigger none."""
    spec = CASES[case]
    monkeypatch.chdir(_repository(tmp_path, spec) / "docs")
    code = review_checklist.main(["--base", "base", *([] if spec.get("uncommitted") else ["--head", "main"])])
    assert_output(f"exit status {code}\n{capsys.readouterr().out}")


def test_review_checklist_prints_ten_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A diff that triggers more items than ten prints the first ten of the table and names the others."""
    repository = _repository(tmp_path, {"head": {"notes/a.txt": "".join(f"line {n}\n" for n in range(5))}})
    rows = "".join(
        f'[[item]]\nid = "item-{n}"\ntitle = "Item {n}"\nrule = "AGENTS.md"\nwords = "Instructions for coding agents"\n'
        f'question = "Question {n}?"\npulls = [1, 2]\n\n[[item.trigger]]\npaths = ["notes/*.txt"]\n\n'
        for n in range(12)
    )
    _write(tmp_path, {"table.toml": rows})
    monkeypatch.chdir(repository)
    code = review_checklist.main(["--base", "base", "--head", "main", "--table", str(tmp_path / "table.toml")])
    assert_output(f"exit status {code}\n{capsys.readouterr().out}")


def test_review_checklist_refuses_an_invalid_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every item states its rule in words its file holds, names two pull requests and compiles its triggers."""
    repository = _repository(tmp_path, {"head": {}})
    _write(
        tmp_path,
        {
            "table.toml": """[[item]]
title = "No id"

[[item]]
id = "unstated"
title = "Unstated"
rule = "AGENTS.md"
words = "Words no rule file holds"
question = "?"
pulls = [1]
trigger = []

[[item]]
id = "no-file"
title = "No file"
rule = "docs/missing.md"
words = "Anything"
question = "?"
pulls = [1, 2]

[[item.trigger]]
paths = ["src/**"]
added = ["(unclosed"]
removed = ["[a-"]
"""
        },
    )
    monkeypatch.chdir(repository)
    code = review_checklist.main(["--base", "base", "--table", str(tmp_path / "table.toml")])
    assert_output(f"exit status {code}\n{capsys.readouterr().out}")


def test_review_checklist_reports_an_unknown_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A base revision git does not know ends the run with the git command that failed."""
    monkeypatch.chdir(_repository(tmp_path, {"head": {}}))
    with pytest.raises(SystemExit) as raised:
        review_checklist.main(["--base", "no-such-revision"])
    assert_output(str(raised.value).partition(":")[0])
