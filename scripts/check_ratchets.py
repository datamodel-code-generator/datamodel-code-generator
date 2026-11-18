"""Count the escape hatches of a source tree and compare them with a base revision.

An escape hatch switches a check off for some code. Each one is counted under a key ``<kind>:<code>@<area>``, where
the area is ``src``, ``tests``, ``scripts`` or ``other``; keys of configuration entries have no area:

- ``ruff:<rule>``: findings that a suppression hides, whatever its form (a ``noqa`` comment, a ``ruff: ignore``
  comment, a per-file ignore, a narrower rule selection), as the difference between a ruff run as the pre-commit hook
  runs it and one without those suppressions. Moving a hatch between these forms is neutral; hiding a new finding in
  the configuration counts like a new comment.
- ``ruff-config:ignore:<rule>``: rules ignored in the whole project, one key per rule whether ``lint.ignore`` or the
  hook's ``--extend-ignore`` lists it (their findings grow with every new function, so the entries are counted per
  entry, not per finding); ``ruff-config:exclude:<path>`` and ``ruff-config:hook-exclude:<pattern>``: paths ruff does
  not check, keyed by their text, so widening one in place is a new key; ``ruff-config:file:<path>``: every other
  ruff configuration file (``ruff.toml``, ``.ruff.toml``, a nested ``pyproject.toml`` with ``[tool.ruff]``).
- ``pragma:no-cover``, ``pragma:no-branch``, ``pragma:conditional-cover``, ``ty-ignore:<rule>``,
  ``pyright-ignore:<rule>`` and ``type-ignore:<code>`` comments, read with ``tokenize``.
- ``cast``, ``any`` (``Any`` inside an annotation or a ``cast`` target), ``getattr-private`` (a constant private
  name given to ``getattr`` and its siblings), ``dynamic-import``, ``filterwarnings-ignore``, ``no-type-check`` and
  ``abnormal-path`` (the test marker), read with ``ast``.
- Entries: ty rules below error, coverage ``omit`` and ``exclude`` lists (by text), pytest ``ignore`` warning filters
  and the direct-assert exemption list, the generation store allowlist, the host baseline
  (``allowlist:host-private-access``, the sum of its access counts), and the allowlists of the test style and
  architecture checks (``allowlist:<NAME>:unreadable`` when one is written in a form this script cannot read).
- ``unreadable:<what>`` and ``allowlist:<NAME>:unreadable``: a hook setting (no value or a block scalar, a ``--select``
  or ``--config`` argument) or an allowlist or host baseline this script cannot read. Such a key always fails under
  ``--enforce``, because what it hides cannot be counted.

The source comments and calls are counted in every ``.py`` and ``.pyi`` file outside ``tests/data``, whether ruff
checks it or not; ruff findings only in the files the hook checks. ``--base <ref>`` counts the merge-base of ``<ref>``
and HEAD and the working tree (untracked files included) and prints every key with both counts; the base is the budget,
so a key may stay or fall. When the base has its own copy of this script, that copy counts both trees too, so a change
cannot weaken the counter that judges it. For each key that rose, the report names the files whose count rose, their
sites and how to fix it. The exit status is 0 unless ``--enforce`` is given and a key rose.

``--count <dir>`` prints the counts of one tree as JSON, ``{key: {file: count}}``; a later copy of this script reads it
from the base revision's copy, so its form stays.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import tokenize
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import tomllib

SCRIPT = "scripts/check_ratchets.py"
STORE_ALLOWLIST = "scripts/generation_store_allowlist.toml"
HOST_BASELINE = "scripts/host_private_access_baseline.toml"
AREAS = frozenset({"src", "tests", "scripts"})
FIX_FLAGS = frozenset({"--fix", "--fix-only", "--unsafe-fixes", "--exit-non-zero-on-fix", "--show-fixes"})
IGNORE_FLAGS = frozenset({"--ignore", "--extend-ignore"})
RAW_FLAGS = (
    "--ignore-noqa",
    "--select",
    "ALL",
    "--preview",
    "--config",
    "lint.explicit-preview-rules = false",
)
PREVIEW_FLAGS = frozenset({"--preview", "--no-preview"})
UNCHECKED = ("tests/data/",)
HOOK_UNREADABLE_FLAGS = frozenset({"--select", "--config"})
AREA_GLOB = re.compile(r"(?:src|tests|scripts)/(?:\*\*/\*|\*\*|\*)(?:\.pyi?)?")
CHUNK = 200
PRAGMA = re.compile(r"\bpragma:\s*(no\s+cover|no\s+branch|\S+(?:\s+no)?\s+cover)\b")
PRAGMA_KINDS = {"no cover": "no-cover", "no branch": "no-branch"}
TYPE_IGNORE = re.compile(r"\b(ty|pyright|type):\s*ignore\b(?:\[([^\]]*)\])?")
COMMENT_HINT = re.compile(r"pragma:|ignore")
CALL_HINT = re.compile(
    r"cast|attr\(|attrgetter|__getattribute__|import_module|__import__|filterwarnings|simplefilter|catch_warnings"
    r"|abnormal_path|no_type_check|\bAny\b"
)
PRIVATE_READERS = {"getattr": 1, "setattr": 1, "hasattr": 1, "delattr": 1, "attrgetter": 0, "__getattribute__": 1}
WARNING_FILTERS = frozenset({"filterwarnings", "simplefilter", "catch_warnings"})
MARKERS = {"abnormal_path": "abnormal-path", "no_type_check": "no-type-check"}
ALLOWLISTS = (
    (
        "tests/assert_helper_allowlists.py",
        frozenset({
            "JUSTIFIED_VIOLATIONS",
            "FROZEN_VIOLATIONS",
            "LEGACY_VIOLATIONS",
            "FROZEN_EXEMPT_FILES",
            "FROZEN_UNCOVERED_FILES",
        }),
    ),
    ("tests/generated_import_closure_allowlist.py", frozenset({"UNBOUND_NAME_VIOLATIONS"})),
    ("scripts/check_architecture_boundaries.py", frozenset({"DEFAULT_ALLOWLIST"})),
)
ADVICE = {
    "ruff": (
        "Fix the finding instead of suppressing it. A noqa comment, a `ruff: ignore` comment and a per-file ignore "
        "count the same, so moving one changes nothing; if one must stay, remove another suppression of this rule in "
        "the same area."
    ),
    "ruff-config": (
        "A project-wide ignore or exclusion hides every present and future finding: fix the findings, or suppress "
        "each where it occurs (counted per finding)."
    ),
    "pragma": (
        "Reach the line with a test, or delete the code if no input reaches it; if the pragma must stay, remove "
        "another one in the same area."
    ),
    "ty-ignore": "Fix the type error or the annotation instead of ignoring it.",
    "pyright-ignore": "pyright checks only generated packages: drop the comment outside `_runtime`, or fix the type.",
    "type-ignore": "Fix the type error or the annotation instead of ignoring it.",
    "cast": "Narrow the type with isinstance, an annotation or a TypeGuard instead of cast().",
    "any": "Name the type (a Protocol, a TypeVar, object or a union) instead of Any.",
    "no-type-check": "Fix the annotations instead of switching the type checker off for the function.",
    "getattr-private": "A private name read through getattr() hides it from SLF001: give its owner a method instead.",
    "dynamic-import": "importlib and __import__ hide an import from the lint and the cycle checks: import it by name.",
    "filterwarnings-ignore": "Fix the warning's cause, or assert it with pytest.warns, instead of ignoring it.",
    "abnormal-path": "Reach the path end to end through the public entry points instead of marking it abnormal.",
    "coverage-config": "An omitted or excluded file is never measured: reach it with tests instead.",
    "ty-config": "A ty rule set below error hides its findings: fix them and keep the rule an error.",
    "pytest-config": "Fix the warning's cause, or assert it with pytest.warns, instead of filtering it for all tests.",
    "store-allowlist": (
        "Route the write through the generation store's mutation APIs (AGENTS.md) instead of listing it."
    ),
    "allowlist": "Allowlist entries only shrink: fix the violation instead of listing it.",
    "allowlist:host-private-access": (
        "Host baseline entries only shrink: declare the member on the Host Protocol the function takes, "
        "or move the code into the class."
    ),
    "unreadable": (
        "Write the ruff hook's `args:` and `exclude:` as inline values or a block list, without --select or --config "
        "(put those in pyproject.toml), so that the counter can read them."
    ),
    "unreadable-allowlist": (
        "Write the allowlist or baseline in the plain form its checker reads (literal entries, tables of integer "
        "counts), so that the counter can read it."
    ),
}


@dataclass
class Tally:
    """The sites of every key: key -> file -> line numbers (0 where an entry has no line)."""

    sites: defaultdict[str, defaultdict[str, list[int]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(list))
    )

    def add(self, key: str, path: str, line: int = 0) -> None:
        """Count one hatch of ``key`` in ``path``."""
        self.sites[key][path].append(line)

    def counts(self) -> dict[str, dict[str, int]]:
        """Return the number of hatches per key and file."""
        return {
            key: {path: len(lines) for path, lines in sorted(files.items())}
            for key, files in sorted(self.sites.items())
        }


@dataclass(frozen=True)
class Hook:
    """The ruff check hook of ``.pre-commit-config.yaml``."""

    rev: str = ""
    exclude: str = ""
    args: tuple[str, ...] = ()
    unreadable: tuple[str, ...] = ()


def _area(path: str) -> str:
    return head if (head := path.split("/", 1)[0]) in AREAS else "other"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)


def _tree_files(root: Path) -> list[str]:
    """List the files of a tree: tracked and untracked unignored files of a work tree, else every file."""
    top = _git(root, "rev-parse", "--show-toplevel")
    if top.returncode == 0 and Path(top.stdout.decode().strip()).resolve() == root.resolve():
        names = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").stdout.decode().split("\0")
        return sorted({name for name in names if name and (root / name).is_file()})
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


def _yaml_value(text: str) -> object:
    """Read the one-line YAML scalars and flow lists the hook configuration uses."""
    value = text.split(" #", 1)[0].strip()
    if value.startswith(("'", '"', "[")):
        return json.loads(value.replace("'", '"')) if value.startswith("'") else json.loads(value)
    return value


def _block_list(lines: list[str], indent: int) -> list[str]:
    """Read the items of the block-style YAML list that follows a key indented by ``indent``."""
    items: list[str] = []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"(\s*)-\s+(\S.*)", line)
        if not match or len(match[1]) < indent or re.match(r"[\w-]+:(\s|$)", match[2]):
            break
        items.append(str(_yaml_value(match[2])))
    return items


def _hook_value(text: str, following: list[str], indent: int) -> object:
    """Read an inline value or a block-style list; None where the form is neither."""
    inline = text.split(" #", 1)[0].strip()
    if inline[:1] in {"|", ">"}:
        return None
    try:
        return _yaml_value(inline) if inline else _block_list(following, indent) or None
    except ValueError:
        return None


def _ruff_hook(root: Path) -> Hook:
    """Read the rev, exclude pattern and arguments of the ruff check hook, and what it could not read."""
    path = root / ".pre-commit-config.yaml"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    lines = text.splitlines()
    repo = hook = rev = ""
    values: dict[str, object] = {}
    unreadable: list[str] = []
    for number, line in enumerate(lines):
        if match := re.match(r"\s*-\s*repo:\s*(\S+)", line):
            repo, hook = match[1], ""
        elif match := re.match(r"\s*rev:\s*(.+)", line):
            rev = str(_yaml_value(match[1])) if "ruff-pre-commit" in repo else rev
        elif match := re.match(r"\s*-\s*id:\s*(\S+)", line):
            hook = str(_yaml_value(match[1]))
        elif (match := re.match(r"(\s*)(exclude|args):\s*(.*)", line)) and "ruff-pre-commit" in repo and hook == "ruff":
            value = _hook_value(match[3], lines[number + 1 :], len(match[1]))
            if isinstance(value, list if match[2] == "args" else str):
                values[match[2]] = value
            else:
                unreadable.append(f"hook-{match[2]}")
    args = tuple(map(str, values.get("args", [])))
    unreadable += [
        f"hook-arg:{flag}" for flag in sorted(HOOK_UNREADABLE_FLAGS & {arg.partition("=")[0] for arg in args})
    ]
    return Hook(rev=rev, exclude=str(values.get("exclude", "")), args=args, unreadable=tuple(unreadable))


def _split_ignores(args: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Split hook arguments into the rules they ignore and the other arguments, without the fix flags."""
    ignored: list[str] = []
    rest: list[str] = []
    remaining = iter(args)
    for arg in remaining:
        flag, separator, inline = arg.partition("=")
        if flag in IGNORE_FLAGS:
            ignored += (inline if separator else next(remaining, "")).split(",")
        elif arg not in FIX_FLAGS:
            rest.append(arg)
    return [rule for rule in ignored if rule], rest


