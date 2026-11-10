"""Prepare Claude's copy of a merged PR diff and the release baseline facts for the release draft analysis.

The exact diff stays with the trusted verifier. Claude's copy keeps every line of at most 2,000 characters byte for
byte. A longer changed line paired with its counterpart shows only the segments that changed; any other long changed
line is folded into pieces without loss, and a long unchanged context line is shortened. If the copy is still over
the reading budget, the diff of each stored output becomes one summary line. Every line the workflow writes starts
with ``⟪dcg <nonce>⟫`` after the diff's sign, so PR text cannot pass for one. The registries that name the surfaces
of a release are read through ``git show`` with ``ast`` and regular expressions; no repository code is imported.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from itertools import accumulate, groupby
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

LINE_LIMIT = 2_000
CONTEXT = 160
CONTEXT_HEAD = 320
PIECE = 1_800
BUDGET_CHARACTERS = 400_000
BUDGET_LINES = 12_000
TOKEN = re.compile(r"\\n|\s+|[^\s\\]+|\\")
HUNK = re.compile(rb"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")
NONCE = re.compile(r"[0-9a-f]{16}")
MERGE_SHA = re.compile(r"[0-9a-f]{40}")
RELEASE_TAG = re.compile(r"(?:[0-9]+\.[0-9]+\.[0-9]+)?")
FEATURE_ID = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*")
OPTION = re.compile(r"(?<![\w-])--[a-z0-9]+(?:-[a-z0-9]+)*(?![\w-])")
PRESET = re.compile(r'^\s*"([A-Za-z0-9][A-Za-z0-9._-]*)",?\s*$', re.MULTILINE)
PACKAGE = "src/datamodel_code_generator"
CHANGED_KINDS = frozenset({"old", "new", "marker"})
SIGNS = {"old": ("-", "removed line"), "new": ("+", "added line")}
STATUS_PREFIXES = (
    (b"new file mode ", "added"),
    (b"deleted file mode ", "deleted"),
    (b"rename from ", "renamed"),
    (b"copy from ", "copied"),
)
STORED_OUTPUT_FILES = frozenset({"docs/llms-full.txt", "docs/llms.txt"})

Names = dict[str, str]


@dataclass
class _Line:
    """One diff line: its exact bytes, its text and its role in the diff."""

    raw: bytes
    text: str
    kind: str


@dataclass
class _FileDiff:
    """The lines of one file's diff, from its ``diff --git`` header on."""

    lines: list[_Line] = field(default_factory=list)

    @property
    def path(self) -> str:
        """Return the path after the change, as the ``diff --git`` header names it."""
        return self.lines[0].text.rpartition(" b/")[2]


def _pattern_argument(pattern: re.Pattern[str], what: str) -> Callable[[str], str]:
    """Return an argparse type that accepts only values matching ``pattern`` exactly."""

    def parse(value: str) -> str:
        if not pattern.fullmatch(value):
            msg = f"expected {what}"
            raise argparse.ArgumentTypeError(msg)
        return value

    return parse


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the paths and identities supplied by the release workflow."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--diff-path", required=True, type=Path)
    parser.add_argument("--merge-sha", required=True, type=_pattern_argument(MERGE_SHA, "a full commit SHA"))
    parser.add_argument("--baseline-tag", required=True, type=_pattern_argument(RELEASE_TAG, "a release tag or ''"))
    parser.add_argument("--nonce", required=True, type=_pattern_argument(NONCE, "16 lowercase hex digits"))
    parser.add_argument("--claude-diff-path", required=True, type=Path)
    parser.add_argument("--claude-deleted-lines-path", required=True, type=Path)
    parser.add_argument("--sections-path", required=True, type=Path)
    parser.add_argument("--repository-path", default=Path(), type=Path)
    return parser.parse_args(argv)


