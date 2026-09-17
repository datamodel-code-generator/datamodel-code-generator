"""Tests for release draft analysis validation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts import validate_release_draft_analysis as validator
from tests.conftest import assert_output

EXPECTED_PATH = Path(__file__).parent / "data" / "validate_release_draft_analysis"


def _file_read_output(
    path: Path,
    *,
    start_line: int = 1,
    num_lines: int = 1,
    total_lines: int = 1,
    truncated_by_token_cap: bool = False,
    content: str | None = None,
) -> dict[str, Any]:
    """Build the pinned SDK FileReadOutput metadata for one successful Read."""
    output: dict[str, Any] = {
        "type": "text",
        "file": {
            "filePath": str(path),
            "content": content if content is not None else "\n".join("line" for _ in range(num_lines)),
            "numLines": num_lines,
            "startLine": start_line,
            "totalLines": total_lines,
        },
    }
    if truncated_by_token_cap:
        output["file"]["truncatedByTokenCap"] = True
    return output


def _execution_record(marker_path: Path, analysis_context_path: Path, diff_path: Path) -> list[dict[str, Any]]:
    """Build the pinned SDK message shape for one denied and two successful Reads."""
    marker_tool_use_id = "toolu_read_boundary_marker"
    context_tool_use_id = "toolu_read_analysis_context"
    diff_tool_use_id = "toolu_read_diff"
    return [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": marker_tool_use_id,
                        "name": "Read",
                        "input": {"file_path": str(marker_path)},
                    },
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": marker_tool_use_id, "is_error": True},
                ],
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": context_tool_use_id,
                        "name": "Read",
                        "input": {"file_path": str(analysis_context_path), "offset": 1, "limit": 20},
                    },
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": context_tool_use_id}],
            },
            "tool_use_result": _file_read_output(analysis_context_path),
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": diff_tool_use_id,
                        "name": "Read",
                        "input": {"file_path": str(diff_path), "offset": 1, "limit": 20},
                    },
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": diff_tool_use_id, "is_error": False}],
            },
            "tool_use_result": _file_read_output(diff_path),
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "permission_denials": [
                {
                    "tool_name": "Read",
                    "tool_use_id": marker_tool_use_id,
                    "tool_input": {"file_path": str(marker_path)},
                },
            ],
        },
    ]


def _paginated_execution_record(
    marker_path: Path, analysis_context_path: Path, diff_path: Path
) -> list[dict[str, Any]]:
    """Build an execution record matching the real token-capped first page and continuation."""
    execution_record = _execution_record(marker_path, analysis_context_path, diff_path)
    execution_record[3]["tool_use_result"] = _file_read_output(
        analysis_context_path,
        num_lines=1213,
        total_lines=1507,
        truncated_by_token_cap=True,
    )
    execution_record[2]["message"]["content"][0]["input"] = {"file_path": str(analysis_context_path)}
    execution_record[-1:-1] = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_read_analysis_context_second_page",
                        "name": "Read",
                        "input": {"file_path": str(analysis_context_path), "offset": 1214},
                    },
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": "toolu_read_analysis_context_second_page"}],
            },
            "tool_use_result": _file_read_output(
                analysis_context_path,
                start_line=1214,
                num_lines=294,
                total_lines=1507,
            ),
        },
    ]
    return execution_record


def _valid_breaking_changes_content(pr_number: int = 42) -> str:
    """Return the smallest valid release-note section for a synthetic PR."""
    return (
        "### Code Generation Changes\n"
        f"* Generated annotations change compatibility - Update downstream type assumptions (#{pr_number})"
    )


def test_generated_output_change_without_removal_claim_passes(tmp_path: Path) -> None:
    """Generated-output changes should not be treated as removal hallucinations."""
    deleted_lines = tmp_path / "deleted-lines.txt"
    deleted_lines.write_text("", encoding="utf-8")

    validator._validate_removal_claims(
        "### Code Generation Changes\n"
        "* Optional primitive `const` fields no longer emit an injected default - "
        "A property that is not `required` now renders as `Literal[X] | None = None`.",
        deleted_lines,
    )


def test_explicit_removed_token_must_exist_in_deleted_lines(tmp_path: Path) -> None:
    """Explicit removal claims are accepted when the token exists in deleted lines."""
    deleted_lines = tmp_path / "deleted-lines.txt"
    deleted_lines.write_text("--old-option\n", encoding="utf-8")

    validator._validate_removal_claims("* Removed CLI option `--old-option`.", deleted_lines)


def test_removed_token_can_start_with_dash(tmp_path: Path) -> None:
    """Removed CLI flags are validated against deleted lines that start with dashes."""
    deleted_lines = tmp_path / "deleted-lines.txt"
    deleted_lines.write_text("--old-option was documented here\n", encoding="utf-8")

    validator._validate_removal_claims("* No longer supports `--old-option`.", deleted_lines)


@pytest.mark.parametrize(
    ("claim", "deleted_text"),
    [
        ("--old-option", "--old-option-extra was documented here\n"),
        ("required", "requiredness\n"),
    ],
)
def test_removed_token_partial_match_fails(claim: str, deleted_text: str, tmp_path: Path) -> None:
    """Removed tokens must match deleted lines as complete tokens."""
    deleted_lines = tmp_path / "deleted-lines.txt"
    deleted_lines.write_text(deleted_text, encoding="utf-8")

    with pytest.raises(SystemExit):
        validator._validate_removal_claims(f"* Removed `{claim}`.", deleted_lines)


def test_explicit_removed_token_missing_fails(tmp_path: Path) -> None:
    """Explicit removal claims fail when the token is absent from deleted lines."""
    deleted_lines = tmp_path / "deleted-lines.txt"
    deleted_lines.write_text("--other-option\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        validator._validate_removal_claims("* Removed CLI option `--old-option`.", deleted_lines)


def test_unreadable_prepared_diff_fails() -> None:
    """The workflow should fail instead of silently trusting an unread diff."""
    with pytest.raises(SystemExit):
        validator._validate_diff_was_read("Unable to read the prepared diff because a line was truncated")


def test_empty_claude_output_fails() -> None:
    """Missing structured output should not become a clean no-breaking-change result."""
    with pytest.raises(SystemExit):
        validator._parse_claude_output("")


@pytest.mark.parametrize(
    "marker_content",
    [None, "", "\n"],
)
def test_missing_or_empty_read_boundary_marker_fails(marker_content: str | None, tmp_path: Path) -> None:
    """The read-boundary marker must be present before analysis can continue."""
    marker_path = tmp_path / "marker.txt"
    if marker_content is not None:
        marker_path.write_text(marker_content, encoding="utf-8")

    with pytest.raises(SystemExit):
        validator._read_marker(marker_path)


@pytest.mark.parametrize(
    "execution_content",
    [None, "not JSON", "{}"],
)
def test_missing_or_invalid_execution_record_fails(execution_content: str | None, tmp_path: Path) -> None:
    """The authoritative permission record must be a JSON list."""
    execution_path = tmp_path / "execution.json"
    if execution_content is not None:
        execution_path.write_text(execution_content, encoding="utf-8")

    with pytest.raises(SystemExit):
        validator._read_execution_messages(execution_path)


def test_read_boundary_marker_absent_from_output_passes() -> None:
    """Normal structured output does not trip the marker leak guard."""
    validator._validate_marker_not_leaked(
        "read-boundary-marker",
        '{"reasoning":"Prepared diff was read."}',
        "Prepared diff was read.",
        "",
    )


@pytest.mark.parametrize(
    "output",
    [
        '{"reasoning":"read-boundary-marker"}',
        "### Code Generation Changes\n* read-boundary-marker",
    ],
)
def test_read_boundary_marker_leak_fails(output: str) -> None:
    """Raw and structured output must not contain the marker value."""
    with pytest.raises(SystemExit):
        validator._validate_marker_not_leaked("read-boundary-marker", output)


def test_execution_evidence_accepts_token_capped_initial_page_with_continuation(tmp_path: Path) -> None:
    """A token-capped initial page is valid when subsequent reads cover remaining lines."""
    marker_path = tmp_path / "marker.txt"
    analysis_context_path = tmp_path / "analysis-context.md"
    diff_path = tmp_path / "pr.diff"
    marker_path.write_text("read-boundary-marker\n", encoding="utf-8")
    analysis_context_path.write_text(
        "\n".join("line" for _ in range(1507)),
        encoding="utf-8",
        newline="\n",
    )
    diff_path.write_text("diff\n", encoding="utf-8")

    execution_record = _paginated_execution_record(marker_path, analysis_context_path, diff_path)

    validator._validate_execution_evidence(
        execution_record,
        marker_path,
        analysis_context_path,
        diff_path,
    )


def test_execution_evidence_rejects_token_capped_character_slice(tmp_path: Path) -> None:
    """A token cap cannot skip the remainder of an oversized first line."""
    marker_path = tmp_path / "marker.txt"
    analysis_context_path = tmp_path / "analysis-context.md"
    diff_path = tmp_path / "pr.diff"
    marker_path.write_text("read-boundary-marker\n", encoding="utf-8")
    character_slice = "x" * (64 * 1024)
    analysis_context_path.write_text(
        f"{character_slice} remainder of first line\nnext line",
        encoding="utf-8",
        newline="\n",
    )
    diff_path.write_text("diff\n", encoding="utf-8")

    execution_record = _execution_record(marker_path, analysis_context_path, diff_path)
    execution_record[3]["tool_use_result"] = _file_read_output(
        analysis_context_path,
        num_lines=1,
        total_lines=2,
        truncated_by_token_cap=True,
        content=character_slice,
    )
    execution_record[-1:-1] = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_read_analysis_context_second_page",
                        "name": "Read",
                        "input": {"file_path": str(analysis_context_path), "offset": 2, "limit": 1},
                    },
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": "toolu_read_analysis_context_second_page"}],
            },
            "tool_use_result": _file_read_output(
                analysis_context_path,
                start_line=2,
                num_lines=1,
                total_lines=2,
                content="next line",
            ),
        },
    ]

    with pytest.raises(SystemExit):
        validator._validate_execution_evidence(
            execution_record,
            marker_path,
            analysis_context_path,
            diff_path,
        )


@pytest.mark.parametrize(
    ("artifact_content", "reported_content"),
    [("line\nline", "line\n"), (None, "line")],
    ids=["line-count-mismatch", "unreadable-artifact"],
)
def test_execution_evidence_rejects_unverifiable_token_capped_content(
    artifact_content: str | None,
    reported_content: str,
    tmp_path: Path,
) -> None:
    """Token-capped evidence cannot rely on malformed content or an unreadable artifact."""
    marker_path = tmp_path / "marker.txt"
    analysis_context_path = tmp_path / "analysis-context.md"
    diff_path = tmp_path / "pr.diff"
    marker_path.write_text("read-boundary-marker\n", encoding="utf-8")
    diff_path.write_text("diff\n", encoding="utf-8")
    if artifact_content is not None:
        analysis_context_path.write_text(artifact_content, encoding="utf-8", newline="\n")

    execution_record = _execution_record(marker_path, analysis_context_path, diff_path)
    execution_record[3]["tool_use_result"] = _file_read_output(
        analysis_context_path,
        num_lines=1,
        total_lines=2,
        truncated_by_token_cap=True,
        content=reported_content,
    )

    with pytest.raises(SystemExit):
        validator._validate_execution_evidence(
            execution_record,
            marker_path,
            analysis_context_path,
            diff_path,
        )


@pytest.mark.parametrize(
    ("continuation_start_line", "continuation_is_token_capped"),
    [(None, False), (3, False), (2, True)],
    ids=["initial-page-only", "gap", "token-capped-continuation"],
)
def test_execution_evidence_rejects_incomplete_token_capped_pagination(
    continuation_start_line: int | None,
    continuation_is_token_capped: bool,
    tmp_path: Path,
) -> None:
    """Initial capped pages need complete, non-capped continuation coverage."""
    marker_path = tmp_path / "marker.txt"
    analysis_context_path = tmp_path / "analysis-context.md"
    diff_path = tmp_path / "pr.diff"
    marker_path.write_text("read-boundary-marker\n", encoding="utf-8")
    analysis_context_path.write_text("line\nline\nline", encoding="utf-8", newline="\n")
    diff_path.write_text("diff\n", encoding="utf-8")

    execution_record = _execution_record(marker_path, analysis_context_path, diff_path)
    execution_record[3]["tool_use_result"] = _file_read_output(
        analysis_context_path,
        num_lines=1,
        total_lines=3,
        truncated_by_token_cap=True,
    )
    if continuation_start_line is not None:
        execution_record[-1:-1] = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_read_analysis_context_second_page",
                            "name": "Read",
                            "input": {
                                "file_path": str(analysis_context_path),
                                "offset": continuation_start_line,
                                "limit": 1,
                            },
                        },
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "toolu_read_analysis_context_second_page"}],
                },
                "tool_use_result": _file_read_output(
                    analysis_context_path,
                    start_line=continuation_start_line,
                    num_lines=1,
                    total_lines=3,
                    truncated_by_token_cap=continuation_is_token_capped,
                ),
            },
        ]

    with pytest.raises(SystemExit):
        validator._validate_execution_evidence(
            execution_record,
            marker_path,
            analysis_context_path,
            diff_path,
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_tool",
        "missing_read",
        "mismatched_result",
        "error_read",
        "missing_metadata",
        "partial_read",
        "truncated_read",
        "extra_denial",
    ],
)
def test_execution_evidence_rejects_incomplete_or_unexpected_reads(mutation: str, tmp_path: Path) -> None:
    """Read evidence fails closed for absent, mismatched, failed, or unrelated requests."""
    marker_path = tmp_path / "marker.txt"
    analysis_context_path = tmp_path / "analysis-context.md"
    diff_path = tmp_path / "pr.diff"
    marker_path.write_text("read-boundary-marker\n", encoding="utf-8")
    execution_record = _execution_record(marker_path, analysis_context_path, diff_path)
    match mutation:
        case "wrong_tool":
            execution_record[2]["message"]["content"][0]["name"] = "Glob"
        case "missing_read":
            execution_record.pop(4)
            execution_record.pop(4)
        case "mismatched_result":
            execution_record[3]["message"]["content"][0]["tool_use_id"] = "toolu_other"
        case "error_read":
            execution_record[3]["message"]["content"][0]["is_error"] = True
        case "missing_metadata":
            execution_record[3].pop("tool_use_result")
        case "partial_read":
            execution_record[3]["tool_use_result"] = _file_read_output(
                analysis_context_path,
                start_line=1,
                num_lines=1,
                total_lines=2,
            )
        case "truncated_read":
            execution_record[3]["tool_use_result"]["file"]["truncatedByTokenCap"] = True
        case "extra_denial":
            execution_record[-1]["permission_denials"].append({
                "tool_name": "Read",
                "tool_use_id": "toolu_other",
                "tool_input": {"file_path": "/tmp/other"},
            })
        case _:  # pragma: no cover - parametrization is exhaustive.
            pytest.fail(f"Unexpected mutation: {mutation}")

    with pytest.raises(SystemExit):
        validator._validate_execution_evidence(execution_record, marker_path, analysis_context_path, diff_path)


@pytest.mark.parametrize(
    ("has_breaking_changes", "breaking_changes_content"),
    [
        (False, _valid_breaking_changes_content()),
        (False, " "),
        (True, ""),
    ],
)
def test_breaking_change_content_must_match_boolean(
    has_breaking_changes: bool, breaking_changes_content: str, tmp_path: Path
) -> None:
    """Cross-field output contradictions cannot become release-note content."""
    with pytest.raises(SystemExit):
        validator._validate_breaking_changes_content(
            has_breaking_changes=has_breaking_changes,
            breaking_changes_content=breaking_changes_content,
            deleted_lines_path=tmp_path / "deleted-lines.txt",
            pr_number=42,
        )


@pytest.mark.parametrize(
    "content",
    [
        "## Breaking Changes\n* Generated output changed - Detail (#42)",
        "### Unknown Category\n* Generated output changed - Detail (#42)",
        "    ### Code Generation Changes\n* Generated output changed - Detail (#42)",
        "### Code Generation Changes\n    * Generated output changed - Detail (#42)",
        "### Code Generation Changes\n### API/CLI Changes\n* Generated output changed - Detail (#42)",
        (
            "### Code Generation Changes\n* Generated output changed - Detail (#42)\n"
            "### Code Generation Changes\n* Detail - More (#42)"
        ),
        "### Code Generation Changes\n* Generated output changed (#42)",
        "### Code Generation Changes\n*  - Detail (#42)",
        "### Code Generation Changes\n* Title -  (#42)",
        "### Code Generation Changes\n* Title  - Detail (#42)",
        "### Code Generation Changes\n* Title -  Detail (#42)",
        "### Code Generation Changes\n* 1. Nested list - Detail (#42)",
        "### Code Generation Changes\n* ## Injected heading - Detail (#42)",
        "### Code Generation Changes\n* Title - Detail #999 (#42)",
        "### Code Generation Changes\n* Title - Detail\t(#42)",
        "### Code Generation Changes\n* Generated output changed - Detail (#41)",
        "### Code Generation Changes\n* Generated `unbalanced - Detail (#42)",
        "### Code Generation Changes\n* Generated ``double`` code - Detail (#42)",
        "### Code Generation Changes\n* Generated output changed - <a href='https://example.com'>Detail</a> (#42)",
        "### Code Generation Changes\n* Generated output changed - Notify @maintainer (#42)",
        "### Code Generation Changes\n* Generated output changed - www.example.com (#42)",
        "### Code Generation Changes\n* Generated output changed - //example.com (#42)",
        "### Code Generation Changes\n* Generated output changed - maintainer@example.com (#42)",
        "### Code Generation Changes\n* [foo - bar]: //evil.example(#42)\n* [click][foo - bar] - Details (#42)",
        "### Code Generation Changes\n* Generated output changed - ![image](https://example.com/image) (#42)",
        "### Code Generation Changes\n* Generated output changed - Detail (#42)\n``` python`\n## Injected\n![image](https://example.com/image)",
        (
            "### Code Generation Changes\n* Generated output changed - Detail (#42)\n```\nexample\n"
            "    ```\n### API/CLI Changes\n* Generated output changed - Detail (#42)\n```\n"
            "## Injected\n![image](https://example.com/image)"
        ),
        "### Code Generation Changes\n* Generated output changed - Detail (#42)\n```\nexample",
    ],
)
def test_breaking_change_content_rejects_untrusted_markdown(content: str, tmp_path: Path) -> None:
    """Only the documented, inert changelog subset is admitted to the release draft."""
    with pytest.raises(SystemExit):
        validator._validate_breaking_changes_content(
            has_breaking_changes=True,
            breaking_changes_content=content,
            deleted_lines_path=tmp_path / "deleted-lines.txt",
            pr_number=42,
        )


def test_breaking_change_content_allows_balanced_inline_code(tmp_path: Path) -> None:
    """Balanced single-backtick inline code remains safe without active Markdown."""
    content = "### API/CLI Changes\n\n* Return type changes from `OldType` to `NewType` - Update callers (#42)"

    validator._validate_breaking_changes_content(
        has_breaking_changes=True,
        breaking_changes_content=content,
        deleted_lines_path=tmp_path / "deleted-lines.txt",
        pr_number=42,
    )


@pytest.mark.parametrize(
    "value",
    [
        "Valid Unicode \U0001f680",
        "Bad bidi\u202econtrol",
        "Bad C0\x00control",
        "Bad paragraph\u2028separator",
        " Bad outer whitespace",
        "Bad [reference]",
        "Bad `unbalanced",
    ],
)
def test_reasoning_rejects_invisible_controls(value: str) -> None:
    """Unicode controls cannot alter the rendered analysis comment."""
    if value == "Valid Unicode \U0001f680":
        validator._validate_reasoning(value)
        return
    with pytest.raises(SystemExit):
        validator._validate_reasoning(value)


@pytest.mark.parametrize(
    "output",
    [
        {"has_breaking_changes": False, "breaking_changes_content": "", "reasoning": "ok", "extra": True},
        {"has_breaking_changes": "false", "breaking_changes_content": "", "reasoning": "ok"},
        {"has_breaking_changes": False, "breaking_changes_content": "", "reasoning": "x" * 4_001},
    ],
)
def test_structured_output_rejects_schema_or_type_violations(output: dict[str, Any]) -> None:
    """The validator mirrors the action schema instead of coercing malformed values."""
    with pytest.raises(SystemExit):
        validator._validate_structured_output(output)


@pytest.mark.parametrize("raw_output", ["", "{", "[]"])
def test_claude_output_parser_rejects_missing_or_malformed_values(raw_output: str) -> None:
    """Missing, malformed, and non-object structured results all fail closed."""
    with pytest.raises(SystemExit):
        validator._parse_claude_output(raw_output)


def test_marker_and_execution_readers_accept_present_trusted_files(tmp_path: Path) -> None:
    """Trusted runtime files load through their streaming parser paths."""
    marker_path = tmp_path / "marker.txt"
    execution_path = tmp_path / "execution.json"
    marker_path.write_text("marker\n", encoding="utf-8")
    execution_path.write_text("[]", encoding="utf-8")

    validator._read_marker(marker_path)
    validator._read_execution_messages(execution_path)


def test_execution_message_helpers_ignore_unrelated_frames() -> None:
    """Unrelated SDK frames cannot create synthetic Read or tool-result evidence."""
    validator._message_content({})
    validator._read_tool_uses([
        None,
        {"type": "user"},
        {"type": "assistant", "message": {"content": []}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read"}]}},
    ])
    validator._tool_results([
        None,
        {"type": "assistant"},
        {"type": "user", "message": {"content": []}},
        {"type": "user", "message": {"content": [{"type": "text"}, {"type": "tool_result"}]}},
    ])


def test_execution_message_helpers_reject_duplicate_ids() -> None:
    """Repeated tool-use and tool-result IDs cannot be paired ambiguously."""
    duplicate_read = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "id": "toolu_duplicate", "name": "Read", "input": {"file_path": "/tmp/a"}},
                {"type": "tool_use", "id": "toolu_duplicate", "name": "Read", "input": {"file_path": "/tmp/b"}},
            ],
        },
    }
    duplicate_result = {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": "toolu_duplicate"},
                {"type": "tool_result", "tool_use_id": "toolu_duplicate"},
            ],
        },
    }

    with pytest.raises(SystemExit):
        validator._read_tool_uses([duplicate_read])
    with pytest.raises(SystemExit):
        validator._tool_results([duplicate_result])


@pytest.mark.parametrize(
    "tool_use_result",
    [
        None,
        {"type": "image"},
        {"type": "text", "file": {"filePath": "/tmp/other", "content": "line"}},
        {
            "type": "text",
            "file": {"filePath": "/tmp/file", "content": "line", "startLine": "1", "numLines": 1, "totalLines": 1},
        },
        {
            "type": "text",
            "file": {"filePath": "/tmp/file", "content": "line", "startLine": 0, "numLines": 1, "totalLines": 1},
        },
        {
            "type": "text",
            "file": {
                "filePath": "/tmp/file",
                "content": "line",
                "startLine": 1,
                "numLines": 1,
                "totalLines": 1,
                "truncatedByTokenCap": True,
            },
        },
        {
            "type": "text",
            "file": {"filePath": "/tmp/file", "content": "", "startLine": 2, "numLines": 0, "totalLines": 0},
        },
        {
            "type": "text",
            "file": {"filePath": "/tmp/file", "content": "", "startLine": 1, "numLines": 0, "totalLines": 2},
        },
        {
            "type": "text",
            "file": {"filePath": "/tmp/file", "content": "line", "startLine": 2, "numLines": 1, "totalLines": 1},
        },
        {
            "type": "text",
            "file": {"filePath": "/tmp/file", "content": "line", "startLine": 1, "numLines": 2, "totalLines": 1},
        },
    ],
)
@pytest.mark.allow_direct_assert
def test_file_read_metadata_rejects_invalid_ranges(tool_use_result: object) -> None:
    """Malformed FileReadOutput metadata never supplies trusted coverage."""
    assert validator._read_range(tool_use_result, "/tmp/file") is None


@pytest.mark.allow_direct_assert
def test_file_read_metadata_rejects_token_capped_continuation(tmp_path: Path) -> None:
    """A real artifact cannot make a token-capped continuation eligible for coverage."""
    file_path = tmp_path / "file"
    file_path.write_text("line\nline", encoding="utf-8", newline="\n")

    assert (
        validator._read_range(
            _file_read_output(
                file_path,
                start_line=2,
                num_lines=1,
                total_lines=2,
                truncated_by_token_cap=True,
            ),
            str(file_path),
        )
        is None
    )


@pytest.mark.allow_direct_assert
def test_empty_file_read_metadata_is_a_complete_empty_range(tmp_path: Path) -> None:
    """A verified empty artifact uses the SDK's canonical empty-file range."""
    file_path = tmp_path / "file"

    assert validator._read_range(
        _file_read_output(file_path, start_line=1, num_lines=0, total_lines=0),
        str(file_path),
    ) == (1, 0, 0)