def _ignore_flags(rules: list[str]) -> list[str]:
    return ["--extend-ignore", ",".join(rules)] if rules else []


def _ruff_json(ruff: list[str], root: Path, flags: list[str], files: list[str]) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for start in range(0, len(files), CHUNK):
        command = [
            *ruff,
            "check",
            "--no-cache",
            "--exit-zero",
            "--output-format",
            "json",
            "--force-exclude",
            *flags,
            "--no-fix",
        ]
        output = subprocess.run(
            [*command, *files[start : start + CHUNK]], cwd=root, capture_output=True, text=True, check=True
        )
        findings.extend(json.loads(output.stdout))
    return findings


def _toml(value: object) -> str:
    """Write a value read from a TOML file back as TOML: JSON scalars and lists, inline tables for dicts."""
    if isinstance(value, dict):
        return "{" + ", ".join(f"{json.dumps(key)} = {_toml(item)}" for key, item in value.items()) + "}"
    return "[" + ", ".join(map(_toml, value)) + "]" if isinstance(value, list) else json.dumps(value)


def _without_extended_ignores(config: dict, root: Path) -> dict:
    """Return the ruff settings without ``extend-per-file-ignores``, which no override can clear: it is additive."""
    lint = {key: value for key, value in config.get("lint", {}).items() if key != "extend-per-file-ignores"}
    kept = {key: value for key, value in config.items() if key not in {"extend-per-file-ignores", "lint"}}
    if "extend" in kept:
        kept["extend"] = str(root / kept["extend"])
    return {**kept, "lint": lint}


