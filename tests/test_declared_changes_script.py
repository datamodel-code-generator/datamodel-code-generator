"""End-to-end tests of scripts/declared_changes.py on temporary git repositories.

Each repository has a release tag, a base revision and a head revision; the stored outputs, their `.refs` sidecars and
the change records differ between them as a pull request, a stack layer or a release would make them differ.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from itertools import takewhile
from typing import TYPE_CHECKING, Any

import pytest

from scripts import declared_changes
from tests.conftest import assert_output
from tests.units import REFS_HEADER

if TYPE_CHECKING:
    from pathlib import Path

GIT = ["git", "-c", "user.name=CI", "-c", "user.email=ci@example.com", "-c", "commit.gpgsign=false"]
FRAGMENTS = "docs/data/migration/unreleased"
EXPECTED = "tests/data/expected"


def _sidecar(sections: dict[str, list[str]]) -> str:
    return REFS_HEADER + "".join(
        f"[{test}]\n" + "".join(f"{line}\n" for line in lines) for test, lines in sections.items()
    )


REFS = {
    "test_model": ["a\tmodels/a.py", "b\tmodels/b.py", "c\tmodels/c.py\t= models/a.py", "d\tmodels/d.json"],
    "test_tree": ["\tmodels/pkg/", "py\tmodels/pkg//*.py"],
    "test_warn": ["\tmodels/w.warnings.txt", "\tmodels/w.txt\t?"],
}
RELEASED = {
    "src/pkg.py": "VALUE = 1\n",
    "docs/data/migration/releases.toml": '[[release]]\nversion = "0.1.0"\ndate = "2026-01-01"\n',
    "tests/test_models.refs": _sidecar(REFS),
    "tests/test_help.refs": _sidecar({"test_show_help": ["\thelp/help.txt"]}),
    f"{EXPECTED}/models/a.py": "A = 1\n",
    f"{EXPECTED}/models/b.py": "B = 1\n",
    f"{EXPECTED}/models/d.json": '{"a": 1, "b": [1, 2]}',
    f"{EXPECTED}/models/pkg/__init__.py": "",
    f"{EXPECTED}/models/pkg/x.py": "X = 1\n",
    f"{EXPECTED}/models/pkg/notes.txt": "notes\n",
    f"{EXPECTED}/models/w.warnings.txt": "UserWarning: w\n",
    f"{EXPECTED}/models/w.txt": "w\n",
    f"{EXPECTED}/help/help.txt": "usage: tool\n",
}
"""The tree of the release tag 0.1.0."""


def _record(entry_id: str, covers: list[str], **keys: Any) -> dict[str, str]:
    fields = {"category": "harness", "title": "t", "summary": "s"} | keys
    lines = [f"id = {json.dumps(entry_id)}", *(f"{key} = {json.dumps(value)}" for key, value in fields.items())]
    block = "".join(f'    "{label}",\n' for label in sorted(covers))
    text = "[[change]]\n" + "\n".join(lines) + "\n" + (f"covers = [\n{block}]\n" if covers else "")
    return {f"{FRAGMENTS}/{entry_id}.toml": text}


def _write(repository: Path, files: dict[str, str | None]) -> None:
    for name, text in files.items():
        path = repository / name
        if text is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\r\n")


def _commit(repository: Path, message: str) -> None:
    for command in (["add", "-A"], ["commit", "-q", "--allow-empty", "-m", message]):
        subprocess.run([*GIT, *command], cwd=repository, check=True)


def _repository(tmp_path: Path, spec: dict[str, Any]) -> Path:
    """Commit the release and tag it, commit the base on branch `base`, then the head on branch `fix-the-thing`.

    The `steps` are commits of main between the release and the base. With `origin`, origin/main points at the base,
    so the base is the default one; the commits of `layer` follow it on branch `base`, as a lower stack layer's do.
    """
    repository = tmp_path / "repository"
    _write(repository, RELEASED | spec.get("release", {}))
    subprocess.run([*GIT, "init", "-q", "-b", "main"], cwd=repository, check=True)
    _commit(repository, "release")
    subprocess.run([*GIT, "tag", "0.1.0"], cwd=repository, check=True)
    for number, step in enumerate(spec.get("steps", []), 1):
        _write(repository, step)
        _commit(repository, f"step {number}")
    _write(repository, spec.get("base", {}))
    _commit(repository, "base")
    subprocess.run([*GIT, "switch", "-q", "-c", "base"], cwd=repository, check=True)
    if spec.get("origin"):
        subprocess.run([*GIT, "update-ref", "refs/remotes/origin/main", "base"], cwd=repository, check=True)
    for number, commit in enumerate(spec.get("layer", []), 1):
        _write(repository, commit)
        _commit(repository, f"layer {number}")
    subprocess.run([*GIT, "switch", "-q", "-c", "fix-the-thing"], cwd=repository, check=True)
    _write(repository, spec.get("head", {}))
    _commit(repository, spec.get("message", "Change the thing (#42)\n\nFixes: #41"))
    _write(repository, spec.get("worktree", {}))
    return repository


SRC = {"src/pkg.py": "VALUE = 2\n"}
OUTPUT_TEXT = {
    "category": "output-text",
    "kind": "output-text",
    "action": "none",
    "source": "release-note",
    "refs": ["#42"],
}
RELEASED_SRC = {"src/pkg.py": RELEASED["src/pkg.py"]}
NEW_OUTPUT = {
    "tests/test_models.refs": _sidecar(REFS | {"test_new": ["\tmodels/new.py"]}),
    f"{EXPECTED}/models/new.py": "N = 1\n",
}
RENAMED_OUTPUT = {
    "tests/test_models.refs": _sidecar(REFS | {"test_renamed": ["\tmodels/renamed.py"]}),
    f"{EXPECTED}/models/new.py": None,
    f"{EXPECTED}/models/renamed.py": "N = 1\n",
}
COVERS_B = 'covers = [\n    "models/b.py",\n]\n'
CASES: dict[str, dict[str, Any]] = {
    "changed without a record": {"head": SRC | {f"{EXPECTED}/models/b.py": "B = 2\n"}, "origin": True},
    "changed with a record": {
        "head": SRC | {f"{EXPECTED}/models/b.py": "B = 2\n"} | _record("b-two", ["models/b.py"], **OUTPUT_TEXT)
    },
    "split of a shared unit": {
        "head": {
            "tests/test_models.refs": _sidecar(
                REFS | {"test_model": [*REFS["test_model"][:2], "c\tmodels/c.py", REFS["test_model"][3]]}
            ),
            f"{EXPECTED}/models/c.py": "C = 1\n",
        }
    },
    "storage renamed, content kept": {
        "head": {
            "tests/test_models.refs": _sidecar(
                REFS
                | {"test_model": [REFS["test_model"][0], "b\tmodels/b.py\t= models/b2.py", *REFS["test_model"][2:]]}
            ),
            f"{EXPECTED}/models/b.py": None,
            f"{EXPECTED}/models/b2.py": "B = 1\n",
        }
    },
    "test renamed: a move": {
        "head": {
            "tests/test_models.refs": _sidecar({
                "test_model_renamed" if key == "test_model" else key: lines for key, lines in REFS.items()
            })
        },
    },
    "a src change pairs no removal with an addition": {
        "head": SRC
        | {
            "tests/test_models.refs": _sidecar(
                REFS | {"test_model": [REFS["test_model"][0], *REFS["test_model"][2:]], "test_other": ["\tother.py"]}
            ),
            f"{EXPECTED}/models/b.py": None,
            f"{EXPECTED}/other.py": "B = 1\n",
        },
    },
    "removed without a replacement": {
        "head": {
            "tests/test_models.refs": _sidecar(REFS | {"test_warn": REFS["test_warn"][1:]}),
            f"{EXPECTED}/models/w.warnings.txt": None,
        },
    },
    "added with a src change": {
        "head": SRC
        | {
            "tests/test_models.refs": _sidecar(REFS | {"test_new": ["\tmodels/new.py"]}),
            f"{EXPECTED}/models/new.py": "N = 1\n",
        },
    },
    "added by tests only": {
        "head": {
            "tests/test_models.refs": _sidecar(REFS | {"test_new": ["\tmodels/new.py"]}),
            f"{EXPECTED}/models/new.py": "N = 1\n",
        },
    },
    "flag and predicate changes": {
        "head": {
            "tests/test_models.refs": _sidecar(
                REFS
                | {
                    "test_warn": [REFS["test_warn"][0], "\tmodels/w.txt"],
                    "test_tree": [REFS["test_tree"][0], "py\tmodels/pkg//*.py\t@python=3.11"],
                }
            ),
        },
    },
    "same units in another form": {
        "head": {
            f"{EXPECTED}/models/d.json": '{\n  "a": 1,\n  "b": [\n    1,\n    2\n  ]\n}',
            f"{EXPECTED}/models/a.py": "A = 1\r\n",
            f"{EXPECTED}/models/pkg/.cache/state": "cache\n",
            f"{EXPECTED}/models/pkg/__pycache__/x.cpython-314.pyc": "bytecode\n",
        },
    },
    "an output of one environment gone": {"head": {f"{EXPECTED}/models/w.txt": None}},
    "tree member changes": {
        "head": {f"{EXPECTED}/models/pkg/notes.txt": "other notes\n"},
    },
    "lower layer record covers nothing here": {
        "base": {f"{EXPECTED}/models/b.py": "B = 2\n"} | _record("lower", ["models/b.py"]),
        "head": {f"{EXPECTED}/models/b.py": "B = 3\n"},
    },
    "a later layer extends the lower record": {
        "base": {f"{EXPECTED}/models/b.py": "B = 2\n"} | _record("lower", ["models/b.py"]),
        "head": {
            f"{EXPECTED}/models/b.py": "B = 3\n",
            f"{FRAGMENTS}/more.toml": '[[change]]\nid = "more"\nextends = "lower"\ncovers = [\n    "models/b.py",\n]\n',
        },
    },
    "over-claimed and badly formed records": {
        "head": {f"{EXPECTED}/models/b.py": "B = 2\n"}
        | _record("claims", ["help/help.txt", "models/b.py"])
        | _record(
            "notice-models",
            ["models/b.py"],
            category="notice",
            kind="warning",
            action="warn",
            source="release-note",
            refs=["#1"],
        )
        | {
            f"{FRAGMENTS}/inline.toml": (
                '[[change]]\nid = "inline"\ncategory = "internal"\ntitle = "t"\ncovers = ["models/b.py", "../x"]\n'
            )
        }
        | {f"{FRAGMENTS}/orphan.toml": '[[change]]\nid = "orphan"\nextends = "nothing"\n' + COVERS_B}
        | {f"{FRAGMENTS}/plain.toml": '[[change]]\nid = "plain"\ntitle = "t"\n' + COVERS_B}
        | {f"{FRAGMENTS}/broken.toml": "[[change]\n", f"{FRAGMENTS}/two.toml": '[[change]]\nid = "one"\n'}
        | {
            f"{FRAGMENTS}/presets.toml": (
                '[[change]]\nid = "presets"\ncategory = "new-surface"\ntitle = "t"\nsurface = ["preset:20260101"]\n'
                + COVERS_B
            )
        },
    },
    "a source-added reference stays a gap": {
        "base": SRC
        | {
            "tests/test_models.refs": _sidecar(REFS | {"test_new": ["\tmodels/new.py"]}),
            f"{EXPECTED}/models/new.py": "N = 1\n",
        },
        "args": ["--main"],
    },
    "a source-added reference is backfilled": {
        "base": SRC
        | {
            "tests/test_models.refs": _sidecar(REFS | {"test_new": ["\tmodels/new.py"]}),
            f"{EXPECTED}/models/new.py": "N = 1\n",
        },
        "head": _record("backfill-new", ["models/new.py"]),
    },
    "a later layer deletes the record of a source-added reference": {
        "base": SRC
        | {
            "tests/test_models.refs": _sidecar(REFS | {"test_new": ["\tmodels/new.py"]}),
            f"{EXPECTED}/models/new.py": "N = 1\n",
        }
        | _record("new-output", ["models/new.py"], **OUTPUT_TEXT),
        "head": {f"{FRAGMENTS}/new-output.toml": None},
    },
    "a record changes its category over covered models": {
        "base": {f"{EXPECTED}/models/b.py": "B = 2\n", f"{EXPECTED}/models/d.json": "{}"}
        | _record("lower", ["models/b.py"])
        | {
            f"{FRAGMENTS}/more.toml": (
                '[[change]]\nid = "more"\nextends = "lower"\ncovers = [\n    "models/d.json",\n]\n'
            )
        },
        "head": _record(
            "lower",
            ["models/b.py"],
            category="notice",
            kind="warning",
            action="warn",
            source="release-note",
            refs=["#1"],
        ),
    },
    "reverted to the tag and pruned": {
        "base": {f"{EXPECTED}/models/b.py": "B = 2\n"}
        | _record("lower", ["models/a.py", "models/b.py"])
        | {f"{EXPECTED}/models/a.py": "A = 2\n"},
        "head": {f"{EXPECTED}/models/b.py": "B = 1\n"} | _record("lower", ["models/a.py"]),
    },
    "revert of a change the base does not declare": {
        "base": SRC | {f"{EXPECTED}/models/b.py": "B = 2\n"},
        "head": RELEASED_SRC | {f"{EXPECTED}/models/b.py": "B = 1\n"},
    },
    "revert of a change the base does not declare, declared": {
        "base": SRC | {f"{EXPECTED}/models/b.py": "B = 2\n"},
        "head": RELEASED_SRC | {f"{EXPECTED}/models/b.py": "B = 1\n"} | _record("revert-b", ["models/b.py"]),
    },
    "backfill of an older gap": {
        "base": SRC | {f"{EXPECTED}/models/b.py": "B = 2\n"},
        "head": _record("backfill-b", ["models/b.py"]),
    },
    "backfill of an older gap on main": {
        "base": SRC | {f"{EXPECTED}/models/b.py": "B = 2\n"},
        "head": _record("backfill-b", ["models/b.py"]),
        "args": ["--main"],
    },
    "reverted to the tag, still covered": {
        "base": {f"{EXPECTED}/models/b.py": "B = 2\n"} | _record("lower", ["models/b.py"]),
        "head": {f"{EXPECTED}/models/b.py": "B = 1\n"},
    },
    "a shipped fragment edited": {
        "release": _record("shipped", []),
        "head": _record("shipped", [], title="edited"),
    },
    "stamp pending": {
        "head": {"docs/data/migration/releases.toml": ""},
        "args": ["--main"],
    },
    "every gap on main": {
        "base": {f"{EXPECTED}/models/b.py": "B = 2\n"},
        "args": ["--main"],
    },
    "older gaps stay out": {
        "base": {f"{EXPECTED}/models/b.py": "B = 2\n"},
        "head": {f"{EXPECTED}/models/a.py": "A = 2\n"} | _record("a-two", ["models/a.py"]),
    },
    "notice outputs only": {
        "head": SRC | {f"{EXPECTED}/models/w.warnings.txt": "UserWarning: other\n"},
        "message": "Reword a warning",
    },
    "kinds by test module and label": {
        "release": {
            "tests/test_source_rules.refs": _sidecar({"test_counts": ["\tsource_rules/counts.txt"]}),
            "tests/test_source_model.refs": _sidecar({"test_forms": ["\tsource_model/forms.txt"]}),
            "tests/test_check_ratchets_script.refs": _sidecar({"test_keys": ["\tcheck_ratchets_script/keys.txt"]}),
            "tests/test_ci_workflow.refs": _sidecar({"test_lanes": ["\tci_coverage/lanes.txt"]}),
            "tests/test_build_migration_docs_script.refs": _sidecar({"test_hub": ["\tmigration_docs/hub.txt"]}),
            "tests/test_generate_changelog_script.refs": _sidecar({"test_entry": ["\tchangelog/entry.txt"]}),
            "tests/test_kinds.refs": _sidecar({"test_kinds": ["a\tkinds/a_refused.txt", "b\tpayloads/b_errors.txt"]}),
            f"{EXPECTED}/source_rules/counts.txt": "1\n",
            f"{EXPECTED}/source_model/forms.txt": "1\n",
            f"{EXPECTED}/check_ratchets_script/keys.txt": "1\n",
            f"{EXPECTED}/ci_coverage/lanes.txt": "1\n",
            f"{EXPECTED}/migration_docs/hub.txt": "1\n",
            f"{EXPECTED}/changelog/entry.txt": "1\n",
            f"{EXPECTED}/kinds/a_refused.txt": "refused\n",
            f"{EXPECTED}/payloads/b_errors.txt": "error\n",
        },
        "head": SRC
        | {
            f"{EXPECTED}/models/b.py": "B = 2\n",
            f"{EXPECTED}/source_rules/counts.txt": "2\n",
            f"{EXPECTED}/source_model/forms.txt": "2\n",
            f"{EXPECTED}/check_ratchets_script/keys.txt": "2\n",
            f"{EXPECTED}/ci_coverage/lanes.txt": "2\n",
            f"{EXPECTED}/migration_docs/hub.txt": "2\n",
            f"{EXPECTED}/changelog/entry.txt": "2\n",
            f"{EXPECTED}/kinds/a_refused.txt": "refused again\n",
            f"{EXPECTED}/payloads/b_errors.txt": "other error\n",
            f"{EXPECTED}/help/help.txt": "usage: tool [-h]\n",
        },
    },
    "new surface covers only new model outputs": {
        "head": SRC
        | {
            "tests/test_models.refs": _sidecar(REFS | {"test_new": ["\tmodels/new.py"]}),
            f"{EXPECTED}/models/new.py": "N = 1\n",
            f"{EXPECTED}/models/b.py": "B = 2\n",
        }
        | _record("opt", ["models/b.py", "models/new.py"], category="new-surface", surface=["--x"]),
    },
    "harness on a src change": {
        "head": SRC | {f"{EXPECTED}/models/b.py": "B = 2\n"} | _record("same-output", ["models/b.py"]),
    },
    "report only": {"head": SRC | {f"{EXPECTED}/models/b.py": "B = 2\n"}, "args": ["--report-only"]},
    "a test-only change adds an output after a src change": {
        "base": SRC,
        "head": {
            "tests/test_models.refs": _sidecar(REFS | {"test_new": ["\tmodels/new.py"]}),
            f"{EXPECTED}/models/new.py": "N = 1\n",
        },
    },
    "a test-only change renames a test after a src change": {
        "base": SRC,
        "head": {
            "tests/test_models.refs": _sidecar({
                "test_model_renamed" if key == "test_model" else key: lines for key, lines in REFS.items()
            })
        },
    },
    "a test-only change renames a stored file with its test after a src change": {
        "base": SRC,
        "head": {
            "tests/test_models.refs": _sidecar(
                {key: lines for key, lines in REFS.items() if key != "test_warn"}
                | {"test_warning": ["\tmodels/warning.warnings.txt", REFS["test_warn"][1]]}
            ),
            f"{EXPECTED}/models/w.warnings.txt": None,
            f"{EXPECTED}/models/warning.warnings.txt": "UserWarning: w\n",
        },
    },
    "a test-only change removes an output after a src change": {
        "base": SRC,
        "head": {
            "tests/test_models.refs": _sidecar(REFS | {"test_warn": REFS["test_warn"][1:]}),
            f"{EXPECTED}/models/w.warnings.txt": None,
        },
    },
    "main after a test-only change added an output and a src change": {
        "steps": [
            {
                "tests/test_models.refs": _sidecar(REFS | {"test_new": ["\tmodels/new.py"]}),
                f"{EXPECTED}/models/new.py": "N = 1\n",
            }
        ],
        "base": SRC | {f"{EXPECTED}/models/b.py": "B = 2\n"} | _record("b-two", ["models/b.py"], **OUTPUT_TEXT),
        "args": ["--main"],
    },
    "main after a src change moved a test-added output": {
        "steps": [
            {
                "tests/test_models.refs": _sidecar(REFS | {"test_new": ["\tmodels/new.py"]}),
                f"{EXPECTED}/models/new.py": "N = 1\n",
            }
        ],
        "base": SRC | {f"{EXPECTED}/models/new.py": "N = 2\n"},
        "args": ["--main"],
    },
    "main after a src change and a test-only rename": {
        "steps": [SRC | {f"{EXPECTED}/models/b.py": "B = 2\n"} | _record("b-two", ["models/b.py"], **OUTPUT_TEXT)],
        "base": {
            "tests/test_models.refs": _sidecar({
                "test_model_renamed" if key == "test_model" else key: lines for key, lines in REFS.items()
            })
        },
        "args": ["--main"],
    },
    "a test-only rename of a source-added output its record covers": {
        "base": SRC | NEW_OUTPUT | _record("new-output", ["models/new.py"], **OUTPUT_TEXT),
        "head": RENAMED_OUTPUT,
    },
    "a test-only rename of a source-added output without a record": {"base": SRC | NEW_OUTPUT, "head": RENAMED_OUTPUT},
    "a test-only rename of a source-added output without a record on main": {
        "base": SRC | NEW_OUTPUT,
        "head": RENAMED_OUTPUT,
        "args": ["--main"],
    },
    "a later layer deletes the record its lower layer's test commit added": {
        "origin": True,
        "layer": [SRC, NEW_OUTPUT | _record("new-output", ["models/new.py"], **OUTPUT_TEXT)],
        "head": {f"{FRAGMENTS}/new-output.toml": None},
    },
    "many outputs": {
        "head": SRC
        | {"tests/test_many.refs": _sidecar({"test_many": [f"{index}\tmany/{index:02}.py" for index in range(25)]})}
        | {f"{EXPECTED}/many/{index:02}.py": f"{index}\n" for index in range(25)},
    },
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_declared_changes_check(case: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The static rules over references: changes, splits, moves, removals, additions, layers, records, the release."""
    spec = CASES[case]
    repository = _repository(tmp_path, spec)
    code = declared_changes.main(["check", "--base", "base", "--head", "HEAD", *spec.get("args", [])], repository)
    assert_output(f"exit {code}\n{capsys.readouterr().out}")


