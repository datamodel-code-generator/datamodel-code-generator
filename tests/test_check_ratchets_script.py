"""Count the escape hatches of temporary git repositories against their base commit through the script's CLI."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import check_ratchets
from tests.conftest import assert_output

ROOT = Path(__file__).resolve().parents[1]
GIT = ["git", "-c", "user.name=CI", "-c", "user.email=ci@example.com", "-c", "commit.gpgsign=false"]
PYPROJECT = """[tool.ruff]
extend-exclude = ["scripts"]
lint.select = ["ALL"]
lint.ignore = ["CPY", "D", "INP"]
lint.preview = true
lint.per-file-ignores."tests/**/*.py" = ["S101"]
"""
SIZE = """

def size() -> int:
    import json  # noqa: PLC0415

    return len(json.dumps(1))
"""
NAME = """

def name() -> str:
    return "a"
"""
FUTURE = "from __future__ import annotations\n"
BASE = {
    "pyproject.toml": PYPROJECT + '\n[tool.ty]\nrules.deprecated = "error"\n',
    ".pre-commit-config.yaml": """repos:
- repo: https://github.com/astral-sh/ruff-pre-commit
  rev: v0.0.0
  hooks:
  - id: ruff-format
  - id: ruff
    exclude: "^src/pkg/generated/"
    args: ["--preview", "--fix", "--extend-ignore", "ERA001"]
""",
    "src/pkg/__init__.py": "",
    "src/pkg/a.py": FUTURE + SIZE + NAME,
    "src/pkg/generated/out.py": "import os  # noqa: F401\n",
    "src/pkg/broken.py": 'value = cast("int", (\n',
    "tests/test_a.py": FUTURE
    + """
import warnings


def test_size() -> None:
    warnings.simplefilter("error")
    assert getattr(warnings, "filters") is not None
""",
}
OTHER_KINDS = {
    "pyproject.toml": PYPROJECT
    + """
[tool.coverage]
run.omit = ["src/pkg/d.py"]
report.exclude_also = ["if TYPE_CHECKING:"]

[tool.ty]
rules.deprecated = "warn"

[[tool.ty.overrides]]
include = ["src"]
rules.unresolved-import = "ignore"

[tool.pytest]
ini_options.filterwarnings = ["error", "ignore::DeprecationWarning"]
""",
    "src/pkg/d.py": FUTURE
    + """
import importlib
import warnings
from typing import cast


def risky(value: object) -> int:  # pragma: no cover
    if value:  # pragma: no branch
        warnings.filterwarnings("ignore")
    warnings.filterwarnings(action="ignore", category=DeprecationWarning)
    module = importlib.import_module("json")  # ty: ignore[unresolved-import]
    secret = getattr(module, "_default_encoder", None)  # pyright: ignore[reportPrivateUsage]
    return cast("int", secret)  # type: ignore[return-value]  # pragma: win32 cover
""",
    "tests/test_d.py": FUTURE
    + """
import pytest


@pytest.mark.abnormal_path("reason")
def test_d() -> None:
    pytest.importorskip("json")
""",
    "scripts/generation_store_allowlist.toml": """["src/pkg/d.py"."risky"]
'_cache[...]' = 2
witness = "--input schema.json"
""",
    "tests/assert_helper_allowlists.py": FUTURE
    + """
from pathlib import Path

JUSTIFIED_VIOLATIONS: dict[str, tuple[str, ...]] = {"group": ("rule:tests/test_d.py",)}
FROZEN_EXEMPT_FILES = frozenset({Path("test_d.py")})
""",
    "scripts/check_architecture_boundaries.py": 'DEFAULT_ALLOWLIST = {("a", "b"): "reason"}\n',
}
HOST_BASELINE = """["src/pkg/a.py"."run"]
"host._cache" = 1
"host._size" = 1
"""
ALLOWLIST = 'FROZEN_EXEMPT_FILES = frozenset({"a.py"})\n'
HOOK_START = """repos:
- repo: https://github.com/astral-sh/ruff-pre-commit
  rev: v0.0.0
  hooks:
  - id: ruff
"""
PRAGMA_FILE = FUTURE + NAME.replace("-> str:", "-> str:  # pragma: no cover")
ANY_USES = (
    FUTURE
    + """
import typing
from typing import Any, cast


def keep(value: Any, other: "dict[str, Any]", odd: "a b") -> typing.Any:
    local: list[Any] = [value]
    return cast("dict[str, Any]", cast(typing.Any, local)), other, odd
"""
)
ANY_VALUES = (
    FUTURE
    + """
from typing import Annotated, Literal, cast


def keep(value: Literal["Any"], other: Annotated[int, "Any"], third: Annotated["Any", "Any"]) -> "Literal['Any']":
    return cast("Literal['Any'] | Annotated[int, 'Any']", value), other, third