def _relative(root: Path, name: str) -> str:
    return Path(name).resolve().relative_to(root.resolve()).as_posix()


def _rule_names(ruff: list[str]) -> dict[str, str]:
    """Map each rule name to its code, so a rule counts once however a configuration names it."""
    output = subprocess.run([*ruff, "rule", "--all", "--output-format", "json"], capture_output=True, check=True)
    return {rule["name"]: rule["code"] for rule in json.loads(output.stdout) if rule.get("code")}


def _ruff_tally(tally: Tally, root: Path, files: list[str], ruff: list[str], pyproject: dict) -> None:
    """Count the findings suppressions hide, the project-wide ignores and the paths ruff does not check."""
    hook = _ruff_hook(root)
    hook_ignores, hook_args = _split_ignores(hook.args)
    candidates = [
        name
        for name in files
        if name.endswith((".py", ".pyi")) and not (hook.exclude and re.search(hook.exclude, name))
    ]
    checked: list[str] = []
    for start in range(0, len(candidates), CHUNK):
        listed = subprocess.run(
            [*ruff, "check", "--show-files", "--force-exclude", *hook_args, *candidates[start : start + CHUNK]],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        checked.extend(_relative(root, line) for line in listed.stdout.splitlines() if line.strip())
    checked.sort()
    config = pyproject.get("tool", {}).get("ruff", {})
    lint = config.get("lint", {})
    project_ignores = [*lint.get("ignore", []), *lint.get("extend-ignore", []), *config.get("ignore", [])]
    area_policies: dict[str, list[str]] = defaultdict(list)
    for table in (lint.get("per-file-ignores", {}), lint.get("extend-per-file-ignores", {})):
        for glob, rules in table.items():
            area_policies[glob] += rules if AREA_GLOB.fullmatch(glob) else []
    names = _rule_names(ruff)
    for source, selectors in (("pyproject.toml", project_ignores), (".pre-commit-config.yaml", hook_ignores)):
        for selector in selectors:
            tally.add(f"ruff-config:ignore:{names.get(selector, selector)}", source)
    for glob, rules in area_policies.items():
        for rule in rules:
            tally.add(f"ruff-config:per-file:{glob}:{names.get(rule, rule)}", "pyproject.toml")
    for entry in (*config.get("exclude", []), *config.get("extend-exclude", [])):
        tally.add(f"ruff-config:exclude:{entry}", "pyproject.toml")
    for pattern in filter(None, re.split(r"\|(?![^()]*\))", hook.exclude)):
        tally.add(f"ruff-config:hook-exclude:{pattern}", ".pre-commit-config.yaml")
    for reason in hook.unreadable:
        tally.add(f"unreadable:{reason}", ".pre-commit-config.yaml")
    policy = [*project_ignores, *hook_ignores]
    kept = ", ".join(f"{json.dumps(glob)} = {json.dumps(rules)}" for glob, rules in area_policies.items() if rules)
    raw_flags = [*RAW_FLAGS, "--config", f"lint.per-file-ignores = {{{kept}}}"]
    raw_flags += [arg for arg in hook_args if arg not in PREVIEW_FLAGS]
    with tempfile.TemporaryDirectory() as directory:
        if config.get("extend-per-file-ignores") or lint.get("extend-per-file-ignores"):
            settings = Path(directory) / "ruff.toml"
            settings.write_text(
                "\n".join(
                    f"{json.dumps(key)} = {_toml(value)}"
                    for key, value in _without_extended_ignores(config, root).items()
                ),
                encoding="utf-8",
            )
            raw_flags += ["--config", str(settings)]
        raw = _ruff_json(ruff, root, [*raw_flags, *_ignore_flags(policy)], checked)
    normal = _ruff_json(ruff, root, [*hook_args, *_ignore_flags(hook_ignores)], checked)

    def sites(findings: list[dict[str, object]]) -> Counter[tuple[str, str, int]]:
        return Counter(
            (_relative(root, str(item["filename"])), str(item["code"] or item["name"]), item["location"]["row"])
            for item in findings
        )

    for path, code, line in sorted((sites(raw) - sites(normal)).elements()):
        tally.add(f"ruff:{code}@{_area(path)}", path, line)


def _comment_keys(text: str) -> list[str]:
    pragmas = [
        f"pragma:{PRAGMA_KINDS.get(' '.join(match[1].split()), 'conditional-cover')}" for match in PRAGMA.finditer(text)
    ]
    ignores = [
        f"{match[1]}-ignore:{rule.strip() or 'BARE'}"
        for match in TYPE_IGNORE.finditer(text)
        for rule in (match[2] or "").split(",")
    ]
    return [*pragmas, *ignores]


def _private_name(node: ast.expr) -> bool:
    if isinstance(node, ast.JoinedStr):
        node = node.values[0] if node.values else node
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith("_")
        and not node.value.endswith("__")
    )