def _parse_diff(lines: list[bytes]) -> list[_FileDiff]:
    """Split a unified git diff into file diffs and mark each line as header, hunk, context, old, new or marker."""
    files = [_FileDiff()]
    old = new = 0
    for raw in lines:
        if old > 0 or new > 0:
            match raw[:1]:
                case b" ":
                    old, new, kind = old - 1, new - 1, "context"
                case b"-":
                    old, kind = old - 1, "old"
                case b"+":
                    new, kind = new - 1, "new"
                case _:
                    kind = "marker"
        elif hunk := HUNK.match(raw):
            old, new, kind = int(hunk[1] or 1), int(hunk[2] or 1), "hunk"
        else:
            if raw.startswith(b"diff --git "):
                files.append(_FileDiff())
            kind = "header"
        files[-1].lines.append(_Line(raw, raw.decode("utf-8", "surrogateescape"), kind))
    return [file for file in files if file.lines]


def _changed_segments(old: str, new: str) -> list[tuple[int, int, int, int]]:
    """Return the ``(old_start, old_end, new_start, new_end)`` character ranges where ``old`` and ``new`` differ.

    The common prefix and suffix are trimmed back to whitespace, the rest is compared token by token, and changes
    at most twice the shown context apart become one segment.
    """
    shortest = min(len(old), len(new))
    prefix = next((index for index in range(shortest) if old[index] != new[index]), shortest)
    suffix = next(
        (index for index in range(shortest - prefix) if old[-1 - index] != new[-1 - index]), shortest - prefix
    )
    while prefix and not old[prefix - 1].isspace():
        prefix -= 1
    while suffix and not old[len(old) - suffix].isspace():
        suffix -= 1
    old_tokens = TOKEN.findall(old[prefix : len(old) - suffix])
    new_tokens = TOKEN.findall(new[prefix : len(new) - suffix])
    old_offsets = list(accumulate(map(len, old_tokens), initial=prefix))
    new_offsets = list(accumulate(map(len, new_tokens), initial=prefix))
    segments: list[tuple[int, int, int, int]] = []
    matcher = difflib.SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
    for tag, old_first, old_last, new_first, new_last in matcher.get_opcodes():
        if tag == "equal":
            continue
        segment = (old_offsets[old_first], old_offsets[old_last], new_offsets[new_first], new_offsets[new_last])
        if segments and segment[0] - segments[-1][1] <= 2 * CONTEXT:
            previous = segments.pop()
            segment = (previous[0], segment[1], previous[2], segment[3])
        segments.append(segment)
    return segments


def _pieces(prefix: str, label: str, text: str, start: int) -> list[str]:
    """Return lines that show ``text``, which starts at character ``start`` of its line, in numbered pieces."""
    chunks = [text[offset : offset + PIECE] for offset in range(0, len(text), PIECE)]
    lines = []
    for number, chunk in enumerate(chunks, 1):
        first = start + (number - 1) * PIECE + 1
        parts = [label] if label else []
        if len(chunks) > 1:
            parts.append(f"piece {number} of {len(chunks)}")
        parts.append(f"characters {first:,}-{first + len(chunk) - 1:,}")
        lines.append(f"{prefix}{', '.join(parts)}: {chunk}")
    return lines


def _condensed_pair(nonce: str, old: str, new: str) -> tuple[list[str], list[str]]:
    """Return the lines that show the changed segments of a long removed line and the added line paired with it."""
    segments = _changed_segments(old, new)
    old_prefix, new_prefix = f"-⟪dcg {nonce}⟫ ", f"+⟪dcg {nonce}⟫ "
    old_lines = [
        (
            f"{old_prefix}condensed removed line of {len(old):,} characters, paired with the condensed added line of "
            f"{len(new):,} characters below; changed segments: {len(segments)}, each shown with up to {CONTEXT} "
            "characters of unchanged text around it; the rest of both lines is the same and left out"
        )
    ]
    new_lines = [
        (
            f"{new_prefix}condensed added line of {len(new):,} characters, paired with the condensed removed line "
            f"above; changed segments: {len(segments)}"
        )
    ]
    for number, (old_start, old_end, new_start, new_end) in enumerate(segments, 1):
        label = f"segment {number} of {len(segments)}"
        old_first, new_first = max(0, old_start - CONTEXT), max(0, new_start - CONTEXT)
        old_lines += _pieces(old_prefix, label, old[old_first : old_end + CONTEXT], old_first)
        new_lines += _pieces(new_prefix, label, new[new_first : new_end + CONTEXT], new_first)
    return old_lines, new_lines