"""
)
LOCAL_IMPORT = (
    FUTURE
    + """

def show() -> None:
    import json

    json.dumps(1)
"""
)
DODGES = (
    FUTURE
    + """
import operator
import warnings
from typing import cast as narrow
from typing import no_type_check


@no_type_check
def hidden(value: object, name: str) -> object:
    with warnings.catch_warnings(action="ignore"):
        return narrow("int", operator.attrgetter("_x")(value)), object.__getattribute__(value, f"_{name}")
"""
)
SCRIPT = (ROOT / "scripts/check_ratchets.py").read_text(encoding="utf-8")
CASES = {
    "unchanged": {"base": {"scripts/check_ratchets.py": SCRIPT}, "head": {}},
    "added-hatch": {"head": {"src/pkg/b.py": FUTURE + SIZE}, "args": ["--enforce"]},
    "moved-line": {"head": {"src/pkg/a.py": FUTURE + NAME, "src/pkg/b.py": FUTURE + SIZE}, "args": ["--enforce"]},
    "form-swap": {
        "head": {
            "src/pkg/a.py": FUTURE + SIZE.replace("noqa: PLC0415", "ruff: ignore[import-outside-top-level]") + NAME
        }
    },
    "hidden-by-config": {
        "head": {
            "pyproject.toml": BASE["pyproject.toml"].replace(
                "lint.per-file-ignores.",
                'lint.per-file-ignores."src/pkg/c.py" = ["import-outside-top-level"]\n'
                'lint.per-file-ignores."src/**/*.py" = ["FBT001"]\nlint.per-file-ignores.',
            ),
            ".pre-commit-config.yaml": BASE[".pre-commit-config.yaml"].replace('"ERA001"', '"ERA001,T201"'),
            "src/pkg/c.py": FUTURE
            + """

def show(flag: bool) -> None:
    import json

    print(json.dumps(flag))