def _ignores_warnings(node: ast.Call) -> bool:
    actions = [*node.args[:1], *(keyword.value for keyword in node.keywords if keyword.arg == "action")]
    return any(
        isinstance(action, ast.Constant)
        and isinstance(action.value, str)
        and (action.value == "ignore" or action.value.startswith("ignore:"))
        for action in actions
    )


def _name(node: ast.AST) -> str:
    return node.id if isinstance(node, ast.Name) else getattr(node, "attr", "")


def _type_positions(node: ast.AST) -> list[ast.AST]:
    """Return the parts of an annotation that are types, not ``Literal`` values or ``Annotated`` metadata."""
    if not isinstance(node, ast.Subscript):
        return list(ast.iter_child_nodes(node))
    kind = _name(node.value)
    if kind == "Literal":
        return [node.value]
    if kind == "Annotated" and isinstance(node.slice, ast.Tuple):
        return [node.value, *node.slice.elts[:1]]
    return list(ast.iter_child_nodes(node))


def _any_lines(node: ast.AST) -> list[int]:
    """Return the lines of the ``Any`` names in an annotation, string annotations included."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            inner = ast.parse(node.value.strip(), mode="eval").body
        except (SyntaxError, ValueError):
            return []
        return [node.lineno] * len(_any_lines(inner))
    lines = [node.lineno] if _name(node) == "Any" else []
    return lines + [line for part in _type_positions(node) for line in _any_lines(part)]


def _annotation(node: ast.AST) -> ast.expr | None:
    if isinstance(node, (ast.arg, ast.AnnAssign)):
        return node.annotation
    return node.returns if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None


def _node_sites(node: ast.AST, casts: set[str]) -> list[tuple[str, int]]:
    """Return the (key, line) hatches of one node: calls, markers and ``Any`` in annotations."""
    line = getattr(node, "lineno", 0)
    annotation = _annotation(node)
    sites = [("any", row) for row in _any_lines(annotation)] if annotation else []
    if isinstance(node, (ast.Name, ast.Attribute)) and _name(node) in MARKERS:
        sites.append((MARKERS[_name(node)], line))
    if not isinstance(node, ast.Call):
        return sites
    name = _name(node.func)
    index = PRIVATE_READERS.get(name, -1)
    keys = {
        "cast": name in casts,
        "getattr-private": 0 <= index < len(node.args) and _private_name(node.args[index]),
        "dynamic-import": name in {"import_module", "__import__"},
        "filterwarnings-ignore": name in WARNING_FILTERS and _ignores_warnings(node),
    }
    sites += [(key, line) for key, hit in keys.items() if hit]
    if name in casts and node.args:
        sites += [("any", row) for row in _any_lines(node.args[0])]
    return sites


def _source_sites(data: bytes) -> list[tuple[str, int]]:
    """Return the hatch comments (by tokenize) and hatch calls (by ast) of one file, none where it does not parse."""
    text = data.decode("utf-8", "replace")
    try:
        comments = (
            [token for token in tokenize.tokenize(io.BytesIO(data).readline) if token.type == tokenize.COMMENT]
            if COMMENT_HINT.search(text)
            else []
        )
        nodes = list(ast.walk(ast.parse(data))) if CALL_HINT.search(text) else []
    except (SyntaxError, tokenize.TokenError):
        return []
    casts = {"cast"}
    casts.update(
        alias.asname
        for node in nodes
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name == "cast" and alias.asname
    )
    return [
        *((key, token.start[0]) for token in comments for key in _comment_keys(token.string)),
        *(site for node in nodes for site in _node_sites(node, casts)),
    ]


def _source_tally(tally: Tally, root: Path, files: list[str]) -> None:
    """Count the hatch comments and calls of every Python file, whether ruff checks it or not."""
    for path in files:
        if path.endswith((".py", ".pyi")) and not path.startswith(UNCHECKED):
            for key, line in _source_sites((root / path).read_bytes()):
                tally.add(f"{key}@{_area(path)}", path, line)


def _ruff_files(tally: Tally, root: Path, files: list[str]) -> None:
    """Count every ruff configuration file other than the root ``pyproject.toml``."""
    for path in files:
        name = path.rsplit("/", 1)[-1]
        nested = name == "pyproject.toml" and "/" in path and b"[tool.ruff" in (root / path).read_bytes()
        if (name in {"ruff.toml", ".ruff.toml"} or nested) and not path.startswith(UNCHECKED):
            tally.add(f"ruff-config:file:{path}", path)


def _entries(node: ast.expr) -> list[ast.expr] | None:
    """Return the entries of an allowlist literal, or None where it is not one this script can read.

    The entries are the elements, or the members of a dict's collection values.
    """
    value = node.args[0] if isinstance(node, ast.Call) and node.args else node
    if isinstance(value, ast.Dict):
        groups = [item.elts for item in value.values if isinstance(item, (ast.Tuple, ast.List, ast.Set))]
        if len(groups) == len(value.values):
            return [entry for group in groups for entry in group]
        return [key for key in value.keys if key is not None]
    return list(value.elts) if isinstance(value, (ast.Tuple, ast.List, ast.Set)) else None


def _listed_name(node: ast.AST | None, names: frozenset[str]) -> str:
    """Return the listed name a target or owner expression is, or subscripts (``X["g"]``), or an empty string."""
    while isinstance(node, ast.Subscript):
        node = node.value
    return node.id if isinstance(node, ast.Name) and node.id in names else ""


def _method_owner(statement: ast.stmt, names: frozenset[str]) -> str:
    """Return the listed name a statement calls a method on (``update``, ``add``, ...), or an empty string."""
    call = statement.value if isinstance(statement, ast.Expr) else None
    owner = call.func.value if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) else None
    return _listed_name(owner, names)


def _toml_leaves(root: Path, path: str) -> list[object]:
    """Return the values of an allowlist file of ``["file"."function"]`` tables, or [] where there is none."""
    file = root / path
    tables = tomllib.loads(file.read_text(encoding="utf-8")) if file.is_file() else {}
    return [value for table in tables.values() for entry in table.values() for value in entry.values()]


def _host_baseline_tally(tally: Tally, root: Path) -> None:
    """Count the accesses of the host baseline; a file that is not tables of integer counts is unreadable."""
    try:
        counts = _toml_leaves(root, HOST_BASELINE)
    except (tomllib.TOMLDecodeError, AttributeError):
        counts = [None]
    for count in counts:
        key = "allowlist:host-private-access" + ("" if type(count) is int else ":unreadable")
        for _ in range(count if type(count) is int else 1):
            tally.add(key, HOST_BASELINE)


def _config_tally(tally: Tally, root: Path, pyproject: dict) -> None:
    """Count the configuration entries and allowlist entries that switch a check off."""
    tool = pyproject.get("tool", {})
    coverage = tool.get("coverage", {})
    for section, name, key in (
        ("run", "omit", "omit"),
        ("report", "omit", "omit"),
        ("report", "exclude_lines", "exclude"),
        ("report", "exclude_also", "exclude"),
    ):
        for entry in coverage.get(section, {}).get(name, []):
            tally.add(f"coverage-config:{key}:{entry}", "pyproject.toml")
    ty = tool.get("ty", {})
    for rules in (ty.get("rules", {}), *(override.get("rules", {}) for override in ty.get("overrides", []))):
        for rule in (rule for rule, level in rules.items() if level in {"ignore", "warn"}):
            tally.add(f"ty-config:{rule}", "pyproject.toml")
    pytest = tool.get("pytest", {})
    filters = [*pytest.get("filterwarnings", []), *pytest.get("ini_options", {}).get("filterwarnings", [])]
    for _ in (entry for entry in filters if str(entry).startswith("ignore")):
        tally.add("pytest-config:filterwarnings-ignore", "pyproject.toml")
    for _ in pytest.get("ini_options", {}).get("assert_helper_direct_assert_exempt_files", []):
        tally.add("allowlist:direct-assert-exempt-files", "pyproject.toml")
    _host_baseline_tally(tally, root)
    for value in _toml_leaves(root, STORE_ALLOWLIST):
        counted = isinstance(value, int)
        for _ in range(value if counted else 1):
            tally.add(f"store-allowlist:{'counted' if counted else 'witness'}", STORE_ALLOWLIST)
    for path, names in ALLOWLISTS:
        module = (
            ast.parse((root / path).read_bytes()) if (root / path).is_file() else ast.Module(body=[], type_ignores=[])
        )
        for statement in module.body:
            target = getattr(statement, "target", None) or next(iter(getattr(statement, "targets", [])), None)
            value = getattr(statement, "value", None)
            if owner := _method_owner(statement, names):
                tally.add(f"allowlist:{owner}:unreadable", path, statement.lineno)
            elif (listed := _listed_name(target, names)) and value is not None:
                entries = _entries(value)
                for entry in entries or []:
                    tally.add(f"allowlist:{listed}", path, entry.lineno)
                if entries is None:
                    tally.add(f"allowlist:{listed}:unreadable", path, statement.lineno)


def count_tree(root: Path, ruff: list[str]) -> Tally:
    """Count every escape hatch of the tree at ``root``."""
    path = root / "pyproject.toml"
    pyproject = tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    tally = Tally()
    files = _tree_files(root)
    _ruff_tally(tally, root, files, ruff, pyproject)
    _ruff_files(tally, root, files)
    _source_tally(tally, root, files)
    _config_tally(tally, root, pyproject)
    return tally


def _totals(counts: dict[str, dict[str, int]]) -> dict[str, int]:
    return {key: sum(files.values()) for key, files in counts.items()}


def _rises(base: dict[str, dict[str, int]], head: dict[str, dict[str, int]]) -> list[str]:
    base_totals, head_totals = _totals(base), _totals(head)
    return [
        key
        for key, total in sorted(head_totals.items())
        if total > base_totals.get(key, 0) and _kind(key) != "unreadable"
    ]


def _file_rises(base: dict[str, int], head: dict[str, int]) -> list[tuple[str, int, int]]:
    return [(path, base.get(path, 0), count) for path, count in sorted(head.items()) if count > base.get(path, 0)]


def _kind(key: str) -> str:
    if key.endswith(":unreadable"):
        return "unreadable"
    return re.split(r"[:@]", key, maxsplit=1)[0]


def _table(columns: tuple[str, ...], rows: dict[str, tuple[str, ...]]) -> list[str]:
    width = max(map(len, rows), default=3)
    lines = [f"{'key':<{width}}" + "".join(f"  {column:>6}" for column in columns)]
    lines += [f"{key:<{width}}" + "".join(f"  {value:>6}" for value in values) for key, values in sorted(rows.items())]
    return lines


def _change_table(base: dict[str, int], head: dict[str, int]) -> list[str]:
    pairs = {key: (base.get(key, 0), head.get(key, 0)) for key in {*base, *head}}
    rows = {key: (str(was), str(now), f"{now - was:+d}" if now != was else "0") for key, (was, now) in pairs.items()}
    return _table(("base", "head", "change"), rows)


def _rise_lines(
    key: str, base: dict[str, dict[str, int]], head: dict[str, dict[str, int]], sites: dict[str, list[int]]
) -> list[str]:
    """Describe one key that rose: its totals, how to fix it, and each file whose count rose with its sites."""
    before, after = sum(base.get(key, {}).values()), sum(head[key].values())
    lines = ["", f"{key} rose by {after - before} ({before} -> {after}). {ADVICE.get(key, ADVICE.get(_kind(key), ''))}"]
    for path, was, now in _file_rises(base.get(key, {}), head[key]):
        at = ", ".join(map(str, sorted(line for line in sites.get(path, []) if line)))
        lines.append(f"  {path}: {was} -> {now}" + (f" (lines {at})" if at else ""))
    return lines


def _report_rises(base: Tally, head: Tally) -> list[str]:
    base_counts, head_counts = base.counts(), head.counts()
    rises = _rises(base_counts, head_counts)
    return [line for key in rises for line in _rise_lines(key, base_counts, head_counts, head.sites[key])]


def _copy_counts(copy: Path, root: Path, ruff_args: list[str], what: str, base: str) -> dict[str, dict[str, int]]:
    output = subprocess.run(
        [sys.executable, str(copy), "--count", str(root), *ruff_args], capture_output=True, text=True, check=False
    )
    if output.returncode:
        msg = (
            f"The base revision's {SCRIPT} cannot count {what}:\n{output.stderr}\n"
            f"To reproduce, run `python <(git show {base}:{SCRIPT}) --count .` at the root of that tree, and change "
            "the head so that the base revision's counter can read it (a hook or configuration form it does not know)."
        )
        raise SystemExit(msg)
    return json.loads(output.stdout)


def _report_copy(base_root: Path, head_root: Path, ruff_args: list[str], seen: set[str], base_commit: str) -> list[str]:
    """Count both trees with the base revision's copy of this script and report the rises only it sees."""
    copy = base_root / SCRIPT
    if not copy.is_file():
        return []
    base = _copy_counts(copy, base_root, ruff_args, "the base revision", base_commit)
    head = _copy_counts(copy, head_root, ruff_args, "the working tree", base_commit)
    unseen = [key for key in _rises(base, head) if key not in seen]
    hidden = {key: files for key, files in head.items() if key not in seen}
    header = [
        "",
        f"The base revision's {SCRIPT} also counts these rises, which this copy does not: the change weakens the",
        "counter that judges it. Keep the counter's rules (changes to it are reviewed) and fix the hatches:",
    ]
    body = [line for key in unseen for line in _rise_lines(key, base, head, {})]
    body += _unreadable_lines(hidden)
    return [*header, *body] if body else []