def _prepared_line(nonce: str, line: _Line, counts: Counter[str]) -> list[str] | list[bytes]:
    """Return one line as it appears in Claude's copy: unchanged up to the limit, otherwise shortened or folded."""
    if len(line.text) <= LINE_LIMIT:
        return [line.raw]
    if line.kind == "context":
        counts["shortened"] += 1
        return [
            (
                f" ⟪dcg {nonce}⟫ unchanged context line of {len(line.text) - 1:,} characters; its first {CONTEXT_HEAD} "
                f"are shown: {line.text[1 : CONTEXT_HEAD + 1]}"
            )
        ]
    counts["folded"] += 1
    sign, what = SIGNS.get(line.kind, ("", f"{line.kind} line"))
    text = line.text[len(sign) :]
    prefix = f"{sign}⟪dcg {nonce}⟫ "
    return [f"{prefix}folded {what} of {len(text):,} characters; nothing is left out", *_pieces(prefix, "", text, 0)]


def _prepared_file(nonce: str, file: _FileDiff, counts: Counter[str]) -> list[bytes]:
    """Return Claude's copy of one file's diff, pairing the long removed and added lines of each change."""
    replaced: dict[int, list[str]] = {}
    for changed, run in groupby(enumerate(file.lines), key=lambda item: item[1].kind in CHANGED_KINDS):
        lines = list(run) if changed else []
        long_old = [(index, line) for index, line in lines if line.kind == "old" and len(line.text) > LINE_LIMIT]
        long_new = [(index, line) for index, line in lines if line.kind == "new" and len(line.text) > LINE_LIMIT]
        for (old_index, old_line), (new_index, new_line) in zip(long_old, long_new, strict=False):
            old_lines, new_lines = _condensed_pair(nonce, old_line.text[1:], new_line.text[1:])
            if 2 * sum(map(len, old_lines + new_lines)) > len(old_line.text) + len(new_line.text):
                continue
            replaced[old_index], replaced[new_index] = old_lines, new_lines
            counts["condensed"] += 2
    return [
        item if isinstance(item, bytes) else item.encode("utf-8", "surrogateescape")
        for index, line in enumerate(file.lines)
        for item in replaced.get(index) or _prepared_line(nonce, line, counts)
    ]


def _is_stored_output(path: str) -> bool:
    """Return whether a path holds output that tests store and compare."""
    return path.startswith("tests/data/expected/") or path.endswith(".refs") or path in STORED_OUTPUT_FILES


def _summary_line(nonce: str, file: _FileDiff) -> bytes:
    """Return the one line that replaces a stored output's diff: its path, status and line counts."""
    kinds = Counter(line.kind for line in file.lines)
    status = next(
        (
            status
            for prefix, status in STATUS_PREFIXES
            for line in file.lines
            if line.kind == "header" and line.raw.startswith(prefix)
        ),
        "modified",
    )
    return (
        f"⟪dcg {nonce}⟫ stored output summarised: {file.path} ({status}, {kinds['new']:,} added and "
        f"{kinds['old']:,} removed lines)"
    ).encode("utf-8", "surrogateescape")


def _measure(lines: list[bytes]) -> tuple[int, int, int]:
    """Return the characters, lines and longest line of a diff."""
    lengths = [len(line.decode("utf-8", "surrogateescape")) for line in lines]
    return sum(lengths) + len(lengths), len(lengths), max(lengths, default=0)


