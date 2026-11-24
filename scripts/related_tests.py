"""Print the test node ids, the environments or a row's paths a diff selects: changed tests, mirrors and table rows.

A changed test function or class of the test suite selects itself (a new module, a helper module or changed
module-level code the whole module), a changed source module or script its mirror, a module the stored fast-path
lists name the fast-path tests, and the rows of related_tests.toml the tests they name.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import Path

import tomllib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts._source_model import SourceModelError, SourceModule

RULES = Path(__file__).with_name("related_tests.toml")
SRC = "src/datamodel_code_generator/"
FAST_PATH_TEST = "tests/main/test_cli_fast_paths.py"
FAST_PATH_LISTS = "tests/data/expected/main/cli_fast_paths/"
TESTS = "tests/"
TEST_INPUTS = "tests/data/"
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", re.MULTILINE)
MODULE_NAME = re.compile(r'"(datamodel_code_generator[\w.]*)"')
TOX = "tox.ini"
TOX_SECTION = re.compile(r"^\[testenv:([^\]]+)\]", re.MULTILINE)
TOX_FACTORS = re.compile(r"\{([^{}]*)\}")
LANES = frozenset({"nightly-tier", "dispatch"})


class GitError(Exception):
    """A git command failed, so the diff could not be read."""


def _git(root: Path, *args: str, ok: tuple[int, ...] = (0,)) -> str:
    """Run git and return its output; a status outside `ok` raises, so a failure never reads as an empty diff."""
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    if result.returncode not in ok:
        message = f"git {' '.join(args)} failed with status {result.returncode}"
        raise GitError(message)
    return result.stdout


def _merge_base(root: Path, base: str, head: str | None) -> str:
    """Return the merge base of base and head, or the base when the histories share none."""
    return _git(root, "merge-base", base, head or "HEAD", ok=(0, 1)).strip() or base


def _untracked(root: Path) -> list[str]:
    """Return the untracked files that are not ignored, with their names unquoted."""
    return [path for path in _git(root, "ls-files", "-z", "-o", "--exclude-standard").split("\0") if path]


def _exists(root: Path, head: str | None, path: str) -> bool:
    """Whether the file or directory is in the head commit, or in the working tree without a head."""
    if head is None:
        return (root / path).exists()
    return bool(_git(root, "ls-tree", head, "--", path).strip())


def _changes(root: Path, base: str, head: str | None) -> dict[str, str]:
    """Map each changed path to its diff letter, for base...head or for the merge base and the working tree."""
    left = _merge_base(root, base, head)
    fields = _git(root, "diff", "-z", "--name-status", "--no-renames", left, *([head] if head else [])).split("\0")
    changed = {path: letter[0] for letter, path in zip(fields[0::2], fields[1::2], strict=False)}
    if head is None:
        changed.update(dict.fromkeys(_untracked(root), "A"))
    return changed


def _changed_lines(root: Path, base: str, head: str | None, paths: list[str]) -> list[str]:
    """Return the added and removed lines of the paths; without a head, untracked files count as added."""
    left = _merge_base(root, base, head)
    diff = _git(root, "diff", "-U0", "--no-renames", left, *([head] if head else []), "--", *paths)
    lines = [line[1:] for line in diff.splitlines() if line[:1] in "+-" and line[:3] not in {"+++", "---"}]
    if head is None:
        for path in sorted(set(paths) & set(_untracked(root))):
            lines.extend((root / path).read_text(encoding="utf-8", errors="replace").splitlines())
    return lines


def _matches(path: str, globs: list[str]) -> bool:
    return any(fnmatchcase(path, glob) for glob in globs)


def mirrors(path: str) -> list[str]:
    """Name the test modules that mirror a source module or a script."""
    if path.startswith(SRC) and path.endswith(".py"):
        directory, _, name = path.removeprefix(SRC).rpartition("/")
        prefix = f"tests/{directory}/" if directory else "tests/"
        stem = name.removesuffix(".py")
        return [f"{prefix}test_{stem}.py", f"{prefix}test_{stem.removeprefix('_')}.py"]
    if path.startswith("scripts/") and path.endswith(".py"):
        return [f"tests/test_{path.rpartition('/')[2].removesuffix('.py')}_script.py"]
    return []


def own_module(path: str) -> bool:
    """Whether a path is a module of the test suite (a test module or a helper), whose change selects its own tests.

    The suite's inputs under tests/data are no such module: the rows of related_tests.toml select their tests.
    """
    return path.startswith(TESTS) and path.endswith(".py") and not path.startswith(TEST_INPUTS)


def test_units(model: SourceModule) -> list[tuple[range, str | None]]:
    """Return the top-level statements of a test module with their lines and the node id suffix they select.

    A `test_*` function and a `Test*` class (the scope whose header the statement starts) are units of their own (a
    parametrized test is its function); any other statement (imports, fixtures, constants, helpers) is module-level
    code, None (a fixture too, even one named `test_*`).
    """
    units: list[tuple[range, str | None]] = []
    for node in model.tree.body:
        scope = model.header_of(node.lineno, node.col_offset)
        prefix = {"function": "test", "class": "Test"}.get(scope.kind if scope else "")
        named = scope is not None and prefix is not None and scope.qualname.startswith(prefix)
        test = named and "pytest.fixture" not in model.decorator_origins(node)
        units.append((model.extent(node), scope.qualname if test and scope else None))
    return units


def _touched(model: SourceModule, numbers: range) -> set[str | None]:
    """Return the units the changed lines `numbers` fall in; a changed line of code outside every unit is None.

    Blank and comment-only lines change nothing a test runs.
    """
    units = test_units(model)
    return {
        next((name for lines, name in units if number in lines), None)
        for number in numbers
        if number in model.code_lines
    }


def _changed_tests(root: Path, base: str, head: str | None, path: str, letter: str) -> list[str]:
    """Return the node ids a change of a test-suite module selects.

    A new module, a helper module (not `test_*.py`), a change of module-level code or a side the source model cannot
    read selects the module; otherwise the `test_*` functions and `Test*` classes whose lines (on either side) changed.
    """
    if letter == "A" or not path.rpartition("/")[2].startswith("test_"):
        return [path]
    left = _merge_base(root, base, head)
    new = (root / path).read_text(encoding="utf-8") if head is None else _git(root, "show", f"{head}:{path}")
    old = _git(root, "show", f"{left}:{path}")
    try:
        sides = [SourceModule(text, path) for text in (old, new)]
    except (SyntaxError, SourceModelError):
        return [path]
    diff = _git(root, "diff", "-U0", "--no-renames", left, *([head] if head else []), "--", path)
    touched: set[str | None] = set()
    for match in HUNK.finditer(diff):
        for side, (start, count) in enumerate(((match[1], match[2]), (match[3], match[4]))):
            first = int(start)
            touched |= _touched(sides[side], range(first, first + int(count or "1")))
    if None in touched:
        return [path]
    present = {name for _, name in test_units(sides[1]) if name is not None}
    return [f"{path}::{name}" for name in sorted(present & {name for name in touched if name is not None})]


def _module_name(path: str) -> str | None:
    """Return the dotted name of a package module file (`a/b.py` or `a/b/__init__.py`), or None for other paths."""
    if not (path.startswith(SRC) and path.endswith(".py")):
        return None
    parts = ["datamodel_code_generator", *path.removeprefix(SRC).removesuffix(".py").split("/")]
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _fast_path_modules(root: Path, base: str, head: str | None) -> set[str]:
    """Return the modules the stored fast-path lists name: from the merge base, or the working tree without a head."""
    if head is None:
        files = [path.read_text(encoding="utf-8") for path in sorted((root / FAST_PATH_LISTS).glob("fast_paths_*.txt"))]
    else:
        left = _merge_base(root, base, head)
        names = _git(root, "ls-tree", "-z", "--name-only", left, FAST_PATH_LISTS).split("\0")
        files = [
            _git(root, "show", f"{left}:{name}") for name in names if name.rpartition("/")[2].startswith("fast_paths_")
        ]
    return {name for text in files for name in MODULE_NAME.findall(text)}


def _row_paths(root: Path, base: str, head: str | None, changed: dict[str, str], rule: dict) -> list[str]:
    """Return the changed paths that fire a row: it matches the path, and an added or removed line its pattern."""
    status = rule.get("status")
    hit = [
        path
        for path, letter in changed.items()
        if _matches(path, rule["paths"])
        and not _matches(path, rule.get("exclude", []))
        and (status is None or letter in status)
    ]
    if hit and "pattern" in rule:
        pattern = re.compile(rule["pattern"])
        hit = [path for path in hit if any(pattern.search(line) for line in _changed_lines(root, base, head, [path]))]
    return sorted(hit)


def _fired(root: Path, base: str, head: str | None, changed: dict[str, str], rules: list[dict]) -> list[dict]:
    """Return the rows a change fires: a changed path matches, and an added or removed line matches the pattern."""
    return [rule for rule in rules if _row_paths(root, base, head, changed, rule)]


def row_paths(root: Path, base: str, head: str | None, rules: list[dict], row: str) -> list[str]:
    """Return the sorted changed paths that fire the row with the id `row`; an unknown id raises KeyError.

    The payload suite reads the inputs the `payload-inputs` row names this way: each input holds its own cases.
    """
    rule = next((rule for rule in rules if rule["id"] == row), None)
    if rule is None:
        raise KeyError(row)
    return _row_paths(root, base, head, _changes(root, base, head), rule)


def select(root: Path, base: str, head: str | None, rules: list[dict]) -> list[str]:
    """Return the sorted node ids the diff selects: changed tests, mirrors and every row that fires."""
    changed = _changes(root, base, head)
    nodes: set[str] = set()
    for path, letter in changed.items():
        if letter == "D":
            continue
        if own_module(path) and _exists(root, head, path):
            nodes.update(_changed_tests(root, base, head, path, letter))
        nodes.update(next(([test] for test in mirrors(path) if _exists(root, head, test)), []))
    listed = _fast_path_modules(root, base, head)
    if any(_module_name(path) in listed for path, letter in changed.items() if letter != "D") and _exists(
        root, head, FAST_PATH_TEST
    ):
        nodes.add(FAST_PATH_TEST)
    for rule in _fired(root, base, head, changed, rules):
        nodes.update(test for test in rule.get("tests", []) if _exists(root, head, test.partition("::")[0]))
    return sorted(nodes)


def select_envs(root: Path, base: str, head: str | None, rules: list[dict]) -> dict[str, list[str]]:
    """Map each environment the diff needs, sorted by name, to the ids of the rows that ask for it."""
    envs: dict[str, list[str]] = {}
    for rule in _fired(root, base, head, _changes(root, base, head), rules):
        for env in rule.get("envs", []):
            envs.setdefault(env, []).append(rule["id"])
    return dict(sorted(envs.items()))


def _expand(name: str) -> list[str]:
    """Expand the `{a,b}` factor groups of a tox section name, an empty option included."""
    if not (group := TOX_FACTORS.search(name)):
        return [name]
    head, tail = name[: group.start()], name[group.end() :]
    return [expanded for option in group[1].split(",") for expanded in _expand(f"{head}{option}{tail}")]


def _tox_envs(root: Path) -> set[str]:
    """Return the environment names tox.ini defines, factor groups expanded."""
    text = (root / TOX).read_text(encoding="utf-8") if (root / TOX).exists() else ""
    return {env for section in TOX_SECTION.findall(text) for env in _expand(section)}


def problems(root: Path, rules: list[dict]) -> list[str]:
    """One line per row without an incident, with a repeated id, an unknown environment or a missing target."""
    found: list[str] = []
    seen: set[str] = set()
    known = (_tox_envs(root) if any(rule.get("envs") for rule in rules) else set()) | LANES
    for rule in rules:
        if rule["id"] in seen:
            found.append(f"{rule['id']}: the id is used twice")
        seen.add(rule["id"])
        if not rule.get("incident"):
            found.append(f"{rule['id']}: no incident")
        if not (rule.get("tests") or rule.get("envs")):
            found.append(f"{rule['id']}: selects no test and no environment")
        if rule.get("envs") and not rule.get("prs"):
            found.append(f"{rule['id']}: envs without the pull requests of their incident (prs)")
        found.extend(f"{rule['id']}: unknown environment {env}" for env in rule.get("envs", []) if env not in known)
        for test in rule.get("tests", []):
            file, _, function = test.partition("::")
            path = root / file
            if not path.exists() or (
                function and not re.search(rf"^\s*def {function}\(", path.read_text("utf-8"), re.MULTILINE)
            ):
                found.append(f"{rule['id']}: missing target {test}")
    return found


def _selection(root: Path, args: argparse.Namespace, rules: list[dict]) -> list[str]:
    """Return the lines the arguments ask for: a row's changed paths, the environments or the test node ids."""
    if args.paths_of:
        return row_paths(root, args.base, args.head, rules, args.paths_of)
    if args.envs:
        return [f"{env}\t{','.join(ids)}" for env, ids in select_envs(root, args.base, args.head, rules).items()]
    return select(root, args.base, args.head, rules)


