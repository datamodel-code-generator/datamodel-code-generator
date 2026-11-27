"""End-to-end tests of scripts/declared_changes.py on temporary git repositories.

Each repository has a release tag, a base revision and a head revision; the stored outputs, their `.refs` sidecars and
the change records differ between them as a pull request, a stack layer or a release would make them differ.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from itertools import takewhile
from typing import TYPE_CHECKING, Any

import pytest

from scripts import declared_changes
from tests.conftest import assert_output
from tests.units import REFS_HEADER

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
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


CELLS = '\n[[change.cells]]\ninput = ["a.json"]\nbackends = "*"\noptions = "*"\n'
MATCHING_CELLS = CELLS + 'match = "warn"\n'


def _record(entry_id: str, covers: list[str], *, cells: str = "", **keys: Any) -> dict[str, str]:
    fields = {"category": "harness", "title": "t", "summary": "s"} | keys
    lines = [f"id = {json.dumps(entry_id)}", *(f"{key} = {json.dumps(value)}" for key, value in fields.items())]
    block = "".join(f'    "{label}",\n' for label in sorted(covers))
    text = "[[change]]\n" + "\n".join(lines) + "\n" + (f"covers = [\n{block}]\n" if covers else "")
    return {f"{FRAGMENTS}/{entry_id}.toml": text + cells}


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


def _repository(tmp_path: Path, spec: Mapping[str, Any]) -> Path:
    """Commit the release and tag it, commit the base on branch `base`, then the head on branch `fix-the-thing`.

    The `steps` are commits of main between the release and the base. With `origin`, origin/main points at the base,
    so the base is the default one; the commits of `layer` follow it on branch `base`, as a lower stack layer's do.
    The commits of `pushed` precede the head, and origin/main points at the head: a push of several pull requests.
    The files of `newer` are a commit of main beside the base, which the head merges; origin/main points at it.
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
    for number, commit in enumerate(spec.get("pushed", []), 1):
        _write(repository, commit)
        _commit(repository, f"Change something else ({number})")
    _write(repository, spec.get("head", {}))
    _commit(repository, spec.get("message", "Change the thing (#42)\n\nFixes: #41"))
    if spec.get("pushed"):
        subprocess.run([*GIT, "update-ref", "refs/remotes/origin/main", "HEAD"], cwd=repository, check=True)
    if "newer" in spec:
        subprocess.run([*GIT, "switch", "-q", "-c", "newer", "main~1"], cwd=repository, check=True)
        _write(repository, spec["newer"])
        _commit(repository, "main moved")
        subprocess.run([*GIT, "update-ref", "refs/remotes/origin/main", "newer"], cwd=repository, check=True)
        subprocess.run([*GIT, "switch", "-q", "fix-the-thing"], cwd=repository, check=True)
        subprocess.run([*GIT, "merge", "-q", "--no-ff", "-m", "Merge main", "newer"], cwd=repository, check=True)
    _write(repository, spec.get("worktree", {}))
    return repository