def _git_show(repository: Path, revision: str, path: str) -> str | None:
    """Return a file's text at a revision, or None when git cannot show it."""
    result = subprocess.run(["git", "show", f"{revision}:{path}"], cwd=repository, capture_output=True, check=False)
    return None if result.returncode else result.stdout.decode("utf-8", "replace")


def _module_body(source: str) -> list[ast.stmt]:
    """Return a module's statements, or none when it is not valid Python."""
    try:
        return ast.parse(source).body
    except SyntaxError:
        return []


def _assigned_values(source: str, name: str) -> list[ast.expr]:
    """Return the values that module-level statements assign to ``name``, augmented assignments included."""
    values = []
    for node in _module_body(source):
        targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and node.value is not None:
            values += [node.value for target in targets if isinstance(target, ast.Name) and target.id == name]
    return values


def _experimental_features(source: str) -> Names | None:
    """Return the experimental feature ids with their targets, or None without a registry."""
    if not (
        registries := [
            value for value in _assigned_values(source, "EXPERIMENTAL_FEATURES") if isinstance(value, ast.Dict)
        ]
    ):
        return None
    return {
        key.value: next(
            (ast.unparse(keyword.value) for keyword in getattr(value, "keywords", ()) if keyword.arg == "target"), ""
        )
        for registry in registries
        for key, value in zip(registry.keys, registry.values, strict=True)
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _options(source: str) -> Names:
    """Return the command-line options a module names."""
    return dict.fromkeys(OPTION.findall(source), "")


def _presets(source: str) -> Names:
    """Return the preset names, one string literal per line."""
    return dict.fromkeys(PRESET.findall(source), "")


def _public_names(source: str) -> Names | None:
    """Return the names ``__all__`` lists, or None without ``__all__``."""
    if not (values := _assigned_values(source, "__all__")):
        return None
    return {
        element.value: ""
        for value in values
        for element in getattr(value, "elts", ())
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    }


REGISTRIES: tuple[tuple[str, str, Callable[[str], Names | None], Callable[[str], bool]], ...] = (
    ("Experimental features", f"{PACKAGE}/experimental.py", _experimental_features, FEATURE_ID.fullmatch),
    ("Command-line options", f"{PACKAGE}/cli_options.py", _options, OPTION.fullmatch),
    ("Presets", f"{PACKAGE}/preset_names.py", _presets, PRESET.fullmatch),
    ("Public Python names", f"{PACKAGE}/__init__.py", _public_names, str.isidentifier),
)


def _registry_names(repository: Path, revision: str) -> list[Names | None]:
    """Return the names of each registry at a revision, None where the registry cannot be read."""
    return [
        None if (source := _git_show(repository, revision, path)) is None else reader(source)
        for _, path, reader, _ in REGISTRIES
    ]


def _baseline_section(repository: Path, merge_sha: str, tag: str) -> str:
    """Return the context section that names the baseline release and the surfaces it does not have."""
    if not tag:
        return (
            "## Release Baseline\n\nBaseline release: unknown. No published release tag is an ancestor of the base "
            "commit, so no surface is listed as new.\n"
        )
    lines = []
    merge_names, base_names = _registry_names(repository, merge_sha), _registry_names(repository, f"refs/tags/{tag}")
    for (label, _, _, valid), merge, base in zip(REGISTRIES, merge_names, base_names, strict=True):
        if merge is None or base is None:
            lines.append(
                f"{label}: unavailable (the registry cannot be read at one of the two commits, so none is listed)"
            )
            continue
        new_names = sorted(name for name in merge.keys() - base.keys() if valid(name))
        lines.append(f"{label}:" + ("" if new_names else " none"))
        lines += [f"  {name}{f' (target {merge[name]})' if merge[name] else ''}" for name in new_names]
    return (
        f"## Release Baseline\n\nBaseline release: {tag}, the newest published release whose tag is an ancestor of "
        f"the base commit.\nThese experimental features, command-line options, presets and public Python names are "
        f"present at the merge commit and absent at {tag}, so no release has them:\n\n```text\n"
        + "\n".join(lines)
        + "\n```\n"
    )


def _diff_section(
    nonce: str, exact: tuple[int, int, int], copy: tuple[int, int, int], counts: Counter[str], summarised: list[str]
) -> str:
    """Return the context section that says how Claude's copy of the diff differs from the exact diff."""
    stored = (
        f"- The diff copy was over the reading budget, so the diff of each of the {len(summarised):,} stored outputs "
        "(files under `tests/data/expected/`, `.refs` files and `docs/llms*.txt`) was replaced by one summary line "
        "with its path, status and line counts. Use the source changes and the behaviour diff for them.\n"
        if summarised
        else "- No stored output was summarised.\n"
    )
    return (
        "\n## Prepared Diff\n\n"
        f"Lines of the diff copy that start with `⟪dcg {nonce}⟫`, directly or after the diff's `+`, `-` or space "
        "sign, were written by the workflow; PR text cannot contain this marker. The deleted lines copy is built "
        "from the diff copy. Every other line is exactly as in `git diff -U3` of the merge commit against its first "
        "parent.\n\n"
        f"- Exact diff: {exact[0]:,} characters, {exact[1]:,} lines, longest line {exact[2]:,} characters.\n"
        f"- Diff copy: {copy[0]:,} characters, {copy[1]:,} lines, longest line {copy[2]:,} characters; reading "
        f"budget {BUDGET_CHARACTERS:,} characters and {BUDGET_LINES:,} lines.\n"
        f"- Lines longer than {LINE_LIMIT:,} characters: {counts['condensed']:,} condensed, {counts['folded']:,} "
        f"folded, {counts['shortened']:,} unchanged context lines shortened.\n"
        "- A condensed removed line and the condensed added line paired with it show every segment that changed, "
        f"each with up to {CONTEXT} characters of unchanged text around it and its character positions; the text "
        "outside the segments did not change.\n"
        "- A folded line is shown completely in numbered pieces.\n"
        f"- A shortened context line did not change; only its first {CONTEXT_HEAD} characters are shown.\n" + stored
    )


def main(argv: list[str] | None = None) -> None:
    """Write Claude's diff copy, its deleted lines and the baseline and diff context sections."""
    args = _parse_args(argv)
    data = args.diff_path.read_bytes()
    if f"⟪dcg {args.nonce}".encode() in data:
        msg = "The PR diff contains the annotation marker of this run; refusing to prepare it."
        raise SystemExit(msg)
    files = _parse_diff(data.removesuffix(b"\n").split(b"\n"))
    counts: Counter[str] = Counter()
    prepared = [_prepared_file(args.nonce, file, counts) for file in files]
    summarised = []
    if (
        _measure([line for lines in prepared for line in lines])[0] > BUDGET_CHARACTERS
        or sum(map(len, prepared)) > BUDGET_LINES
    ):
        for index, file in enumerate(files):
            if _is_stored_output(file.path):
                prepared[index] = [_summary_line(args.nonce, file)]
                summarised.append(file.path)
    copy = [line for lines in prepared for line in lines]
    args.claude_diff_path.write_bytes(b"".join(line + b"\n" for line in copy))
    args.claude_deleted_lines_path.write_bytes(
        b"".join(line[1:] + b"\n" for line in copy if line.startswith(b"-") and not line.startswith(b"--- "))
    )
    exact = _measure([line.raw for file in files for line in file.lines])
    args.sections_path.write_text(
        _baseline_section(args.repository_path, args.merge_sha, args.baseline_tag)
        + _diff_section(args.nonce, exact, _measure(copy), counts, summarised),
        encoding="utf-8",
    )


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess tests
    main()
