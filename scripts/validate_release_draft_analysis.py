"""Validate Claude release draft analysis before updating the draft release."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

ALLOWED_BREAKING_CHANGE_HEADINGS = frozenset({
    "Code Generation Changes",
    "Custom Template Update Required",
    "API/CLI Changes",
    "Default Behavior Changes",
    "Python Version Changes",
    "Error Handling Changes",
})
MAX_BREAKING_CHANGES_CONTENT_LENGTH = 20_000
MAX_REASONING_LENGTH = 4_000
REMOVAL_PATTERNS = [
    re.compile(r"\b(?:removed|deleted|dropped)\b(?:(?!`).){0,80}`(?P<token>[^`\n]+)`", re.IGNORECASE),
    re.compile(r"`(?P<token>[^`\n]+)`(?:(?!`).){0,80}\b(?:removed|deleted|dropped)\b", re.IGNORECASE),
    re.compile(
        r"\bno longer\s+(?:supports?|accepts?|recognizes?|exports?|provides?|includes?|allows?|has)\b"
        r"(?:(?!`).){0,80}`(?P<token>[^`\n]+)`",
        re.IGNORECASE,
    ),
    re.compile(
        r"`(?P<token>[^`\n]+)`(?:(?!`).){0,80}\bno longer\s+"
        r"(?:available|exists|supported|accepted|recognized|exported|provided|included|allowed)\b",
        re.IGNORECASE,
    ),
]
UNREADABLE_DIFF_RE = re.compile(
    r"\b(?:unable|could not|can't|cannot)\b.{0,160}\b(?:read|access)\b.{0,160}"
    r"\b(?:prepared diff|analysis artifact|diff file|diff)\b",
    re.IGNORECASE | re.DOTALL,
)
TOKEN_BOUNDARY_CHARS = r"A-Za-z0-9_.:/-"
ACTIVE_MARKDOWN_RE = re.compile(
    r"\[|\]|@|<|>|\b(?:https?|ftp)://|\bwww\.|(?<!:)//",
    re.IGNORECASE | re.MULTILINE,
)
INLINE_CODE_OR_ACTIVE_MARKDOWN_RE = re.compile(
    rf"(?P<code>`[^`\n]+`)|{ACTIVE_MARKDOWN_RE.pattern}",
    ACTIVE_MARKDOWN_RE.flags,
)
ITEM_TITLE_START_RE = re.compile(r"(?:[A-Za-z]|`[A-Za-z0-9_.:/-]+`)")
PLAIN_TEXT_MARKDOWN_RE = re.compile(r"[\[\]<>@#]")


def _parse_args() -> argparse.Namespace:
    """Parse validator CLI arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis-path", required=True, type=Path)
    parser.add_argument("--deleted-lines-path", required=True, type=Path)
    parser.add_argument("--execution-path", required=True, type=Path)
    parser.add_argument("--marker-path", required=True, type=Path)
    parser.add_argument("--analysis-context-path", required=True, type=Path)
    parser.add_argument("--diff-path", required=True, type=Path)
    parser.add_argument("--pr-number", required=True, type=int)
    return parser.parse_args()


def _parse_claude_output(raw_output: str) -> dict[str, Any]:
    """Parse Claude's structured output as a JSON object."""
    if not raw_output:
        message = "Claude structured output is empty"
        raise SystemExit(message)
    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        message = f"Invalid Claude structured output JSON: {exc}"
        raise SystemExit(message) from exc
    if isinstance(parsed, dict):
        return parsed
    message = "Claude structured output must be a JSON object"
    raise SystemExit(message)


def _read_marker(marker_path: Path) -> str:
    """Return the non-empty read-boundary marker or fail closed."""
    try:
        marker = marker_path.read_text(encoding="utf-8").strip()
    except OSError:
        print("Read-boundary marker is unavailable; refusing to update the draft.", file=sys.stderr)
        raise SystemExit(1) from None
    if marker:
        return marker
    print("Read-boundary marker is empty; refusing to update the draft.", file=sys.stderr)
    raise SystemExit(1)