@pytest.mark.allow_direct_assert
def test_range_coverage_handles_empty_and_inconsistent_metadata() -> None:
    """Coverage refuses gaps and incompatible total-line metadata."""
    assert validator._covers_entire_file([]) is False
    assert validator._covers_entire_file([(1, 0, 0)]) is True
    assert validator._covers_entire_file([(1, 1, 1), (1, 1, 2)]) is False
    assert validator._covers_entire_file([(2, 2, 2)]) is False


@pytest.mark.parametrize(
    "mutation",
    ["result_count", "result_failure", "denial_count", "invalid_denial", "wrong_marker", "marker_incomplete"],
)
def test_execution_evidence_rejects_invalid_result_envelope(mutation: str, tmp_path: Path) -> None:
    """The result envelope, marker denial, and matching error result are mandatory."""
    marker_path = tmp_path / "marker.txt"
    analysis_context_path = tmp_path / "analysis-context.md"
    diff_path = tmp_path / "pr.diff"
    execution_record = _execution_record(marker_path, analysis_context_path, diff_path)
    match mutation:
        case "result_count":
            execution_record.pop()
        case "result_failure":
            execution_record[-1]["subtype"] = "error_during_execution"
        case "denial_count":
            execution_record[-1]["permission_denials"] = []
        case "invalid_denial":
            execution_record[-1]["permission_denials"] = [None]
        case "wrong_marker":
            execution_record[-1]["permission_denials"][0]["tool_input"]["file_path"] = "/tmp/other"
        case "marker_incomplete":
            execution_record[1]["message"]["content"][0]["is_error"] = False
        case _:  # pragma: no cover - parametrization is exhaustive.
            pytest.fail(f"Unexpected mutation: {mutation}")

    with pytest.raises(SystemExit):
        validator._validate_execution_evidence(execution_record, marker_path, analysis_context_path, diff_path)