def _unreadable_lines(counts: dict[str, dict[str, int]]) -> list[str]:
    """Describe the settings the counter could not read: what they hide cannot be counted, so they always fail."""
    return [
        line
        for key, files in counts.items()
        if _kind(key) == "unreadable"
        for line in (
            "",
            (
                f"{key} in {', '.join(files)}: the counter cannot read it. "
                f"{ADVICE['unreadable-allowlist' if key.startswith('allowlist:') else 'unreadable']}"
            ),
        )
    ]


def _compare(base_ref: str, head_root: Path, ruff: list[str], ruff_args: list[str]) -> tuple[list[str], bool]:
    fork = _git(head_root, "merge-base", base_ref, "HEAD").stdout.decode().strip() or base_ref
    with tempfile.TemporaryDirectory() as directory:
        base_root = Path(directory)
        archive = subprocess.run(
            ["git", "archive", "--format=tar", fork], cwd=head_root, capture_output=True, check=True
        ).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(base_root, filter="data")
        base, head = count_tree(base_root, ruff), count_tree(head_root, ruff)
        head_rises = set(_rises(base.counts(), head.counts()))
        lines = [f"Escape hatches: {base_ref} (merge-base {fork}) -> working tree", ""]
        lines += _change_table(_totals(base.counts()), _totals(head.counts()))
        lines += _report_rises(base, head)
        unreadable = _unreadable_lines(head.counts())
        lines += unreadable
        seen = head_rises | {key for key in head.counts() if _kind(key) == "unreadable"}
        copy_lines = _report_copy(base_root, head_root, ruff_args, seen, fork)
    rose = bool(head_rises or copy_lines or unreadable)
    verdict = (
        "Some keys rose: the budget is the base. Exit status 0 unless --enforce is given. "
        f"Re-run with `python {SCRIPT} --base {base_ref}`."
    )
    lines += [*copy_lines, "", verdict if rose else "No key rose."]
    return lines, rose