def _read_execution_messages(execution_path: Path) -> list[Any]:
    """Load Claude's execution record or fail closed."""
    try:
        with execution_path.open(encoding="utf-8") as execution_file:
            messages = json.load(execution_file)
    except (OSError, json.JSONDecodeError):
        print("Claude execution record is unavailable; refusing to update the draft.", file=sys.stderr)
        raise SystemExit(1) from None
    if isinstance(messages, list):
        return messages
    print("Claude execution record is invalid; refusing to update the draft.", file=sys.stderr)
    raise SystemExit(1)


def _validate_marker_not_leaked(marker: str, *outputs: str) -> None:
    """Fail closed if the canary marker appears in Claude output."""
    if not any(marker in output for output in outputs):
        return
    print("Read-boundary marker appeared in Claude output; refusing to update the draft.", file=sys.stderr)
    raise SystemExit(1)


def _message_content(message: dict[str, Any]) -> list[Any]:
    """Return an SDK message's content blocks, or no blocks for an unrelated frame."""
    payload = message.get("message")
    if not isinstance(payload, dict) or not isinstance(content := payload.get("content"), list):
        return []
    return content


def _read_tool_uses(messages: list[Any]) -> dict[str, str]:
    """Map each Read tool-use ID to its exact requested file path."""
    tool_uses: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, dict) or message.get("type") != "assistant":
            continue
        for block in _message_content(message):
            if not isinstance(block, dict) or block.get("type") != "tool_use" or block.get("name") != "Read":
                continue
            tool_use_id = block.get("id")
            tool_input = block.get("input")
            if not isinstance(tool_use_id, str) or not tool_use_id or not isinstance(tool_input, dict):
                continue
            if not isinstance(file_path := tool_input.get("file_path"), str) or tool_use_id in tool_uses:
                _fail_closed("Claude execution record has an invalid Read request; refusing to update the draft.")
            tool_uses[tool_use_id] = file_path
    return tool_uses


def _tool_results(messages: list[Any]) -> dict[str, tuple[bool, Any]]:
    """Map SDK user tool-result IDs to their error state and structured tool output."""
    results: dict[str, tuple[bool, Any]] = {}
    for message in messages:
        if not isinstance(message, dict) or message.get("type") != "user":
            continue
        tool_use_result = message.get("tool_use_result")
        for block in _message_content(message):
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_use_id = block.get("tool_use_id")
            is_error = block.get("is_error", False)
            if not isinstance(tool_use_id, str) or not tool_use_id or not isinstance(is_error, bool):
                continue
            if tool_use_id in results:
                _fail_closed("Claude execution record has duplicate tool results; refusing to update the draft.")
            results[tool_use_id] = is_error, tool_use_result
    return results


def _matches_complete_initial_lines(content: str, file_path: str, num_lines: int) -> bool:
    """Return whether content ends at the trusted artifact's requested line boundary."""
    if content.count("\n") + 1 != num_lines:
        return False
    try:
        with Path(file_path).open(encoding="utf-8", newline="") as artifact:
            return artifact.read(len(content)) == content and artifact.read(1) == "\n"
    except (OSError, UnicodeError):
        return False


def _read_range(tool_use_result: Any, expected_path: str) -> tuple[int, int, int] | None:
    """Return one verified complete range or canonical token-capped initial page."""
    match tool_use_result:
        case {
            "type": "text",
            "file": {"filePath": str() as file_path, "content": str()} as file,
        } if file_path == expected_path:
            pass
        case _:
            return None
    start_line = file.get("startLine")
    num_lines = file.get("numLines")
    total_lines = file.get("totalLines")
    truncated_by_token_cap = file.get("truncatedByTokenCap", False)
    if any(type(value) is not int for value in (start_line, num_lines, total_lines)) or not isinstance(
        truncated_by_token_cap, bool
    ):
        return None
    invalid_range = any((start_line < 1, num_lines < 0, total_lines < 0))
    if truncated_by_token_cap:
        invalid_range |= not (start_line == 1 and 0 < num_lines < total_lines)
        if not invalid_range:
            invalid_range = not _matches_complete_initial_lines(file["content"], file_path, num_lines)
    if invalid_range:
        return None
    if total_lines == 0:
        return (1, 0, 0) if start_line == 1 and num_lines == 0 else None
    if num_lines == 0 or start_line > total_lines:
        return None
    end_line = start_line + num_lines - 1
    return (start_line, end_line, total_lines) if end_line <= total_lines else None