SRC = {"src/pkg.py": "VALUE = 2\n"}
OUTPUT_TEXT = {
    "cells": CELLS,
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
SHARED_B = {
    "tests/test_models.refs": _sidecar(
        REFS
        | {
            "test_model": [
                line if line != "b\tmodels/b.py" else "b\tmodels/b.py\t= models/a.py" for line in REFS["test_model"]
            ]
        }
    ),
    f"{EXPECTED}/models/b.py": None,
}
"""The output of `b` is now the stored file of `a`."""
OWN_C = {
    "tests/test_models.refs": _sidecar(
        REFS | {"test_model": [line if not line.startswith("c\t") else "c\tmodels/c.py" for line in REFS["test_model"]]}
    ),
    f"{EXPECTED}/models/c.py": "C = 9\n",
}
"""The output of `c` was the stored file of `a`; it is now a file of its own with other content."""
VARIANTS_B = {
    "tests/test_models.refs": _sidecar(
        REFS
        | {
            "test_model": [
                line
                if line != "b\tmodels/b.py"
                else "\tmodels/b.py\t@python==3.14\n\tmodels/b.py\t@python==3.15\t= models/a.py"
                for line in REFS["test_model"]
            ]
        }
    ),
}
"""The output of `b` is its own file under one Python and the stored file of `a` under another."""
TWO_UNITS_A = {
    "tests/test_models.refs": _sidecar(REFS | {"test_model": [*REFS["test_model"], "e\tmodels/a.py\t= models/b.py"]})
}
"""A second test item compares the label `models/a.py` with the stored file of `b`, under the same predicate."""
SWAPPED_A_B = {f"{EXPECTED}/models/a.py": "B = 1\n", f"{EXPECTED}/models/b.py": "A = 1\n"}
"""The stored files of `a` and `b` swap their contents: each test item of `models/a.py` compares the other one."""
FAST_PATHS = "main/cli_fast_paths"
FAST_PATH_LIST = f"{FAST_PATHS}/fast_paths_load_these_modules[help].txt"
FAST_PATH_REFUSAL = f"{FAST_PATHS}/parsed_fast_paths_refuse_the_options_of_the_skill_installer.result.txt"
FAST_PATH = {
    "tests/main/test_cli_fast_paths.refs": _sidecar({
        "test_fast_paths_load_these_modules": [f"help\t{FAST_PATH_LIST}"],
        "test_parsed_fast_paths_refuse_the_options_of_the_skill_installer": [f"\t{FAST_PATH_REFUSAL}"],
    }),
    f"{EXPECTED}/{FAST_PATH_LIST}": "pkg\npkg.arguments\n",
    f"{EXPECTED}/{FAST_PATH_REFUSAL}": "2\nError: --skill-scope requires --install-skill\n",
}
"""The stored module list of a CLI fast path (internal output) and the exit code and message of one of its refusals."""
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
                    "test_tree": [REFS["test_tree"][0], "py\tmodels/pkg//*.py\t@python==3.11"],
                }
            ),
        },
    },
    "selectors of an older revision in the old spelling": {
        "release": {
            "tests/test_models.refs": _sidecar(
                REFS | {"test_tree": [*REFS["test_tree"], "py\tmodels/pkg//*.py\t@formatter=builtin,python=3.11"]}
            )
        },
        "head": {
            "tests/test_models.refs": _sidecar(
                REFS | {"test_tree": [*REFS["test_tree"], "py\tmodels/pkg//*.py\t@formatter==builtin,python==3.11"]}
            )
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
    "a later layer extends a crash-fix record without cells": {
        "base": {f"{EXPECTED}/models/b.py": "B = 2\n"}
        | _record("lower", ["models/b.py"], category="crash-fix", refs=["#1"]),
        "head": {
            f"{EXPECTED}/models/b.py": "B = 3\n",
            f"{FRAGMENTS}/more.toml": '[[change]]\nid = "more"\nextends = "lower"\n' + COVERS_B,
        },
    },
    "a later layer extends a record that extends another": {
        "base": {f"{EXPECTED}/models/b.py": "B = 2\n"}
        | _record("lower", ["models/b.py"])
        | {f"{FRAGMENTS}/middle.toml": '[[change]]\nid = "middle"\nextends = "lower"\n'},
        "head": {
            f"{EXPECTED}/models/b.py": "B = 3\n",
            f"{FRAGMENTS}/more.toml": '[[change]]\nid = "more"\nextends = "middle"\n' + COVERS_B,
        },
    },
    "over-claimed and badly formed records": {
        "head": {f"{EXPECTED}/models/b.py": "B = 2\n"}
        | _record("claims", ["help/help.txt", "models/b.py"])
        | _record(
            "notice-models",
            ["models/b.py"],
            cells=MATCHING_CELLS,
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
    "harness on a src change the gate admits": {
        "head": SRC | {f"{EXPECTED}/models/b.py": "B = 2\n"} | _record("same-output", ["models/b.py"]),
        "verdicts": {"models/b.py": None},
    },
    "harness on a src change the gate refuses": {
        "head": SRC | {f"{EXPECTED}/models/b.py": "B = 2\n"} | _record("same-output", ["models/b.py"]),
        "verdicts": {"models/b.py": "models/b.py: [type] B: int -> float"},
    },
    "harness on a src change the gate judged for other contents": {
        "head": SRC | {f"{EXPECTED}/models/b.py": "B = 2\n"} | _record("same-output", ["models/b.py"]),
        "verdicts": {"models/b.py": None},
        "verdicts_head": "0.1.0",
    },
    "harness on a src change covers internal and added outputs": {
        "release": FAST_PATH,
        "head": SRC
        | NEW_OUTPUT
        | {f"{EXPECTED}/{FAST_PATH_LIST}": "pkg\n"}
        | _record("same-output", [FAST_PATH_LIST, "models/new.py"]),
    },
    "an internal record covers a changed refusal of a CLI fast path": {
        "release": FAST_PATH,
        "head": SRC
        | {f"{EXPECTED}/{FAST_PATH_REFUSAL}": "0\n\n"}
        | _record("same-output", [FAST_PATH_REFUSAL], category="internal"),
    },
    "a harness record covers a changed refusal of a CLI fast path": {
        "release": FAST_PATH,
        "head": SRC | {f"{EXPECTED}/{FAST_PATH_REFUSAL}": "0\n\n"} | _record("same-output", [FAST_PATH_REFUSAL]),
    },
    "harness on a change of uv.lock alone": {
        "head": {"uv.lock": "locked\n", f"{EXPECTED}/models/b.py": "B = 2\n"} | _record("same-output", ["models/b.py"]),
    },
    "harness on a src change that makes a label share another's output": {
        "head": SRC | SHARED_B | _record("same-output", ["models/b.py"]),
    },
    "harness on a src change that makes a label share another's output, the gate admits": {
        "head": SRC | SHARED_B | _record("same-output", ["models/b.py"]),
        "verdicts": {"models/b.py": None},
    },
    "harness on a src change that gives a label several stored outputs": {
        "head": SRC | VARIANTS_B | _record("same-output", ["models/b.py"]),
    },
    "harness on a src change that gives a label several stored outputs, the gate admits": {
        "head": SRC | VARIANTS_B | _record("same-output", ["models/b.py"]),
        "verdicts": {"models/b.py": None},
    },
    "harness on a src change that swaps the stored outputs of one label's test items": {
        "release": TWO_UNITS_A,
        "head": SRC | SWAPPED_A_B | _record("same-output", ["models/a.py", "models/b.py", "models/c.py"]),
        "verdicts": {"models/b.py": None, "models/c.py": None},
    },
    "harness on a src change that gives a shared label its own output": {
        "head": SRC | OWN_C | _record("same-output", ["models/c.py"]),
    },
    "harness on a src change that gives a shared label its own output, the gate admits": {
        "head": SRC | OWN_C | _record("same-output", ["models/c.py"]),
        "verdicts": {"models/c.py": None},
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
    "a push to main of a src change, then a test-only change a harness record covers": {
        "pushed": [SRC],
        "head": NEW_OUTPUT | {f"{EXPECTED}/models/b.py": "B = 2\n"} | _record("same-output", ["models/b.py"]),
        "args": ["--main"],
    },
    "a push to main of a test-only change, then a src change a harness record covers": {
        "pushed": [NEW_OUTPUT],
        "head": SRC | {f"{EXPECTED}/models/b.py": "B = 2\n"} | _record("same-output", ["models/b.py"]),
        "args": ["--main"],
    },
    "a push to main of a src change a harness record covers, then a test-only change": {
        "pushed": [SRC | {f"{EXPECTED}/models/b.py": "B = 2\n"} | _record("same-output", ["models/b.py"])],
        "head": NEW_OUTPUT,
        "args": ["--main"],
    },
    "a push to main of a src change a harness record covers, then its revert, with the gate's verdict": {
        "pushed": [SRC | {f"{EXPECTED}/models/b.py": "B = 2\n"} | _record("same-output", ["models/b.py"])],
        "head": RELEASED_SRC,
        "verdicts": {"models/b.py": None},
        "verdicts_head": "HEAD~1",
        "args": ["--main"],
    },
    "a push to main of a src change a harness record covers, then its revert, without the gate's verdict": {
        "pushed": [SRC | {f"{EXPECTED}/models/b.py": "B = 2\n"} | _record("same-output", ["models/b.py"])],
        "head": RELEASED_SRC,
        "args": ["--main"],
    },
    "the head merges a newer commit of main": {
        "newer": {"docs/notes.md": "notes\n"},
        "head": SRC | {f"{EXPECTED}/models/b.py": "B = 2\n"} | _record("same-output", ["models/b.py"]),
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
    """The static rules over references: changes, splits, moves, removals, additions, layers, records, the release.

    `verdicts` are the gate's on the labels a `harness` record covers in a src change, by the label's contents at the
    base and at `verdicts_head` (default the head). The report names a pushed commit by its distance from the head.
    """
    spec = CASES[case]
    repository = _repository(tmp_path, spec)
    args = ["check", "--base", "base", "--head", "HEAD", *spec.get("args", [])]
    if "verdicts" in spec:
        base, head = (declared_changes.Side(repository, rev) for rev in ("base", spec.get("verdicts_head", "HEAD")))
        rows = [
            {"label": label, "base": base.content(label), "head": head.content(label), "problem": problem}
            for label, problem in spec["verdicts"].items()
        ]
        (verdicts := tmp_path / "verdicts.json").write_text(json.dumps({"verdicts": rows}), encoding="utf-8")
        args += ["--verdicts", str(verdicts)]
    code = declared_changes.main(args, repository)
    printed = capsys.readouterr().out
    for number in range(1, len(spec.get("pushed", [])) + 1):
        commit = subprocess.check_output([*GIT, "rev-parse", f"HEAD~{number}"], cwd=repository, text=True)
        printed = printed.replace(commit.strip(), f"HEAD~{number}")
    assert_output(f"exit {code}\n{printed}")


CELL_RULE_CASES: dict[str, dict[str, str]] = {
    "a crash fix names no cells": _record("crash", [], category="crash-fix", refs=["#1"], decision=["#1"]),
    "a default fix selects no aspects or match": _record(
        "default",
        [],
        cells=CELLS,
        category="default-fix",
        kind="default-output",
        action="review",
        refs=["#1"],
        decision=["#1"],
    ),
    "an internal record names cells": _record("internal", [], cells=CELLS, category="internal"),
    "a crash fix names its cells": _record("crash", [], cells=CELLS, category="crash-fix", refs=["#1"]),
}


@pytest.mark.parametrize("case", sorted(CELL_RULE_CASES))
def test_check_applies_category_cell_rules(case: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A record the change adds is held to the cell rules of its category, as the gate holds it."""
    repository = _repository(tmp_path, {"head": CELL_RULE_CASES[case]})
    code = declared_changes.main(["check", "--base", "base", "--head", "HEAD"], repository)
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


RENAMED: dict[str, tuple[Mapping[str, str | None], list[str]]] = {
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
    (repository / "malformed-verdicts.json").write_text('{"verdicts": [{"label": "models/b.py"}]}', encoding="utf-8")
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
        _run(repository, capsys, ["check", "--base", "base", "--verdicts", "no-verdicts.json"]),
        _run(repository, capsys, ["check", "--base", "base", "--verdicts", "malformed-verdicts.json"]),
    ]
    subprocess.run([*GIT, "tag", "-d", "0.1.0"], cwd=repository, check=True, capture_output=True)
    output.append(_run(repository, capsys, ["check", "--base", "base"]))
    assert_output("\n".join(output))


CHECK_TREE = {
    name: (declared_changes.ROOT / name).read_text(encoding="utf-8")
    for name in (
        "scripts/__init__.py",
        "scripts/declared_changes.py",
        "scripts/_change_records.py",
        "scripts/compare_release.py",
        "tests/__init__.py",
        "tests/units.py",
    )
}
"""The check and the modules it imports as the repository has them, so that a temporary repository has a copy to run."""
UNDECLARED_B = SRC | {f"{EXPECTED}/models/b.py": "B = 2\n"}
"""A change of a stored output that no record covers."""


WEAKENED = {
    "scripts/declared_changes.py": CHECK_TREE["scripts/declared_changes.py"].replace(
        "        return bool(\n            self.undeclared",
        "        return False and bool(\n            self.undeclared",
        1,
    )
}
"""A change of the check that makes it pass every change (the head's own copy then passes the undeclared output)."""
CI_CASES: dict[str, Mapping[str, Mapping[str, str | None] | Sequence[str] | str]] = {
    "the verdicts file is missing where the gate wrote none": {
        "release": CHECK_TREE,
        "head": UNDECLARED_B | _record("same-output", ["models/b.py"]),
        "verdicts": "missing",
    },
    "the verdicts file is there": {
        "release": CHECK_TREE,
        "head": UNDECLARED_B | _record("same-output", ["models/b.py"]),
        "verdicts": "admit",
    },
    "no file of the check changes": {"release": CHECK_TREE, "head": UNDECLARED_B},
    "the check passes every change in the head": {"release": CHECK_TREE, "head": UNDECLARED_B | WEAKENED},
    "the check changes and the change is declared": {
        "release": CHECK_TREE,
        "head": UNDECLARED_B
        | _record("b-two", ["models/b.py"], **OUTPUT_TEXT)
        | {"tests/units.py": CHECK_TREE["tests/units.py"] + "\n"},
        "args": ["--main"],
    },
    "the check is judged by its base without a verdicts file": {
        "release": CHECK_TREE,
        "head": UNDECLARED_B | WEAKENED | _record("same-output", ["models/b.py"]),
        "verdicts": "missing",
    },
    "the base has no copy of the check": {"head": UNDECLARED_B | CHECK_TREE | WEAKENED},
    "the head adds a module to the check and weakens it": {
        "release": CHECK_TREE,
        "head": UNDECLARED_B | WEAKENED | {"scripts/_added.py": "VALUE = 1\n"},
    },
    "the head drops a module the base's check imports": {
        "release": CHECK_TREE
        | {
            "scripts/_dropped.py": "VALUE = 1\n",
            "scripts/_change_records.py": CHECK_TREE["scripts/_change_records.py"]
            + "\nfrom scripts._dropped import VALUE\n",
        },
        "head": UNDECLARED_B
        | _record("b-two", ["models/b.py"], **OUTPUT_TEXT)
        | {"scripts/_dropped.py": None, "scripts/_change_records.py": CHECK_TREE["scripts/_change_records.py"]},
    },
    "the head tightens the check and the base's copy passes": {
        "release": CHECK_TREE,
        "head": UNDECLARED_B
        | _record("b-two", ["models/b.py"], **OUTPUT_TEXT)
        | {
            "scripts/declared_changes.py": CHECK_TREE["scripts/declared_changes.py"].replace(
                "        return bool(\n            self.undeclared",
                "        return True or bool(\n            self.undeclared",
                1,
            )
        },
    },
    "the verdicts reach the base's copy": {
        "release": CHECK_TREE,
        "head": UNDECLARED_B
        | _record("same-output", ["models/b.py"])
        | {"tests/units.py": CHECK_TREE["tests/units.py"] + "\n"},
        "verdicts": "admit",
    },
}


@pytest.mark.parametrize("case", sorted(CI_CASES))
def test_declared_changes_check_in_ci(case: str, tmp_path: Path) -> None:
    """`check --ci` reads the gate's verdicts if there are some; the base's copy of the check judges a change of it.

    The head's own script runs, as it does in CI: with the base's copy of the check first and its own judgement after
    it, either of which fails the step. A base without the check has no copy, so the head's script judges alone.
    """
    spec = CI_CASES[case]
    repository = _repository(tmp_path, spec)
    args = ["check", "--base", "base", "--head", "HEAD", *spec.get("args", [])]
    if "verdicts" in spec:
        base, head = (declared_changes.Side(repository, rev) for rev in ("base", "HEAD"))
        rows = [{"label": "models/b.py", "base": base.content("models/b.py"), "head": head.content("models/b.py")}]
        (tmp_path / "verdicts.json").write_text(
            json.dumps({"verdicts": [row | {"problem": None} for row in rows]}), encoding="utf-8"
        )
        name = "verdicts.json" if spec["verdicts"] == "admit" else "missing.json"
        args += ["--verdicts", (tmp_path / name).as_posix()]
    run = subprocess.run(
        [sys.executable, str(repository / declared_changes.SCRIPT), args[0], "--ci", *args[1:]],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    shown = f"$ {args[0]} --ci {' '.join(args[1:])}\nexit {run.returncode}\n{run.stdout}{run.stderr}"
    assert_output(shown.replace(tmp_path.as_posix(), "TMP"))