def test_execution_evidence_rejects_relative_artifact_paths(tmp_path: Path) -> None:
    """Trusted Read evidence only permits the workflow's absolute runner paths."""
    marker_path = tmp_path / "marker.txt"
    execution_record = _execution_record(marker_path, Path("analysis-context.md"), Path("pr.diff"))

    with pytest.raises(SystemExit):
        validator._validate_execution_evidence(
            execution_record,
            marker_path,
            Path("analysis-context.md"),
            Path("pr.diff"),
        )


def test_removal_and_diff_guards_cover_empty_and_fenced_input(tmp_path: Path) -> None:
    """Removal claims need exact deleted evidence, while fenced unit input is ignored."""
    deleted_lines = tmp_path / "deleted-lines.txt"

    with pytest.raises(SystemExit):
        validator._validate_removal_claims("* Removed `--old-option`.", deleted_lines)
    validator._validate_removal_claims("```\n* Removed `--old-option`.\n```", deleted_lines)
    validator._normalize_token("method()")
    validator._token_present("", "anything")
    validator._claimed_removed_tokens("* Removed `   `.")
    validator._validate_diff_was_read("Prepared diff was read.")


@pytest.mark.allow_direct_assert
def test_breaking_change_validator_ignores_non_boolean_internal_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The runtime helper has no permissive default branch for invalid internal callers."""
    monkeypatch.setattr(
        validator,
        "_validate_removal_claims",
        lambda *_args: pytest.fail("Removal validation must not run for a non-boolean value."),
    )

    assert (
        validator._validate_breaking_changes_content(
            has_breaking_changes="invalid",  # type: ignore[arg-type]
            breaking_changes_content="",
            deleted_lines_path=tmp_path / "deleted-lines.txt",
            pr_number=42,
        )
        is None
    )


def test_main_persists_in_process_validated_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The entrypoint writes the same trusted artifact as the workflow subprocess."""
    analysis_path = tmp_path / "analysis.json"
    deleted_lines = tmp_path / "deleted-lines.txt"
    execution_path = tmp_path / "execution.json"
    marker_path = tmp_path / "marker.txt"
    analysis_context_path = tmp_path / "analysis-context.md"
    diff_path = tmp_path / "pr.diff"
    deleted_lines.write_text("", encoding="utf-8")
    marker_path.write_text("marker\n", encoding="utf-8")
    execution_path.write_text(
        json.dumps(_execution_record(marker_path, analysis_context_path, diff_path)),
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "CLAUDE_OUTPUT",
        json.dumps({
            "has_breaking_changes": False,
            "breaking_changes_content": "",
            "reasoning": "Prepared diff was read.",
        }),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_release_draft_analysis.py",
            "--analysis-path",
            str(analysis_path),
            "--deleted-lines-path",
            str(deleted_lines),
            "--execution-path",
            str(execution_path),
            "--marker-path",
            str(marker_path),
            "--analysis-context-path",
            str(analysis_context_path),
            "--diff-path",
            str(diff_path),
            "--pr-number",
            "42",
        ],
    )

    validator.main()
    assert_output(analysis_path.read_text(encoding="utf-8"), EXPECTED_PATH / "no_breaking_changes.txt")