def _covers_entire_file(read_ranges: list[tuple[int, int, int]]) -> bool:
    """Return whether successful Read ranges cover every line in one file."""
    if not read_ranges or len(total_lines := {read_range[2] for read_range in read_ranges}) != 1:
        return False
    total_line_count = total_lines.pop()
    if total_line_count == 0:
        return True
    next_line = 1
    for start_line, end_line, _ in sorted(read_ranges):
        if start_line > next_line:
            return False
        next_line = max(next_line, end_line + 1)
    return next_line > total_line_count


def _validate_execution_evidence(
    messages: list[Any], marker_path: Path, analysis_context_path: Path, diff_path: Path
) -> None:
    """Require one successful run, one denied marker read, and both trusted artifact reads."""
    result_messages = [message for message in messages if isinstance(message, dict) and message.get("type") == "result"]
    if len(result_messages) != 1:
        _fail_closed("Claude execution record has an invalid result count; refusing to update the draft.")
    result = result_messages[0]
    if result.get("subtype") != "success" or result.get("is_error") is not False:
        _fail_closed("Claude analysis did not complete successfully; refusing to update the draft.")
    denials = result.get("permission_denials")
    if not isinstance(denials, list) or len(denials) != 1:
        _fail_closed("Claude execution record has unexpected permission denials; refusing to update the draft.")

    marker_file_path = str(marker_path)
    denial = denials[0]
    if not isinstance(denial, dict):
        _fail_closed("Read-boundary marker was not denied; refusing to update the draft.")
    marker_tool_use_id = denial.get("tool_use_id")
    tool_input = denial.get("tool_input")
    if (
        denial.get("tool_name") != "Read"
        or not isinstance(marker_tool_use_id, str)
        or not marker_tool_use_id
        or not isinstance(tool_input, dict)
        or tool_input.get("file_path") != marker_file_path
    ):
        _fail_closed("Read-boundary marker was not denied; refusing to update the draft.")

    tool_uses = _read_tool_uses(messages)
    tool_results = _tool_results(messages)
    if (
        tool_uses.get(marker_tool_use_id) != marker_file_path
        or tool_results.get(marker_tool_use_id, (False, None))[0] is not True
    ):
        _fail_closed("Read-boundary marker denial is incomplete; refusing to update the draft.")

    expected_paths = {str(analysis_context_path), str(diff_path)}
    if not all(Path(path).is_absolute() for path in expected_paths):
        _fail_closed("Trusted artifact paths must be absolute; refusing to update the draft.")
    successful_read_ranges = {path: [] for path in expected_paths}
    for tool_use_id, file_path in tool_uses.items():
        is_error, tool_use_result = tool_results.get(tool_use_id, (True, None))
        if tool_use_id == marker_tool_use_id or is_error or file_path not in successful_read_ranges:
            continue
        if read_range := _read_range(tool_use_result, file_path):
            successful_read_ranges[file_path].append(read_range)
    if all(_covers_entire_file(read_ranges) for read_ranges in successful_read_ranges.values()):
        return
    _fail_closed("Claude did not completely read all prepared artifacts; refusing to update the draft.")


def _fail_closed(message: str) -> None:
    """Print a non-sensitive validation error and stop the workflow."""
    print(message, file=sys.stderr)
    raise SystemExit(1)


def _validate_structured_output(claude_output: dict[str, Any]) -> tuple[bool, str, str]:
    """Require the schema's exact, bounded primitive fields before persisting output."""
    expected_fields = {"has_breaking_changes", "breaking_changes_content", "reasoning"}
    if set(claude_output) != expected_fields:
        _fail_closed("Claude structured output has an unexpected schema; refusing to update the draft.")

    has_breaking_changes = claude_output["has_breaking_changes"]
    breaking_changes_content = claude_output["breaking_changes_content"]
    reasoning = claude_output["reasoning"]
    if (
        not isinstance(has_breaking_changes, bool)
        or not isinstance(breaking_changes_content, str)
        or not isinstance(reasoning, str)
    ):
        _fail_closed("Claude structured output has invalid field types; refusing to update the draft.")
    if len(breaking_changes_content) > MAX_BREAKING_CHANGES_CONTENT_LENGTH or len(reasoning) > MAX_REASONING_LENGTH:
        _fail_closed("Claude structured output exceeds its size limit; refusing to update the draft.")
    return has_breaking_changes, breaking_changes_content, reasoning