def _pin_note(root: Path, ruff: list[str]) -> str:
    pin = _ruff_hook(root).rev.removeprefix("v")
    version = subprocess.run([*ruff, "--version"], capture_output=True, text=True, check=True).stdout.split()[-1]
    return (
        f"note: ruff {version} counts, .pre-commit-config.yaml pins {pin}; pass --ruff <that ruff>\n"
        if pin and pin != version
        else ""
    )


def main(argv: list[str] | None = None) -> int:
    """Print the escape-hatch counts, or their changes against a base revision."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", help="git revision whose counts are the budget")
    parser.add_argument("--enforce", action="store_true", help="exit 1 when a key rose")
    parser.add_argument("--count", type=Path, help="print the counts of this tree as JSON")
    parser.add_argument("--ruff", help="ruff executable (default: this interpreter's ruff module)")
    parser.add_argument("--ruff-pin", action="store_true", help="print the ruff version the pre-commit hook pins")
    args = parser.parse_args(argv)
    ruff = [args.ruff] if args.ruff else [sys.executable, "-m", "ruff"]
    if args.count:
        print(json.dumps(count_tree(args.count.resolve(), ruff).counts(), indent=1, sort_keys=True))
        return 0
    top = _git(Path.cwd(), "rev-parse", "--show-toplevel")
    head_root = Path(top.stdout.decode().strip()) if top.returncode == 0 else Path.cwd()
    if args.ruff_pin:
        print(_ruff_hook(head_root).rev.removeprefix("v"))
        return 0
    sys.stderr.write(_pin_note(head_root, ruff))
    if not args.base:
        counts = _totals(count_tree(head_root, ruff).counts())
        print("\n".join(_table(("count",), {key: (str(count),) for key, count in counts.items()})))
        return 0
    lines, rose = _compare(args.base, head_root, ruff, ["--ruff", args.ruff] if args.ruff else [])
    print("\n".join(lines))
    return 1 if rose and args.enforce else 0


if __name__ == "__main__":
    sys.exit(main())