def test_main_rejects_nonpositive_pr_number(monkeypatch: pytest.MonkeyPatch) -> None:
    """An invalid PR number cannot weaken suffix validation."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_release_draft_analysis.py",
            "--analysis-path",
            "analysis.json",
            "--deleted-lines-path",
            "deleted-lines.txt",
            "--execution-path",
            "execution.json",
            "--marker-path",
            "marker.txt",
            "--analysis-context-path",
            "analysis-context.md",
            "--diff-path",
            "pr.diff",
            "--pr-number",
            "0",
        ],
    )

    with pytest.raises(SystemExit):
        validator.main()


@pytest.mark.parametrize(
    ("claude_output", "expected_file", "token_capped_initial_page"),
    [
        (
            {"has_breaking_changes": False, "breaking_changes_content": "", "reasoning": "Prepared diff was read."},
            "no_breaking_changes.txt",
            False,
        ),
        (
            {
                "has_breaking_changes": True,
                "breaking_changes_content": _valid_breaking_changes_content(),
                "reasoning": "Prepared diff was read.",
            },
            "breaking_changes.txt",
            False,
        ),
        (
            {"has_breaking_changes": False, "breaking_changes_content": "", "reasoning": "Prepared diff was read."},
            "no_breaking_changes.txt",
            True,
        ),
    ],
)
def test_script_writes_validated_analysis(
    claude_output: dict[str, Any],
    expected_file: str,
    token_capped_initial_page: bool,
    tmp_path: Path,
) -> None:
    """The real CLI persists only output that passes every workflow boundary check."""
    analysis_path = tmp_path / "analysis.json"
    deleted_lines = tmp_path / "deleted-lines.txt"
    execution_path = tmp_path / "execution.json"
    marker_path = tmp_path / "marker.txt"
    analysis_context_path = tmp_path / "analysis-context.md"
    diff_path = tmp_path / "pr.diff"
    deleted_lines.write_text("", encoding="utf-8")
    marker_path.write_text("read-boundary-marker\n", encoding="utf-8")
    analysis_context_path.write_text(
        "\n".join("line" for _ in range(1507)) if token_capped_initial_page else "context\n",
        encoding="utf-8",
        newline="\n",
    )
    diff_path.write_text("diff\n", encoding="utf-8")
    execution_path.write_text(
        json.dumps(
            _paginated_execution_record(marker_path, analysis_context_path, diff_path)
            if token_capped_initial_page
            else _execution_record(marker_path, analysis_context_path, diff_path)
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "scripts/validate_release_draft_analysis.py",
            "--analysis-path",
            str(analysis_path),
            "--deleted-lines-path",
            str(deleted_lines),
            "--execution-path",
            str(execution_path),
            "--marker-path",
            str(marker_path),
            "--analysis-context-path",
            str(analysis_context_path),
            "--diff-path",
            str(diff_path),
            "--pr-number",
            "42",
        ],
        capture_output=True,
        check=False,
        cwd=Path(__file__).parents[1],
        env={
            **os.environ,
            "CLAUDE_OUTPUT": json.dumps(claude_output),
        },
        text=True,
    )

    result.check_returncode()
    assert_output(analysis_path.read_text(encoding="utf-8"), EXPECTED_PATH / expected_file)


def test_script_rejects_partial_artifact_read(tmp_path: Path) -> None:
    """The real CLI fails instead of accepting a partial trusted-diff Read."""
    analysis_path = tmp_path / "analysis.json"
    deleted_lines = tmp_path / "deleted-lines.txt"
    execution_path = tmp_path / "execution.json"
    marker_path = tmp_path / "marker.txt"
    analysis_context_path = tmp_path / "analysis-context.md"
    diff_path = tmp_path / "pr.diff"
    deleted_lines.write_text("", encoding="utf-8")
    marker_path.write_text("read-boundary-marker\n", encoding="utf-8")
    execution_record = _execution_record(marker_path, analysis_context_path, diff_path)
    execution_record[3]["tool_use_result"] = _file_read_output(
        analysis_context_path,
        start_line=1,
        num_lines=1,
        total_lines=2,
    )
    execution_path.write_text(json.dumps(execution_record), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/validate_release_draft_analysis.py",
            "--analysis-path",
            str(analysis_path),
            "--deleted-lines-path",
            str(deleted_lines),
            "--execution-path",
            str(execution_path),
            "--marker-path",
            str(marker_path),
            "--analysis-context-path",
            str(analysis_context_path),
            "--diff-path",
            str(diff_path),
            "--pr-number",
            "42",
        ],
        capture_output=True,
        check=False,
        cwd=Path(__file__).parents[1],
        env={
            **os.environ,
            "CLAUDE_OUTPUT": json.dumps({
                "has_breaking_changes": False,
                "breaking_changes_content": "",
                "reasoning": "Prepared diff was read.",
            }),
        },
        text=True,
    )

    assert_output(
        json.dumps(
            {
                "analysis_exists": analysis_path.exists(),
                "returncode": result.returncode,
                "stderr": result.stderr,
            },
            sort_keys=True,
        )
        + "\n",
        EXPECTED_PATH / "partial_artifact_read.txt",
    )


@pytest.mark.parametrize(
    "input_path", sorted((EXPECTED_PATH / "inline_code").glob("*.json")), ids=lambda path: path.stem
)
def test_script_validates_inline_code(input_path: Path, tmp_path: Path) -> None:
    """Exercise safe type expressions and surrounding Markdown attacks through the CLI."""
    analysis_path = tmp_path / "analysis.json"
    paths = {name: tmp_path / name for name in ("deleted-lines", "execution", "marker", "analysis-context", "diff")}
    paths["deleted-lines"].write_text("", encoding="utf-8")
    paths["marker"].write_text("read-boundary-marker\n", encoding="utf-8")
    paths["analysis-context"].write_text("context\n", encoding="utf-8")
    paths["diff"].write_text("diff\n", encoding="utf-8")
    paths["execution"].write_text(
        json.dumps(_execution_record(paths["marker"], paths["analysis-context"], paths["diff"])),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "scripts/validate_release_draft_analysis.py",
            "--analysis-path",
            str(analysis_path),
            "--pr-number",
            "42",
            *(argument for name, path in paths.items() for argument in (f"--{name}-path", str(path))),
        ],
        capture_output=True,
        check=False,
        cwd=Path(__file__).parents[1],
        env={**os.environ, "CLAUDE_OUTPUT": input_path.read_text(encoding="utf-8")},
        text=True,
    )
    assert_output(
        json.dumps(
            {
                "analysis": json.loads(analysis_path.read_text(encoding="utf-8")) if analysis_path.exists() else None,
                "returncode": result.returncode,
                "stderr": result.stderr,
            },
            sort_keys=True,
        )
        + "\n",
        input_path.with_suffix(".txt"),
    )