def _validate_text_characters(text: str, *, allowed_controls: frozenset[str]) -> None:
    """Reject invisible controls that could change rendered release-note meaning."""
    if any(
        character not in allowed_controls and unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
        for character in text
    ):
        _fail_closed("Claude output contains forbidden control characters; refusing to update the draft.")


def _validate_no_active_markdown(text: str) -> None:
    """Reject active Markdown outside already validated single-backtick code spans."""
    if not ACTIVE_MARKDOWN_RE.search(text):
        return
    for match in INLINE_CODE_OR_ACTIVE_MARKDOWN_RE.finditer(text):
        if match.group("code") is None:
            _fail_closed("Claude output contains active Markdown; refusing to update the draft.")


def _validate_inline_code(line: str) -> None:
    """Allow only balanced single-backtick inline code spans."""
    if "``" in line or r"\`" in line or line.count("`") % 2:
        _fail_closed("Claude output has an invalid inline code span; refusing to update the draft.")


def _validate_no_fenced_code(line: str) -> None:
    """Reject fenced code so the validator and downstream release merge stay aligned."""
    if "```" in line or "~~~" in line:
        _fail_closed("Claude output contains a fenced code block; refusing to update the draft.")


def _validate_changelog_structure(breaking_changes_content: str, pr_number: int) -> None:
    """Require the narrow, inert release-note Markdown format promised to downstream steps."""
    current_heading: str | None = None
    items_by_heading: dict[str, int] = {}
    pr_suffix = f" (#{pr_number})"

    for line in breaking_changes_content.splitlines():
        if not line.strip():
            continue
        _validate_no_fenced_code(line)
        _validate_inline_code(line)
        if line.startswith("### "):
            heading = line.removeprefix("### ")
            if heading not in ALLOWED_BREAKING_CHANGE_HEADINGS or heading in items_by_heading:
                _fail_closed("Claude output has an invalid breaking-change heading; refusing to update the draft.")
            current_heading = heading
            items_by_heading[heading] = 0
            continue
        if line.startswith("#"):
            _fail_closed("Claude output has an unexpected heading; refusing to update the draft.")
        if current_heading is None or not line.startswith("* ") or not line.endswith(pr_suffix):
            _fail_closed("Claude output has an invalid breaking-change item; refusing to update the draft.")
        item = line.removeprefix("* ").removesuffix(pr_suffix)
        title, separator, details = item.partition(" - ")
        if not item or item != item.strip() or "#" in item:
            _fail_closed("Claude output has an empty breaking-change item; refusing to update the draft.")
        if not separator or not title or not details:
            _fail_closed("Claude output has an empty breaking-change item; refusing to update the draft.")
        if title != title.strip() or details != details.strip() or not ITEM_TITLE_START_RE.match(title):
            _fail_closed("Claude output has an empty breaking-change item; refusing to update the draft.")
        _validate_no_active_markdown(line)
        items_by_heading[current_heading] += 1

    if not items_by_heading or not all(items_by_heading.values()):
        _fail_closed("Claude output has an empty breaking-change section; refusing to update the draft.")


def _validate_breaking_changes_content(
    *, has_breaking_changes: bool, breaking_changes_content: str, deleted_lines_path: Path, pr_number: int
) -> None:
    """Validate cross-field coherence, constrained Markdown, and removal claims once."""
    _validate_text_characters(breaking_changes_content, allowed_controls=frozenset({"\n"}))
    match has_breaking_changes:
        case False if breaking_changes_content:
            _fail_closed("Non-breaking analysis included release-note content; refusing to update the draft.")
        case True if not breaking_changes_content.strip():
            _fail_closed("Breaking analysis omitted release-note content; refusing to update the draft.")
        case False:
            return
        case True:
            _validate_changelog_structure(breaking_changes_content, pr_number)
            _validate_removal_claims(breaking_changes_content, deleted_lines_path)


def _validate_reasoning(reasoning: str) -> None:
    """Keep the PR comment explanation short, inert, and single-paragraph."""
    if (
        not reasoning.strip()
        or reasoning != reasoning.strip()
        or "\n" in reasoning
        or "\r" in reasoning
        or PLAIN_TEXT_MARKDOWN_RE.search(reasoning)
    ):
        _fail_closed("Claude reasoning must be one non-empty paragraph; refusing to update the draft.")
    _validate_text_characters(reasoning, allowed_controls=frozenset())
    _validate_no_fenced_code(reasoning)
    _validate_inline_code(reasoning)
    _validate_no_active_markdown(reasoning)