""",
        },
        "args": ["--enforce"],
    },
    "other-kinds": {"head": OTHER_KINDS},
    "hook-exclude-widened": {
        "head": {
            ".pre-commit-config.yaml": BASE[".pre-commit-config.yaml"].replace(
                "^src/pkg/generated/", "^src/pkg/(generated/|b)"
            ),
            "src/pkg/b.py": PRAGMA_FILE,
        },
        "args": ["--enforce"],
    },
    "extend-exclude-swapped": {
        "head": {
            "pyproject.toml": BASE["pyproject.toml"].replace('["scripts"]', '["src/pkg/b.py"]'),
            "src/pkg/b.py": PRAGMA_FILE,
        },
        "args": ["--enforce"],
    },
    "coverage-omit-widened": {
        "base": {"pyproject.toml": PYPROJECT + '\n[tool.coverage]\nrun.omit = ["src/pkg/generated/*"]\n'},
        "head": {"pyproject.toml": PYPROJECT + '\n[tool.coverage]\nrun.omit = ["src/*"]\n'},
        "args": ["--enforce"],
    },
    "nested-ruff-config": {
        "head": {
            "src/pkg/sub/ruff.toml": 'extend = "../../../pyproject.toml"\nexclude = ["b.py"]\n',
            "src/pkg/sub/b.py": PRAGMA_FILE,
            "src/pkg/other/pyproject.toml": "[tool.ruff]\nline-length = 100\n",
            "src/pkg/third/pyproject.toml": "[project]\nname = 'third'\n",
            "tests/data/fixture/ruff.toml": "line-length = 100\n",
        },
        "args": ["--enforce"],
    },
    "exempt-files-listed": {
        "base": {
            "pyproject.toml": PYPROJECT
            + '\n[tool.pytest]\nini_options.assert_helper_direct_assert_exempt_files = ["a.py"]\n'
        },
        "head": {
            "pyproject.toml": PYPROJECT
            + '\n[tool.pytest]\nini_options.assert_helper_direct_assert_exempt_files = ["a.py", "b.py"]\n'
        },
        "args": ["--enforce"],
    },
    "host-baseline-grows": {
        "base": {"scripts/host_private_access_baseline.toml": HOST_BASELINE},
        "head": {
            "scripts/host_private_access_baseline.toml": HOST_BASELINE.replace("= 1", "= 3")
            + '\n["src/pkg/b.py"."run"]\n"host._cache" = 2\n'
        },
        "args": ["--enforce"],
    },
    "host-baseline-shrinks": {
        "base": {"scripts/host_private_access_baseline.toml": HOST_BASELINE},
        "head": {"scripts/host_private_access_baseline.toml": HOST_BASELINE.replace("= 1", "= 0")},
        "args": ["--enforce"],
    },
    "host-baseline-unreadable": {
        "head": {"scripts/host_private_access_baseline.toml": 'x = 1\n["src/pkg/a.py"."run"\n'},
        "args": ["--enforce"],
    },
    "host-baseline-unreadable-shape": {
        "head": {"scripts/host_private_access_baseline.toml": "count = 1\n"},
        "args": ["--enforce"],
    },
    "host-baseline-unreadable-value": {
        "head": {"scripts/host_private_access_baseline.toml": '["src/pkg/a.py"."run"]\n"host._x" = "two"\n'},
        "args": ["--enforce"],
    },
    "allowlist-unreadable": {
        "base": {"tests/assert_helper_allowlists.py": ALLOWLIST},
        "head": {
            "tests/assert_helper_allowlists.py": 'FROZEN_EXEMPT_FILES = frozenset(f"{n}.py" for n in ("a", "b"))\n'
            'JUSTIFIED_VIOLATIONS = {}\nJUSTIFIED_VIOLATIONS.update({"g": ("x", "y")})\n'
        },
        "args": ["--enforce"],
    },
    "any-uses": {"head": {"src/pkg/c.py": ANY_USES}, "args": ["--enforce"]},
    "any-values": {"head": {"src/pkg/c.py": ANY_VALUES}, "args": ["--enforce"]},
    "dodges": {"head": {"src/pkg/c.py": DODGES}, "args": ["--enforce"]},
    "fix-enabled": {
        "head": {
            "pyproject.toml": BASE["pyproject.toml"].replace("lint.select", "fix = true\nlint.select"),
            "src/pkg/b.py": "import os  # noqa: F401\n",
        },
        "args": ["--enforce"],
    },
    "extended-per-file-ignores": {
        "head": {
            "pyproject.toml": BASE["pyproject.toml"].replace(
                "lint.per-file-ignores.",
                'lint.extend-per-file-ignores."src/pkg/b.py" = ["PLC0415"]\n'
                'lint.extend-per-file-ignores."src/**/*.py" = ["FBT001"]\nextend = "base.toml"\nlint.per-file-ignores.',
            ),
            "base.toml": "line-length = 100\n",
            "src/pkg/b.py": LOCAL_IMPORT,
        },
        "args": ["--enforce"],
    },
    "top-level-extended-ignores": {
        "head": {
            "pyproject.toml": BASE["pyproject.toml"].replace(
                "lint.per-file-ignores.",
                'extend-per-file-ignores."src/pkg/b.py" = ["PLC0415"]\nlint.per-file-ignores.',
            ),
            "src/pkg/b.py": LOCAL_IMPORT,
        },
        "args": ["--enforce"],
    },
    "quoted-hook-id": {
        "head": {
            ".pre-commit-config.yaml": HOOK_START.replace("id: ruff", 'id: "ruff"')
            + '    exclude: "^src/pkg/generated/"\n    args: ["--extend-ignore", "T201"]\n'
        },
        "args": ["--enforce"],
    },
    "allowlist-subscript-writes": {
        "base": {"tests/assert_helper_allowlists.py": ALLOWLIST},
        "head": {
            "tests/assert_helper_allowlists.py": ALLOWLIST
            + 'JUSTIFIED_VIOLATIONS = {"g": ("a",)}\nJUSTIFIED_VIOLATIONS["g"] += ("b",)\n'
            'JUSTIFIED_VIOLATIONS["h"] = ("c", "d")\nFROZEN_VIOLATIONS = {"g": ["a"]}\n'
            'FROZEN_VIOLATIONS["g"].append("x")\nLEGACY_VIOLATIONS = {}\n'
            'LEGACY_VIOLATIONS["g"] += FROZEN_VIOLATIONS["g"]\n'
        },
        "args": ["--enforce"],
    },
    "explicit-preview-rules": {
        "base": {
            "src/pkg/m.py": FUTURE + "\n\nclass M:\n    def run(self) -> int:\n        return 1\n",
        },
        "head": {
            "pyproject.toml": PYPROJECT
            + 'lint.explicit-preview-rules = true\n\n[tool.ty]\nrules.deprecated = "error"\n'
        },
        "args": ["--enforce"],
    },
    "block-style-args": {
        "head": {
            ".pre-commit-config.yaml": HOOK_START
            + """    exclude: "^src/pkg/generated/"
    args:
      # kept

      - --preview
      - --fix
      - --extend-ignore
      - "ERA001,T201"
- repo: https://github.com/codespell-project/codespell
  rev: v2.4.3
  hooks:
  - id: codespell
""",
            "src/pkg/b.py": FUTURE + "\n\ndef show() -> None:\n    print(1)\n",
        },
        "args": ["--enforce"],
    },
    "unreadable-hook": {
        "head": {
            ".pre-commit-config.yaml": HOOK_START
            + """    exclude: |
      ^src/pkg/generated/
    args: ['--fix', "--preview"]