def _run(repository: Path, capsys: pytest.CaptureFixture[str], args: list[str]) -> str:
    code = declared_changes.main(args, repository)
    printed = capsys.readouterr()
    return f"$ {' '.join(args)}\nexit {code}\n{printed.out}{printed.err}"


def _fragments(repository: Path) -> str:
    return "".join(
        f"--- {path.name} ---\n{path.read_text(encoding='utf-8')}"
        for path in sorted((repository / FRAGMENTS).glob("*.toml"))
    )


PRINTED: dict[str, dict[str, Any]] = {
    "a stack layer changes what the lower layer declared": {
        "base": {f"{EXPECTED}/models/b.py": "B = 2\n"} | _record("lower", ["models/b.py"]),
        "worktree": {f"{EXPECTED}/models/b.py": "B = 3\n", f"{EXPECTED}/help/help.txt": "usage: tool [-h]\n"},
    },
    "a later layer adds to its record that extends the lower one": {
        "base": {f"{EXPECTED}/models/b.py": "B = 2\n", f"{EXPECTED}/models/d.json": "{}"}
        | _record("lower", ["models/b.py", "models/d.json"]),
        "head": {
            f"{EXPECTED}/models/b.py": "B = 3\n",
            f"{FRAGMENTS}/more.toml": '[[change]]\nid = "more"\nextends = "lower"\ncovers = [\n    "models/b.py",\n]\n',
        },
        "worktree": {f"{EXPECTED}/models/d.json": "[]"},
    },
    "a backfill of an older gap": {
        "base": SRC | {f"{EXPECTED}/models/b.py": "B = 2\n"},
        "worktree": _record("backfill-b", ["models/b.py"]),
    },
    "more outputs after the first declare": {
        "head": SRC | {f"{EXPECTED}/models/b.py": "B = 2\n"} | _record("b-two", ["models/b.py"], **OUTPUT_TEXT),
        "worktree": {f"{EXPECTED}/models/a.py": "A = 2\n"},
    },
    "an output text record does not take a warning": {
        "head": {f"{EXPECTED}/models/b.py": "B = 2\n"} | _record("b-text", ["models/b.py"], **OUTPUT_TEXT),
        "worktree": {f"{EXPECTED}/models/w.warnings.txt": "UserWarning: other\n"},
    },
    "a test-only change moves and adds outputs after a src change": {
        "base": SRC,
        "worktree": {
            "tests/test_models.refs": _sidecar(REFS | {"test_new": ["\tmodels/new.py"]}),
            f"{EXPECTED}/models/new.py": "N = 1\n",
            f"{EXPECTED}/models/b.py": "B = 2\n",
        },
    },
    "the branch's record among others": {
        "head": SRC
        | {f"{EXPECTED}/models/b.py": "B = 2\n", f"{EXPECTED}/models/d.json": "{}"}
        | _record("fix-the-thing", ["models/b.py"], **OUTPUT_TEXT)
        | _record("d-text", ["models/d.json"], **OUTPUT_TEXT),
        "worktree": {f"{EXPECTED}/models/a.py": "A = 2\n"},
    },
}