def _normalize_token(token: str) -> str:
    """Normalize a claimed removed token for comparison."""
    stripped = token.strip()
    normalized = stripped
    match stripped:
        case value if value.endswith("()"):
            normalized = value[:-2]
        case value:
            normalized = value
    return normalized


def _iter_non_fenced_lines(text: str) -> list[str]:
    """Return lines outside Markdown code fences."""
    lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if line.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(line)
    return lines


def _claimed_removed_tokens(line: str) -> set[str]:
    """Extract explicit removed-token claims from one non-fenced line."""
    tokens: set[str] = set()
    for pattern in REMOVAL_PATTERNS:
        for match in pattern.finditer(line):
            if normalized := _normalize_token(match.group("token")):
                tokens.add(normalized)
    return tokens


def _token_present(token: str, deleted_lines: str) -> bool:
    """Return whether token appears with non-token boundaries in deleted lines."""
    if not (normalized := _normalize_token(token)):
        return False
    pattern = re.compile(rf"(?<![{TOKEN_BOUNDARY_CHARS}]){re.escape(normalized)}(?![{TOKEN_BOUNDARY_CHARS}])")
    return bool(pattern.search(deleted_lines))


def _validate_removal_claims(breaking_changes_content: str, deleted_lines_path: Path) -> None:
    """Validate explicit removal claims against trusted deleted diff lines."""
    claims = [
        (line, token)
        for line in _iter_non_fenced_lines(breaking_changes_content)
        for token in _claimed_removed_tokens(line)
    ]
    if not claims:
        return

    deleted_lines = deleted_lines_path.read_text(encoding="utf-8") if deleted_lines_path.exists() else ""
    if not deleted_lines.strip():
        print(
            "Release draft analysis claimed removals, but the exact PR diff has no deleted lines:",
            file=sys.stderr,
        )
        print(breaking_changes_content, file=sys.stderr)
        raise SystemExit(1)

    invalid_claims = [
        f"{line}\n  Missing deleted token: {token}"
        for line, token in claims
        if not _token_present(token, deleted_lines)
    ]
    if not invalid_claims:
        return

    print("Release draft analysis claimed removals that are not present in the exact PR diff:", file=sys.stderr)
    print("\n".join(invalid_claims), file=sys.stderr)
    raise SystemExit(1)


def _validate_diff_was_read(reasoning: str) -> None:
    """Fail if Claude says it could not read the prepared diff."""
    if not (reasoning and UNREADABLE_DIFF_RE.search(reasoning)):
        return
    print("Release draft analysis could not read the prepared diff; refusing to update the draft.", file=sys.stderr)
    print(reasoning, file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    """Validate Claude output and persist the normalized analysis JSON artifact."""
    args = _parse_args()
    if args.pr_number < 1:
        _fail_closed("PR number must be positive; refusing to update the draft.")
    marker = _read_marker(args.marker_path)
    execution_messages = _read_execution_messages(args.execution_path)
    raw_output = os.environ.get("CLAUDE_OUTPUT", "")
    _validate_marker_not_leaked(marker, raw_output)
    claude_output = _parse_claude_output(raw_output)
    has_breaking_changes, breaking_changes_content, reasoning = _validate_structured_output(claude_output)

    _validate_marker_not_leaked(marker, breaking_changes_content, reasoning)
    _validate_execution_evidence(
        execution_messages,
        args.marker_path,
        args.analysis_context_path,
        args.diff_path,
    )
    _validate_diff_was_read(reasoning)
    _validate_reasoning(reasoning)
    _validate_breaking_changes_content(
        has_breaking_changes=has_breaking_changes,
        breaking_changes_content=breaking_changes_content,
        deleted_lines_path=args.deleted_lines_path,
        pr_number=args.pr_number,
    )

    args.analysis_path.write_text(
        json.dumps(
            {
                "has_breaking_changes": has_breaking_changes,
                "breaking_changes_content": breaking_changes_content,
                "reasoning": reasoning,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