def main(argv: list[str] | None = None) -> int:
    """Print the selection of a diff, or check that every row's target exists."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="the commit the change is measured against")
    parser.add_argument("--head", help="the commit with the change (default: the working tree)")
    parser.add_argument("--rules", type=Path, default=RULES, help="the rows to read (default: related_tests.toml)")
    parser.add_argument(
        "--check",
        action="store_true",
        help="list the rows with a problem (missing target, unknown environment, no incident) and exit 1 if any",
    )
    parser.add_argument(
        "--envs",
        action="store_true",
        help="print the environments the change needs, each with the rows that ask for it, instead of the tests",
    )
    parser.add_argument(
        "--paths-of",
        metavar="ROW",
        help="print the changed paths that fire the row with this id, instead of the tests",
    )
    args = parser.parse_args(argv)
    rules = tomllib.loads(args.rules.read_text(encoding="utf-8"))["rule"]
    root = Path.cwd()
    if args.check:
        found = problems(root, rules)
        sys.stdout.write("".join(f"{line}\n" for line in found))
        return 1 if found else 0
    if not args.base:
        parser.error("--base is required unless --check is given")
    try:
        lines = _selection(root, args, rules)
    except GitError as error:
        sys.stderr.write(f"related_tests: {error}\n")
        return 2
    except KeyError as error:
        sys.stderr.write(f"related_tests: no row has the id {error}\n")
        return 2
    sys.stdout.write("".join(f"{line}\n" for line in lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