"""
        },
        "args": ["--enforce"],
    },
    "unreadable-arguments": {
        "head": {
            ".pre-commit-config.yaml": HOOK_START
            + """    args:
      - --select=E
      - --config
      - lint.preview = true
"""
        },
        "args": ["--enforce"],
    },
    "ahead-base": {
        "ahead": {"src/pkg/a.py": FUTURE + NAME},
        "base_ref": "ahead",
        "head": {"src/pkg/d.py": FUTURE + NAME},
        "args": ["--enforce"],
    },
    "weakened-counter": {
        "base": {"scripts/check_ratchets.py": SCRIPT},
        "head": {
            "scripts/check_ratchets.py": SCRIPT.replace(
                r'PRAGMA = re.compile(r"\bpragma:', r'PRAGMA = re.compile(r"\bnever:'
            ),
            "src/pkg/a.py": FUTURE + SIZE + NAME.replace("-> str:", "-> str:  # pragma: no cover"),
        },
        "subprocess": True,
    },
    "weakened-counter-unreadable": {
        "base": {"scripts/check_ratchets.py": SCRIPT},
        "head": {
            "scripts/check_ratchets.py": SCRIPT.replace(
                'HOOK_UNREADABLE_FLAGS = frozenset({"--select", "--config"})',
                'HOOK_UNREADABLE_FLAGS = frozenset({"--select"})',
            ),
            ".pre-commit-config.yaml": HOOK_START + '    args: ["--config", "lint.preview = true"]\n',
        },
        "args": ["--enforce"],
        "subprocess": True,
    },
}


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def _repository(
    tmp_path: Path, base: dict[str, str], head: dict[str, str], ahead: dict[str, str] | None = None
) -> Path:
    """Commit the base files on branch ``base``, ``ahead`` files on a branch beyond it, then the head on ``main``."""
    repository = tmp_path / "repository"
    _write(repository, {**BASE, **base})
    for command in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"], ["branch", "base"]):
        subprocess.run([*GIT, *command], cwd=repository, check=True)
    if ahead:
        subprocess.run([*GIT, "switch", "-q", "-c", "ahead"], cwd=repository, check=True)
        _write(repository, ahead)
        for command in (["add", "-A"], ["commit", "-q", "-m", "ahead"], ["switch", "-q", "main"]):
            subprocess.run([*GIT, *command], cwd=repository, check=True)
    _write(repository, head)
    for command in (["add", "-A"], ["commit", "-q", "--allow-empty", "-m", "head"]):
        subprocess.run([*GIT, *command], cwd=repository, check=True)
    return repository


def _stable(text: str) -> str:
    """Replace commit ids, which differ on every run, by a placeholder."""
    return re.sub(r"\b[0-9a-f]{40}\b", "<commit>", text)


@pytest.mark.parametrize("case", CASES)
def test_check_ratchets_against_base(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rises are reported with their files and fix; moves and form swaps are neutral; the base's counter also judges."""
    spec = CASES[case]
    repository = _repository(tmp_path, spec.get("base", {}), spec["head"], spec.get("ahead"))
    args = ["--base", spec.get("base_ref", "base"), *spec.get("args", [])]
    if spec.get("subprocess"):
        command = [sys.executable, "scripts/check_ratchets.py", *args]
        result = subprocess.run(command, cwd=repository, capture_output=True, text=True, check=False)
        code, stdout = result.returncode, result.stdout
    else:
        monkeypatch.chdir(repository)
        code = check_ratchets.main(args)
        stdout = capsys.readouterr().out
    assert_output(f"exit status {code}\n{_stable(stdout)}")


def test_check_ratchets_reports_a_failing_base_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A base copy that cannot count a tree ends the run with its message and the command to reproduce it."""
    repository = _repository(tmp_path, {"scripts/check_ratchets.py": "import sys\n\nsys.exit('cannot read')\n"}, {})
    monkeypatch.chdir(repository)
    with pytest.raises(SystemExit) as raised:
        check_ratchets.main(["--base", "base"])
    assert_output(f"{_stable(str(raised.value))}\n{capsys.readouterr().out}")


def test_check_ratchets_counts_one_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without a base the script prints each key's count; --count prints them per file; --ruff-pin the hook's pin."""
    repository = _repository(tmp_path, {}, OTHER_KINDS)
    monkeypatch.chdir(repository / "src")
    code = check_ratchets.main([])
    assert_output(f"exit status {code}\n{capsys.readouterr().out}", name="table")
    code = check_ratchets.main(["--count", ".."])
    assert_output(f"exit status {code}\n{capsys.readouterr().out}", name="json")
    code = check_ratchets.main(["--ruff-pin"])
    assert_output(f"exit status {code}\n{capsys.readouterr().out}", name="ruff-pin")