@pytest.mark.parametrize("case", sorted(PRINTED))
def test_declared_changes_printed_commands(case: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The `declare` commands a check prints, run as printed, declare every output it lists, and `prune` keeps them.

    A stack layer's command passes the check's base and extends the lower layer's record; a change whose record exists
    adds to it, or to the branch's record among several, with no record options; a backfill of an older gap needs none.
    """
    repository = _repository(tmp_path, PRINTED[case])
    first = _run(repository, capsys, ["check", "--base", "base"])
    printed = first.partition("Declare them:\n")[2].splitlines()
    output = [first]
    output += [
        _run(repository, capsys, shlex.split(command)[2:])
        for command in takewhile(lambda line: line.startswith("  python "), printed)
    ]
    output += [_run(repository, capsys, [command, "--base", "base"]) for command in ("prune", "check")]
    output.append(_fragments(repository))
    assert_output("\n".join(output))


def test_declared_changes_declare_and_prune(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`declare` writes covers from the diff into a new or an existing record; `prune` drops what no longer moves.

    The selection is by label, by test item or the rest; a record that exists only takes more covers, and a released
    or invalid id, a selection with nothing undeclared and a new record without a category are refused. `prune` drops
    the labels this change added that do not move against the base and the labels equal to the tag.
    """
    repository = _repository(
        tmp_path,
        {
            "base": {f"{EXPECTED}/models/w.txt": "w2\n"} | _record("lower", ["models/w.txt"]),
            "release": _record("shipped", []),
            "head": SRC,
            "worktree": {
                f"{EXPECTED}/models/a.py": "A = 2\n",
                f"{EXPECTED}/models/b.py": "B = 2\n",
                f"{EXPECTED}/models/w.warnings.txt": "UserWarning: other\n",
                f"{EXPECTED}/help/help.txt": "usage: tool [-h]\n",
            },
        },
    )
    base = ["--base", "base"]
    output = [
        _run(repository, capsys, ["status", *base]),
        _run(
            repository,
            capsys,
            [
                "declare",
                "b-two",
                "--category",
                "output-text",
                "--title",
                "t",
                "--refs",
                "#42",
                "--labels",
                "models/b*",
                *base,
            ],
        ),
        _run(repository, capsys, ["declare", "b-two", "--tests", "*::test_model[[]a]", *base]),
        _run(repository, capsys, ["declare", "b-two", "--category", "notice", "--rest", *base]),
        _run(repository, capsys, ["declare", "rest", "--rest", *base]),
        _run(repository, capsys, ["declare", "shipped", "--category", "harness", "--rest", *base]),
        _run(repository, capsys, ["declare", "Not A Slug", "--category", "harness", "--rest", *base]),
        _run(repository, capsys, ["declare", "help", "--extends", "b-two", "--tests", "tests/test_help.py::*", *base]),
        _run(
            repository,
            capsys,
            [
                "declare",
                "rest",
                "--category",
                "notice",
                "--title",
                "t",
                "--summary",
                "s",
                "--decision",
                "#7",
                "--surface=--x",
                "--rest",
                *base,
            ],
        ),
        _run(repository, capsys, ["declare", "rest", "--rest", *base]),
        _run(repository, capsys, ["check", *base, "--report-only"]),
        _fragments(repository),
    ]
    _write(repository, {f"{EXPECTED}/models/a.py": "A = 1\n", f"{EXPECTED}/models/w.txt": "w\n"})
    output += [
        _run(repository, capsys, ["prune", *base]),
        _run(repository, capsys, ["check", *base]),
        _fragments(repository),
    ]
    assert_output("\n".join(output))


def test_declared_changes_declare_backfill(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--labels` and `--tests` declare the gaps of the base that the head still moves; `--rest` leaves them alone."""
    repository = _repository(
        tmp_path,
        {
            "base": SRC
            | {
                f"{EXPECTED}/models/a.py": "A = 2\n",
                f"{EXPECTED}/models/b.py": "B = 2\n",
                f"{EXPECTED}/models/d.json": "{}",
            },
        },
    )
    base = ["--base", "base"]
    record = ["--category", "harness", "--title", "t", "--summary", "s"]
    output = [
        _run(repository, capsys, ["declare", "backfill-b", *record, "--labels", "models/b.py", *base]),
        _run(repository, capsys, ["declare", "backfill-a", *record, "--tests", "*::test_model[[]a]", *base]),
        _run(repository, capsys, ["declare", "backfill-d", *record, "--rest", *base]),
        _run(repository, capsys, ["check", *base]),
        _run(repository, capsys, ["check", *base, "--main"]),
        _fragments(repository),
    ]
    assert_output("\n".join(output))


RENAMED: dict[str, tuple[dict[str, str | None], list[str]]] = {
    "covered by its old name": (
        SRC | NEW_OUTPUT | _record("new-output", ["models/new.py"], **OUTPUT_TEXT),
        ["new-output", "--tests", "*::test_renamed"],
    ),
    "a gap of the base": (
        SRC | NEW_OUTPUT,
        ["backfill-new", "--category", "harness", "--title", "t", "--summary", "s", "--labels", "*/renamed.py"],
    ),
}
"""The base of a change that renames the test of an output a src change added, and the `declare` that takes it."""


@pytest.mark.parametrize("case", sorted(RENAMED))
def test_declared_changes_declare_renamed_output(case: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A test-only rename of an output a src change added keeps its need of a record; `declare` takes its new name.

    Covered by its old name, it is a gap of the change and the old name is stale; a gap of the base stays an older gap.
    """
    lower, declare = RENAMED[case]
    repository = _repository(tmp_path, {"base": lower, "worktree": RENAMED_OUTPUT})
    base = ["--base", "base"]
    output = [_run(repository, capsys, [command, *base]) for command in ("check", "prune")]
    output += [
        _run(repository, capsys, ["declare", *declare, *base]),
        _run(repository, capsys, ["check", *base, "--main"]),
        _fragments(repository),
    ]
    assert_output("\n".join(output))


def test_declared_changes_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A revision that does not exist, a repository without a tag or without origin/main stops with exit 2.

    So does a release check without the history it walks: a tag on another line of history, a shallow clone; a tag
    after the base (a release commit's own) has no history to walk.
    """
    repository = _repository(tmp_path, {})
    shallow = tmp_path / "shallow"
    subprocess.run([*GIT, "clone", "-q", "--depth", "1", repository.as_uri(), str(shallow)], check=True)
    for name, parents in (("side", ["-p", "0.1.0"]), ("unrelated", [])):
        commit = subprocess.run(
            [*GIT, "commit-tree", "0.1.0^{tree}", *parents, "-m", name],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run([*GIT, "update-ref", f"refs/heads/{name}", commit], cwd=repository, check=True)
    output = [
        _run(repository, capsys, ["check", "--base", "nothing"]),
        _run(repository, capsys, ["check", "--base", "base", "--head", "nothing"]),
        _run(repository, capsys, ["check", "--base", "base", "--tag", "0.9.9"]),
        _run(repository, capsys, ["check", "--base", "base", "--tag", "0.1.0"]),
        _run(repository, capsys, ["check", "--base", "base", "--tag", "side"]),
        _run(repository, capsys, ["check", "--base", "base", "--tag", "unrelated"]),
        _run(repository, capsys, ["check", "--base", "0.1.0", "--head", "base", "--tag", "base"]),
        _run(shallow, capsys, ["check", "--base", "HEAD", "--tag", "HEAD"]),
        _run(repository, capsys, ["status"]),
    ]
    subprocess.run([*GIT, "tag", "-d", "0.1.0"], cwd=repository, check=True, capture_output=True)
    output.append(_run(repository, capsys, ["check", "--base", "base"]))
    assert_output("\n".join(output))
